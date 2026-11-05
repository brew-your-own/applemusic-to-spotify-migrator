import io
import plistlib
from datetime import datetime

import pytest

from am2sp.apple_xml import load_library


def _track(tid, pid, name, artist, album, ms, **extra):
    return {
        "Track ID": tid,
        "Persistent ID": pid,
        "Name": name,
        "Artist": artist,
        "Album Artist": artist,
        "Album": album,
        "Total Time": ms,
        **extra,
    }


@pytest.fixture
def library_xml() -> bytes:
    tracks = {
        "1": _track(
            1,
            "A1",
            "Ne me quitte pas",
            "Jacques Brel",
            "La valse à mille temps",
            230000,
            **{"Date Added": datetime(2020, 1, 2)},
        ),
        "2": _track(
            2,
            "A2",
            "Formidable",
            "Stromae",
            "Racine carrée",
            214000,
            **{"Date Added": datetime(2019, 5, 1), "Loved": True},
        ),
        "3": _track(3, "A3", "Papaoutai", "Stromae", "Racine carrée", 232000, **{"Playlist Only": True}),
        "4": _track(4, "A4", "Some Video", "Someone", "Clip", 200000, **{"Has Video": True}),
    }
    playlists = [
        {"Name": "Library", "Master": True, "Playlist Persistent ID": "LIB", "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Music", "Distinguished Kind": 4, "Playlist Persistent ID": "MUS", "Playlist Items": []},
        {
            "Name": "French",
            "Description": "fr",
            "Playlist Persistent ID": "P1",
            "Playlist Items": [{"Track ID": 3}, {"Track ID": 1}, {"Track ID": 4}],
        },
    ]
    return plistlib.dumps({"Date": datetime(2026, 11, 5), "Tracks": tracks, "Playlists": playlists})


@pytest.fixture
def library(library_xml):
    return load_library(io.BytesIO(library_xml))


def sp_track(uri_id, name, artists, album, ms):
    return {
        "uri": f"spotify:track:{uri_id}",
        "name": name,
        "artists": [{"name": a} for a in artists],
        "album": {"name": album},
        "duration_ms": ms,
    }
