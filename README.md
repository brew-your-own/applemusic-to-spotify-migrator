# am2sp — Apple Music → Spotify

Moves your Apple Music albums and playlists into Spotify. You run it from the terminal, and a small TUI lets you review matches it isn't sure about.

- Each **album** you saved in Apple Music (3 or more of its tracks in your library) is found on Spotify **as an album** and saved to **Library → Albums**. The match is checked against the album's tracklist, so classical albums with long, differently worded track titles still match reliably.
- Each user **playlist** becomes a private Spotify playlist with the same name and track order, rebuilt track by track. System playlists ("Library", "Music") and music videos are skipped.
- Nothing is added to **Liked Songs**. Library tracks that are neither part of a saved album nor in a playlist are not migrated; `extract` says how many there are.

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
uv run am2sp review --albums               # TUI: resolve ambiguous / not-found albums
uv run am2sp review                        # TUI: resolve ambiguous / not-found playlist tracks
uv run am2sp review --check-below 0.90     # …and also double-check low-scoring auto matches
uv run am2sp status                        # coverage per target
uv run am2sp push --dry-run                # see what would be written
uv run am2sp push                          # write to Spotify (asks for confirmation)
uv run am2sp unmatched -o missing.csv      # what didn't make it
```

Every step is safe to re-run.

- `match` matches albums first (`albums.json`), then playlist tracks (`matches.json`), and only searches what it hasn't matched yet. Add `--retry` to re-search ambiguous and not-found items, and `--no-albums` / `--no-tracks` to do one kind only.
- If Spotify's daily request quota runs out, `match` stops, saves, and says when to re-run it.
- `push` keeps a record in `.am2sp/push.json` of everything it has already written, so it never duplicates albums, tracks or playlists.
- If you delete an imported playlist on Spotify, the next `push` creates it again.

`push` also reads what is already on the Spotify account (for example, leftovers from an earlier migration) and only fills the gaps:

- Albums already in Library → Albums are not saved again, nor are albums saved there in another edition (deluxe, remaster…).
- An Apple playlist with the same name as one of your Spotify playlists (ignoring case and accents) is merged into it: only the missing tracks are appended. If several share the name, the largest one is used.
- A track that is already there as another release of the same song (same title without "Remastered"/"Live" decorations, same artist, length within 15 s) is not added again.
- `push --dry-run` lists these near-duplicates so you can check them before writing.

`extract` leaves out the "Spotify Liked Songs" playlist, which an earlier Spotify → Apple Music move created from your Spotify likes. Use `--no-default-skips` to keep it, and `-x NAME` to leave out other playlists.

Useful `push` options:

| Option | Effect |
|---|---|
| `-p NAME` (repeatable) | Push only the named playlists |
| `--no-playlists` | Skip playlists |
| `--no-albums` | Skip albums |
| `--public` | Create the playlists as public |

### Review TUI keys

| Key | Action |
|---|---|
| `↑` / `↓` | Move through the tracks (or albums, with `review --albums`) |
| `Tab` | Switch to the candidate list |
| `Enter` | Accept the highlighted candidate |
| `s` | Skip the track (it won't be pushed) |
| `u` | Unset the track's match |
| `/` | Search Spotify, or paste an `open.spotify.com/track/…` (or `/album/…`) URL |
| `a` | Toggle showing all tracks, including auto-matched ones, to audit them |
| `q` | Quit |

Every change is saved immediately, to `.am2sp/matches.json` (tracks) or `.am2sp/albums.json` (albums).
