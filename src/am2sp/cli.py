"""Typer CLI entry point: `am2sp extract | auth | match | review | status | push | unmatched`."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
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
    skip_playlist: Annotated[
        list[str] | None, typer.Option("--skip-playlist", "-x", help="Leave out this playlist (repeatable).")
    ] = None,
    default_skips: Annotated[
        bool,
        typer.Option(help=f"Also leave out {', '.join(apple_xml.DEFAULT_SKIP_PLAYLISTS)!r} (already on Spotify)."),
    ] = True,
) -> None:
    """Parse Library.xml into <state-dir>/library.json."""
    skip = [*(skip_playlist or []), *(apple_xml.DEFAULT_SKIP_PLAYLISTS if default_skips else ())]
    lib = apple_xml.load_library(xml, skip_playlists=skip)
    _store(state_dir).save_library(lib)
    t = Table(title=f"Extracted from {xml.name} ({lib.source_date or 'unknown date'})")
    t.add_column("Target")
    t.add_column("Count", justify="right")
    t.add_row("[b]Saved albums (→ Library → Albums)[/b]", str(len(lib.albums())))
    for p in lib.playlists:
        t.add_row(p.name, f"{len(p.track_pids)} tracks")
    console.print(t)
    console.print(f"{len(lib.albums())} albums and {len(lib.wanted_pids())} unique playlist tracks to match.")
    if skip:
        console.print(f"[dim]Left out playlists: {', '.join(skip)}[/dim]")
    if solo := lib.solo_tracks():
        console.print(
            f"[dim]Not migrated: {len(solo)} library tracks in no playlist and not part of a saved album.[/dim]"
        )


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


def _run_matching(
    label: str,
    todo: list[str],
    describe: Callable[[str], str],
    do_match: Callable[[str], Match],
    matches: dict[str, Match],
    save: Callable[[dict[str, Match]], None],
) -> None:
    """Match each key in `todo`, saving every 20 and on exit; stop cleanly on a Spotify error (e.g. quota)."""
    done = 0
    try:
        with Progress(
            TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(), console=console
        ) as prog:
            task = prog.add_task(label, total=len(todo))
            try:
                for i, key in enumerate(todo, 1):
                    prog.update(task, description=f"[dim]{label}: {describe(key)[:40]:<40}[/dim]")
                    matches[key] = do_match(key)
                    done = i
                    prog.advance(task)
                    if i % 20 == 0:
                        save(matches)
            finally:
                save(matches)
    except SpotifyError as e:
        _fail(f"Stopped after {done}/{len(todo)} {label.lower()} (saved).\n{e}")


@app.command()
def match(
    state_dir: StateDir = DEFAULT_STATE,
    retry: Annotated[bool, typer.Option(help="Re-run search for ambiguous / not_found items too.")] = False,
    limit: Annotated[int | None, typer.Option(help="Only match this many albums/tracks (for a trial run).")] = None,
    albums: Annotated[bool, typer.Option(help="Match saved albums.")] = True,
    tracks: Annotated[bool, typer.Option(help="Match playlist tracks.")] = True,
) -> None:
    """Search Spotify for each saved album, then each playlist track. Resumable.

    Results go to albums.json and matches.json.
    """
    store = _store(state_dir)
    lib = store.load_library()
    redo = {"ambiguous", "not_found"} if retry else set()
    album_matches = store.load_album_matches()
    matches = store.load_matches()
    by_key = {a.key: a for a in lib.albums()}
    album_todo = [k for k in by_key if k not in album_matches or album_matches[k].status in redo] if albums else []
    track_todo = [p for p in lib.wanted_pids() if p not in matches or matches[p].status in redo] if tracks else []
    if limit:
        album_todo, track_todo = album_todo[:limit], track_todo[:limit]
    if not album_todo and not track_todo:
        console.print("Nothing to match. Use --retry to re-search unresolved items.")
    else:
        sp = _spotify()
        if album_todo:
            _run_matching(
                "Albums",
                album_todo,
                lambda k: by_key[k].name,
                lambda k: matching.match_album(by_key[k], sp.search_albums, sp.album_tracks),
                album_matches,
                store.save_album_matches,
            )
        if track_todo:
            _run_matching(
                "Tracks",
                track_todo,
                lambda p: lib.tracks[p].name,
                lambda p: matching.match_track(lib.tracks[p], sp.search_tracks),
                matches,
                store.save_matches,
            )
    _print_status(lib, matches, album_matches)


@app.command()
def review(
    state_dir: StateDir = DEFAULT_STATE,
    albums: Annotated[bool, typer.Option("--albums", help="Review album matches instead of playlist tracks.")] = False,
    check_below: Annotated[
        float | None,
        typer.Option(
            min=0.0,
            max=1.0,
            help="Also list auto matches scoring below this (e.g. 0.90). Enter confirms one, s skips it.",
        ),
    ] = None,
) -> None:
    """Open the TUI to resolve ambiguous / not-found matches (playlist tracks, or albums with --albums)."""
    from .review import ReviewApp

    try:
        sp: Spotify | None = Spotify()
    except SpotifyError:
        sp = None
    ReviewApp(_store(state_dir), sp, check_below=check_below, kind="albums" if albums else "tracks").run()


_STATUSES = ("auto", "manual", "ambiguous", "not_found", "skipped", "unmatched")


def _print_status(lib, matches: dict[str, Match], album_matches: dict[str, Match]) -> None:
    ca = Counter(album_matches[a.key].status if a.key in album_matches else "unmatched" for a in lib.albums())
    ct = Counter(matches[p].status if p in matches else "unmatched" for p in lib.wanted_pids())
    t = Table(title="Match status")
    t.add_column("Status")
    t.add_column("Albums", justify="right")
    t.add_column("Playlist tracks", justify="right")
    for s in _STATUSES:
        if ca[s] or ct[s]:
            t.add_row(s, str(ca[s]), str(ct[s]))
    console.print(t)


@app.command()
def status(state_dir: StateDir = DEFAULT_STATE) -> None:
    """Show match coverage per target and what `push` would still do."""
    store = _store(state_dir)
    lib = store.load_library()
    matches = store.load_matches()
    album_matches = store.load_album_matches()
    _print_status(lib, matches, album_matches)
    state = store.load_push()
    plan = push_mod.plan(lib, matches, album_matches, state)
    pending = plan.pending(state)
    t = Table(title="Push targets")
    t.add_column("Target")
    t.add_column("Resolved", justify="right")
    t.add_column("Pending push", justify="right")
    n_albums = len(lib.albums())
    t.add_row("[b]Albums[/b]", f"{n_albums - len(plan.unresolved_albums)}/{n_albums}", str(len(plan.album_uris)))
    for pl in lib.playlists:
        res = sum(1 for p in pl.track_pids if p in matches and matches[p].resolved)
        t.add_row(pl.name, f"{res}/{len(pl.track_pids)}", str(len(pending.get(pl.pid, []))))
    console.print(t)
    console.print("[dim]Pending ignores what's already on Spotify; `am2sp push --dry-run` checks the account.[/dim]")


def _print_reconciliation(rec: push_mod.Reconciliation, plan: push_mod.Plan, show: int = 15) -> None:
    """Explain what was found on the account and therefore won't be added again."""
    if rec.already_saved_albums:
        console.print(f"{rec.already_saved_albums} albums are already in your Spotify library.")
    if rec.album_other_editions:
        n = len(rec.album_other_editions)
        console.print(f"{n} albums are already saved in another edition, so they are skipped:")
        for album, sp in rec.album_other_editions[:show]:
            artists = ", ".join(a["name"] for a in sp.get("artists", []))
            there = escape(f"{artists} — {sp.get('name', '')} ({(sp.get('release_date') or '')[:4]})")
            console.print(f"  {escape(f'{album.artist} — {album.name}')} [dim]≈ {there}[/dim]")
    near = []
    for pid, (sp_name, _, dups) in rec.merged.items():
        if pid in plan.playlists:
            near += [(sp_name, tr, sp) for tr, sp in dups]
    if near:
        console.print(f"{len(near)} tracks are already there as another release of the same song, so they are skipped:")
        for target, tr, sp in near[:show]:
            artists = ", ".join(a["name"] for a in sp.get("artists", []))
            album = sp.get("album", {}).get("name", "")
            apple = escape(f"{tr.artist} — {tr.name}")
            there = escape(f"{artists} — {sp['name']} [{album}]")
            console.print(f"  [dim]{escape(target)}:[/dim] {apple} [dim]≈ {there}[/dim]")
        if len(near) > show:
            console.print(f"  [dim]… and {len(near) - show} more[/dim]")
    for name, n in rec.name_clashes.items():
        console.print(f"[yellow]{n} Spotify playlists are named '{name}'; merging into the largest one.[/yellow]")


