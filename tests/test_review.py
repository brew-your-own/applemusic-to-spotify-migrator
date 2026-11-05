import asyncio

from am2sp.models import Candidate, Match
from am2sp.review import ReviewApp
from am2sp.store import Store


def _setup(tmp_path, library):
    store = Store(tmp_path)
    store.save_library(library)
    c1 = Candidate("spotify:track:1", "Ne me quitte pas", ["Jacques Brel"], "x", 230000, 0.7)
    c2 = Candidate("spotify:track:9", "Ne me quitte pas", ["Nina Simone"], "y", 210000, 0.6)
    store.save_matches(
        {
            "A1": Match(status="ambiguous", score=0.7, candidates=[c1, c2]),
            "A2": Match(status="auto", uri="spotify:track:2", score=0.99),
            "A3": Match(status="not_found"),
        }
    )
    return store


def test_accept_and_skip(tmp_path, library):
    store = _setup(tmp_path, library)

    async def run():
        app = ReviewApp(store)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current == "A1"  # A2 is auto → hidden
            await pilot.press("enter")  # accept top candidate
            await pilot.pause()
            assert app.current == "A3"
            await pilot.press("s")
            await pilot.pause()
            assert app.current is None

    asyncio.run(run())
    m = store.load_matches()
    assert m["A1"].status == "manual" and m["A1"].uri == "spotify:track:1"
    assert m["A3"].status == "skipped"
    assert m["A2"].status == "auto"


def test_pick_second_candidate_via_list(tmp_path, library):
    store = _setup(tmp_path, library)

    async def run():
        app = ReviewApp(store)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab", "down", "enter")
            await pilot.pause()

    asyncio.run(run())
    assert store.load_matches()["A1"].uri == "spotify:track:9"
