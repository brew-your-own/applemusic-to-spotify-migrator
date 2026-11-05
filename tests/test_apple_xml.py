def test_skips_system_playlists_and_videos(library):
    assert [p.name for p in library.playlists] == ["French"]
    assert library.playlists[0].track_pids == ["A3", "A1"]  # order kept, video dropped


def test_library_tracks_oldest_first_excluding_playlist_only(library):
    assert [t.pid for t in library.library_tracks()] == ["A2", "A1"]
    assert library.tracks["A2"].loved


def test_wanted_pids_dedupes(library):
    assert library.wanted_pids() == ["A2", "A1", "A3"]


def test_roundtrip(library):
    from am2sp.models import Library

    assert Library.from_dict(library.to_dict()) == library
