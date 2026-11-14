"""Match stage logic: query building and candidate scoring. Pure functions, no network."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any

from rapidfuzz import fuzz

from .models import Album, Candidate, Match, Track

# Total score at/above which a match is accepted without review.
AUTO_ACCEPT = 0.85
# Below this, the best candidate is considered "not found" (candidates are still kept for review).
MIN_PLAUSIBLE = 0.55
# match_track stops searching once the best candidate reaches this, and never runs more than MAX_SEARCHES queries.
GOOD_ENOUGH = 0.70
MAX_SEARCHES = 3

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
    """Run queries in order, stopping as soon as more searching is unlikely to change the outcome.

    Searches are rationed (Development Mode apps get a daily request quota):
    - stop at a candidate >= GOOD_ENOUGH: it is auto-accepted or goes to review either way, and further
      queries rarely lift it to AUTO_ACCEPT;
    - the free-text "title artist" query only runs if the fielded `track:… artist:…` one found nothing,
      since otherwise it mostly returns the same tracks;
    - at most MAX_SEARCHES per track.

    `search` takes a query string and returns raw Spotify track objects.
    """
    qs = build_queries(track)
    fielded = bool(qs) and qs[0].startswith("track:")
    pool: list[dict[str, Any]] = []
    candidates: list[Candidate] = []
    searches = 0
    for i, q in enumerate(qs):
        if searches == MAX_SEARCHES:
            break
        if i == 1 and fielded and pool:
            continue
        pool.extend(search(q))
        searches += 1
        candidates = rank(track, pool)
        if candidates and candidates[0].score >= GOOD_ENOUGH:
            break
    return classify(candidates)


# Thresholds for `same_song`: is a Spotify track already on the account the same recording as an Apple track,
# possibly from another release (remaster, compilation, single)? Album is deliberately ignored.
SAME_TITLE = 0.90
SAME_ARTIST = 0.80
SAME_DURATION_S = 15


def song_key(title: str) -> str:
    """Coarse index key for `same_song` lookups."""
    return fold(clean_title(title)) or fold(title)


def same_song(track: Track, sp: dict[str, Any]) -> bool:
    if _sim(clean_title(track.name), clean_title(sp.get("name", ""))) < SAME_TITLE:
        return False
    sp_artists = " ".join(a["name"] for a in sp.get("artists", []))
    artist = max(_sim(track.artist, sp_artists), _sim(track.album_artist, sp_artists), _sim(track.composer, sp_artists))
    if artist < SAME_ARTIST:
        return False
    a, b = track.duration_ms, sp.get("duration_ms")
    return not (a and b) or abs(a - b) <= SAME_DURATION_S * 1000


# --- albums --------------------------------------------------------------------------------------------------
# A saved Apple album is matched to a Spotify album as a whole: search by album name + artist, score on
# metadata, then confirm the best candidates by comparing tracklists (titles + durations).

ALBUM_WEIGHTS = {"name": 0.40, "artist": 0.30, "tracks": 0.20, "year": 0.10}
ALBUM_MAX_SEARCHES = 2
ALBUM_VERIFY = 2  # how many top candidates get their tracklist fetched
_ALBUM_NOISE = re.compile(
    r"\s*[\(\[][^)\]]*\b(soundtrack|original|score|remaster(ed)?|deluxe|edition|expanded|anniversary|bonus|"
    r"version|live)\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)
_ALBUM_DASH = re.compile(r"\s+-\s+.*\b(edition|remaster(ed)?|deluxe|version|soundtrack)\b.*$", re.IGNORECASE)


def clean_album(s: str) -> str:
    """Drop edition decorations ("(Deluxe Edition)", " - The Complete Edition", "[Remastered]")."""
    prev = None
    while prev != s:
        prev, s = s, _ALBUM_DASH.sub("", _ALBUM_NOISE.sub("", s)).strip()
    return s


def album_queries(album: Album) -> list[str]:
    name = _q(clean_album(album.name)) or _q(album.name)
    artist = _q(primary_artist(album.artist))
    qs = [f"album:{name} artist:{artist}", f"{name} {artist}"] if artist else []
    qs.append(name)
    return list(dict.fromkeys(q for q in qs if q.strip()))


def _count_score(a: int | None, b: int | None) -> float:
    if not a or not b:
        return 0.5
    return max(0.0, 1 - abs(a - b) / max(a, b) * 2)


def _year_score(a: int | None, release_date: str | None) -> float:
    if not a or not release_date or not release_date[:4].isdigit():
        return 0.5
    d = abs(a - int(release_date[:4]))
    return 1.0 if d == 0 else 0.6 if d == 1 else 0.0


def album_meta_score(album: Album, sp: dict[str, Any]) -> float:
    sp_artists = " ".join(a["name"] for a in sp.get("artists", []))
    name = max(_sim(album.name, sp.get("name", "")), _sim(clean_album(album.name), clean_album(sp.get("name", ""))))
    # Classical albums on Spotify often list the composer first and the performer second.
    artist = max(_sim(album.artist, sp_artists), _sim(album.composers, sp_artists) * 0.8)
    tracks = _count_score(album.track_count or len(album.tracks), sp.get("total_tracks"))
    year = _year_score(album.year, sp.get("release_date"))
    w = ALBUM_WEIGHTS
    return round(w["name"] * name + w["artist"] * artist + w["tracks"] * tracks + w["year"] * year, 4)


def _same_album_track(t: Track, sp: dict[str, Any]) -> bool:
    title = max(_sim(t.name, sp.get("name", "")), _sim(clean_title(t.name), clean_title(sp.get("name", ""))))
    a, b = t.duration_ms, sp.get("duration_ms")
    if a and b:
        return abs(a - b) <= 5000 and title >= 0.5
    return title >= 0.9


def tracklist_coverage(album: Album, sp_tracks: list[dict[str, Any]]) -> float:
    """Share of the saved Apple tracks found on the Spotify album (each Spotify track used once)."""
    if not album.tracks:
        return 0.0
    left = list(sp_tracks)
    hits = 0
    for t in album.tracks:
        i = next((i for i, s in enumerate(left) if _same_album_track(t, s)), None)
        if i is not None:
            hits += 1
            left.pop(i)
    return hits / len(album.tracks)


def album_candidate(sp: dict[str, Any], score: float, coverage: float | None) -> Candidate:
    year = (sp.get("release_date") or "")[:4] or "?"
    info = f"{year} · {sp.get('total_tracks', '?')} tracks"
    if coverage is not None:
        info += f" · tracklist {coverage:.0%}"
    return Candidate(
        uri=sp["uri"],
        name=sp.get("name", ""),
        artists=[a["name"] for a in sp.get("artists", [])],
        album=info,
        duration_ms=0,
        score=score,
    )


def rank_albums(album: Album, results: Iterable[dict[str, Any]], keep: int = 8) -> list[tuple[float, dict[str, Any]]]:
    seen: dict[str, dict[str, Any]] = {}
    for sp in results:
        if sp and sp.get("uri") and sp["uri"] not in seen:
            seen[sp["uri"]] = sp
    scored = [(album_meta_score(album, sp), sp) for sp in seen.values()]
    return sorted(scored, key=lambda x: x[0], reverse=True)[:keep]


def match_album(
    album: Album,
    search: Callable[[str], list[dict[str, Any]]],
    album_tracks: Callable[[str], list[dict[str, Any]]],
) -> Match:
    """Search for the album, then verify the top candidates' tracklists.

    Final score = half metadata, half tracklist coverage, so a candidate only auto-matches if its
    tracklist really contains the saved tracks. Unverified candidates keep half their metadata score.
    """
    pool: list[dict[str, Any]] = []
    ranked: list[tuple[float, dict[str, Any]]] = []
    for q in album_queries(album)[:ALBUM_MAX_SEARCHES]:
        pool.extend(search(q))
        ranked = rank_albums(album, pool)
        if ranked and ranked[0][0] >= AUTO_ACCEPT:
            break
    candidates = []
    for i, (meta, sp) in enumerate(ranked):
        if i < ALBUM_VERIFY and meta >= MIN_PLAUSIBLE:
            cov = tracklist_coverage(album, album_tracks(sp["id"]))
            candidates.append(album_candidate(sp, round(0.5 * meta + 0.5 * cov, 4), cov))
        else:
            candidates.append(album_candidate(sp, round(0.5 * meta, 4), None))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return classify(candidates)


def same_album(album: Album, sp: dict[str, Any]) -> bool:
    """Another edition of the same album (deluxe, remaster…): same name once decorations are dropped, same artist."""
    if fold(clean_album(album.name)) != fold(clean_album(sp.get("name", ""))):
        return False
    sp_artists = " ".join(a["name"] for a in sp.get("artists", []))
    return max(_sim(album.artist, sp_artists), _sim(album.composers, sp_artists)) >= SAME_ARTIST


def album_search_candidates(album: Album, results: list[dict[str, Any]]) -> list[Candidate]:
    """Candidates from a manual search in the review TUI (metadata only, no tracklist fetch)."""
    return [album_candidate(sp, meta, None) for meta, sp in rank_albums(album, results)]
