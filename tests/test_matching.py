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