@app.command()
def push(
    state_dir: StateDir = DEFAULT_STATE,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be written, change nothing.")] = False,
    albums: Annotated[bool, typer.Option(help="Save matched albums to Library → Albums.")] = True,
    playlist: Annotated[
        list[str] | None, typer.Option("--playlist", "-p", help="Only these playlists (repeatable). Default: all.")
    ] = None,
    no_playlists: Annotated[bool, typer.Option("--no-playlists", help="Skip playlists entirely.")] = False,
    public: Annotated[bool, typer.Option(help="Create playlists as public.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Save matched albums and rebuild playlists on Spotify, filling gaps only. Nothing goes to Liked Songs.

    Albums already saved (even another edition) are skipped; same-name playlists are merged into rather than
    duplicated, and songs already in them (even as another release) are not added again. Safe to re-run.
    """
    store = _store(state_dir)
    lib = store.load_library()
    matches = store.load_matches()
    album_matches = store.load_album_matches()
    state = store.load_push()
    names = set(playlist or [])
    if names:
        unknown = names - {p.name for p in lib.playlists}
        if unknown:
            _fail(f"Unknown playlist(s): {', '.join(sorted(unknown))}")
    flt = (lambda n: False) if no_playlists else ((lambda n: n in names) if names else None)

    sp = _spotify()
    try:
        with console.status("Reading what's already on your Spotify account…"):
            existing = push_mod.fetch_existing(sp, lib, state, albums=albums)
    except SpotifyError as e:
        _fail(f"{e}\nIf this is a scope error, run `am2sp auth` again.")
    rec = push_mod.reconcile(lib, matches, album_matches, state, existing)
    plan = push_mod.plan(lib, matches, album_matches, state, include_albums=albums, playlist_filter=flt)
    pending = plan.pending(state)

    t = Table(title="Push plan" + (" (dry run)" if dry_run else ""))
    t.add_column("Target")
    t.add_column("Action")
    t.add_column("To add", justify="right")
    if albums:
        t.add_row("[b]Albums[/b]", "save to Library → Albums", str(len(plan.album_uris)))
    for pid, (name, _, uris) in plan.playlists.items():
        n = len(pending[pid])
        if pid in rec.merged:
            sp_name, already, _ = rec.merged[pid]
            t.add_row(name, f"merge into existing '{sp_name}' ({already} tracks)", str(n))
        elif pid not in state["playlists"]:
            if uris:
                t.add_row(name, "create", str(len(uris)))
        elif n:
            t.add_row(name, "append", str(n))
    console.print(t)
    _print_reconciliation(rec, plan)
    if plan.unresolved_albums:
        console.print(
            f"[yellow]{len(plan.unresolved_albums)} albums are unresolved and will be left out "
            "(run `am2sp review --albums`).[/yellow]"
        )
    if plan.unresolved:
        console.print(
            f"[yellow]{plan.unresolved} playlist track slots are unresolved and will be left out "
            "(run `am2sp review`).[/yellow]"
        )
    total = len(plan.album_uris) + sum(len(v) for v in pending.values())
    if total == 0:
        if not dry_run:
            store.save_push(state)  # keep what reconcile learned about the account
        console.print("Nothing to push.")
        return
    if dry_run:
        return
    if not yes and not typer.confirm("Write this to your Spotify account?"):
        raise typer.Abort()

    store.save_push(state)
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
    """Export albums and playlist tracks that won't be pushed (unresolved or skipped) as CSV."""
    store = _store(state_dir)
    lib = store.load_library()
    matches = store.load_matches()
    album_matches = store.load_album_matches()
    f = out.open("w", newline="") if out else sys.stdout
    w = csv.writer(f)
    w.writerow(["kind", "status", "title", "artist", "album", "playlists"])
    for a in lib.albums():
        m = album_matches.get(a.key)
        if not (m and m.resolved):
            w.writerow(["album", m.status if m else "unmatched", "", a.artist, a.name, ""])
    in_pl: dict[str, list[str]] = {}
    for pl in lib.playlists:
        for p in pl.track_pids:
            in_pl.setdefault(p, []).append(pl.name)
    for pid in lib.wanted_pids():
        m = matches.get(pid)
        if m and m.resolved:
            continue
        tr = lib.tracks[pid]
        row = [m.status if m else "unmatched", tr.name, tr.artist, tr.album, "; ".join(in_pl.get(pid, []))]
        w.writerow(["track", *row])
    if out:
        f.close()
        console.print(f"Wrote {out}")


if __name__ == "__main__":
    app()
