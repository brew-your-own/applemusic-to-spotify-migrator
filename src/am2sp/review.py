"""Textual TUI for reviewing matches: playlist tracks (default) or saved albums (`kind="albums"`).

Lists ambiguous / not-found items and, with `check_below`, low-scoring auto matches. Every action is saved
immediately. Accepting or skipping an item sets `manual`/`skipped`, which takes it off the list.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import matching
from .models import Album, Candidate, Match
from .store import Store

REVIEW_STATUSES = {"ambiguous", "not_found"}
_TRACK_RE = re.compile(r"(?:spotify:track:|open\.spotify\.com/(?:intl-\w+/)?track/)([A-Za-z0-9]{22})")
_ALBUM_RE = re.compile(r"(?:spotify:album:|open\.spotify\.com/(?:intl-\w+/)?album/)([A-Za-z0-9]{22})")


def _clip(s: str, n: int) -> Text:
    return Text(s if len(s) <= n else s[: n - 1] + "…")


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "?:??"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class _Tracks:
    """Playlist tracks, keyed by Apple track pid."""

    noun = "tracks"
    placeholder = "Spotify search query, or paste a track URL/URI"

    def __init__(self, store: Store):
        self.lib = store.load_library()
        self.matches = store.load_matches()
        self._save = store.save_matches

    def save(self) -> None:
        self._save(self.matches)

    def keys(self) -> list[str]:
        return self.lib.wanted_pids()

    def row(self, key: str) -> tuple[str, str]:
        tr = self.lib.tracks[key]
        return tr.name, tr.artist

    def detail(self, key: str) -> str:
        tr = self.lib.tracks[key]
        return (
            f"[b]{escape(tr.name)}[/b]\n{escape(tr.artist)}"
            + (f"  ·  composer: {escape(tr.composer)}" if tr.composer else "")
            + f"\n[i]{escape(tr.album)}[/i]  ({_fmt_ms(tr.duration_ms)})"
        )

    def default_query(self, key: str) -> str:
        tr = self.lib.tracks[key]
        return f"{matching.clean_title(tr.name)} {matching.primary_artist(tr.artist)}".strip()

    def search(self, sp: Any, key: str, q: str) -> list[Candidate]:
        tr = self.lib.tracks[key]
        results = [sp.get_track(m.group(1))] if (m := _TRACK_RE.search(q)) else sp.search_tracks(q)
        previous = [matching.candidate_as_result(c) for c in self.matches[key].candidates]
        return matching.rank(tr, results + previous, keep=12)


class _Albums:
    """Saved Apple albums, keyed by `Album.key`."""

    noun = "albums"
    placeholder = "Spotify album search query, or paste an album URL/URI"

    def __init__(self, store: Store):
        self.lib = store.load_library()
        self.albums: dict[str, Album] = {a.key: a for a in self.lib.albums()}
        self.matches = store.load_album_matches()
        self._save = store.save_album_matches

    def save(self) -> None:
        self._save(self.matches)

    def keys(self) -> list[str]:
        return list(self.albums)

    def row(self, key: str) -> tuple[str, str]:
        a = self.albums[key]
        return a.name, a.artist

    def detail(self, key: str) -> str:
        a = self.albums[key]
        total = f"{len(a.tracks)}/{a.track_count}" if a.track_count else str(len(a.tracks))
        lines = [f"[b]{escape(a.name)}[/b]", escape(a.artist), f"[i]{a.year or '?'} · {total} tracks saved[/i]", ""]
        lines += [
            f"  {t.track_number or '·':>2}. {escape(t.name[:70])} ({_fmt_ms(t.duration_ms)})" for t in a.tracks[:8]
        ]
        if len(a.tracks) > 8:
            lines.append(f"  … {len(a.tracks) - 8} more")
        return "\n".join(lines)

    def default_query(self, key: str) -> str:
        a = self.albums[key]
        return f"{matching.clean_album(a.name)} {matching.primary_artist(a.artist)}".strip()

    def search(self, sp: Any, key: str, q: str) -> list[Candidate]:
        a = self.albums[key]
        results = [sp.get_album(m.group(1))] if (m := _ALBUM_RE.search(q)) else sp.search_albums(q)
        fresh = matching.album_search_candidates(a, results)
        seen = {c.uri for c in fresh}
        merged = fresh + [c for c in self.matches[key].candidates if c.uri not in seen]
        return sorted(merged, key=lambda c: c.score, reverse=True)[:12]


class ReviewApp(App[None]):
    TITLE = "am2sp — review matches"
    CSS = """
    #tracks { width: 50%; }
    #right { width: 50%; padding: 0 1; }
    #detail { height: auto; padding: 1 0; }
    #candidates { height: 1fr; }
    #search { display: none; }
    #search.visible { display: block; }
    """
    BINDINGS = [
        Binding("enter", "accept", "Accept", priority=False),
        Binding("s", "skip", "Skip"),
        Binding("u", "unset", "Unset"),
        Binding("slash", "search", "Search"),
        Binding("a", "toggle_all", "Show all"),
        Binding("tab", "focus_next", "Switch pane", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        store: Store,
        spotify: Any | None = None,
        check_below: float | None = None,
        kind: Literal["tracks", "albums"] = "tracks",
    ):
        super().__init__()
        self.src: _Tracks | _Albums = _Albums(store) if kind == "albums" else _Tracks(store)
        self.sp = spotify
        self.check_below = check_below
        self.show_all = False
        self.current: str | None = None
        self.title = f"am2sp — review {self.src.noun}"

    @property
    def matches(self) -> dict[str, Match]:
        return self.src.matches

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
            with Vertical(id="right"):
                yield Static(id="detail")
                yield Input(placeholder=self.src.placeholder, id="search")
                yield OptionList(id="candidates")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#tracks", DataTable)
        t.add_columns("status", "score", "title", "artist")
        self.refresh_table()
        t.focus()

    def needs_review(self, m: Match | None) -> bool:
        if m is None:
            return False
        if m.status in REVIEW_STATUSES:
            return True
        return self.check_below is not None and m.status == "auto" and m.score < self.check_below

    def _rows(self) -> list[str]:
        keys = self.src.keys()
        if self.show_all:
            return [k for k in keys if k in self.matches]
        return [k for k in keys if self.needs_review(self.matches.get(k))]

    def refresh_table(self, keep: str | None = None) -> None:
        t = self.query_one("#tracks", DataTable)
        t.clear()
        rows = self._rows()
        for key in rows:
            title, artist = self.src.row(key)
            m = self.matches[key]
            t.add_row(m.status, f"{m.score:.2f}", _clip(title, 26), _clip(artist, 16), key=key)
        todo = sum(1 for k in self.src.keys() if self.needs_review(self.matches.get(k)))
        checking = f" · incl. auto < {self.check_below:.2f}" if self.check_below is not None else ""
        self.sub_title = f"{todo} to review{checking}" + (" · showing all" if self.show_all else "")
        if keep in rows:
            t.move_cursor(row=rows.index(keep))
        elif rows:
            self.show_item(rows[min(t.cursor_row, len(rows) - 1)])
        else:
            self.current = None
            self.query_one("#detail", Static).update("Nothing to review. Press [b]a[/b] to show all, [b]q[/b] to quit.")
            self.query_one("#candidates", OptionList).clear_options()

    def show_item(self, key: str) -> None:
        self.current = key
        m = self.matches[key]
        chosen = next((c for c in m.candidates if c.uri == m.uri), None)
        self.query_one("#detail", Static).update(
            self.src.detail(key)
            + f"\n\nstatus: [b]{m.status}[/b]"
            + (f"  →  {escape(chosen.label)}" if chosen else (f"  →  {m.uri}" if m.uri else ""))
        )
        ol = self.query_one("#candidates", OptionList)
        ol.clear_options()
        for c in m.candidates:
            mark = "✓ " if c.uri == m.uri else "  "
            ol.add_option(Option(Text(f"{mark}{c.score:.2f}  {c.label}"), id=c.uri))
        if not m.candidates:
            ol.add_option(Option("(no candidates — press / to search)", disabled=True))
        else:
            ol.highlighted = 0

    # --- events -------------------------------------------------------------

    @on(DataTable.RowHighlighted, "#tracks")
    def _row(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key and ev.row_key.value:
            self.show_item(ev.row_key.value)

    @on(DataTable.RowSelected, "#tracks")
    def _row_enter(self, ev: DataTable.RowSelected) -> None:
        # DataTable consumes Enter itself; treat it as "accept highlighted candidate".
        self.action_accept()

    @on(OptionList.OptionSelected, "#candidates")
    def _pick(self, ev: OptionList.OptionSelected) -> None:
        if ev.option.id:
            self._resolve(ev.option.id)

    @on(Input.Submitted, "#search")
    def _search(self, ev: Input.Submitted) -> None:
        q = ev.value.strip()
        inp = self.query_one("#search", Input)
        inp.remove_class("visible")
        inp.value = ""
        if q and self.current:
            self.run_search(self.current, q)

    @work(thread=True, exclusive=True)
    def run_search(self, key: str, q: str) -> None:
        if self.sp is None:
            self.call_from_thread(self.notify, "Not logged in to Spotify (run `am2sp auth`).", severity="error")
            return
        try:
            candidates = self.src.search(self.sp, key, q)
        except Exception as e:  # surface API errors in the UI, don't crash it
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        self.matches[key].candidates = candidates
        self.call_from_thread(self._after_search, key)

    def _after_search(self, key: str) -> None:
        self.src.save()
        if self.current == key:
            self.show_item(key)
            self.query_one("#candidates", OptionList).focus()

    # --- actions ------------------------------------------------------------

    def _resolve(self, uri: str) -> None:
        if not self.current:
            return
        m = self.matches[self.current]
        m.status, m.uri = "manual", uri
        m.score = next((c.score for c in m.candidates if c.uri == uri), m.score)
        self._commit()

    def _commit(self) -> None:
        self.src.save()
        cur = self.current
        self.refresh_table(keep=cur if self.show_all else None)
        self.query_one("#tracks", DataTable).focus()

    def action_accept(self) -> None:
        ol = self.query_one("#candidates", OptionList)
        if self.focused is ol:
            return  # OptionSelected handles it
        if ol.highlighted is None:
            ol.highlighted = 0
        opt = ol.get_option_at_index(ol.highlighted) if ol.option_count else None
        if opt and opt.id:
            self._resolve(opt.id)

    def action_skip(self) -> None:
        if self.current:
            m = self.matches[self.current]
            m.status, m.uri = "skipped", None
            self._commit()

    def action_unset(self) -> None:
        if self.current:
            m = self.matches[self.current]
            m.status = "ambiguous" if m.candidates else "not_found"
            m.uri = None
            self._commit()

    def action_search(self) -> None:
        if not self.current:
            return
        inp = self.query_one("#search", Input)
        inp.add_class("visible")
        inp.value = self.src.default_query(self.current)
        inp.focus()

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.refresh_table(keep=self.current)
