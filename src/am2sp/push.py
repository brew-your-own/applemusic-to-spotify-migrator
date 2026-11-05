"""Load stage: write resolved matches to Spotify. Idempotent via push.json, saved after every batch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import Library, Match
from .spotify import LIBRARY_BATCH, PLAYLIST_BATCH
from .store import Store

PLAYLIST_SUFFIX = ""  # e.g. " (from Apple Music)"


class SpotifyLike(Protocol):
    def create_playlist(self, name: str, description: str = "", public: bool = False) -> dict[str, Any]: ...
    def playlist_exists(self, playlist_id: str) -> bool: ...
    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None: ...
    def save_to_library(self, uris: list[str]) -> None: ...


@dataclass
class Plan:
    library_uris: list[str] = field(default_factory=list)
    # apple playlist pid → (name, description, all resolved uris in order)
    playlists: dict[str, tuple[str, str, list[str]]] = field(default_factory=dict)
    unresolved: int = 0

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
    state: dict[str, Any],
    *,
    include_library: bool = True,
    playlist_filter: Callable[[str], bool] | None = None,
) -> Plan:
    p = Plan()
    if include_library:
        uris, missing = _uris([t.pid for t in lib.library_tracks()], matches)
        done = set(state["library"])
        p.library_uris = [u for u in uris if u not in done]
        p.unresolved += missing
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
    for i in range(0, len(p.library_uris), LIBRARY_BATCH):
        batch = p.library_uris[i : i + LIBRARY_BATCH]
        sp.save_to_library(batch)
        state["library"].extend(batch)
        store.save_push(state)
        on_progress("Liked Songs", len(batch))

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
