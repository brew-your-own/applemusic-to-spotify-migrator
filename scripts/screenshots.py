"""Regenerate the README screenshots of the review TUI from invented demo data (no real library involved).

    uv run python scripts/screenshots.py

Writes SVGs to docs/screenshots/.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from am2sp.models import Candidate, Library, Match, Playlist, Track
from am2sp.review import ReviewApp
from am2sp.store import Store

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SIZE = (132, 34)


def _album_tracks(prefix: str, album: str, artist: str, composer: str, titles: list[str], added: str) -> list[Track]:
    return [
        Track(
            pid=f"{prefix}{i:02d}",
            name=title,
            artist=artist,
            album_artist=artist,
            album=album,
            composer=composer,
            duration_ms=60_000 + (i * 37_000) % 240_000,
            track_number=i,
            track_count=len(titles),
            year=2021,
            date_added=added,
            in_library=True,
        )
        for i, title in enumerate(titles, 1)
    ]


def demo_library() -> Library:
    goldberg = ["Aria", *[f"Variatio {i} a 1 Clav." for i in range(1, 31)], "Aria da capo"]
    nocturnes = [f"Nocturne No. {i}" for i in range(1, 11)]
    night = ["Intro", "Night Drive", "Neon Rain", "Overpass", "Afterglow", "Static", "Last Exit", "Home"]
    tracks = [
        *_album_tracks(
            "G", "Bach: Goldberg Variations, BWV 988", "Mira Lindqvist", "J.S. Bach", goldberg, "2024-01-05"
        ),
        *_album_tracks("N", "Chopin: Nocturnes", "Tomás Rey", "Frédéric Chopin", nocturnes, "2024-02-11"),
        *_album_tracks("D", "Night Drive (Deluxe Edition)", "The Neon Hours", "", night, "2024-03-20"),
        Track(
            "P1",
            "Clair de lune",
            "Léa Martin",
            album="Suite bergamasque",
            composer="Claude Debussy",
            duration_ms=301_000,
        ),
        Track("P2", "Blue in Green", "The Harbor Trio", album="Late Set", duration_ms=337_000),
        Track(
            "P3",
            "Gymnopédie No. 1",
            "Léa Martin",
            album="Satie: Piano Works",
            composer="Erik Satie",
            duration_ms=205_000,
        ),
        Track("P4", "Sunrise Over the Bay", "Oskar Lund", album="Morning Tide", duration_ms=244_000),
    ]
    playlists = [Playlist("PL1", "Sunday Morning", track_pids=["P1", "P2", "P3", "P4"])]
    return Library(tracks={t.pid: t for t in tracks}, playlists=playlists)


def demo_album_matches(lib: Library) -> dict[str, Match]:
    by_name = {a.name: a for a in lib.albums()}

    def key(name: str) -> str:
        return by_name[name].key

    goldberg = [
        Candidate(
            "spotify:album:g1",
            "Bach: Goldberg Variations, BWV 988",
            ["Johann Sebastian Bach", "Mira Lindqvist"],
            "2021 · 32 tracks · tracklist 94%",
            0,
            0.83,
        ),
        Candidate(
            "spotify:album:g2",
            "Goldberg Variations",
            ["Johann Sebastian Bach", "Pieter Hall"],
            "1999 · 32 tracks · tracklist 41%",
            0,
            0.61,
        ),
        Candidate(
            "spotify:album:g3",
            "Bach: Goldberg Variations, BWV 988: Aria",
            ["Mira Lindqvist"],
            "2021 · 1 tracks",
            0,
            0.40,
        ),
    ]
    nocturnes = [
        Candidate(
            "spotify:album:n1",
            "Chopin: Nocturnes",
            ["Frédéric Chopin", "Tomás Rey"],
            "2021 · 21 tracks · tracklist 70%",
            0,
            0.78,
        ),
        Candidate(
            "spotify:album:n2",
            "Chopin: Complete Nocturnes",
            ["Frédéric Chopin", "Tomás Rey"],
            "2022 · 21 tracks · tracklist 60%",
            0,
            0.72,
        ),
    ]
    night = [
        Candidate("spotify:album:d1", "Night Drive", ["The Neon Hours"], "2021 · 6 tracks · tracklist 75%", 0, 0.86),
    ]
    return {
        key("Bach: Goldberg Variations, BWV 988"): Match("ambiguous", None, 0.83, goldberg),
        key("Chopin: Nocturnes"): Match("ambiguous", None, 0.78, nocturnes),
        key("Night Drive (Deluxe Edition)"): Match("auto", "spotify:album:d1", 0.86, night),
    }


def demo_track_matches() -> dict[str, Match]:
    clair = [
        Candidate(
            "spotify:track:c1",
            "Suite bergamasque, L. 75: III. Clair de lune",
            ["Claude Debussy", "Léa Martin"],
            "Debussy: Piano Works",
            302_000,
            0.81,
        ),
        Candidate(
            "spotify:track:c2", "Clair de lune", ["Claude Debussy", "Ivan Petrov"], "Moonlight Classics", 288_000, 0.74
        ),
        Candidate(
            "spotify:track:c3",
            "Clair de lune (Arr. for Strings)",
            ["Claude Debussy", "Aurora Strings"],
            "Strings at Night",
            315_000,
            0.62,
        ),
    ]
    blue = [
        Candidate("spotify:track:b1", "Blue in Green - Live", ["The Harbor Trio"], "Live at the Harbor", 402_000, 0.71),
    ]
    return {
        "P1": Match("ambiguous", None, 0.81, clair),
        "P2": Match("ambiguous", None, 0.71, blue),
        "P3": Match("auto", "spotify:track:s1", 0.97),
        "P4": Match("not_found"),
    }


async def _shoot(store: Store, kind: str, filename: str, check_below: float | None = None) -> None:
    app = ReviewApp(store, None, check_below=check_below, kind=kind)  # type: ignore[arg-type]
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        app.save_screenshot(filename, str(OUT))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp))
        lib = demo_library()
        assert [a.name for a in lib.albums()] == [
            "Bach: Goldberg Variations, BWV 988",
            "Chopin: Nocturnes",
            "Night Drive (Deluxe Edition)",
        ]
        store.save_library(lib)
        store.save_album_matches(demo_album_matches(lib))
        store.save_matches(demo_track_matches())
        asyncio.run(_shoot(store, "albums", "review-albums.svg", check_below=0.90))
        asyncio.run(_shoot(store, "tracks", "review-tracks.svg"))
    for f in sorted(OUT.glob("*.svg")):
        print(f.relative_to(OUT.parent.parent))


if __name__ == "__main__":
    main()
