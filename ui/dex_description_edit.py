"""
ui/dex_description_edit.py
Smart Pokédex description editor — enforces per-line character limits and
line-count limits with real-time visual feedback.

Used by both the Pokédex detail panel and the Species → Info tab.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QTextCursor
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QTextEdit


# ── DexDescriptionEdit ────────────────────────────────────────────────────────

class DexDescriptionEdit(QPlainTextEdit):
    """
    QPlainTextEdit subclass for Pokédex entry descriptions.

    Features:
    • Per-line character limit: overflow characters are highlighted in red.
    • Line-count limit: Enter key is blocked when the limit is reached.
    • Optional counter QLabel (attach via set_counter_label) showing
      "L1: 18/42  L2: 36/42  L3: 7/42" with colour-coded status.
    """

    def __init__(
        self,
        max_chars_per_line: int = 42,
        max_lines: int = 3,
        parent=None,
    ):
        super().__init__(parent)
        self._max_cpl: int = max_chars_per_line
        self._max_lines: int = max_lines
        self._counter: QLabel | None = None
        self.textChanged.connect(self._refresh)

    # ── public API ────────────────────────────────────────────────────────────

    def set_limits(self, max_chars_per_line: int, max_lines: int = 3) -> None:
        """Update limits and re-validate current content."""
        self._max_cpl = max_chars_per_line
        self._max_lines = max_lines
        self._refresh()

    def set_counter_label(self, lbl: QLabel) -> None:
        """Attach an external QLabel that receives the formatted count text."""
        self._counter = lbl
        self._refresh()

    # ── key handling ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.toPlainText().count("\n") >= self._max_lines - 1:
                return  # already at the maximum number of lines — block it
        super().keyPressEvent(event)

    # ── internal ─────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        txt = self.toPlainText()
        lines = txt.split("\n")
        max_cpl = self._max_cpl
        max_ln = self._max_lines

        # ── update counter label ─────────────────────────────────────────────
        if self._counter is not None:
            parts: list[str] = []
            for i, line in enumerate(lines[:max_ln]):
                n = len(line)
                if n > max_cpl:
                    col = "#e57373"          # red — over limit
                elif n > int(max_cpl * 0.85):
                    col = "#ffb74d"          # amber — close to limit
                else:
                    col = "#555555"          # dim grey — fine
                parts.append(
                    f'<span style="color:{col}">L{i + 1}: {n}/{max_cpl}</span>'
                )
            if len(lines) > max_ln:
                ex = len(lines) - max_ln
                parts.append(
                    f'<span style="color:#e57373">'
                    f'+{ex} line{"s" if ex > 1 else ""}</span>'
                )
            self._counter.setText("&nbsp;&nbsp;".join(parts))

        # ── ExtraSelections – highlight overflow ──────────────────────────────
        extra: list[QTextEdit.ExtraSelection] = []
        pos = 0
        for i, line in enumerate(lines):
            ln_len = len(line)
            over_cpl = ln_len > max_cpl
            over_lines = (i >= max_ln) and (ln_len > 0)

            if over_cpl or over_lines:
                sel = QTextEdit.ExtraSelection()
                if over_cpl:
                    start = pos + max_cpl
                    bg    = QColor("#5c1a1a")
                    fg    = QColor("#ff6b6b")
                else:
                    start = pos
                    bg    = QColor("#3d1a00")
                    fg    = QColor("#ff9944")
                end = pos + ln_len
                cur = self.textCursor()
                cur.setPosition(start)
                cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = cur
                sel.format.setBackground(bg)
                sel.format.setForeground(fg)
                extra.append(sel)

            pos += ln_len + 1  # +1 for the \n separator


        self.setExtraSelections(extra)


# ── attach_dex_limit_ui ───────────────────────────────────────────────────────

def attach_dex_limit_ui(
    plain_edit: QPlainTextEdit,
    counter_lbl: QLabel,
    max_chars_per_line: int = 42,
    max_lines: int = 3,
) -> "_DexLimitAttachment":
    """
    Attach description-limit behaviour to an *existing* QPlainTextEdit
    (one you cannot replace with a DexDescriptionEdit subclass, e.g.
    ui_mainwindow-generated widgets).

    Returns the attachment object — keep a reference to prevent GC.
    """
    attachment = _DexLimitAttachment(
        plain_edit, counter_lbl, max_chars_per_line, max_lines
    )
    return attachment


class _DexLimitAttachment(QObject):
    """
    Installs an event-filter on an existing QPlainTextEdit and drives
    per-line highlighting + a counter label — mirroring DexDescriptionEdit
    without subclassing.
    """

    def __init__(
        self,
        edit: QPlainTextEdit,
        counter: QLabel,
        max_cpl: int,
        max_lines: int,
    ):
        super().__init__(edit)          # parent = edit keeps us alive
        self._edit = edit
        self._counter = counter
        self._max_cpl = max_cpl
        self._max_lines = max_lines

        edit.installEventFilter(self)
        edit.textChanged.connect(self._refresh)
        self._refresh()

    def update_limits(self, max_cpl: int, max_lines: int = 3) -> None:
        self._max_cpl = max_cpl
        self._max_lines = max_lines
        self._refresh()

    # ── event filter – block Enter at line limit ──────────────────────────────

    def eventFilter(self, obj, event) -> bool:   # type: ignore[override]
        if obj is self._edit and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self._edit.toPlainText().count("\n") >= self._max_lines - 1:
                        return True   # block
        return False

    # ── refresh counter + highlights ─────────────────────────────────────────

    def _refresh(self) -> None:
        txt = self._edit.toPlainText()
        lines = txt.split("\n")
        max_cpl = self._max_cpl
        max_ln = self._max_lines

        # counter
        parts: list[str] = []
        for i, line in enumerate(lines[:max_ln]):
            n = len(line)
            if n > max_cpl:
                col = "#e57373"
            elif n > int(max_cpl * 0.85):
                col = "#ffb74d"
            else:
                col = "#555555"
            parts.append(
                f'<span style="color:{col}">L{i + 1}: {n}/{max_cpl}</span>'
            )
        if len(lines) > max_ln:
            ex = len(lines) - max_ln
            parts.append(
                f'<span style="color:#e57373">'
                f'+{ex} line{"s" if ex > 1 else ""}</span>'
            )
        self._counter.setText("&nbsp;&nbsp;".join(parts))

        # extra selections
        extra: list[QTextEdit.ExtraSelection] = []
        pos = 0
        for i, line in enumerate(lines):
            ln_len = len(line)
            over_cpl   = ln_len > max_cpl
            over_lines = (i >= max_ln) and (ln_len > 0)
            if over_cpl or over_lines:
                sel = QTextEdit.ExtraSelection()
                if over_cpl:
                    start = pos + max_cpl
                    bg = QColor("#5c1a1a")
                    fg = QColor("#ff6b6b")
                else:
                    start = pos
                    bg = QColor("#3d1a00")
                    fg = QColor("#ff9944")
                end = pos + ln_len
                cur = self._edit.textCursor()
                cur.setPosition(start)
                cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = cur
                sel.format.setBackground(bg)
                sel.format.setForeground(fg)
                extra.append(sel)
            pos += ln_len + 1
        self._edit.setExtraSelections(extra)
