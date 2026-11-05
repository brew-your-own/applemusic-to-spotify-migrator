"""Textual TUI for reviewing ambiguous / not-found matches. Every action is saved immediately."""

from __future__ import annotations

import re
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import matching
from .models import Library, Match, Track
from .store import Store

REVIEW_STATUSES = {"ambiguous", "not_found"}
_URI_RE = re.compile(r"(?:spotify:track:|open\.spotify\.com/(?:intl-\w+/)?track/)([A-Za-z0-9]{22})")


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "?:??"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class ReviewApp(App[None]):
    TITLE = "am2sp — review matches"
    CSS = """
    #tracks { width: 45%; }
    #right { width: 55%; padding: 0 1; }
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

    def __init__(self, store: Store, spotify: Any | None = None):
        super().__init__()
        self.store = store
        self.sp = spotify
        self.lib: Library = store.load_library()
        self.matches: dict[str, Match] = store.load_matches()
        self.show_all = False
        self.current: str | None = None

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
            with Vertical(id="right"):
                yield Static(id="detail")
                yield Input(placeholder="Spotify search query, or paste a track URL/URI", id="search")
                yield OptionList(id="candidates")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#tracks", DataTable)
        t.add_columns("status", "score", "title", "artist")
        self.refresh_table()
        t.focus()

    def _rows(self) -> list[str]:
        pids = self.lib.wanted_pids()
        if self.show_all:
            return [p for p in pids if p in self.matches]
        return [p for p in pids if self.matches.get(p) and self.matches[p].status in REVIEW_STATUSES]

    def refresh_table(self, keep: str | None = None) -> None:
        t = self.query_one("#tracks", DataTable)
        t.clear()
        rows = self._rows()
        for pid in rows:
            tr, m = self.lib.tracks[pid], self.matches[pid]
            t.add_row(m.status, f"{m.score:.2f}", tr.name[:50], tr.artist[:30], key=pid)
        todo = sum(1 for m in self.matches.values() if m.status in REVIEW_STATUSES)
        self.sub_title = f"{todo} to review" + (" · showing all" if self.show_all else "")
        if keep in rows:
            t.move_cursor(row=rows.index(keep))
        elif rows:
            self.show_track(rows[min(t.cursor_row, len(rows) - 1)])
        else:
            self.current = None
            self.query_one("#detail", Static).update("Nothing to review. Press [b]a[/b] to show all, [b]q[/b] to quit.")
            self.query_one("#candidates", OptionList).clear_options()

    def show_track(self, pid: str) -> None:
        self.current = pid
        tr: Track = self.lib.tracks[pid]
        m = self.matches[pid]
        chosen = next((c for c in m.candidates if c.uri == m.uri), None)
        self.query_one("#detail", Static).update(
            f"[b]{tr.name}[/b]\n{tr.artist}"
            + (f"  ·  composer: {tr.composer}" if tr.composer else "")
            + f"\n[i]{tr.album}[/i]  ({_fmt_ms(tr.duration_ms)})\n\n"
            f"status: [b]{m.status}[/b]" + (f"  →  {chosen.label}" if chosen else (f"  →  {m.uri}" if m.uri else ""))
        )
        ol = self.query_one("#candidates", OptionList)
        ol.clear_options()
        for c in m.candidates:
            mark = "✓ " if c.uri == m.uri else "  "
            ol.add_option(Option(f"{mark}{c.score:.2f}  {c.label}", id=c.uri))
        if not m.candidates:
            ol.add_option(Option("(no candidates — press / to search)", disabled=True))
        else:
            ol.highlighted = 0

    # --- events -------------------------------------------------------------

    @on(DataTable.RowHighlighted, "#tracks")
    def _row(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key and ev.row_key.value:
            self.show_track(ev.row_key.value)

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
    def run_search(self, pid: str, q: str) -> None:
        if self.sp is None:
            self.call_from_thread(self.notify, "Not logged in to Spotify (run `am2sp auth`).", severity="error")
            return
        tr = self.lib.tracks[pid]
        try:
            if m := _URI_RE.search(q):
                results = [self.sp.get_track(m.group(1))]
            else:
                results = self.sp.search_tracks(q)
        except Exception as e:  # surface API errors in the UI, don't crash it
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        match = self.matches[pid]
        previous = [matching.candidate_as_result(c) for c in match.candidates]
        match.candidates = matching.rank(tr, results + previous, keep=12)
        self.call_from_thread(self._after_search, pid)

    def _after_search(self, pid: str) -> None:
        self.store.save_matches(self.matches)
        if self.current == pid:
            self.show_track(pid)
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
        self.store.save_matches(self.matches)
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
        tr = self.lib.tracks[self.current]
        inp = self.query_one("#search", Input)
        inp.add_class("visible")
        inp.value = f"{matching.clean_title(tr.name)} {matching.primary_artist(tr.artist)}".strip()
        inp.focus()

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.refresh_table(keep=self.current)
