def test_skips_system_playlists_and_videos(library):
    assert [p.name for p in library.playlists] == ["French"]
    assert library.playlists[0].track_pids == ["A3", "A1"]  # order kept, video dropped


def test_saved_albums_are_grouped_in_tracklist_order(library):
    [album] = library.albums()
    assert album.key == "stromae|racine carrée"
    assert (album.name, album.artist, album.year, album.track_count) == ("Racine carrée", "Stromae", 2013, 11)
    assert [t.pid for t in album.tracks] == ["A6", "A2", "A5"]  # by track number
    assert library.tracks["A2"].loved


def test_only_playlist_tracks_need_track_matching(library):
    assert library.wanted_pids() == ["A3", "A1"]  # album tracks A2/A5/A6 are matched as an album


def test_solo_tracks_are_not_migrated(library):
    # A1 is saved but a lone track from its album: it's covered by the playlist, so not solo.
    assert sorted(t.pid for t in library.solo_tracks()) == ["A7", "A8"]


def test_roundtrip(library):
    from am2sp.models import Library

    assert Library.from_dict(library.to_dict()) == library


def test_skip_playlists_by_name(library_xml):
    import io

    from am2sp.apple_xml import load_library

    assert load_library(io.BytesIO(library_xml), skip_playlists=["french"]).playlists == []
