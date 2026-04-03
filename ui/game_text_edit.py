"""
ui/game_text_edit.py
Standardised text editor for GBA game text — enforces per-line character
limits with colour-coded feedback, highlights {COMMANDS} in blue, provides
an insert menu for text commands, and hides raw escape codes behind
human-friendly display.

This widget is the standard for ALL text editing in PorySuite-Z:
trainer dialogue, NPC text, item descriptions, move descriptions, etc.

Character limits are auto-detected from vanilla pokefirered files.
The GBA text box can display 36 characters per line.
"""
from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import (
    QAction, QColor, QFont, QKeyEvent, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor,
)
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QTextEdit,
    QVBoxLayout, QWidget,
)


# ── Constants ────────────────────────────────────────────────────────────────

# Default character limit per display line (auto-detected from vanilla files)
DEFAULT_CHARS_PER_LINE = 36

# Maximum display lines per text box type
DEFAULT_MAX_LINES = 20  # generous default; specific types can tighten

# Recognised text commands — these display as tags in-game, not as characters
TEXT_COMMANDS = [
    "{PLAYER}", "{RIVAL}", "{KUN}",
    "{PLAY_BGM}", "{PAUSE_MUSIC}", "{RESUME_MUSIC}",
    "{MUS_ENCOUNTER_GYM_LEADER}", "{MUS_ENCOUNTER_ROCKET}", "{MUS_OBTAIN_BADGE}",
    "{FONT_NORMAL}", "{FONT_MALE}", "{FONT_FEMALE}",
]

# Categorised for the insert menu
_COMMAND_CATEGORIES = {
    "Variables": ["{PLAYER}", "{RIVAL}", "{KUN}"],
    "Music": [
        "{PLAY_BGM}", "{PAUSE_MUSIC}", "{RESUME_MUSIC}",
        "{MUS_ENCOUNTER_GYM_LEADER}", "{MUS_ENCOUNTER_ROCKET}",
        "{MUS_OBTAIN_BADGE}",
    ],
    "Font": ["{FONT_NORMAL}", "{FONT_MALE}", "{FONT_FEMALE}"],
}

_COMMAND_RE = re.compile(r"\{[A-Z_]+\}")


# ── Colour constants ────────────────────────────────────────────────────────

_CLR_NORMAL = "#555555"      # dim grey — within limit
_CLR_AMBER  = "#ffb74d"      # amber — close to limit (>85%)
_CLR_RED    = "#e57373"      # red — over limit

_BG_OVERFLOW     = QColor("#5c1a1a")  # dark red bg for overflow chars
_FG_OVERFLOW     = QColor("#ff6b6b")  # bright red fg for overflow chars
_BG_EXTRA_LINES  = QColor("#3d1a00")  # dark orange bg for extra lines
_FG_EXTRA_LINES  = QColor("#ff9944")  # orange fg for extra lines

_CLR_COMMAND = QColor("#64b5f6")      # blue for {COMMANDS}


# ── Syntax highlighter for {COMMANDS} ───────────────────────────────────────

