"""Fame Checker editor tab.

Edits the Fame Checker — a per-person trivia database — which projects commonly
repurpose as a QUEST TRACKER (person -> quest, flavor-text entry -> objective).

TEXT EDITING IS LIVE. It saves through the SHARED writer in `core/text_index`,
which replaces one `<label>::` block in place — so the 14 labels in
`data/text/fame_checker.inc` that belong to another system survive untouched by
construction, and the Text Editor tab keeps working on the same file. This tab
has no writer of its own and must never grow one.

(Historical note worth keeping: this tab shipped read-only for a whole phase on
the belief that saving required a whole-file regenerator that had to be built
first. That regenerator was never needed — the in-place writer already existed.
Verify a claimed missing dependency before designing around it.)

Safety properties enforced here (see docs/FAME_CHECKER_PLAN.md Phase 1 spec):
  * `blocking_problems` non-empty  -> Save disabled AND every field locked.
  * symbol in `unresolved_text_symbols` -> field locked with a visible reason
    (its real text lives in a file we didn't read; writing "" would delete it).
  * symbol not in `owned_text_symbols`  -> locked; it belongs to another system.
  * `name_source == "trainer"`    -> the list name comes from gTrainers[]; shown
    read-only rather than as a field that silently does nothing.
  * text containing `"`, `$` or a trailing `\\` -> NOT pushed to the index at
    all, with the reason under the field: each would break the build or
    truncate the text in game, and neither failure is traceable to the
    character that caused it.
  * project has no Fame Checker   -> the tab never appears and nothing is written.

A field is locked by a CONDITION, never by the phase alone (see `_field_lock`).
"""

from __future__ import annotations

import html

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QGroupBox, QFormLayout,
    QScrollArea, QFrame, QPushButton, QSizePolicy, QComboBox,
)

from core.fame_checker_data import has_fame_checker, load_fame_checker
from ui.game_text_edit import GameTextEdit

_log = logging.getLogger("PorySuite.FameChecker")

# Every sprite in this app routes through the bus + sprite_render. No QPixmap(path).
try:
    from core.sprite_palette_bus import (
        get_bus, CAT_FAME_CHECKER_PIC, CAT_TRAINER_PIC, CAT_OVERWORLD)
    from core.sprite_render import load_sprite_pixmap
except Exception:                                    # pragma: no cover
    get_bus = None
    load_sprite_pixmap = None
    CAT_FAME_CHECKER_PIC = CAT_TRAINER_PIC = CAT_OVERWORLD = ""

try:
    from ui.custom_widgets.scroll_guard import (
        install_scroll_guard, install_scroll_guard_recursive)
except Exception:                                    # pragma: no cover
    install_scroll_guard = None
    install_scroll_guard_recursive = None


def _text_problem(value: str) -> str:
    """Why *value* cannot be written to a `.string`, or "" if it can.

    These are not style rules — each one produces a failure the user cannot
    trace back to the character they typed:

    * A double quote closes the `.string` early. The assembler then sees an
      unterminated string and the BUILD BREAKS, in a file the user did not
      knowingly edit. (It is also absent from this project's charmap, so it
      could never have rendered.)
    * A `$` is the end-of-string marker. `preproc` emits a terminator there, so
      the text is CUT SHORT IN GAME at that point — while the editor reads it
      back perfectly, because the editor is not the assembler.
    * A trailing backslash escapes the closing quote, with the same result as
      the first case.
    """
    body = (value or "").rstrip("$")     # the real terminator is added on save
    if '"' in body:
        return ('A double quote can\'t be stored in game text — it would end '
                'the line early and break the build. Use a different mark.')
    if "$" in body:
        return ('"$" means "end of text" in the game, so everything after it '
                'would be cut off. Remove it.')
    if body.endswith("\\"):
        return ('A line can\'t end with a backslash — it would swallow the '
                'end of the text and break the build.')
    return ""


def _pickstate_text(state: str) -> str:
    """Plain English for an FCPICKSTATE_* value, without hardcoding the set.

    An unknown value is echoed rather than guessed — a project is free to add
    its own states, and inventing a description for one would be a confident
    wrong answer.
    """
    return {
        "FCPICKSTATE_NO_DRAW": "hidden until the player hears about them",
        "FCPICKSTATE_SILHOUETTE": "shown as a silhouette until unlocked",
        "FCPICKSTATE_COLORED": "visible from the start",
    }.get(state, state)


# ── Vocabulary ──────────────────────────────────────────────────────────────
# The engine calls these "persons" and "flavor text". Projects overwhelmingly
# repurpose them as quests/objectives, so that is the default wording — but it is
# ONE project's framing, so every user-facing noun lives here and Phase 4's
# terminology toggle is a change to these four constants, not a sweep.
_TERM_ITEM = "Quest"
_TERM_ITEMS = "quests"
_TERM_SUB = "Objective"
_TERM_SUBS = "objectives"

# Editing is ON. The shared per-label writer in `core/text_index` replaces one
# `<label>::` block in place, so the 14 labels in this file that belong to
# another system survive untouched by construction — which was the whole reason
# this tab waited for a writer instead of growing one of its own.
# This flag was never what kept unsafe fields locked; `_field_lock` is.
_PHASE_READONLY = False

