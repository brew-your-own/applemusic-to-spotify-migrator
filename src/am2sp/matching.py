"""Match stage logic: query building and candidate scoring. Pure functions, no network."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any

from rapidfuzz import fuzz

from .models import Candidate, Match, Track

# Total score at/above which a match is accepted without review.
AUTO_ACCEPT = 0.85
# Below this, the best candidate is considered "not found" (candidates are still kept for review).
MIN_PLAUSIBLE = 0.55

WEIGHTS = {"title": 0.45, "artist": 0.30, "album": 0.10, "duration": 0.15}

_NOISE = re.compile(
    r"\s*[\(\[][^)\]]*\b(remaster(ed)?|live|version|edit|mono|stereo|deluxe|bonus|"
    r"anniversary|expanded|edition|mix|feat\.?|ft\.?|with)\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)
_DASH_SUFFIX = re.compile(r"\s+-\s+.*\b(remaster(ed)?|live|version|edit|mono|mix)\b.*$", re.IGNORECASE)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def fold(s: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _PUNCT.sub(" ", s.lower().replace("&", " and "))
    return _SPACES.sub(" ", s).strip()


def clean_title(s: str) -> str:
    """Drop decorations like "(Remastered 2011)", " - Live", "feat. X" that differ between services."""
    s = _NOISE.sub("", s)
    s = _DASH_SUFFIX.sub("", s)
    s = _FEAT.sub("", s)
    return s.strip()


def primary_artist(s: str) -> str:
    return re.split(r"\s*(?:&|,|\bfeat\.?|\bft\.?|\bx\b|\band\b)\s*", s, maxsplit=1)[0].strip()


def _sim(a: str, b: str) -> float:
    a, b = fold(a), fold(b)
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100


def _duration_score(a_ms: int | None, b_ms: int | None) -> float:
    if not a_ms or not b_ms:
        return 0.5
    diff = abs(a_ms - b_ms) / 1000
    if diff <= 2:
        return 1.0
    return max(0.0, 1 - (diff - 2) / 28)


def score(track: Track, sp: dict[str, Any]) -> float:
    sp_artists = " ".join(a["name"] for a in sp.get("artists", []))
    title = max(_sim(track.name, sp["name"]), _sim(clean_title(track.name), clean_title(sp["name"])))
    # Classical tracks on Spotify often credit the composer as an artist.
    artist = max(
        _sim(track.artist, sp_artists),
        _sim(track.album_artist, sp_artists),
        _sim(track.composer, sp_artists) * 0.9,
    )
    album = _sim(clean_title(track.album), clean_title(sp.get("album", {}).get("name", "")))
    duration = _duration_score(track.duration_ms, sp.get("duration_ms"))
    total = (
        WEIGHTS["title"] * title
        + WEIGHTS["artist"] * artist
        + WEIGHTS["album"] * album
        + WEIGHTS["duration"] * duration
    )
    return round(total, 4)


def _q(v: str) -> str:
    return v.replace('"', "").strip()


def build_queries(track: Track) -> list[str]:
    """Search queries from most to least specific. Duplicates are removed, order kept."""
    title = _q(clean_title(track.name)) or _q(track.name)
    artist = _q(primary_artist(track.artist or track.album_artist))
    album = _q(clean_title(track.album))
    qs = []
    if artist:
        qs.append(f"track:{title} artist:{artist}")
        qs.append(f"{title} {artist}")
    if album:
        qs.append(f"{title} {album}")
    if track.composer and track.composer != track.artist:
        qs.append(f"{title} {_q(track.composer)}")
    qs.append(title)
    return list(dict.fromkeys(q for q in qs if q.strip()))


def to_candidate(track: Track, sp: dict[str, Any]) -> Candidate:
    return Candidate(
        uri=sp["uri"],
        name=sp["name"],
        artists=[a["name"] for a in sp.get("artists", [])],
        album=sp.get("album", {}).get("name", ""),
        duration_ms=sp.get("duration_ms", 0),
        score=score(track, sp),
    )


def candidate_as_result(c: Candidate) -> dict[str, Any]:
    """Inverse of to_candidate, so stored candidates can be re-ranked alongside fresh results."""
    return {
        "uri": c.uri,
        "name": c.name,
        "artists": [{"name": a} for a in c.artists],
        "album": {"name": c.album},
        "duration_ms": c.duration_ms,
    }


def rank(track: Track, results: Iterable[dict[str, Any]], keep: int = 8) -> list[Candidate]:
    seen: dict[str, Candidate] = {}
    for sp in results:
        if sp and sp.get("uri") and sp["uri"] not in seen:
            seen[sp["uri"]] = to_candidate(track, sp)
    return sorted(seen.values(), key=lambda c: c.score, reverse=True)[:keep]


def classify(candidates: list[Candidate]) -> Match:
    if not candidates:
        return Match(status="not_found")
    best = candidates[0]
    if best.score >= AUTO_ACCEPT:
        return Match(status="auto", uri=best.uri, score=best.score, candidates=candidates)
    if best.score >= MIN_PLAUSIBLE:
        return Match(status="ambiguous", score=best.score, candidates=candidates)
    return Match(status="not_found", score=best.score, candidates=candidates)


def match_track(track: Track, search: Callable[[str], list[dict[str, Any]]]) -> Match:
    """Run queries in order, stopping early once a confident match appears.

    `search` takes a query string and returns raw Spotify track objects.
    """
    pool: list[dict[str, Any]] = []
    candidates: list[Candidate] = []
    for q in build_queries(track):
        pool.extend(search(q))
        candidates = rank(track, pool)
        if candidates and candidates[0].score >= AUTO_ACCEPT:
            break
    return classify(candidates)
