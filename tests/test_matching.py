from conftest import sp_track

from am2sp import matching
from am2sp.models import Track


def mk(**kw):
    return Track(pid="x", **({"name": "", "artist": "", "album": ""} | kw))


def test_clean_title():
    assert matching.clean_title("Hey Jude (Remastered 2015)") == "Hey Jude"
    assert matching.clean_title("Song - Live at Wembley") == "Song"
    assert matching.clean_title("Song feat. Someone") == "Song"
    assert matching.clean_title("Symphony No. 9 (Live)") == "Symphony No. 9"


def test_fold_strips_accents():
    assert matching.fold("Électronique & Co.") == "electronique and co"


def test_exact_match_scores_high():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", duration_ms=214000)
    assert matching.score(t, sp_track("1", "formidable", ["Stromae"], "Racine Carrée", 213500)) > 0.95


def test_wrong_artist_scores_low():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", duration_ms=214000)
    s = matching.score(t, sp_track("1", "Formidable", ["Charles Aznavour"], "Best of", 180000))
    assert s < matching.AUTO_ACCEPT


def test_classical_composer_credit_helps():
    t = mk(
        name="Keyboard concerto in G Minor, BWV 1058: I. Allegro",
        artist="David Fray & The Deutsche Kammerphilharmonie Bremen",
        composer="Johann Sebastian Bach",
        album="Bach: Piano Concertos",
        duration_ms=230000,
    )
    sp = sp_track(
        "1",
        "Keyboard Concerto No. 7 in G Minor, BWV 1058: I. Allegro",
        ["Johann Sebastian Bach", "David Fray", "Die Deutsche Kammerphilharmonie Bremen"],
        "Bach: Piano Concertos",
        231000,
    )
    assert matching.score(t, sp) >= matching.AUTO_ACCEPT


def test_build_queries_order_and_dedupe():
    t = mk(name="Papaoutai (Live)", artist="Stromae & Someone", album="Racine carrée")
    qs = matching.build_queries(t)
    assert qs[0] == "track:Papaoutai artist:Stromae"
    assert len(qs) == len(set(qs))
    assert qs[-1] == "Papaoutai"


def test_match_track_stops_early():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", duration_ms=214000)
    calls = []

    def search(q):
        calls.append(q)
        return [sp_track("good", "Formidable", ["Stromae"], "Racine carrée", 214000)]

    m = matching.match_track(t, search)
    assert m.status == "auto" and m.uri == "spotify:track:good"
    assert len(calls) == 1


def test_match_track_ambiguous_and_not_found():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", duration_ms=214000)
    meh = sp_track("meh", "Formidable", ["Stromae"], "Other album", 300000)
    m = matching.match_track(t, lambda q: [meh])
    assert m.status == "ambiguous" and m.uri is None and m.candidates[0].uri == meh["uri"]
    assert matching.match_track(t, lambda q: []).status == "not_found"


def test_candidate_roundtrip_rank():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", duration_ms=214000)
    c = matching.rank(t, [sp_track("1", "Formidable", ["Stromae"], "Racine carrée", 214000)])[0]
    again = matching.rank(t, [matching.candidate_as_result(c)])[0]
    assert again == c


def test_same_song_ignores_album_and_remaster_decoration():
    same_song = matching.same_song
    tr = Track(pid="x", name="Ne me quitte pas", artist="Jacques Brel", album="La valse", duration_ms=230000)
    remaster = sp_track("r", "Ne me quitte pas - Remastered 2013", ["Jacques Brel"], "Best Of", 231000)
    other_artist = sp_track("o", "Ne me quitte pas", ["Nina Simone"], "I Put a Spell", 230000)
    long_live = sp_track("l", "Ne me quitte pas (Live)", ["Jacques Brel"], "Olympia", 290000)
    assert same_song(tr, remaster)
    assert not same_song(tr, other_artist)
    assert not same_song(tr, long_live)


def _counting(results_for):
    calls = []

    def search(q):
        calls.append(q)
        return results_for(q)

    return search, calls


