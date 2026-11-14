from conftest import sp_track

from am2sp import push
from am2sp.models import Match
from am2sp.store import Store

ALBUM_KEY = "stromae|racine carrée"  # A2, A5, A6 in the fixture


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


class FakeAccount(FakeSpotify):
    """FakeSpotify that also has pre-existing saved albums and playlists (with full objects)."""

    def __init__(self, albums=(), playlists=None):
        super().__init__()
        self.album_objs = list(albums)
        self.library = [a["uri"] for a in albums]
        self.meta = {}
        for sid, (name, tracks) in (playlists or {}).items():
            self.meta[sid] = (name, list(tracks))
            self.playlists[sid] = [t["uri"] for t in tracks]

    def saved_albums(self):
        return self.album_objs

    def my_playlists(self):
        return [{"id": sid, "name": name} for sid, (name, _) in self.meta.items()]

    def playlist_items(self, playlist_id):
        return self.meta[playlist_id][1]


def sp_album(uri_id, name, artists):
    return {"uri": f"spotify:album:{uri_id}", "id": uri_id, "name": name, "artists": [{"name": a} for a in artists]}


BREL_REMASTER = sp_track("1b", "Ne me quitte pas - Remastered", ["Jacques Brel"], "Infiniment", 231000)
PAPAOUTAI = sp_track("3", "Papaoutai", ["Stromae"], "Racine carrée", 232000)


def _matches():
    return {
        "A1": Match(status="auto", uri="spotify:track:1"),
        "A3": Match(status="ambiguous"),
    }


def _full_matches():
    return _matches() | {"A3": Match(status="manual", uri="spotify:track:3")}


def _albums():
    return {ALBUM_KEY: Match(status="auto", uri="spotify:album:rc")}


def _run(sp, store, lib, matches, albums=None):
    state = store.load_push()
    p = push.plan(lib, matches, albums or {}, state)
    push.execute(sp, store, p, state)
    return p


def _reconcile_and_run(sp, store, lib, matches, albums=None):
    state = store.load_push()
    rec = push.reconcile(lib, matches, albums or {}, state, push.fetch_existing(sp, lib, state))
    p = push.plan(lib, matches, albums or {}, state)
    push.execute(sp, store, p, state)
    return rec


# --- plan / execute -----------------------------------------------------------------------------------------


def test_albums_are_saved_and_nothing_goes_to_liked_songs(tmp_path, library):
    store, sp = Store(tmp_path), FakeSpotify()
    p = _run(sp, store, library, _full_matches(), _albums())
    assert sp.library == ["spotify:album:rc"]  # the album only; no track URIs
    assert p.unresolved_albums == []
    assert sp.playlists == {"sp1": ["spotify:track:3", "spotify:track:1"]}


def test_unresolved_album_is_reported_not_saved(tmp_path, library):
    store, sp = Store(tmp_path), FakeSpotify()
    p = _run(sp, store, library, _matches(), {ALBUM_KEY: Match(status="ambiguous")})
    assert [a.key for a in p.unresolved_albums] == [ALBUM_KEY]
    assert sp.library == []


def test_push_is_idempotent(tmp_path, library):
    store, sp = Store(tmp_path), FakeSpotify()
    p = _run(sp, store, library, _matches(), _albums())
    assert p.unresolved == 1  # A3
    assert sp.playlists == {"sp1": ["spotify:track:1"]}

    _run(sp, store, library, _matches(), _albums())
    assert sp.created == 1
    assert sp.library == ["spotify:album:rc"]
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
    _run(sp, store, library, _full_matches())
    del sp.playlists["sp1"]
    _run(sp, store, library, _full_matches())
    assert sp.playlists == {"sp2": ["spotify:track:3", "spotify:track:1"]}


def test_playlist_and_album_filters(tmp_path, library):
    store = Store(tmp_path)
    p = push.plan(
        library, _matches(), _albums(), store.load_push(), include_albums=False, playlist_filter=lambda n: False
    )
    assert p.album_uris == [] and p.playlists == {}


# --- reconciling with what's already on the account ---------------------------------------------------------


def test_album_already_saved_is_not_saved_again(tmp_path, library):
    store, sp = Store(tmp_path), FakeAccount(albums=[sp_album("rc", "Racine carrée", ["Stromae"])])
    rec = _reconcile_and_run(sp, store, library, _matches(), _albums())
    assert rec.already_saved_albums == 1
    assert sp.library == ["spotify:album:rc"]


def test_other_edition_of_an_album_counts_as_saved(tmp_path, library):
    deluxe = sp_album("rcd", "Racine carrée (Deluxe Edition)", ["Stromae"])
    store, sp = Store(tmp_path), FakeAccount(albums=[deluxe])
    rec = _reconcile_and_run(sp, store, library, _matches(), _albums())
    assert [(a.key, s["uri"]) for a, s in rec.album_other_editions] == [(ALBUM_KEY, "spotify:album:rcd")]
    assert sp.library == ["spotify:album:rcd"]  # the standard edition wasn't added next to it


def test_same_name_playlist_is_merged_not_duplicated(tmp_path, library):
    store = Store(tmp_path)
    sp = FakeAccount(playlists={"old": ("french", [BREL_REMASTER])})  # name match ignores case
    rec = _reconcile_and_run(sp, store, library, _full_matches())
    assert sp.created == 0
    assert rec.merged["P1"][0] == "french" and len(rec.merged["P1"][2]) == 1  # Brel already there (remaster)
    assert sp.playlists["old"] == ["spotify:track:1b", "spotify:track:3"]  # only Papaoutai appended

    # Second run: nothing new, nothing re-merged.
    _reconcile_and_run(sp, store, library, _full_matches())
    assert sp.created == 0 and sp.playlists["old"] == ["spotify:track:1b", "spotify:track:3"]


def test_name_clash_merges_into_largest(tmp_path, library):
    store = Store(tmp_path)
    sp = FakeAccount(playlists={"small": ("French", []), "big": ("French", [PAPAOUTAI])})
    rec = _reconcile_and_run(sp, store, library, _full_matches())
    assert rec.name_clashes == {"French": 2}
    assert sp.playlists["big"] == ["spotify:track:3", "spotify:track:1"]
    assert sp.playlists["small"] == []


def test_playlist_without_counterpart_is_created(tmp_path, library):
    store, sp = Store(tmp_path), FakeAccount(playlists={"x": ("Something else", [PAPAOUTAI])})
    rec = _reconcile_and_run(sp, store, library, _full_matches())
    assert rec.merged == {}
    assert sp.playlists["sp1"] == ["spotify:track:3", "spotify:track:1"]


def test_reconcile_does_not_touch_playlists_already_linked(tmp_path, library):
    store, sp = Store(tmp_path), FakeAccount()
    _reconcile_and_run(sp, store, library, _full_matches())  # creates sp1
    sp.meta["dup"] = ("French", [])  # a same-name playlist appears later
    sp.playlists["dup"] = []
    rec = _reconcile_and_run(sp, store, library, _full_matches())
    assert rec.merged == {} and sp.playlists["dup"] == []
