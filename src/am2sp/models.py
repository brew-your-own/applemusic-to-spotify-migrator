"""Normalized intermediate model shared by every stage (extract → match → push)."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MIN_ALBUM_TRACKS = 3  # this many saved tracks from one album → the album is a target (saved as an album)


def album_key(t: Track) -> str:
    """Groups saved tracks into albums: folded album artist (or artist) + album name."""
    return f"{(t.album_artist or t.artist).strip().lower()}|{t.album.strip().lower()}"


MatchStatus = Literal["auto", "manual", "ambiguous", "not_found", "skipped"]
RESOLVED: frozenset[str] = frozenset({"auto", "manual"})


@dataclass
class Track:
    """One Apple Music track. `pid` (Persistent ID) is the stable key across all stages."""

    pid: str
    name: str
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    composer: str = ""
    duration_ms: int | None = None
    track_number: int | None = None
    track_count: int | None = None  # tracks on the album, per Apple
    disc_number: int | None = None
    year: int | None = None
    date_added: str | None = None  # ISO-8601; only set for tracks in the library proper
    in_library: bool = False  # False for "Playlist Only" tracks
    loved: bool = False
    is_video: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Track:
        return cls(**d)


@dataclass
class Playlist:
    pid: str
    name: str
    description: str = ""
    track_pids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Playlist:
        return cls(**d)


@dataclass
class Library:
    tracks: dict[str, Track]
    playlists: list[Playlist]
    source_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_date": self.source_date,
            "tracks": {k: asdict(v) for k, v in self.tracks.items()},
            "playlists": [asdict(p) for p in self.playlists],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Library:
        return cls(
            tracks={k: Track.from_dict(v) for k, v in d["tracks"].items()},
            playlists=[Playlist.from_dict(p) for p in d["playlists"]],
            source_date=d.get("source_date"),
        )

    def wanted_pids(self) -> list[str]:
        """Every track pid that needs a track-level match: the playlist tracks, in stable order.

        Saved albums are matched as albums (see `albums()`), and loose library tracks are not migrated.
        """
        return list(dict.fromkeys(pid for p in self.playlists for pid in p.track_pids))

    def _saved_tracks(self) -> list[Track]:
        return [t for t in self.tracks.values() if t.in_library and not t.is_video]

    def albums(self) -> list[Album]:
        """Albums saved in Apple Music: >= MIN_ALBUM_TRACKS library tracks sharing album artist + album name."""
        groups: dict[str, list[Track]] = {}
        for t in self._saved_tracks():
            if t.album.strip():
                groups.setdefault(album_key(t), []).append(t)
        out = []
        for key, ts in groups.items():
            if len(ts) < MIN_ALBUM_TRACKS:
                continue
            ts.sort(key=lambda t: (t.disc_number or 1, t.track_number or 0))
            artist = ts[0].album_artist or Counter(t.artist for t in ts).most_common(1)[0][0]
            out.append(
                Album(
                    key=key,
                    name=ts[0].album,
                    artist=artist,
                    year=max((t.year for t in ts if t.year), default=None),
                    track_count=max((t.track_count for t in ts if t.track_count), default=None),
                    tracks=ts,
                    date_added=min((t.date_added for t in ts if t.date_added), default=None),
                )
            )
        return sorted(out, key=lambda a: a.date_added or "")

    def solo_tracks(self) -> list[Track]:
        """Library tracks that are neither part of a saved album nor in a playlist: not migrated."""
        in_album = {t.pid for a in self.albums() for t in a.tracks}
        in_playlist = {pid for p in self.playlists for pid in p.track_pids}
        return [t for t in self._saved_tracks() if t.pid not in in_album and t.pid not in in_playlist]


@dataclass
class Album:
    """An album saved in Apple Music, derived from its library tracks (not persisted; see `Library.albums`)."""

    key: str
    name: str
    artist: str
    year: int | None
    track_count: int | None
    tracks: list[Track]
    date_added: str | None = None

    @property
    def composers(self) -> str:
        return " ".join(dict.fromkeys(t.composer for t in self.tracks if t.composer))


@dataclass
class Candidate:
    """A Spotify track (or, for album matches, a Spotify album: `album` then holds "year · N tracks · …")."""

    uri: str
    name: str
    artists: list[str]
    album: str
    duration_ms: int
    score: float

    @property
    def label(self) -> str:
        base = f"{self.name} — {', '.join(self.artists)} [{self.album}]"
        if not self.duration_ms:
            return base
        secs = self.duration_ms // 1000
        return f"{base} ({secs // 60}:{secs % 60:02d})"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Candidate:
        return cls(**d)


@dataclass
class Match:
    status: MatchStatus
    uri: str | None = None
    score: float = 0.0
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED and self.uri is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Match:
        return cls(
            status=d["status"],
            uri=d.get("uri"),
            score=d.get("score", 0.0),
            candidates=[Candidate.from_dict(c) for c in d.get("candidates", [])],
        )
