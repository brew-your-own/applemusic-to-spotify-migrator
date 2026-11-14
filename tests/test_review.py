import asyncio

from am2sp.models import Candidate, Match
from am2sp.review import ReviewApp
from am2sp.store import Store

ALBUM_KEY = "stromae|racine carrée"


def _setup(tmp_path, library):
    """Playlist tracks are A3 then A1 (playlist order)."""
    store = Store(tmp_path)
    store.save_library(library)
    c1 = Candidate("spotify:track:1", "Ne me quitte pas", ["Jacques Brel"], "x", 230000, 0.7)
    c2 = Candidate("spotify:track:9", "Ne me quitte pas", ["Nina Simone"], "y", 210000, 0.6)
    store.save_matches(
        {
            "A3": Match(status="not_found"),
            "A1": Match(status="ambiguous", score=0.7, candidates=[c1, c2]),
        }
    )
    return store


def test_skip_and_accept(tmp_path, library):
    store = _setup(tmp_path, library)

    async def run():
        app = ReviewApp(store)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current == "A3"
            await pilot.press("s")
            await pilot.pause()
            assert app.current == "A1"
            await pilot.press("enter")  # accept top candidate
            await pilot.pause()
            assert app.current is None

    asyncio.run(run())
    m = store.load_matches()
    assert m["A1"].status == "manual" and m["A1"].uri == "spotify:track:1"
    assert m["A3"].status == "skipped"


def test_pick_second_candidate_via_list(tmp_path, library):
    store = _setup(tmp_path, library)

    async def run():
        app = ReviewApp(store)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")  # A3 → A1
            await pilot.pause()
            assert app.current == "A1"
            await pilot.press("tab", "down", "enter")
            await pilot.pause()

    asyncio.run(run())
    assert store.load_matches()["A1"].uri == "spotify:track:9"


def test_check_below_lists_low_scoring_auto_matches(tmp_path, library):
    store = _setup(tmp_path, library)
    c = Candidate("spotify:track:1", "Ne me quitte pas", ["Jacques Brel"], "x", 230000, 0.86)
    store.save_matches({"A3": Match(status="skipped"), "A1": Match("auto", "spotify:track:1", 0.86, [c])})

    async def run():
        app = ReviewApp(store, check_below=0.90)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._rows() == ["A1"]
            await pilot.press("enter")  # confirm the auto pick
            await pilot.pause()
            assert app.current is None

    asyncio.run(run())
    assert store.load_matches()["A1"].status == "manual"
    assert ReviewApp(store).needs_review(Match("auto", "x", 0.5)) is False  # hidden without check_below


def test_review_albums(tmp_path, library):
    store = Store(tmp_path)
    store.save_library(library)
    good = Candidate("spotify:album:rc", "Racine carrée", ["Stromae"], "2013 · 11 tracks · tracklist 100%", 0, 0.8)
    other = Candidate("spotify:album:x", "Racine carrée (Live)", ["Stromae"], "2015 · 20 tracks", 0, 0.4)
    store.save_album_matches({ALBUM_KEY: Match(status="ambiguous", score=0.8, candidates=[good, other])})

    async def run():
        app = ReviewApp(store, kind="albums")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current == ALBUM_KEY
            assert app.title == "am2sp — review albums"
            await pilot.press("enter")
            await pilot.pause()
            assert app.current is None

    asyncio.run(run())
    m = store.load_album_matches()[ALBUM_KEY]
    assert m.status == "manual" and m.uri == "spotify:album:rc"
    assert store.load_matches() == {}  # track matches untouched
