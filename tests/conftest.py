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
            **{"Date Added": datetime(2019, 5, 1), "Loved": True, "Track Number": 3, "Track Count": 11, "Year": 2013},
        ),
        "3": _track(3, "A3", "Papaoutai", "Stromae", "Racine carrée", 232000, **{"Playlist Only": True}),
        "4": _track(4, "A4", "Some Video", "Someone", "Clip", 200000, **{"Has Video": True}),
        # Two more saved tracks from "Racine carrée" so it counts as a saved album (A2, A5, A6).
        "5": _track(
            5,
            "A5",
            "Tous les mêmes",
            "Stromae",
            "Racine carrée",
            213000,
            **{"Date Added": datetime(2021, 1, 1), "Track Number": 9, "Track Count": 11},
        ),
        "6": _track(
            6,
            "A6",
            "Carmen",
            "Stromae",
            "Racine carrée",
            190000,
            **{"Date Added": datetime(2021, 1, 2), "Track Number": 1, "Track Count": 11},
        ),
        # Solo: saved, in no playlist, the only saved track from its album.
        "7": _track(
            7, "A7", "Money", "Pink Floyd", "The Dark Side of the Moon", 382000, **{"Date Added": datetime(2018, 1, 1)}
        ),
        # Solo: saved, no album tag.
        "8": _track(8, "A8", "Hoy en clase 231", "", "", 600000, **{"Date Added": datetime(2018, 2, 1)}),
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
