"""On-disk state for each stage, so runs are resumable and inspectable.

<state_dir>/library.json  extract output (normalized Library)
<state_dir>/matches.json  Apple track pid → Match (playlist tracks)
<state_dir>/albums.json   Apple album key → Match (candidates are Spotify albums)
<state_dir>/push.json     what has already been written to Spotify (makes `push` idempotent)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import Library, Match


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


class Store:
    def __init__(self, root: Path):
        self.root = root

    @property
    def library_path(self) -> Path:
        return self.root / "library.json"

    def save_library(self, lib: Library) -> None:
        _write(self.library_path, lib.to_dict())

    def load_library(self) -> Library:
        if not self.library_path.exists():
            raise FileNotFoundError("No extracted library. Run `am2sp extract <Library.xml>` first.")
        return Library.from_dict(_read(self.library_path, {}))

    def load_matches(self) -> dict[str, Match]:
        return {k: Match.from_dict(v) for k, v in _read(self.root / "matches.json", {}).items()}

    def save_matches(self, matches: dict[str, Match]) -> None:
        _write(self.root / "matches.json", {k: m.to_dict() for k, m in matches.items()})

    def load_album_matches(self) -> dict[str, Match]:
        return {k: Match.from_dict(v) for k, v in _read(self.root / "albums.json", {}).items()}

    def save_album_matches(self, matches: dict[str, Match]) -> None:
        _write(self.root / "albums.json", {k: m.to_dict() for k, m in matches.items()})

    def load_push(self) -> dict[str, Any]:
        data = _read(self.root / "push.json", {})
        data.setdefault("albums", [])
        data.setdefault("playlists", {})
        return data

    def save_push(self, data: dict[str, Any]) -> None:
        _write(self.root / "push.json", data)