_SEV_STYLE = {
    "blocking": "color:#ff6b6b;",
    "warn": "color:#ffb74d;",
    "info": "color:#9aa0a6;",
}
_SEV_LABEL = {"blocking": "BLOCKING", "warn": "WARNING", "info": "note"}
_LOCKED_SS = "QLineEdit, QPlainTextEdit { color:#9aa0a6; background:#2a2a2a; }"

_ROLE_PERSON = Qt.ItemDataRole.UserRole          # person.index
_ROLE_SEARCH = Qt.ItemDataRole.UserRole + 1      # lowercased search key


class FameCheckerTab(QWidget):
    """Left: the quest list. Right: the selected quest and its objectives."""

    def __init__(self, mainwindow=None, parent=None):
        super().__init__(parent)
        self._mw = mainwindow
        self.project_info: dict = {}
        self._data = None
        self._loading = False
        self._rendered_index = None
        # Sprite catalogues, rebuilt per project. `_pic_map` resolves
        # TRAINER_PIC_* -> PNG; `_ow_sprites` / `_ow_pal` resolve the informant
        # icons. Both come from the modules that already own that parsing —
        # a second parser for either would be the drift the sprite rules exist
        # to prevent.
        self._pic_map: dict = {}
        self._ow_sprites: dict = {}
        self._ow_pal: dict = {}
        # The SHARED text index — the same one the Text Editor tab uses, so
        # there is one reader and one writer for this file, not two.
        self._index = None
        # symbol -> why its current text can't be written. A field in this
        # state is NOT pushed to the index, so it can never reach the file.
        self._text_problems: dict = {}
        # Palette tags the CURRENTLY SHOWN person's icons use, so a palette
        # edit elsewhere only repaints when it affects what is on screen.
        self._shown_tags: set = set()

        # A palette edited in ANOTHER tab changes what this screen draws: every
        # portrait is blitted into the same OBJ slot, so the twelve non-custom
        # people render with the palette the Trainer Graphics tab edits.
        if get_bus is not None:
            try:
                get_bus().palette_changed.connect(self._on_palette_changed)
            except Exception:                        # pragma: no cover
                _log.warning("Could not subscribe to the palette bus",
                             exc_info=True)

        root = QVBoxLayout(self)

        self._diag = QLabel("")
        self._diag.setWordWrap(True)
        self._diag.setTextFormat(Qt.TextFormat.RichText)
        self._diag.setStyleSheet("padding:4px;")
        self._diag.setVisible(False)
        root.addWidget(self._diag)

        self._readonly_note = QLabel(
            "Text edits are written straight back to "
            "<code>data/text/fame_checker.inc</code>, one label at a time, so "
            "everything else in that file is left alone. The Text Editor tab "
            "reads the same file — save here before editing it there.")
        self._readonly_note.setWordWrap(True)
        self._readonly_note.setStyleSheet("color:#8fc; padding:4px;")
        root.addWidget(self._readonly_note)

        self._split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(3)
        self._search = QLineEdit()
        self._search.setPlaceholderText(f"Search {_TERM_ITEMS}…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        lv.addWidget(self._search)
        self._list = QListWidget()
        self._list.setMinimumWidth(220)
        self._list.currentRowChanged.connect(self._on_row_changed)
        lv.addWidget(self._list, 1)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color:#888; font-size:10px;")
        lv.addWidget(self._count_lbl)
        self._split.addWidget(left)

        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_host = QWidget()
        self._detail = QVBoxLayout(self._detail_host)
        self._detail.setContentsMargins(6, 6, 6, 6)
        self._detail_scroll.setWidget(self._detail_host)
        self._split.addWidget(self._detail_scroll)
        self._split.setStretchFactor(0, 0)
        self._split.setStretchFactor(1, 1)

        self._save_row = QWidget()
        bar = QHBoxLayout(self._save_row)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addStretch()
        self._save_reason = QLabel("")
        self._save_reason.setStyleSheet("color:#ff6b6b;")
        bar.addWidget(self._save_reason)
        self.btn_save = QPushButton("Save")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._do_save)
        bar.addWidget(self.btn_save)
        root.addWidget(self._save_row)

        if install_scroll_guard_recursive:
            try:
                install_scroll_guard_recursive(self)
            except Exception:
                pass

    # ── project lifecycle ──────────────────────────────────────────────────

    @staticmethod
    def is_supported(project_dir: str) -> bool:
        """Whether to show this tab at all. A project that removed the Fame
        Checker gets no tab and is never modified."""
        return has_fame_checker(project_dir)

    def load_project(self, project_info: dict) -> None:
        self.project_info = project_info or {}
        self.load()

    def clear_project(self) -> None:
        """Forget the project entirely and show the placeholder.

        `load("")` cannot do this: an empty argument falls back to the STORED
        directory, so it would quietly repopulate the previous project's people
        — which is exactly the stale-state leak the tab contract forbids.
        """
        self.project_info = {}
        self.load()

    def load(self, project_dir: str = "") -> None:
        """Full reset — in-memory AND visual — per the CLAUDE.md tab contract.

        Takes an optional directory so it matches how the main window calls
        every other tab (`tab.load(project_dir)`), while the no-argument form
        used by F5 keeps working off the stored project info.
        """
        if project_dir:
            self.project_info = dict(self.project_info or {})
            self.project_info["dir"] = project_dir
        self._loading = True
        try:
            # (When editing lands, this is where the debounce timer is stopped
            #  and `_dirty_rows` / the amber detail frame are reset — before any
            #  rebuild, so a pending signal can't re-dirty the fresh state.)
            self._search.clear()             # box and list must never disagree
            self._list.clear()
            self._clear_detail()
            self._diag.setVisible(False)
            self._diag.setText("")
            self._save_reason.setText("")
            self.btn_save.setEnabled(False)
            self._count_lbl.setText("")
            self._rendered_index = None
            # Sprite catalogues are project state: not clearing them here would
            # draw the previous project's artwork after a project switch.
            self._pic_map = {}
            self._ow_sprites = {}
            self._ow_pal = {}
            self._shown_tags = set()
            self._index = None
            self._text_problems = {}

            project_dir = (self.project_info or {}).get("dir", "")
            self._data = load_fame_checker(project_dir) if project_dir else None

            available = bool(self._data and self._data.available)
            self._readonly_note.setVisible(available)
            self._save_row.setVisible(available)
            self._split.setVisible(True)

            if not available:
                reason = (self._data.unavailable_reason if self._data
                          else "No project is open.")
                self._show_placeholder(reason)
                return

            self._load_text_index(project_dir)
            self._load_sprite_catalogues(project_dir)
            self._render_diagnostics()
            self._populate_list()
        finally:
            self._loading = False

        # Selection is driven explicitly, never by "did Qt emit a signal".
        if self._data and self._data.available:
            self._select_row(self._first_visible_row())

    # ── diagnostics + save gating ──────────────────────────────────────────

    def _graphics_notes(self) -> list:
        """Section-level facts about the artwork — true of the screen, not of
        any one person, so they belong here rather than repeated per person."""
        d = self._data
        if not d or not d.available:
            return []
        g = d.graphics
        out = []
        if g.custom:
            out.append(
                f"Artwork loaded whenever this screen opens: "
                f"<b>{g.static_sheet_bytes / 1024:.1f} KB</b> of the "
                f"{g.obj_vram_bytes // 1024} KB sprite memory, of which "
                f"{g.custom_tile_bytes / 1024:.1f} KB is the {len(g.custom)} "
                f"custom portrait(s) — loaded whether they are shown or not. "
                f"Trainer pictures and informant icons are loaded on top of "
                f"this as they appear, so the remainder is not all free.")
        for tag, note in g.shared_tag_notes:
            out.append(f"Your source notes that <code>{html.escape(tag)}</code> "
                       f"may be shared: {html.escape(note)}")
        if g.silhouette_gbapal:
            ok = os.path.isfile(g.silhouette_gbapal) or os.path.isfile(
                os.path.splitext(g.silhouette_gbapal)[0] + ".pal")
            out.append(
                "The silhouette colours used for people the player has not "
                "unlocked yet are one shared palette for the whole screen"
                + ("." if ok else " — but its file was not found on disk."))
        return out

    def _render_diagnostics(self) -> None:
        d = self._data
        notes = self._graphics_notes()
        if not d or (not d.problems and not notes):
            self._diag.setVisible(False)
            return
        rows = [f'<div style="color:#888888">{n}</div>' for n in notes]
        for p in d.problems:
            # Messages quote project-derived text (macro bodies, symbol names),
            # so they MUST be escaped before going into a RichText label.
            rows.append(
                f'<div style="{_SEV_STYLE.get(p.severity, "")}">'
                f'<b>{_SEV_LABEL.get(p.severity, p.severity)}:</b> '
                f'{html.escape(p.message)}</div>')
        self._diag.setText("".join(rows))
        self._diag.setVisible(True)

        # ONE data-driven rule — never pattern-match the message text.
        # `_refresh_save_state` owns the Save button and its reason line; this
        # only renders the problem list, so the two can't disagree about why
        # Save is disabled.
        self._refresh_save_state()

    # ── locking (condition-driven, never phase-driven) ─────────────────────

    def _field_lock(self, symbol: str = "", *, needs_symbol: bool = True) -> tuple:
        """(locked, reason) for a field. THE single gate — every widget that can
        accept input calls this and nothing reimplements it.

        Order matters: the UNSAFE-to-write conditions are checked first, so
        dropping `_PHASE_READONLY` when the writer lands cannot silently unlock
        a field whose text we could not resolve, or a model we know is wrong.

        `needs_symbol=False` is for inputs whose value is an ENUM rather than a
        text symbol (the portrait / informant dropdowns). "Has no symbol by
        nature" and "should have a symbol and doesn't" are different conditions
        with opposite correct answers, so the caller states which it is instead
        of the gate guessing from truthiness.
        """
        d = self._data
        if needs_symbol and not symbol:
            # No symbol means there is nowhere to write. Checked FIRST, or an
            # empty symbol short-circuits the unresolved test below and lands on
            # the phase flag - becoming editable the moment that flag drops.
            return True, "No text symbol is defined for this field."
        if d and symbol in (d.unresolved_text_symbols or []):
            return True, (f"'{symbol}' has no string in the files read — its text "
                          f"lives elsewhere. Locked so saving can't erase it.")
        if d and d.blocking_problems:
            return True, ("The data could not be read correctly — see the "
                          "problems above. Editing is locked.")
        if d and symbol and symbol not in (d.owned_text_symbols or []):
            # A label in this file that the tables do not reference belongs to
            # another system. Editable here would mean this tab writing text it
            # does not own.
            return True, (f"'{symbol}' is not one of this feature's strings — "
                          f"it belongs to another part of the game.")
        if _PHASE_READONLY:
            return True, "Editing is not enabled yet."
        return False, ""

    # ── list ───────────────────────────────────────────────────────────────

    @staticmethod
    def _search_key(p) -> str:
        """The row's search key. ONE definition, because a name edit rebuilds
        it and a drifting second copy would make edited rows unsearchable.

        Searches the NAME only — including the leading index would make "1"
        match rows 1 and 10-15.
        """
        base = (p.const[len("FAMECHECKER_"):]
                if p.const.startswith("FAMECHECKER_") else p.const)
        return f"{base} {p.custom_name}".lower()

    def _person_label(self, p) -> str:
        base = (p.const[len("FAMECHECKER_"):]
                if p.const.startswith("FAMECHECKER_") else p.const)
        if p.name_source == "custom" and p.custom_name:
            return f"{p.index:>3}  {base}   ({p.custom_name})"
        return f"{p.index:>3}  {base}"

    def _populate_list(self) -> None:
        d = self._data
        for p in d.persons:
            it = QListWidgetItem(self._person_label(p))
            it.setData(_ROLE_PERSON, p.index)
            it.setData(_ROLE_SEARCH, self._search_key(p))
            self._list.addItem(it)
        self._count_lbl.setText(
            f"{d.person_count} {_TERM_ITEMS} × {d.entries_per_person} {_TERM_SUBS}")

    def _first_visible_row(self) -> int:
        for i in range(self._list.count()):
            if not self._list.item(i).isHidden():
                return i
        return -1

    def _select_row(self, row: int) -> None:
        """Select AND render. Never relies on a signal having been emitted."""
        if row is None or row < 0 or row >= self._list.count():
            self._rendered_index = None
            self._show_placeholder(f"No {_TERM_ITEMS} match this search.")
            return
        if self._list.currentRow() != row:
            # Block signals: setCurrentRow would fire _on_row_changed and render,
            # then we render again below - twice the widget churn per click.
            blocked = self._list.blockSignals(True)
            try:
                self._list.setCurrentRow(row)
            finally:
                self._list.blockSignals(blocked)
        self._render_row(row)

    def _render_row(self, row: int, force: bool = False) -> None:
        item = self._list.item(row)
        if item is None or not self._data:
            return
        idx = item.data(_ROLE_PERSON)
        if not force and idx == self._rendered_index:
            # Already on screen. NOTE for the editing phase: any refresh after an
            # edit or a save must pass force=True, or the same index will be
            # treated as already-rendered and the stale panel kept.
            return
        # Look the person up by STORED INDEX, not by list position — the two stop
        # agreeing the moment the list is sorted, grouped, or has rows removed.
        person = next((p for p in self._data.persons if p.index == idx), None)
        if person is not None:
            self._render_person(person)

    def _on_row_changed(self, row: int) -> None:
        if self._loading or not self._data:
            return
        if row >= 0:
            self._render_row(row)

    def _apply_filter(self, text: str = "") -> None:
        # load() clears the search box, which fires textChanged while _data is
        # still the OLD project's model and the list still holds the OLD rows.
        if self._loading or not self._data:
            return
        q = (text or "").strip().lower()
        blocked = self._list.blockSignals(True)
        try:
            for i in range(self._list.count()):
                it = self._list.item(i)
                if not q:
                    it.setHidden(False)
                    continue
                # Exact index match OR name match — an OR, not an either/or. A
                # quest tracker will have names like "LEVEL_3_KEY", so a digit
                # query must never hide a name that genuinely matches.
                hit = q in (it.data(_ROLE_SEARCH) or "")
                if not hit and q.isdigit():
                    try:
                        hit = int(q) == int(it.data(_ROLE_PERSON))
                    except (TypeError, ValueError):
                        hit = False
                it.setHidden(not hit)
        finally:
            self._list.blockSignals(blocked)
        # Hiding the current row does NOT move the selection, so without this the
        # panel keeps showing a quest that is no longer in the list. The else
        # branch re-renders so the panel recovers from a "no matches" placeholder
        # once the search is cleared again.
        cur = self._list.currentRow()
        if cur < 0 or self._list.item(cur).isHidden():
            self._select_row(self._first_visible_row())
        else:
            self._render_row(cur)

    # ── detail panel ───────────────────────────────────────────────────────

    def _clear_detail(self) -> None:
        # Reset HERE, not at the call sites: the flag must always name what is
        # actually on screen. Any future caller (revert, post-save rebuild,
        # diagnostics refresh) would otherwise leave it pointing at a person that
        # is gone, and the next _render_row would skip - the empty-panel bug.
        self._rendered_index = None
        while self._detail.count():
            item = self._detail.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _show_placeholder(self, text: str) -> None:
        self._clear_detail()
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#9aa0a6; padding:12px;")
        self._detail.addWidget(lbl)
        self._detail.addStretch()

    def _ro_edit(self, value: str, reason: str = "") -> QLineEdit:
        """A field that is read-only by nature (it is owned elsewhere)."""
        e = QLineEdit(value)
        e.setReadOnly(True)
        e.setStyleSheet(_LOCKED_SS)
        if reason:
            e.setToolTip(reason)
            e.setPlaceholderText(reason)
        return e

    def _combo(self, options, current: str, symbol: str = "") -> QComboBox:
        """A dropdown that routes through `_field_lock` like every other input.

        Exists so the sprite/portrait piece fills this in rather than inventing
        its own path around the gate. Wheel-guarded individually because the
        detail panel is rebuilt after __init__.
        """
        opts = list(options)
        c = QComboBox()
        # Never silently show option 0 in place of a value the project actually
        # has (or is missing). An unknown current value is preserved verbatim;
        # a MISSING one locks the control rather than offering to write a
        # portrait/icon the project never had.
        missing_current = not current
        if current and current not in opts:
            opts.insert(0, current)
        c.addItems(opts)
        if current and current in opts:
            c.setCurrentIndex(opts.index(current))

        # ONE gate. `needs_symbol=False` because a dropdown's value is an enum,
        # not a text symbol — but blocking/unresolved/phase all still apply.
        locked, reason = self._field_lock(symbol, needs_symbol=False)
        if missing_current and not locked:
            locked, reason = True, (
                "This project has no value for this field — locked so saving "
                "can't invent one.")
        c.setEnabled(not locked)
        if locked:
            c.setToolTip(reason)
        if install_scroll_guard:
            try:
                install_scroll_guard(c)
            except Exception:
                pass
        return c

    def _text_field(self, symbol: str, value: str, rows: int = 3,
                    budget: str = "msgbox",
                    placeholders_ok: bool = True) -> QWidget:
        """One editable string, with a live GBA-pixel length counter.

        `budget` picks which window the text has to fit: "msgbox" is the
        flavor-text box (wide, two lines per screen), "icondesc" is the pair of
        centred origin captions under the portrait (narrow, one line). Both the
        pixel width and the font come from the project, never from a constant.

        `placeholders_ok` is False for a field the engine prints WITHOUT running
        `StringExpandPlaceholders` first — a `{PLAYER}` there measures as 0 px
        and draws nothing, so it needs its own warning.
        """
        locked, reason = self._field_lock(symbol)
        m = self._data.metrics if self._data else None
        if budget == "icondesc":
            px = getattr(m, "icondesc_px", 84)
            font = getattr(m, "icondesc_font", "FONT_SMALL")
            lines = 1
        elif budget == "list":
            px = getattr(m, "list_px", 56)
            font = getattr(m, "list_font", "FONT_NORMAL")
            lines = 1
        else:
            px = getattr(m, "msgbox_px", 208)
            font = getattr(m, "msgbox_font", "FONT_NORMAL")
            lines = getattr(m, "msgbox_lines", 2)

        # `GameTextEdit` is the app's standard game-text editor — the same one
        # the Trainers tab and EVENTide's message editor use. It already knows
        # `.inc` format, the `{COLOR}` / emoji tokens, the trailing `$`, the
        # formatting toolbar and the per-line limit warning, so this tab gets
        # all of that for free rather than a bespoke widget beside it.
        #
        # The LIMIT is still measured from this project rather than assumed:
        # the pixel budget and font come from the parsed source, and the
        # characters-per-line figure is derived from them.
        cpl = 36
        gba = getattr(m, "gba", None)
        if gba is not None:
            try:
                cpl = gba.approx_chars(px, font)
            except Exception:
                pass
        # Read the CURRENT value from the shared index when it has one: an edit
        # must survive clicking away to another quest and back, and the index is
        # where the edit lives until Save.
        if self._index is not None and symbol:
            entry = self._index.get(symbol)
            if entry is not None:
                value = entry.content
        box = GameTextEdit(max_chars_per_line=cpl, max_lines=max(1, lines),
                           show_toolbar=not locked)
        box.setMaximumHeight(28 * max(1, rows))
        box.set_inc_text(value or "")
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if locked:
            box.setEnabled(False)
            box.setToolTip(reason)
            if symbol and symbol in (self._data.unresolved_text_symbols or []):
                box.set_inc_text("")       # never show "" as if it were the text
                box.setPlaceholderText(reason)
        else:
            box.connectChanged(
                lambda s=symbol, b=box: self._on_text_edited(s, b))

        # Per-field problem line. Sits under the editor so the reason is next
        # to the text that caused it, not in a dialog after the fact.
        note = QLabel("")
        note.setWordWrap(True)
        note.setStyleSheet("color:#ff6b6b; font-size:10px;")
        note.setVisible(False)
        box._fc_problem_label = note
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.addWidget(box)
        v.addWidget(note)
        return holder

    def _on_text_edited(self, symbol: str, box) -> None:
        """Push an edit into the shared text index. Never writes to disk."""
        if self._loading or not symbol or self._index is None:
            return
        entry = self._index.get(symbol)
        if entry is None:
            return
        try:
            # `get_inc_text` appends the `$` terminator, which belongs to the
            # `.string` FORM, not to the text. The index stores content without
            # it — and a C string (`_("OAK")`) has no terminator at all, so
            # leaving it on wrote `_("LINK$")` into src/strings.c and the `$`
            # became part of the name.
            value = box.get_inc_text().rstrip("$")
        except Exception:
            _log.warning("Could not read the edited text for %s", symbol,
                         exc_info=True)
            return

        # Refuse characters the file format cannot carry, BEFORE they reach the
        # index. The writer would emit them verbatim, and the failure would land
        # on the user as a broken build or text that silently truncates in game
        # — neither traceable to the character they typed.
        problem = _text_problem(value)
        self._text_problems.pop(symbol, None)
        if problem:
            self._text_problems[symbol] = problem
        note = getattr(box, "_fc_problem_label", None)
        if note is not None:
            note.setText(problem)
            note.setVisible(bool(problem))
        if problem:
            self._refresh_save_state()
            return                       # not pushed: it cannot be saved

        entry.content = value
        self._refresh_list_label(symbol, value)
        self._refresh_save_state()

    def _refresh_list_label(self, symbol: str, value: str) -> None:
        """Keep the left-hand list in step with a name edit.

        The person's name is also their row label AND their search key, so an
        edit that only updated the field would leave the list showing the old
        name until F5 — the stale-state the tab contract forbids.
        """
        if not self._data:
            return
        person = next((p for p in self._data.persons
                       if p.custom_name_symbol == symbol), None)
        if person is None:
            return
        person.custom_name = value
        for row in range(self._list.count()):
            it = self._list.item(row)
            if it is not None and it.data(_ROLE_PERSON) == person.index:
                it.setText(self._person_label(person))
                it.setData(_ROLE_SEARCH, self._search_key(person))
                break

    def _refresh_save_state(self) -> None:
        """Enable Save only when there is something safe to write."""
        n = self._dirty_count()
        bad = len(self._text_problems)
        blocked = bool(self._data and self._data.blocking_problems)
        self.btn_save.setEnabled(n > 0 and not blocked and not bad)
        if bad:
            self._save_reason.setText(
                f"{bad} field(s) contain something that can't be saved — see "
                f"the red note under them.")
            return
        if blocked:
            self._save_reason.setText(
                f"Save disabled — {len(self._data.blocking_problems)} problem(s) "
                f"mean the data could not be read correctly.")
        elif n:
            self._save_reason.setText(f"{n} unsaved change(s).")
        else:
            self._save_reason.setText("")
        if self._mw is not None:
            try:
                self._mw.setWindowModified(bool(n))
                self._mw.sectionDirtyChanged.emit("fame_checker", bool(n))
            except Exception:
                pass

    def _owned_entries(self) -> list:
        """Index entries this tab is allowed to write.

        Ownership comes from what the TABLES reference, never from a name
        prefix — `data/text/fame_checker.inc` also holds 14 labels belonging to
        another system, and this tab must never touch them.
        """
        if self._index is None or not self._data:
            return []
        owned = set(self._data.owned_text_symbols or [])
        return [e for e in self._index.entries if e.label in owned]

    def _dirty_count(self) -> int:
        return sum(1 for e in self._owned_entries() if e.is_dirty)

    def _do_save(self) -> None:
        """Write the edited strings back, one label at a time.

        The shared writer replaces a single `<label>::` block in place, so
        every other label in the file — including the ones this tab does not
        own — survives untouched by construction. That is why this tab does not
        need, and must not have, a writer of its own.
        """
        dirty = [e for e in self._owned_entries() if e.is_dirty]
        if not dirty:
            return
        try:
            from core.text_index import save_dirty_entries
            failed = save_dirty_entries(dirty) or []
        except Exception:
            _log.exception("Saving Fame Checker text failed")
            self._save_reason.setText(
                "Save failed — see porysuite.log. Nothing was written.")
            return
        self._refresh_save_state()
        if failed:
            # Anything that could not be written stays dirty, and says so. A
            # save that quietly writes nothing is worse than one that fails.
            self._save_reason.setText(
                f"Saved {len(dirty) - len(failed)} change(s); "
                f"{len(failed)} could not be written "
                f"({', '.join(failed[:3])}) and are still unsaved.")
        else:
            self._save_reason.setText(f"Saved {len(dirty)} change(s).")

    def _on_palette_changed(self, category: str, key: str) -> None:
        """Repaint when a palette this screen draws with changes anywhere."""
        if category not in (CAT_FAME_CHECKER_PIC, CAT_TRAINER_PIC,
                            CAT_OVERWORLD):
            return
        if self._loading or self._rendered_index is None:
            return
        # Overworld palette editing is a drag-heavy interaction, so this fires
        # a lot. Repainting a page nobody is looking at is pure cost.
        if not self.isVisible():
            return
        if category == CAT_OVERWORLD and key and key not in self._shown_tags:
            return                     # not a palette anything on screen uses
        # Belt and braces against re-entrancy. The draw path only READS from
        # the bus now, but any future push from inside a render would otherwise
        # loop straight back through here.
        if getattr(self, "_repainting", False):
            return
        row = self._list.currentRow()
        if row >= 0:
            # force=True: the person on screen has not changed, so the
            # already-rendered guard would skip the repaint entirely and the
            # user would see stale colours after editing them elsewhere.
            self._repainting = True
            try:
                self._render_row(row, force=True)
            finally:
                self._repainting = False

    def _load_text_index(self, project_dir: str) -> None:
        """Load the SHARED text index. Reading only — nothing is written."""
        self._index = None
        if not project_dir:
            return
        try:
            from core.text_index import TextIndex
            ix = TextIndex()
            ix.load(project_dir)
            self._index = ix
        except Exception:
            _log.warning("Could not load the shared text index", exc_info=True)

    def _load_sprite_catalogues(self, project_dir: str) -> None:
        """Resolve the two lookup tables the previews need. Never writes."""
        self._pic_map = {}
        self._ow_sprites = {}
        self._ow_pal = {}
        if not project_dir:
            return
        try:
            from ui.trainers_tab_widget import _parse_trainer_pic_map
            self._pic_map = _parse_trainer_pic_map(project_dir) or {}
        except Exception:
            _log.warning("Could not read this project's trainer pictures",
                         exc_info=True)
        try:
            # read_overworld_data, NOT build_overworld_data: the latter repairs
            # `.pal` siblings as it goes, which would have this read-only page
            # writing files into the user's game repo just for opening it.
            from ui.overworld_graphics_tab import read_overworld_data
            pools, sprites = read_overworld_data(project_dir)
            self._ow_sprites = sprites or {}
            for pool in (pools or []):
                colors = None
                try:
                    from core.overworld_palette_io import read_palette_pair
                    colors = read_palette_pair(
                        pool.gbapal_path or pool.pal_path)
                except Exception:
                    colors = None
                if colors:
                    self._ow_pal[pool.tag_name] = colors
        except Exception:
            _log.warning("Could not read this project's overworld graphics",
                         exc_info=True)

    # ── sprite previews ────────────────────────────────────────────────────

    def _preview_label(self, pm, reason: str, size: int = 64) -> QLabel:
        """A fixed-size preview cell — the art, or WHY there is no art.

        Never a blank square: an empty cell reads as "this has no picture",
        which is a different statement from "the picture could not be read".
        """
        lbl = QLabel()
        lbl.setFixedSize(size, size)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFrameShape(QFrame.Shape.StyledPanel)
        if pm is not None and not pm.isNull():
            lbl.setPixmap(pm.scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation))
        else:
            lbl.setText("—")
            lbl.setStyleSheet("color:#888888;")
        if reason:
            lbl.setToolTip(reason)
        return lbl

    def _portrait_pixmap(self, p):
        """(pixmap, source description, reason) for one person's portrait.

        Always renders the person's TRUE portrait palette. The silhouette state
        is a runtime save value applied over whoever is in it — it carries no
        per-person information, and letting it drive the preview would make the
        same art look different depending on a field elsewhere on the page.
        """
        if load_sprite_pixmap is None or self._data is None:
            return None, "", "sprite rendering is unavailable"
        g = self._data.graphics
        try:
            if p.uses_custom_pic:
                info = g.custom.get(p.const) or {}
                png = info.get("png", "")
                if not png or not os.path.isfile(png):
                    return None, info.get("gfx", ""), (
                        "this project's artwork file for this "
                        f"{_TERM_ITEM.lower()} was not found on disk")
                # `ensure_*`, NOT `set_palette`. This is a VIEWER: `set_palette`
                # broadcasts an EDIT, so pushing from the draw path made the
                # bus re-enter this very render — an infinite loop that hung
                # the tab on load. `ensure_*` hydrates the cache silently
                # because nothing changed; we only read.
                gbapal = info.get("gbapal", "")
                colors, reason = \
                    get_bus().ensure_fame_checker_palette_with_reason(gbapal)
                # No palette FILE is not a failure: the PNG is indexed and
                # carries its own table, which is then the only source of truth
                # there is. The reason string says so rather than pretending.
                return (load_sprite_pixmap(png, colors or None),
                        info.get("gfx", ""), reason)

            pic = p.trainer_pic or ""
            png = self._pic_map.get(pic, "")
            if not png or not os.path.isfile(png):
                return None, pic, (
                    f"no artwork on disk for {pic}" if pic
                    else "this person has no portrait assigned")
            colors = get_bus().ensure_trainer_palette_from_png(png, pic) or None
            return load_sprite_pixmap(png, colors), pic, ""
        except Exception:
            _log.warning("Portrait preview failed for %s", p.const, exc_info=True)
            return None, "", "the portrait could not be drawn — see the log"

    def _icon_pixmap(self, gfx_const: str):
        """(pixmap, palette tag, reason) for one informant icon."""
        if load_sprite_pixmap is None:
            return None, "", "sprite rendering is unavailable"
        entry = (self._ow_sprites or {}).get(gfx_const)
        if entry is None:
            return None, "", (f"{gfx_const} is not in this project's overworld "
                              "graphics table" if gfx_const else "")
        # Keyed on the palette TAG from the graphics info, never on the
        # OBJ_EVENT_GFX_* const: the engine forces the template's tag to
        # TAG_NONE and loads via `gObjectEventGraphicsInfo[id].paletteTag`.
        tag = getattr(entry, "palette_tag", "") or ""
        png = getattr(entry, "png_path", "") or ""
        try:
            if not png or not os.path.isfile(png):
                return None, tag, "no artwork on disk for this icon"
            colors = get_bus().get_overworld_palette(tag) if tag else None
            if colors is None and tag:
                colors = self._ow_pal.get(tag)
            return load_sprite_pixmap(png, colors or None), tag, ""
        except Exception:
            _log.warning("Icon preview failed for %s", gfx_const, exc_info=True)
            return None, tag, "the icon could not be drawn — see the log"

    def _render_person(self, p) -> None:
        self._clear_detail()
        self._shown_tags = set()
        self._rendered_index = p.index
        self._detail_scroll.verticalScrollBar().setValue(0)

        gb = QGroupBox(f"{_TERM_ITEM} {p.index} — {p.const}")
        form = QFormLayout(gb)

        if p.name_source == "custom":
            form.addRow("Name source:", QLabel("Custom name"))
            # EDITABLE, and measured against the LIST window — a completely
            # different budget from the description boxes (56px vs 208px), so
            # reusing theirs would let a name be typed at four times the width
            # it can draw. The string lives in src/strings.c, shared with the
            # whole game, which is why ownership is checked per symbol rather
            # than by name prefix.
            name_field = self._text_field(
                p.custom_name_symbol, p.custom_name, rows=1, budget="list")
            # `include/strings.h` documents each of these with a comment
            # showing its ORIGINAL value (`// "OAK$"`). Renaming here does not
            # touch that comment — deliberately, because editing a second file
            # as a side effect of a rename is not something to do silently.
            # Said here so the staleness is a known choice, not a surprise.
            name_field.setToolTip(
                "Renaming this changes the name in game. The comment beside "
                "the declaration in include/strings.h still shows the old "
                "name — it is only a comment, and nothing reads it.")
            form.addRow("Name:", name_field)
        else:
            form.addRow("Name source:", QLabel(
                f"<i>From the Trainers tab</i> — this {_TERM_ITEM.lower()} takes "
                f"its list name from <code>{html.escape(p.trainer_idx or '')}</code>."))
            form.addRow("Name:", self._ro_edit(
                "", "Edit this on the Trainers tab, not here."))

        form.addRow("Trainer link:", self._ro_edit(p.trainer_idx))
        # For a custom-art person the engine never reads `trainer_pic` — showing
        # it as "the portrait" would state something the game does not do.
        pm, source, reason = self._portrait_pixmap(p)
        row = QHBoxLayout()
        row.addWidget(self._preview_label(pm, reason))
        side = QVBoxLayout()
        if p.uses_custom_pic:
            side.addWidget(QLabel("Custom artwork"))
            side.addWidget(QLabel(
                f"<code>{html.escape(source)}</code>" if source else ""))
            side.addWidget(QLabel(
                f"<span style='color:#888888'>{html.escape(p.trainer_pic)} is "
                f"stored for this {_TERM_ITEM.lower()} but nothing reads it"
                f"</span>" if p.trainer_pic else ""))
        else:
            side.addWidget(QLabel("Trainer picture"))
            side.addWidget(QLabel(
                f"<code>{html.escape(source)}</code>" if source else "(none)"))
            side.addWidget(QLabel(
                "<span style='color:#888888'>Edit these colours on the Trainer "
                "Graphics tab — this screen shares them.</span>"))
        if reason:
            note = QLabel(html.escape(reason))
            note.setWordWrap(True)
            note.setStyleSheet("color:#ffb74d;")
            side.addWidget(note)
        side.addStretch()
        row.addLayout(side, 1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Portrait:", holder)

        state = self._data.graphics.pickstate_defaults.get(p.const, "")
        if state:
            form.addRow("Starts as:", QLabel(
                f"<span style='color:#888888'>{html.escape(_pickstate_text(state))}"
                f" — this is the NEW GAME default; each save tracks its own."
                f"</span>"))
        form.addRow("Header name:", self._text_field(p.name_symbol, p.name, rows=2))
        form.addRow("Quote:", self._text_field(p.quote_symbol, p.quote, rows=3))
        self._detail.addWidget(gb)

        for e in p.entries:
            eb = QGroupBox(f"{_TERM_SUB} {e.index}")
            ef = QFormLayout(eb)
            ef.addRow("Text:", self._text_field(e.text_symbol, e.text, rows=4))

            ipm, itag, ireason = self._icon_pixmap(e.npc_gfx)
            if itag:
                self._shown_tags.add(itag)
            irow = QHBoxLayout()
            irow.addWidget(self._preview_label(ipm, ireason, size=40))
            iside = QVBoxLayout()
            iside.addWidget(QLabel(
                f"<code>{html.escape(e.npc_gfx or '(none)')}</code>"))
            iside.addWidget(QLabel(
                f"<span style='color:#888888'>palette "
                f"<code>{html.escape(itag)}</code></span>" if itag else ""))
            # H8: a locked entry does NOT draw this icon — the engine puts a
            # question mark there instead. Without saying so, a user sets an
            # icon, sees it here, and never sees it in game.
            iside.addWidget(QLabel(
                "<span style='color:#888888'>Shown once this "
                f"{_TERM_SUB.lower()} is unlocked; until then the game draws a "
                "question mark here.</span>"))
            if ireason:
                inote = QLabel(html.escape(ireason))
                inote.setWordWrap(True)
                inote.setStyleSheet("color:#ffb74d;")
                iside.addWidget(inote)
            iside.addStretch()
            irow.addLayout(iside, 1)
            iholder = QWidget()
            iholder.setLayout(irow)
            ef.addRow("Informant icon:", iholder)
            # The location caption is printed RAW; only the object caption goes
            # through StringExpandPlaceholders (`UpdateIconDescriptionBox`).
            ef.addRow("Origin — location:", self._text_field(
                e.origin_location_symbol, e.origin_location, rows=1,
                budget="icondesc", placeholders_ok=False))
            ef.addRow("Origin — source:", self._text_field(
                e.origin_object_symbol, e.origin_object, rows=1,
                budget="icondesc"))
            self._detail.addWidget(eb)

        if not p.entries:
            self._detail.addWidget(QLabel(
                f"This project's {_TERM_SUB.lower()} count could not be derived "
                f"— see the problems above."))
        self._detail.addStretch()
        # The detail panel is rebuilt after __init__, so the constructor's
        # recursive guard never reached these widgets. Without this the portrait
        # / informant dropdowns would ship wheel-scrollable.
        if install_scroll_guard_recursive:
            try:
                install_scroll_guard_recursive(self._detail_host)
            except Exception:
                pass
