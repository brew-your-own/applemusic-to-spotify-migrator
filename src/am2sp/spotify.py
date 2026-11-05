"""Spotify Web API client (Authorization Code + PKCE).

Targets the endpoints as they exist for Development Mode apps after the February 2026
migration: `POST /me/playlists`, `POST /playlists/{id}/items`, `PUT /me/library` (URIs in the
query string, max 40), `GET /search` (limit max 10).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

API = "https://api.spotify.com/v1"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = [
    "user-library-read",
    "user-library-modify",
    "user-follow-modify",  # listed as required by PUT /me/library
    "playlist-read-private",
    "playlist-modify-private",
    "playlist-modify-public",
]
SEARCH_LIMIT = 10
LIBRARY_BATCH = 40
PLAYLIST_BATCH = 100

CONFIG_DIR = Path(os.environ.get("AM2SP_CONFIG_DIR", Path.home() / ".config" / "am2sp"))


class SpotifyError(RuntimeError):
    pass


def _client_id() -> str:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    if not cid:
        cfg = CONFIG_DIR / "config.json"
        if cfg.exists():
            cid = json.loads(cfg.read_text()).get("client_id")
    if not cid:
        raise SpotifyError("No Spotify client ID. Set SPOTIFY_CLIENT_ID or run `am2sp auth --client-id <id>`.")
    return cid


def save_client_id(cid: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = CONFIG_DIR / "config.json"
    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    data["client_id"] = cid
    cfg.write_text(json.dumps(data, indent=2))


def _token_path() -> Path:
    return CONFIG_DIR / "token.json"


def _save_token(tok: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    p = _token_path()
    p.write_text(json.dumps(tok))
    p.chmod(0o600)


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login(redirect_uri: str = DEFAULT_REDIRECT, open_browser: bool = True) -> dict[str, Any]:
    """Interactive PKCE login: opens the browser and catches the redirect on 127.0.0.1."""
    cid = _client_id()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    url = (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": cid,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "scope": " ".join(SCOPES),
                "state": state,
            }
        )
    )
    parsed = urlparse(redirect_uri)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path != parsed.path:
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>am2sp: you can close this tab.</h2>")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), Handler)
    print(f"Opening browser for Spotify login. If it does not open, visit:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()
    server.server_close()

    if result.get("state") != state:
        raise SpotifyError("OAuth state mismatch")
    if "error" in result:
        raise SpotifyError(f"Spotify authorization failed: {result['error']}")

    r = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "client_id": cid,
            "code_verifier": verifier,
        },
    )
    if r.status_code != 200:
        raise SpotifyError(f"Token exchange failed: {r.status_code} {r.text}")
    tok = r.json()
    _save_token(tok)
    return tok


class Spotify:
    def __init__(self, http: httpx.Client | None = None, token: dict[str, Any] | None = None):
        self.http = http or httpx.Client(base_url=API, timeout=30)
        if token is None:
            p = _token_path()
            if not p.exists():
                raise SpotifyError("Not logged in. Run `am2sp auth` first.")
            token = json.loads(p.read_text())
        self.token = token

    # --- plumbing ---------------------------------------------------------

    def _refresh(self) -> None:
        if "refresh_token" not in self.token:
            raise SpotifyError("Token expired and no refresh token. Run `am2sp auth` again.")
        r = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.token["refresh_token"],
                "client_id": _client_id(),
            },
        )
        if r.status_code != 200:
            raise SpotifyError(f"Token refresh failed ({r.status_code}). Run `am2sp auth` again.")
        new = r.json()
        new.setdefault("refresh_token", self.token["refresh_token"])  # PKCE may rotate it
        _save_token(new)
        self.token = new

    def request(self, method: str, path: str, *, retries: int = 6, **kw: Any) -> httpx.Response:
        for attempt in range(retries):
            if self.token.get("expires_at", float("inf")) < time.time():
                self._refresh()
            headers = {"Authorization": f"Bearer {self.token['access_token']}"}
            r = self.http.request(method, path, headers=headers, **kw)
            if r.status_code == 401 and attempt == 0 and "refresh_token" in self.token:
                self._refresh()
                continue
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "2")) + 1)
                continue
            if r.status_code >= 500:
                time.sleep(2**attempt)
                continue
            if r.status_code >= 400:
                raise SpotifyError(f"{method} {path} → {r.status_code}: {r.text[:300]}")
            return r
        raise SpotifyError(f"{method} {path}: gave up after {retries} attempts")

    # --- endpoints --------------------------------------------------------

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/me").json()

    def search_tracks(self, q: str) -> list[dict[str, Any]]:
        r = self.request(
            "GET",
            "/search",
            params={"q": q, "type": "track", "limit": SEARCH_LIMIT, "market": "from_token"},
        )
        return [t for t in r.json().get("tracks", {}).get("items", []) if t]

    def get_track(self, track_id: str) -> dict[str, Any]:
        return self.request("GET", f"/tracks/{track_id}", params={"market": "from_token"}).json()

    def create_playlist(self, name: str, description: str = "", public: bool = False) -> dict[str, Any]:
        body = {"name": name, "public": public, "description": description[:300]}
        return self.request("POST", "/me/playlists", json=body).json()

    def playlist_exists(self, playlist_id: str) -> bool:
        try:
            self.request("GET", f"/playlists/{playlist_id}", params={"fields": "id"})
            return True
        except SpotifyError as e:
            if "→ 404" in str(e):
                return False
            raise

    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None:
        for i in range(0, len(uris), PLAYLIST_BATCH):
            self.request("POST", f"/playlists/{playlist_id}/items", json={"uris": uris[i : i + PLAYLIST_BATCH]})

    def save_to_library(self, uris: list[str]) -> None:
        for i in range(0, len(uris), LIBRARY_BATCH):
            self.request("PUT", "/me/library", params={"uris": ",".join(uris[i : i + LIBRARY_BATCH])})
