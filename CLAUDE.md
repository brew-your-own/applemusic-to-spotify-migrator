# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal, single-user CLI/TUI (`am2sp`) that migrates a Music.app `Library.xml` export to Spotify. Stack: Python 3.14 managed with uv; Typer and Rich for the CLI, Textual for the review TUI, httpx for the Spotify API, rapidfuzz for scoring. See README.md for the end-user workflow and Spotify app setup.

## Commands

```sh
uv sync                                   # install deps (incl. dev group)
uv run pytest                             # all tests (offline; Spotify is mocked with respx/fakes)
uv run pytest tests/test_matching.py::test_exact_match_scores_high   # single test
uv run ruff check --fix . && uv run ruff format .
uv run am2sp --help                       # CLI entry point: am2sp.cli:app
```

## Architecture

The pipeline has three stages. Each one reads and writes JSON under the state dir (`.am2sp/` by default; set `--state-dir` or `AM2SP_STATE_DIR` to change it). `store.py` owns those files. The Apple track **Persistent ID** (`pid`) is the key that connects all three stages.

1. **extract** (`apple_xml.py`) parses `Library.xml` (a plist) into the models in `models.py` and writes `library.json`.
   - The Master "Library" playlist, the "Music" playlist (Distinguished Kind), folders, and videos are dropped.
   - `Track.in_library` is false when a track is "Playlist Only". The library tracks that go to Liked Songs are `Library.library_tracks()`, sorted oldest `date_added` first.
   - `Library.wanted_pids()` is everything any push target needs.
2. **match** (`matching.py`) is pure logic with no I/O. `match_track(track, search_fn)` runs `build_queries` from most to least specific and stops at the first candidate scoring ≥ `AUTO_ACCEPT`.
   - `score()` is a weighted mix of title, artist, album, and duration similarity. The composer counts as an artist, because Spotify credits composers on classical tracks.
   - Each match gets a status: `auto`, `ambiguous`, or `not_found`. The review TUI (`review.py`) sets `manual` and `skipped`. Only `auto` and `manual` with a URI count as resolved, and only those get pushed.
3. **push** (`push.py`) separates `plan()`, which is pure and used by `status` and `--dry-run`, from `execute()`, which does the writes.
   - It is idempotent: `push.json` records the URIs pushed to Liked Songs and, for each Apple playlist pid, `{spotify_id, added[]}`. The state is saved after every batch, so a crash loses nothing.
   - `execute` works against the `SpotifyLike` protocol, so tests use an in-memory fake.

`spotify.py` is a thin httpx client with no spotipy.
- Login is PKCE with a one-shot callback server on `127.0.0.1:8888`.
- The token is cached at `~/.config/am2sp/token.json` and the client ID at `~/.config/am2sp/config.json` (or the `SPOTIFY_CLIENT_ID` env var). Override the directory with `AM2SP_CONFIG_DIR`.
- `request()` refreshes the token on expiry or a 401, sleeps for `Retry-After` on a 429, and backs off on 5xx.

## Spotify API constraints (February 2026 Development Mode migration)

Spotify changed its Web API for Development Mode apps in February 2026. Do not reintroduce the old endpoints, and do not add spotipy, which still uses them.

| Use | Not the old | Notes |
|---|---|---|
| `POST /me/playlists` | `/users/{id}/playlists` | |
| `POST /playlists/{id}/items` | `/tracks` | 100 URIs per request, sent in the body |
| `PUT /me/library?uris=...` | `/me/tracks` | Takes **URIs, not IDs**, in the **query string**, max 40 per request |
| `GET /search` | | `limit` max is **10** (was 50) |

Also removed:
- Batch `GET /tracks?ids=`. Use `GET /tracks/{id}`.
- Reading playlists you don't own.

## Data

`Library.xml` and `.am2sp/` hold personal data and are gitignored. The XML has no ISRCs, so matching relies on text and duration only. The Apple Music API is a possible later source that would supply ISRCs.
