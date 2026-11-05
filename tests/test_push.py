from am2sp import push
from am2sp.models import Match
from am2sp.store import Store


class FakeSpotify:
    def __init__(self):
        self.playlists: dict[str, list[str]] = {}
        self.library: list[str] = []
        self.created = 0

    def create_playlist(self, name, description="", public=False):
        self.created += 1
        pid = f"sp{self.created}"
        self.playlists[pid] = []
        return {"id": pid}

    def playlist_exists(self, playlist_id):
        return playlist_id in self.playlists

    def add_to_playlist(self, playlist_id, uris):
        self.playlists[playlist_id].extend(uris)

    def save_to_library(self, uris):
        self.library.extend(uris)


def _matches():
    return {
        "A1": Match(status="auto", uri="spotify:track:1"),
        "A2": Match(status="manual", uri="spotify:track:2"),
        "A3": Match(status="ambiguous"),
    }


def _run(sp, store, lib, matches):
    state = store.load_push()
    p = push.plan(lib, matches, state)
    push.execute(sp, store, p, state)
    return p


def test_push_is_idempotent(tmp_path, library):
    store, sp = Store(tmp_path), FakeSpotify()
    p = _run(sp, store, library, _matches())
    assert p.unresolved == 1  # A3, only in the playlist
    assert sp.library == ["spotify:track:2", "spotify:track:1"]  # oldest added first
    assert sp.playlists == {"sp1": ["spotify:track:1"]}

    _run(sp, store, library, _matches())
    assert sp.created == 1
    assert sp.library == ["spotify:track:2", "spotify:track:1"]
    assert sp.playlists == {"sp1": ["spotify:track:1"]}


def test_newly_resolved_tracks_are_appended(tmp_path, library):
    store, sp = Store(tmp_path), FakeSpotify()
    m = _matches()
    _run(sp, store, library, m)
    m["A3"] = Match(status="manual", uri="spotify:track:3")
    _run(sp, store, library, m)
    assert sp.created == 1
    assert sp.playlists["sp1"] == ["spotify:track:1", "spotify:track:3"]


def test_playlist_deleted_on_spotify_is_recreated_in_full(tmp_path, library):
    store, sp = Store(tmp_path), FakeSpotify()
    m = _matches() | {"A3": Match(status="manual", uri="spotify:track:3")}
    _run(sp, store, library, m)
    del sp.playlists["sp1"]
    _run(sp, store, library, m)
    assert sp.playlists == {"sp2": ["spotify:track:3", "spotify:track:1"]}


def test_playlist_filter(tmp_path, library):
    store = Store(tmp_path)
    p = push.plan(library, _matches(), store.load_push(), include_library=False, playlist_filter=lambda n: False)
    assert p.library_uris == [] and p.playlists == {}
