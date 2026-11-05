"""Typer CLI entry point: `am2sp extract | auth | match | review | status | push | unmatched`."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from . import apple_xml, matching
from . import push as push_mod
from .models import Match
from .spotify import DEFAULT_REDIRECT, Spotify, SpotifyError, login, save_client_id
from .store import Store

app = typer.Typer(help="Migrate an Apple Music library export to Spotify.", no_args_is_help=True)
console = Console()

StateDir = Annotated[
    Path, typer.Option("--state-dir", envvar="AM2SP_STATE_DIR", help="Where library/matches/push state is kept.")
]
DEFAULT_STATE = Path(".am2sp")


def _fail(msg: str) -> None:
    console.print(f"[red]{msg}[/red]")
    raise typer.Exit(1)


def _store(state_dir: Path) -> Store:
    return Store(state_dir)


def _spotify() -> Spotify:
    try:
        return Spotify()
    except SpotifyError as e:
        _fail(str(e))
        raise


@app.command()
def extract(
    xml: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Music.app Library.xml export")],
    state_dir: StateDir = DEFAULT_STATE,
) -> None:
    """Parse Library.xml into <state-dir>/library.json."""
    lib = apple_xml.load_library(xml)
    _store(state_dir).save_library(lib)
    t = Table(title=f"Extracted from {xml.name} ({lib.source_date or 'unknown date'})")
    t.add_column("Playlist")
    t.add_column("Tracks", justify="right")
    t.add_row("[b]Library (→ Liked Songs)[/b]", str(len(lib.library_tracks())))
    for p in lib.playlists:
        t.add_row(p.name, str(len(p.track_pids)))
    console.print(t)
    console.print(f"{len(lib.wanted_pids())} unique tracks to match.")


@app.command()
def auth(
    client_id: Annotated[str | None, typer.Option(help="Spotify app client ID (saved for later runs).")] = None,
    redirect_uri: Annotated[str, typer.Option(help="Must be registered in the Spotify app.")] = DEFAULT_REDIRECT,
) -> None:
    """Log in to Spotify (PKCE) and cache the token in ~/.config/am2sp."""
    if client_id:
        save_client_id(client_id)
    try:
        login(redirect_uri)
        me = Spotify().me()
    except SpotifyError as e:
        _fail(str(e))
    console.print(f"[green]Logged in as {me.get('display_name') or me['id']}[/green]")


@app.command()
def match(
    state_dir: StateDir = DEFAULT_STATE,
    retry: Annotated[bool, typer.Option(help="Re-run search for ambiguous / not_found tracks too.")] = False,
    limit: Annotated[int | None, typer.Option(help="Only match this many tracks (for a trial run).")] = None,
) -> None:
    """Search Spotify for each Apple track. Resumable; results go to matches.json."""
    store = _store(state_dir)
    lib = store.load_library()
    matches = store.load_matches()
    redo = {"ambiguous", "not_found"} if retry else set()
    todo = [p for p in lib.wanted_pids() if p not in matches or matches[p].status in redo]
    if limit:
        todo = todo[:limit]
    if not todo:
        console.print("Nothing to match. Use --retry to re-search unresolved tracks.")
        return
    sp = _spotify()
    with Progress(
        TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(), console=console
    ) as prog:
        task = prog.add_task("Matching", total=len(todo))
        try:
            for i, pid in enumerate(todo, 1):
                tr = lib.tracks[pid]
                prog.update(task, description=f"[dim]{tr.name[:40]:<40}[/dim]")
                matches[pid] = matching.match_track(tr, sp.search_tracks)
                prog.advance(task)
                if i % 20 == 0:
                    store.save_matches(matches)
        finally:
            store.save_matches(matches)
    _print_status(lib, matches)


@app.command()
def review(state_dir: StateDir = DEFAULT_STATE) -> None:
    """Open the TUI to resolve ambiguous / not-found matches."""
    from .review import ReviewApp

    try:
        sp: Spotify | None = Spotify()
    except SpotifyError:
        sp = None
    ReviewApp(_store(state_dir), sp).run()


def _print_status(lib, matches: dict[str, Match]) -> None:
    wanted = lib.wanted_pids()
    c = Counter(matches[p].status if p in matches else "unmatched" for p in wanted)
    t = Table(title="Match status")
    t.add_column("Status")
    t.add_column("Tracks", justify="right")
    for s in ("auto", "manual", "ambiguous", "not_found", "skipped", "unmatched"):
        if c[s]:
            t.add_row(s, str(c[s]))
    console.print(t)


@app.command()
def status(state_dir: StateDir = DEFAULT_STATE) -> None:
    """Show match coverage per target and what `push` would still do."""
    store = _store(state_dir)
    lib = store.load_library()
    matches = store.load_matches()
    _print_status(lib, matches)
    state = store.load_push()
    plan = push_mod.plan(lib, matches, state)
    pending = plan.pending(state)
    t = Table(title="Push targets")
    t.add_column("Target")
    t.add_column("Resolved", justify="right")
    t.add_column("Pending push", justify="right")
    lib_pids = [x.pid for x in lib.library_tracks()]
    res = sum(1 for p in lib_pids if p in matches and matches[p].resolved)
    t.add_row("Liked Songs", f"{res}/{len(lib_pids)}", str(len(plan.library_uris)))
    for pl in lib.playlists:
        res = sum(1 for p in pl.track_pids if p in matches and matches[p].resolved)
        t.add_row(pl.name, f"{res}/{len(pl.track_pids)}", str(len(pending.get(pl.pid, []))))
    console.print(t)


@app.command()
def push(
    state_dir: StateDir = DEFAULT_STATE,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be written, change nothing.")] = False,
    library: Annotated[bool, typer.Option(help="Save library tracks to Liked Songs.")] = True,
    playlist: Annotated[
        list[str] | None, typer.Option("--playlist", "-p", help="Only these playlists (repeatable). Default: all.")
    ] = None,
    no_playlists: Annotated[bool, typer.Option("--no-playlists", help="Skip playlists entirely.")] = False,
    public: Annotated[bool, typer.Option(help="Create playlists as public.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Write resolved matches to Spotify. Safe to re-run: already-pushed items are skipped."""
    store = _store(state_dir)
    lib = store.load_library()
    matches = store.load_matches()
    state = store.load_push()
    names = set(playlist or [])
    if names:
        unknown = names - {p.name for p in lib.playlists}
        if unknown:
            _fail(f"Unknown playlist(s): {', '.join(sorted(unknown))}")
    flt = (lambda n: False) if no_playlists else ((lambda n: n in names) if names else None)
    plan = push_mod.plan(lib, matches, state, include_library=library, playlist_filter=flt)
    pending = plan.pending(state)

    t = Table(title="Push plan" + (" (dry run)" if dry_run else ""))
    t.add_column("Target")
    t.add_column("Action")
    t.add_column("Tracks", justify="right")
    if library:
        t.add_row("Liked Songs", "save", str(len(plan.library_uris)))
    for pid, (name, _, uris) in plan.playlists.items():
        exists = pid in state["playlists"]
        n = len(pending[pid])
        if n or not exists:
            t.add_row(name, "append" if exists else "create", str(n if exists else len(uris)))
    console.print(t)
    if plan.unresolved:
        console.print(
            f"[yellow]{plan.unresolved} track slots are unresolved and will be left out (run `am2sp review`).[/yellow]"
        )
    total = len(plan.library_uris) + sum(len(v) for v in pending.values())
    if total == 0:
        console.print("Nothing to push.")
        return
    if dry_run:
        return
    if not yes and not typer.confirm("Write this to your Spotify account?"):
        raise typer.Abort()

    sp = _spotify()
    with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), console=console) as prog:
        task = prog.add_task("Pushing", total=total)

        def tick(label: str, n: int) -> None:
            prog.update(task, description=label[:30], advance=n)

        try:
            push_mod.execute(sp, store, plan, state, public=public, on_progress=tick)
        except SpotifyError as e:
            _fail(f"{e}\nProgress so far is saved; re-run `am2sp push` to continue.")
    console.print("[green]Done.[/green]")


@app.command()
def unmatched(
    state_dir: StateDir = DEFAULT_STATE,
    out: Annotated[Path | None, typer.Option("-o", help="CSV file (default: stdout).")] = None,
) -> None:
    """Export tracks that won't be pushed (unresolved or skipped) as CSV."""
    store = _store(state_dir)
    lib = store.load_library()
    matches = store.load_matches()
    f = out.open("w", newline="") if out else sys.stdout
    w = csv.writer(f)
    w.writerow(["status", "title", "artist", "album", "playlists"])
    in_pl: dict[str, list[str]] = {}
    for pl in lib.playlists:
        for p in pl.track_pids:
            in_pl.setdefault(p, []).append(pl.name)
    for pid in lib.wanted_pids():
        m = matches.get(pid)
        if m and m.resolved:
            continue
        tr = lib.tracks[pid]
        w.writerow([m.status if m else "unmatched", tr.name, tr.artist, tr.album, "; ".join(in_pl.get(pid, []))])
    if out:
        f.close()
        console.print(f"Wrote {out}")


if __name__ == "__main__":
    app()