class _CommandHighlighter(QSyntaxHighlighter):
    """Highlights {COMMAND_NAME} tokens in blue within a QPlainTextEdit.

    This sets FOREGROUND colour only. Overflow highlighting is done via
    ExtraSelections which set BACKGROUND only. The two don't conflict
    because they control different properties — blue text shows through
    any overflow background colour.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fmt = QTextCharFormat()
        self._fmt.setForeground(_CLR_COMMAND)
        self._fmt.setFontWeight(QFont.Weight.Bold)

    def highlightBlock(self, text: str) -> None:
        for m in _COMMAND_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._fmt)


# ── Conversion helpers ──────────────────────────────────────────────────────

def inc_to_display(raw: str) -> tuple[str, list[str]]:
    """
    Convert raw .inc text content into human-friendly display text.

    Returns (display_text, escape_map) where escape_map records which
    escape sequence was used at each line break so we can restore them
    on save (preserving \\p and \\l instead of converting everything to \\n).

    - \\n, \\p, \\l  →  newlines (displayed as line breaks)
    - $  →  stripped (it's just the terminator)
    - {COMMANDS}  →  kept as-is (shown in blue via ExtraSelections)
    """
    text = raw
    # Remove trailing $ terminator
    text = text.rstrip("$").rstrip()

    # Record the escape sequences in order, then replace with newlines
    escape_map: list[str] = []
    result_parts: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in "npl":
            esc = text[i:i + 2]  # \n, \p, or \l
            escape_map.append(esc)
            result_parts.append("\n")
            i += 2
        else:
            result_parts.append(text[i])
            i += 1

    return "".join(result_parts), escape_map


def display_to_inc(display: str, escape_map: list[str] | None = None) -> str:
    """
    Convert display text back to .inc format.

    Uses escape_map (from inc_to_display) to restore original escape
    sequences. If no map is provided, defaults to \\n for all line breaks.
    """
    parts = display.split("\n")
    result_parts: list[str] = []
    for idx, part in enumerate(parts):
        result_parts.append(part)
        if idx < len(parts) - 1:
            if escape_map and idx < len(escape_map):
                result_parts.append(escape_map[idx])
            else:
                result_parts.append("\\n")
    text = "".join(result_parts)
    if not text.endswith("$"):
        text += "$"
    return text


def eventide_to_display(internal: str) -> tuple[str, list[str]]:
    """
    Convert EVENTide's internal text format to display text.

    EVENTide stores \\n as real newlines and \\p as double-newlines.
    The $ terminator is kept as a literal character.

    Returns (display_text, escape_map) like inc_to_display.
    """
    # First convert EVENTide internal → .inc format, then use inc_to_display
    # EVENTide: \n\n = \\p, \n = \\n, $ kept as-is
    inc = internal.replace("\n\n", "\\p").replace("\n", "\\n")
    return inc_to_display(inc)


def display_to_eventide(display: str, escape_map: list[str] | None = None) -> str:
    """
    Convert display text back to EVENTide's internal format.

    EVENTide's internal format keeps $ as part of the text (parse_text_inc
    preserves it, write_text_inc writes it as-is). So we must add $ back.
    """
    inc = display_to_inc(display, escape_map)
    # display_to_inc already adds $ at the end — strip it, convert escapes,
    # then add $ back in the EVENTide-internal position
    result = inc.rstrip("$")
    result = result.replace("\\p", "\n\n").replace("\\n", "\n").replace("\\l", "\n")
    result += "$"
    return result


def display_char_count(line: str) -> int:
    """
    Count the number of *display* characters in a line, excluding {COMMANDS}
    which don't take up character space in the GBA text box.
    """
    clean = _COMMAND_RE.sub("", line)
    return len(clean)


# ── Shared ExtraSelections builder ──────────────────────────────────────────

def _build_selections(
    edit: QPlainTextEdit,
    max_cpl: int,
    max_lines: int,
) -> list[QTextEdit.ExtraSelection]:
    """
    Build ExtraSelections for overflow highlighting.

    Only sets BACKGROUND colour — foreground is left to the
    QSyntaxHighlighter (_CommandHighlighter) so {COMMANDS} stay
    blue even inside overflow zones.
    """
    txt = edit.toPlainText()
    lines = txt.split("\n")
    extra: list[QTextEdit.ExtraSelection] = []

    pos = 0
    for i, line in enumerate(lines):
        raw_len = len(line)
        disp_len = display_char_count(line)
        over_cpl = disp_len > max_cpl
        over_lines = (i >= max_lines) and (raw_len > 0)

        if over_cpl or over_lines:
            sel = QTextEdit.ExtraSelection()
            if over_cpl:
                start = pos + _find_overflow_pos(line, max_cpl)
                bg = _BG_OVERFLOW
            else:
                start = pos
                bg = _BG_EXTRA_LINES
            end = pos + raw_len
            if start < end:
                cur = edit.textCursor()
                cur.setPosition(start)
                cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = cur
                sel.format.setBackground(bg)
                extra.append(sel)

        pos += raw_len + 1

    return extra


def _build_counter_html(
    lines: list[str],
    max_cpl: int,
    max_lines: int,
) -> str:
    """Build the HTML for the counter label."""
    parts: list[str] = []
    for i, line in enumerate(lines[:max_lines]):
        n = display_char_count(line)
        if n > max_cpl:
            col = _CLR_RED
        elif n > int(max_cpl * 0.85):
            col = _CLR_AMBER
        else:
            col = _CLR_NORMAL
        parts.append(
            f'<span style="color:{col}">L{i + 1}: {n}/{max_cpl}</span>'
        )
    if len(lines) > max_lines:
        ex = len(lines) - max_lines
        parts.append(
            f'<span style="color:{_CLR_RED}">'
            f'+{ex} line{"s" if ex > 1 else ""}</span>'
        )
    return "&nbsp;&nbsp;".join(parts)


def _find_overflow_pos(line: str, max_display_chars: int) -> int:
    """
    Find the raw string position where display character count exceeds
    the limit, accounting for {COMMANDS} that don't count as display chars.
    """
    display_count = 0
    i = 0
    while i < len(line):
        if line[i] == "{":
            end = line.find("}", i)
            if end != -1 and _COMMAND_RE.match(line[i:end + 1]):
                i = end + 1
                continue
        display_count += 1
        if display_count > max_display_chars:
            return i
        i += 1
    return len(line)


# ── GameTextEdit widget ─────────────────────────────────────────────────────

class GameTextEdit(QWidget):
    """
    Complete text editing widget with:
    • QPlainTextEdit with monospace font
    • Per-line character counter label (colour-coded)
    • {COMMAND} highlighting in blue (via ExtraSelections, not syntax hl)
    • Right-click menu to insert text commands
    • Overflow highlighting (same as DexDescriptionEdit)
    • Converts to/from .inc escape format automatically
    """

    def __init__(
        self,
        max_chars_per_line: int = DEFAULT_CHARS_PER_LINE,
        max_lines: int = DEFAULT_MAX_LINES,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._max_cpl = max_chars_per_line
        self._max_lines = max_lines

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # The text editor
        self._edit = QPlainTextEdit()
        self._edit.setFont(QFont("Courier New", 9))
        self._edit.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._edit.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._edit)

        # Counter label
        self._counter = QLabel()
        self._counter.setStyleSheet("font-size: 10px; padding: 0 2px;")
        layout.addWidget(self._counter)

        # Escape map for preserving \p, \l on round-trip
        self._escape_map: list[str] = []

        # Syntax highlighter — sets foreground blue for {COMMANDS}
        self._highlighter = _CommandHighlighter(self._edit.document())

        # Key filter to block Enter past max lines
        self._edit.installEventFilter(self)

        # Connect for live updates
        self._edit.textChanged.connect(self._refresh)

    # ── public API ───────────────────────────────────────────────────────────

    @property
    def editor(self) -> QPlainTextEdit:
        """Access the underlying QPlainTextEdit if needed."""
        return self._edit

    def set_inc_text(self, raw: str) -> None:
        """Load raw .inc file text, converting escapes to display form."""
        display, self._escape_map = inc_to_display(raw)
        self._edit.blockSignals(True)
        self._edit.setPlainText(display)
        self._edit.blockSignals(False)
        self._refresh()

    def get_inc_text(self) -> str:
        """Return the text converted back to .inc format, preserving
        original escape sequences (\\p, \\l) where possible."""
        return display_to_inc(self._edit.toPlainText(), self._escape_map)

    def set_eventide_text(self, internal: str) -> None:
        """Load EVENTide's internal text format (real \\n, \\n\\n for pages)."""
        display, self._escape_map = eventide_to_display(internal)
        self._edit.blockSignals(True)
        self._edit.setPlainText(display)
        self._edit.blockSignals(False)
        self._refresh()

    def get_eventide_text(self) -> str:
        """Return text in EVENTide's internal format."""
        return display_to_eventide(self._edit.toPlainText(), self._escape_map)

    def get_display_text(self) -> str:
        """Return the display text as-is."""
        return self._edit.toPlainText()

    def set_limits(self, max_chars_per_line: int, max_lines: int) -> None:
        self._max_cpl = max_chars_per_line
        self._max_lines = max_lines
        self._refresh()

    def setMaximumHeight(self, h: int) -> None:
        """Proxy to inner edit widget for layout compat."""
        self._edit.setMaximumHeight(h)

    def setPlaceholderText(self, text: str) -> None:
        self._edit.setPlaceholderText(text)

    def connectChanged(self, slot) -> None:
        """Connect the textChanged signal to an external slot."""
        self._edit.textChanged.connect(slot)

    # ── event filter — block Enter at line limit ─────────────────────────────

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._edit and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self._edit.toPlainText().count("\n") >= self._max_lines - 1:
                        return True  # block
        return False

    # ── context menu with command insertion ──────────────────────────────────

    def _show_context_menu(self, pos):
        menu = self._edit.createStandardContextMenu()
        menu.addSeparator()

        insert_menu = QMenu("Insert Command", menu)
        insert_menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #ddd; }"
            "QMenu::item:selected { background: #1565c0; }"
        )

        for category, commands in _COMMAND_CATEGORIES.items():
            cat_menu = QMenu(category, insert_menu)
            cat_menu.setStyleSheet(insert_menu.styleSheet())
            for cmd in commands:
                action = QAction(cmd, cat_menu)
                action.triggered.connect(
                    lambda checked=False, c=cmd: self._insert_command(c)
                )
                cat_menu.addAction(action)
            insert_menu.addMenu(cat_menu)

        menu.addMenu(insert_menu)
        menu.exec(self._edit.mapToGlobal(pos))

    def _insert_command(self, cmd: str) -> None:
        cursor = self._edit.textCursor()
        cursor.insertText(cmd)
        self._edit.setTextCursor(cursor)

    # ── refresh counter + highlighting ───────────────────────────────────────

    def _refresh(self) -> None:
        txt = self._edit.toPlainText()
        lines = txt.split("\n")
        self._counter.setText(
            _build_counter_html(lines, self._max_cpl, self._max_lines)
        )
        self._edit.setExtraSelections(
            _build_selections(self._edit, self._max_cpl, self._max_lines)
        )