def test_free_text_query_only_when_fielded_finds_nothing():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", duration_ms=214000)
    weak = sp_track("w", "Formidable", ["Someone"], "Else", 100000)  # below GOOD_ENOUGH
    search, calls = _counting(lambda q: [weak])
    matching.match_track(t, search)
    assert calls[0] == "track:Formidable artist:Stromae"
    assert "Formidable Stromae" not in calls

    search, calls = _counting(lambda q: [] if q.startswith("track:") else [weak])
    matching.match_track(t, search)
    assert calls[:2] == ["track:Formidable artist:Stromae", "Formidable Stromae"]


def test_match_track_caps_searches():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", composer="Paul Van Haver")
    search, calls = _counting(lambda q: [])
    assert matching.match_track(t, search).status == "not_found"
    assert len(matching.build_queries(t)) == 5 and len(calls) == matching.MAX_SEARCHES


def test_match_track_stops_at_good_enough():
    t = mk(name="Formidable", artist="Stromae", album="Racine carrée", duration_ms=214000)
    meh = sp_track("meh", "Formidable", ["Stromae"], "Other album", 300000)  # ambiguous, but >= GOOD_ENOUGH
    search, calls = _counting(lambda q: [meh])
    assert matching.match_track(t, search).status == "ambiguous"
    assert len(calls) == 1


# --- albums ------------------------------------------------------------------------------------------------


def _album():
    from am2sp.models import Album

    tracks = [
        mk(name="1. Prelude in C Minor BWV 847", artist="Hélène Grimaud", album="Bach", duration_ms=91040),
        mk(name="2. Fugue in C Minor BWV 847", artist="Hélène Grimaud", album="Bach", duration_ms=101000),
        mk(name="Chaconne", artist="Hélène Grimaud", album="Bach", duration_ms=840000),
    ]
    return Album(key="k", name="Bach", artist="Hélène Grimaud", year=2008, track_count=3, tracks=tracks)


def _sp_album(uri_id, name, artists, total, year):
    return {
        "uri": f"spotify:album:{uri_id}",
        "id": uri_id,
        "name": name,
        "artists": [{"name": a} for a in artists],
        "total_tracks": total,
        "release_date": f"{year}-01-01",
    }


def test_clean_album():
    assert matching.clean_album("Solo Piano II (Deluxe Edition)") == "Solo Piano II"
    assert (
        matching.clean_album("TRON: Legacy - The Complete Edition (Original Motion Picture Soundtrack)")
        == "TRON: Legacy"
    )
    dsotm = "The Dark Side of the Moon"
    assert matching.clean_album(f"{dsotm} (50th Anniversary) [Remastered]") == dsotm


def test_match_album_verifies_tracklist():
    album = _album()
    right = _sp_album("right", "Bach", ["Johann Sebastian Bach", "Hélène Grimaud"], 3, 2008)
    lookalike = _sp_album("look", "Bach", ["Hélène Grimaud"], 3, 2008)  # same metadata, different recordings
    tracks = {
        "right": [
            sp_track("a", "Prelude & Fugue No. 2 in C minor, BWV 847: I. Prelude", ["Bach"], "", 91500),
            sp_track("b", "Prelude & Fugue No. 2 in C minor, BWV 847: II. Fugue", ["Bach"], "", 100500),
            sp_track("c", "Partita No. 2: Chaconne (Arr. Busoni)", ["Bach"], "", 841000),
        ],
        "look": [sp_track("x", "Something else", ["Bach"], "", 300000)],
    }
    fetched = []

    def album_tracks(aid):
        fetched.append(aid)
        return tracks[aid]

    m = matching.match_album(album, lambda q: [lookalike, right], album_tracks)
    assert m.status == "auto" and m.uri == "spotify:album:right"
    assert "tracklist 100%" in m.candidates[0].album
    assert sorted(fetched) == ["look", "right"]  # both top candidates verified


def test_match_album_ambiguous_when_tracklist_differs():
    album = _album()
    live = _sp_album("live", "Bach", ["Hélène Grimaud"], 3, 2008)
    m = matching.match_album(album, lambda q: [live], lambda aid: [sp_track("x", "Other", ["B"], "", 1000)])
    assert m.status != "auto"


def test_same_album_other_edition():
    album = _album()
    assert matching.same_album(album, _sp_album("d", "Bach (Deluxe Edition)", ["Hélène Grimaud"], 20, 2010))
    assert not matching.same_album(album, _sp_album("o", "Bach", ["Glenn Gould"], 3, 1981))
