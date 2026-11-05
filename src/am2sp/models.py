"""Normalized intermediate model shared by every stage (extract → match → push)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

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
        """Every track pid that some push target (library or a playlist) needs, in stable order."""
        pids = [t.pid for t in self.library_tracks()]
        for p in self.playlists:
            pids.extend(p.track_pids)
        return list(dict.fromkeys(pids))

    def library_tracks(self) -> list[Track]:
        """Tracks saved to the library, oldest first (so newest ends up on top in Spotify)."""
        lib = [t for t in self.tracks.values() if t.in_library and not t.is_video]
        return sorted(lib, key=lambda t: t.date_added or "")


@dataclass
class Candidate:
    uri: str
    name: str
    artists: list[str]
    album: str
    duration_ms: int
    score: float

    @property
    def label(self) -> str:
        secs = self.duration_ms // 1000
        return f"{self.name} — {', '.join(self.artists)} [{self.album}] ({secs // 60}:{secs % 60:02d})"

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
