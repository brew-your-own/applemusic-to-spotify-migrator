# am2sp — Apple Music → Spotify

Moves your Apple Music library and playlists into Spotify. You run it from the terminal, and a small TUI lets you review matches it isn't sure about.

- Songs in your Apple Music **library** go to Spotify **Liked Songs**. They are added oldest first, so the newest additions end up at the top.
- Each user **playlist** becomes a private Spotify playlist with the same name and track order. System playlists ("Library", "Music") and music videos are skipped.

## One-time setup

1. Create an app at <https://developer.spotify.com/dashboard>:
   - Redirect URI: `http://127.0.0.1:8888/callback`. Spotify no longer accepts `localhost`; it has to be `127.0.0.1`.
   - APIs used: Web API.
   - Under *User Management*, add your own Spotify account's email.
   - Since February 2026, Development Mode apps need the app owner to have Spotify Premium.
2. Export your library from Music.app: **File → Library → Export Library…** → `Library.xml`.
3. `uv sync`

## Usage

```sh
uv run am2sp extract Library.xml           # → .am2sp/library.json
uv run am2sp auth --client-id <CLIENT_ID>  # browser login; token cached in ~/.config/am2sp
uv run am2sp match --limit 50              # trial run; then without --limit (resumable)
uv run am2sp review                        # TUI: resolve ambiguous / not-found tracks
uv run am2sp status                        # coverage per playlist
uv run am2sp push --dry-run                # see what would be written
uv run am2sp push                          # write to Spotify (asks for confirmation)
uv run am2sp unmatched -o missing.csv      # what didn't make it
```

Every step is safe to re-run.

- `match` only searches tracks it hasn't matched yet. Add `--retry` to re-search ambiguous and not-found tracks.
- `push` keeps a record in `.am2sp/push.json` of everything it has already written, so it never duplicates tracks or playlists.
- If you delete an imported playlist on Spotify, the next `push` creates it again.

Useful `push` options:

| Option | Effect |
|---|---|
| `-p NAME` (repeatable) | Push only the named playlists |
| `--no-playlists` | Skip playlists |
| `--no-library` | Skip Liked Songs |
| `--public` | Create the playlists as public |

### Review TUI keys

| Key | Action |
|---|---|
| `↑` / `↓` | Move through the tracks |
| `Tab` | Switch to the candidate list |
| `Enter` | Accept the highlighted candidate |
| `s` | Skip the track (it won't be pushed) |
| `u` | Unset the track's match |
| `/` | Search Spotify, or paste an `open.spotify.com/track/…` URL |
| `a` | Toggle showing all tracks, including auto-matched ones, to audit them |
| `q` | Quit |

Every change is saved to `.am2sp/matches.json` immediately.
