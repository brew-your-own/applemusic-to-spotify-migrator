"""Extract stage: parse a Music.app `Library.xml` export (File → Library → Export Library…)."""

from __future__ import annotations

import plistlib
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from .models import Library, Playlist, Track


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def _is_system_playlist(p: dict[str, Any]) -> bool:
    # "Library" (Master) and "Music" (Distinguished Kind) mirror the whole library;
    # folders have no tracks of their own.
    return bool(p.get("Master") or "Distinguished Kind" in p or p.get("Folder"))


def parse_track(d: dict[str, Any]) -> Track:
    return Track(
        pid=d["Persistent ID"],
        name=d.get("Name", ""),
        artist=d.get("Artist", ""),
        album_artist=d.get("Album Artist", ""),
        album=d.get("Album", ""),
        composer=d.get("Composer", ""),
        duration_ms=d.get("Total Time"),
        track_number=d.get("Track Number"),
        track_count=d.get("Track Count"),
        disc_number=d.get("Disc Number"),
        year=d.get("Year"),
        date_added=_iso(d.get("Date Added")),
        in_library=not d.get("Playlist Only", False),
        loved=bool(d.get("Loved") or d.get("Favorited")),
        is_video=bool(d.get("Has Video") or d.get("Music Video")),
    )


# Playlists left out by default. Tools that move Spotify likes to Apple Music create a "Spotify Liked Songs"
# playlist; pushing it back to Spotify would only duplicate Liked Songs as a playlist.
DEFAULT_SKIP_PLAYLISTS = ("Spotify Liked Songs",)


def load_library(src: Path | BinaryIO, skip_playlists: Iterable[str] = ()) -> Library:
    if isinstance(src, Path):
        with src.open("rb") as f:
            raw = plistlib.load(f)
    else:
        raw = plistlib.load(src)

    by_id: dict[int, Track] = {}
    for d in raw.get("Tracks", {}).values():
        by_id[d["Track ID"]] = parse_track(d)

    skip = {n.casefold() for n in skip_playlists}
    playlists: list[Playlist] = []
    for p in raw.get("Playlists", []):
        if _is_system_playlist(p) or p.get("Name", "").casefold() in skip:
            continue
        pids = [
            by_id[item["Track ID"]].pid
            for item in p.get("Playlist Items", [])
            if item.get("Track ID") in by_id and not by_id[item["Track ID"]].is_video
        ]
        playlists.append(
            Playlist(
                pid=p["Playlist Persistent ID"],
                name=p.get("Name", "Untitled"),
                description=p.get("Description", ""),
                track_pids=pids,
            )
        )

    return Library(
        tracks={t.pid: t for t in by_id.values()},
        playlists=playlists,
        source_date=_iso(raw.get("Date")),
    )