# ── attach_game_text_ui ─────────────────────────────────────────────────────

def attach_game_text_ui(
    plain_edit: QPlainTextEdit,
    counter_lbl: QLabel,
    max_chars_per_line: int = DEFAULT_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
) -> "_GameTextAttachment":
    """
    Attach game-text behaviour to an *existing* QPlainTextEdit (e.g. one
    generated by Qt Designer). Returns the attachment — keep a reference.
    """
    return _GameTextAttachment(
        plain_edit, counter_lbl, max_chars_per_line, max_lines
    )


class _GameTextAttachment(QObject):
    """
    Event-filter attachment that adds character limit enforcement,
    {COMMAND} highlighting, and right-click insert menu to an existing
    QPlainTextEdit — without subclassing.
    """

    def __init__(
        self,
        edit: QPlainTextEdit,
        counter: QLabel,
        max_cpl: int,
        max_lines: int,
    ):
        super().__init__(edit)
        self._edit = edit
        self._counter = counter
        self._max_cpl = max_cpl
        self._max_lines = max_lines

        edit.installEventFilter(self)
        edit.textChanged.connect(self._refresh)

        # Syntax highlighter for {COMMANDS} in blue
        self._highlighter = _CommandHighlighter(edit.document())

        # Context menu
        edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        edit.customContextMenuRequested.connect(self._show_context_menu)

        self._refresh()

    def update_limits(self, max_cpl: int, max_lines: int) -> None:
        self._max_cpl = max_cpl
        self._max_lines = max_lines
        self._refresh()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._edit and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self._edit.toPlainText().count("\n") >= self._max_lines - 1:
                        return True
        return False

    def _show_context_menu(self, pos):
        menu = self._edit.createStandardContextMenu()
        menu.addSeparator()
        insert_menu = QMenu("Insert Command", menu)
        for category, commands in _COMMAND_CATEGORIES.items():
            cat_menu = QMenu(category, insert_menu)
            for cmd in commands:
                action = QAction(cmd, cat_menu)
                action.triggered.connect(
                    lambda checked=False, c=cmd: self._insert_command(c)
                )
                cat_menu.addAction(action)
            insert_menu.addMenu(cat_menu)
        menu.addMenu(insert_menu)
        menu.exec(self._edit.mapToGlobal(pos))

    def _insert_command(self, cmd: str) -> None:
        cursor = self._edit.textCursor()
        cursor.insertText(cmd)
        self._edit.setTextCursor(cursor)

    def _refresh(self) -> None:
        txt = self._edit.toPlainText()
        lines = txt.split("\n")
        self._counter.setText(
            _build_counter_html(lines, self._max_cpl, self._max_lines)
        )
        self._edit.setExtraSelections(
            _build_selections(self._edit, self._max_cpl, self._max_lines)
        )
