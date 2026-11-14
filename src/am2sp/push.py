"""Load stage: write resolved matches to Spotify. Idempotent via push.json, saved after every batch.

Two kinds of target:
- albums saved in Apple Music → saved to Spotify's Library → Albums (as albums, not track by track);
- playlists → rebuilt track by track.
Nothing is added to Liked Songs.

Before the first write, `reconcile` folds in what is already on the account (e.g. leftovers from an earlier
migration): albums already saved (even another edition) are marked done, Apple playlists are merged into owned
Spotify playlists of the same name, and tracks already present as another release of the same song are not
added again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .matching import fold, same_album, same_song, song_key
from .models import Album, Library, Match, Track
from .spotify import LIBRARY_BATCH, PLAYLIST_BATCH
from .store import Store

PLAYLIST_SUFFIX = ""  # e.g. " (from Apple Music)"


class SpotifyLike(Protocol):
    def create_playlist(self, name: str, description: str = "", public: bool = False) -> dict[str, Any]: ...
    def playlist_exists(self, playlist_id: str) -> bool: ...
    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None: ...
    def save_to_library(self, uris: list[str]) -> None: ...


class SpotifyReader(Protocol):
    def saved_albums(self) -> list[dict[str, Any]]: ...
    def my_playlists(self) -> list[dict[str, Any]]: ...
    def playlist_items(self, playlist_id: str) -> list[dict[str, Any]]: ...


@dataclass
class Existing:
    """Snapshot of the account: saved albums, and owned playlists whose name matches an Apple playlist."""

    albums: list[dict[str, Any]] = field(default_factory=list)
    # spotify playlist id → (name, tracks)
    playlists: dict[str, tuple[str, list[dict[str, Any]]]] = field(default_factory=dict)


def _name_key(name: str) -> str:
    return fold(name)


def fetch_existing(sp: SpotifyReader, lib: Library, state: dict[str, Any], *, albums: bool = True) -> Existing:
    """Read the account. Only playlists that could be merged (same name, not yet linked) have their items fetched."""
    linked = set(state["playlists"])
    wanted = {_name_key(p.name) for p in lib.playlists if p.pid not in linked}
    wanted |= {_name_key(p.name + PLAYLIST_SUFFIX) for p in lib.playlists if p.pid not in linked}
    ex = Existing(albums=sp.saved_albums() if albums else [])
    for pl in sp.my_playlists():
        if _name_key(pl.get("name", "")) in wanted:
            ex.playlists[pl["id"]] = (pl["name"], sp.playlist_items(pl["id"]))
    return ex


@dataclass
class Reconciliation:
    already_saved_albums: int = 0
    # Apple album and the other edition of it already saved on Spotify
    album_other_editions: list[tuple[Album, dict[str, Any]]] = field(default_factory=list)
    # apple playlist pid → (spotify playlist name, tracks already there, near-dups found)
    merged: dict[str, tuple[str, int, list[tuple[Track, dict[str, Any]]]]] = field(default_factory=dict)
    # apple playlist name → number of same-name Spotify playlists (the largest is used)
    name_clashes: dict[str, int] = field(default_factory=dict)


class _Index:
    def __init__(self, tracks: list[dict[str, Any]]):
        self.uris = {t["uri"] for t in tracks}
        self.by_key: dict[str, list[dict[str, Any]]] = {}
        for t in tracks:
            self.by_key.setdefault(song_key(t.get("name", "")), []).append(t)

    def find(self, track: Track, uri: str) -> dict[str, Any] | None:
        """The existing track that `track`/`uri` duplicates (same uri, or same song on another release), else None."""
        if uri in self.uris:
            return {"uri": uri}
        return next((t for t in self.by_key.get(song_key(track.name), []) if same_song(track, t)), None)


def reconcile(
    lib: Library,
    matches: dict[str, Match],
    album_matches: dict[str, Match],
    state: dict[str, Any],
    ex: Existing,
) -> Reconciliation:
    """Fold the account snapshot into push state, so `plan`/`execute` only fill gaps. Mutates `state`."""
    rec = Reconciliation()

    saved = {a["uri"] for a in ex.albums}
    done = set(state["albums"])
    for album in lib.albums():
        m = album_matches.get(album.key)
        if not (m and m.resolved and m.uri) or m.uri in done:
            continue
        if m.uri in saved:
            rec.already_saved_albums += 1
        elif other := next((a for a in ex.albums if same_album(album, a)), None):
            rec.album_other_editions.append((album, other))
        else:
            continue
        state["albums"].append(m.uri)
        done.add(m.uri)

    by_name: dict[str, list[str]] = {}
    for sid, (name, _) in ex.playlists.items():
        by_name.setdefault(_name_key(name), []).append(sid)
    claimed: set[str] = {e["spotify_id"] for e in state["playlists"].values()}
    for pl in lib.playlists:
        if pl.pid in state["playlists"]:
            continue
        sids = by_name.get(_name_key(pl.name), []) or by_name.get(_name_key(pl.name + PLAYLIST_SUFFIX), [])
        sids = [s for s in sids if s not in claimed]
        if not sids:
            continue
        if len(sids) > 1:
            rec.name_clashes[pl.name] = len(sids)
        sid = max(sids, key=lambda s: len(ex.playlists[s][1]))
        sp_name, items = ex.playlists[sid]
        idx = _Index(items)
        added = [t["uri"] for t in items]
        near: list[tuple[Track, dict[str, Any]]] = []
        for tpid in pl.track_pids:
            m = matches.get(tpid)
            if not (m and m.resolved and m.uri) or m.uri in idx.uris:
                continue
            hit = idx.find(lib.tracks[tpid], m.uri)
            if hit is not None:
                near.append((lib.tracks[tpid], hit))
                added.append(m.uri)
        state["playlists"][pl.pid] = {"spotify_id": sid, "name": pl.name, "added": added, "merged": True}
        claimed.add(sid)
        rec.merged[pl.pid] = (sp_name, len(items), near)
    return rec


@dataclass
class Plan:
    album_uris: list[str] = field(default_factory=list)  # still to save
    unresolved_albums: list[Album] = field(default_factory=list)
    # apple playlist pid → (name, description, all resolved uris in order)
    playlists: dict[str, tuple[str, str, list[str]]] = field(default_factory=dict)
    unresolved: int = 0  # playlist track slots without a resolved match

    def pending(self, state: dict[str, Any]) -> dict[str, list[str]]:
        """Per playlist, the uris not yet added according to push state."""
        out = {}
        for pid, (_, _, uris) in self.playlists.items():
            done = set(state["playlists"].get(pid, {}).get("added", []))
            out[pid] = [u for u in uris if u not in done]
        return out


def _uris(pids: list[str], matches: dict[str, Match]) -> tuple[list[str], int]:
    out, missing = [], 0
    for pid in pids:
        m = matches.get(pid)
        if m and m.resolved and m.uri:
            out.append(m.uri)
        else:
            missing += 1
    return list(dict.fromkeys(out)), missing  # Spotify allows dupes; we don't want them


def plan(
    lib: Library,
    matches: dict[str, Match],
    album_matches: dict[str, Match],
    state: dict[str, Any],
    *,
    include_albums: bool = True,
    playlist_filter: Callable[[str], bool] | None = None,
) -> Plan:
    p = Plan()
    if include_albums:
        done = set(state["albums"])
        for album in lib.albums():
            m = album_matches.get(album.key)
            if not (m and m.resolved and m.uri):
                p.unresolved_albums.append(album)
            elif m.uri not in done and m.uri not in p.album_uris:
                p.album_uris.append(m.uri)
    for pl in lib.playlists:
        if playlist_filter and not playlist_filter(pl.name):
            continue
        uris, missing = _uris(pl.track_pids, matches)
        p.playlists[pl.pid] = (pl.name, pl.description, uris)
        p.unresolved += missing
    return p


def execute(
    sp: SpotifyLike,
    store: Store,
    p: Plan,
    state: dict[str, Any],
    *,
    public: bool = False,
    on_progress: Callable[[str, int], None] = lambda label, n: None,
) -> None:
    for i in range(0, len(p.album_uris), LIBRARY_BATCH):
        batch = p.album_uris[i : i + LIBRARY_BATCH]
        sp.save_to_library(batch)
        state["albums"].extend(batch)
        store.save_push(state)
        on_progress("Albums", len(batch))

    for apple_pid, (name, desc, all_uris) in p.playlists.items():
        entry = state["playlists"].get(apple_pid)
        if entry and not sp.playlist_exists(entry["spotify_id"]):
            entry = None  # deleted on Spotify since last run: recreate it
        if entry is None:
            if not all_uris:
                continue
            created = sp.create_playlist(name + PLAYLIST_SUFFIX, desc or "Imported from Apple Music", public)
            entry = {"spotify_id": created["id"], "name": name, "added": []}
            state["playlists"][apple_pid] = entry
            store.save_push(state)
        done = set(entry["added"])
        uris = [u for u in all_uris if u not in done]
        for i in range(0, len(uris), PLAYLIST_BATCH):
            batch = uris[i : i + PLAYLIST_BATCH]
            sp.add_to_playlist(entry["spotify_id"], batch)
            entry["added"].extend(batch)
            store.save_push(state)
            on_progress(name, len(batch))
