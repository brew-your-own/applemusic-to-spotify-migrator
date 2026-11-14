# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal, single-user CLI/TUI (`am2sp`) that migrates a Music.app `Library.xml` export to Spotify: saved albums become saved Spotify albums, playlists are rebuilt track by track, and nothing goes to Liked Songs. Stack: Python 3.14 managed with uv; Typer and Rich for the CLI, Textual for the review TUI, httpx for the Spotify API, rapidfuzz for scoring. See README.md for the end-user workflow and Spotify app setup.

## Commands

```sh
uv sync                                   # install deps (incl. dev group)
uv run pytest                             # all tests (offline; Spotify is mocked with respx/fakes)
uv run pytest tests/test_matching.py::test_exact_match_scores_high   # single test
uv run ruff check --fix . && uv run ruff format .
uv run am2sp --help                       # CLI entry point: am2sp.cli:app
uv run python scripts/screenshots.py     # regenerate README screenshots (docs/screenshots/) from invented demo data
```

## Architecture

The pipeline has three stages. Each one reads and writes JSON under the state dir (`.am2sp/` by default; set `--state-dir` or `AM2SP_STATE_DIR` to change it). `store.py` owns those files. The Apple track **Persistent ID** (`pid`) is the key that connects all three stages.

1. **extract** (`apple_xml.py`) parses `Library.xml` (a plist) into the models in `models.py` and writes `library.json`.
   - The Master "Library" playlist, the "Music" playlist (Distinguished Kind), folders, and videos are dropped, and so are the playlists in `DEFAULT_SKIP_PLAYLISTS` ("Spotify Liked Songs") unless `--no-default-skips` is passed.
   - `Track.in_library` is false when a track is "Playlist Only". `Library.albums()` groups library tracks by `album_key` (album artist + album); a group with ≥ `MIN_ALBUM_TRACKS` tracks is a saved album. It is derived on the fly, not persisted.
   - `Library.wanted_pids()` is the playlist tracks only: they are the only tracks matched one by one. `solo_tracks()` (in no album and no playlist) are not migrated.
2. **match** (`matching.py`) is pure logic with no I/O. `match_track(track, search_fn)` runs `build_queries` from most to least specific, rationing searches because Development Mode apps have a daily quota: it stops once a candidate scores ≥ `GOOD_ENOUGH`, runs the free-text query only if the fielded `track:… artist:…` one found nothing, and makes at most `MAX_SEARCHES` per track.
   - `score()` is a weighted mix of title, artist, album, and duration similarity. The composer counts as an artist, because Spotify credits composers on classical tracks.
   - `match_album(album, search_albums, album_tracks)` scores album candidates on name/artist/track count/year, then fetches the tracklists of the top `ALBUM_VERIFY` and blends in `tracklist_coverage` (title + duration ±5 s). Album matches reuse `Match`/`Candidate` (the candidate's `album` field holds "year · N tracks · tracklist X%") and live in `albums.json`, keyed by album key.
   - Each match gets a status: `auto`, `ambiguous`, or `not_found`. The review TUI (`review.py`) sets `manual` and `skipped`. Only `auto` and `manual` with a URI count as resolved, and only those get pushed.
3. **push** (`push.py`) separates `plan()`, which is pure and used by `status` and `--dry-run`, from `execute()`, which does the writes.
   - It saves album URIs via `PUT /me/library` and rebuilds playlists. It is idempotent: `push.json` records the saved album URIs (`albums`) and, for each Apple playlist pid, `{spotify_id, added[]}`. The state is saved after every batch, so a crash loses nothing.
   - `execute` works against the `SpotifyLike` protocol, so tests use an in-memory fake.
   - Before planning, `fetch_existing` + `reconcile` fold the account's current state into push state: albums already saved (or another edition, `matching.same_album`) go into `albums`, and an Apple playlist with an owned same-name Spotify playlist gets linked to it (`merged: true`) with its current items as `added`. `matching.same_song` (title without decorations, artist, ±15 s, album ignored) also treats another release of a song already there as done.

`spotify.py` is a thin httpx client with no spotipy.
- Login is PKCE with a one-shot callback server on `127.0.0.1:8888`.
- The token is cached at `~/.config/am2sp/token.json` and the client ID at `~/.config/am2sp/config.json` (or the `SPOTIFY_CLIENT_ID` env var). Override the directory with `AM2SP_CONFIG_DIR`.
- `request()` refreshes the token on expiry or a 401, sleeps for `Retry-After` on a 429 (raising `RateLimitError` instead when it exceeds `MAX_RETRY_WAIT`, e.g. daily quota exhausted), and backs off on 5xx.

## Spotify API constraints (February 2026 Development Mode migration)

Spotify changed its Web API for Development Mode apps in February 2026. Do not reintroduce the old endpoints, and do not add spotipy, which still uses them.

| Use | Not the old | Notes |
|---|---|---|
| `POST /me/playlists` | `/users/{id}/playlists` | |
| `POST /playlists/{id}/items` | `/tracks` | 100 URIs per request, sent in the body |
| `PUT /me/library?uris=...` | `PUT /me/tracks`, `PUT /me/albums` | Takes **URIs, not IDs** (tracks or albums), in the **query string**, max 40 per request. Reading `GET /me/albums` is still allowed |
| `GET /search` | | `limit` max is **10** (was 50) |
| `GET /playlists/{id}/items` | `/tracks` | Entries carry `item`, not `track`; only your own playlists return items |

Also removed:
- Batch `GET /tracks?ids=`. Use `GET /tracks/{id}`.
- Reading playlists you don't own.

## Data

`Library.xml` and `.am2sp/` hold personal data and are gitignored. The XML has no ISRCs, so matching relies on text and duration only. The Apple Music API is a possible later source that would supply ISRCs.
