"""ui/ev_suggest_dialog.py — the Suggest EV Yield preview dialog.

Opened by the "Suggest EV Yield…" button next to the EV Yield controls on
the Pokemon Stats tab. Pure preview: it shows the current yield, the
recommendation from ``core/ev_yield.py`` with its plain-English reasoning
and genuine vanilla Gen 3 comparables, and lets the user pick between the
suggestion and its alternatives. NOTHING is changed until the user clicks
Apply — and Apply only reports the chosen values back to mainwindow, which
sets the ordinary EV spinboxes exactly as if the user had typed them (so
dirty tracking, save and undo behave identically to a manual edit).
Cancel changes nothing at all.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget,
)

from core.ev_yield import (
    CATEGORY_LABELS, EvSuggestion, suggest_ev_yield,
)
from core.stat_balance import STAT_KEYS, STAT_LABELS
from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive

_log = logging.getLogger("StatBalance")

#: Stage choices offered when the evolution graph could not decide.
_STAGE_CHOICES = ("first", "middle", "final", "standalone")


def _fmt(evs: Dict[str, int]) -> str:
    parts = [f"+{evs[k]} {STAT_LABELS[k]}" for k in STAT_KEYS
             if evs.get(k)]
    return ", ".join(parts) if parts else "none"


class EvSuggestDialog(QDialog):
    """Preview/compare/apply dialog. ``selected_evs()`` returns the chosen
    allocation after ``exec()`` returned Accepted; the caller applies it
    through the normal spinbox path."""

    def __init__(self, display_name: str,
                 stats: Dict[str, int],
                 current: Dict[str, int],
                 category: str, category_text: str,
                 per_stat_max: int = 3,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Suggest EV Yield")
        self._name = display_name
        self._stats = dict(stats)
        self._current = {k: int(current.get(k, 0) or 0) for k in STAT_KEYS}
        self._per_stat_max = max(1, int(per_stat_max))
        self._choice_group: Optional[QButtonGroup] = None
        self._choices: List[Dict[str, int]] = []
        self._suggestion: Optional[EvSuggestion] = None

        root = QVBoxLayout(self)
        root.setSpacing(6)

        # Stage row (combo only shown when the stage is uncertain).
        stage_row = QHBoxLayout()
        self._stage_label = QLabel("")
        self._stage_label.setWordWrap(True)
        stage_row.addWidget(self._stage_label, 1)
        self._stage_combo = QComboBox()
        for c in _STAGE_CHOICES:
            self._stage_combo.addItem(CATEGORY_LABELS[c], c)
        self._stage_combo.currentIndexChanged.connect(self._on_stage_picked)
        self._stage_combo.setVisible(False)
        stage_row.addWidget(self._stage_combo, 1)
        root.addLayout(stage_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(6)
        root.addWidget(self._body)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(
            QDialogButtonBox.StandardButton.Apply).clicked.connect(
                self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._category = category
        self._category_text = category_text
        self._rebuild(category, category_text, first=True)
        install_scroll_guard_recursive(self)
        self.setMinimumWidth(460)

    # ── building ─────────────────────────────────────────────────────────

    def _on_stage_picked(self, _i) -> None:
        cat = self._stage_combo.currentData()
        if cat:
            self._rebuild(cat, "Stage chosen by you.", first=False)

    def _rebuild(self, category: str, category_text: str,
                 first: bool) -> None:
        sug = suggest_ev_yield(self._stats, category, category_text,
                               self._name)
        self._suggestion = sug
        if first and sug.uncertain:
            self._stage_label.setText(
                "The project's evolution data could not determine this "
                "creature's stage — pick it here:")
            self._stage_combo.setVisible(True)
        elif first:
            self._stage_label.setText(category_text or
                                      CATEGORY_LABELS.get(category, ""))

        # Clear and refill the body.
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        cur_lbl = QLabel(f"Current EV Yield:  {_fmt(self._current)}")
        grid.addWidget(cur_lbl, 0, 0, 1, 2)
        self._body_lay.addLayout(grid)

        # The suggestion and its alternatives, as pickable options.
        self._choice_group = QButtonGroup(self)
        self._choices = []
        options = [("Suggested", sug.evs,
                    f"Total: {sug.total} EV "
                    f"point{'s' if sug.total != 1 else ''}")]
        options += [(a.label, a.evs, a.text) for a in sug.alternatives]
        for i, (label, evs, text) in enumerate(options):
            rb = QRadioButton(f"{label}:  {_fmt(evs)}")
            f = rb.font()
            if i == 0:
                f.setBold(True)
            rb.setFont(f)
            rb.setChecked(i == 0)
            rb.setToolTip(text)
            self._choice_group.addButton(rb, i)
            self._choices.append(
                {k: min(int(v), self._per_stat_max)
                 for k, v in evs.items()})
            self._body_lay.addWidget(rb)
            note = QLabel(text)
            note.setWordWrap(True)
            note.setStyleSheet("color: #9e9e9e; margin-left: 18px;")
            self._body_lay.addWidget(note)

        for para in sug.rationale:
            lab = QLabel(para)
            lab.setWordWrap(True)
            self._body_lay.addWidget(lab)

        if sug.examples:
            ex = QLabel("Comparable vanilla Gen 3 species:\n  "
                        + "\n  ".join(sug.examples))
            ex.setWordWrap(True)
            ex.setStyleSheet("color: #9e9e9e;")
            self._body_lay.addWidget(ex)

    # ── result ───────────────────────────────────────────────────────────

    def selected_evs(self) -> Optional[Dict[str, int]]:
        """The allocation the user picked (every STAT_KEY present, zeros
        filled in), or None when nothing is selected."""
        if self._choice_group is None:
            return None
        idx = self._choice_group.checkedId()
        if idx < 0 or idx >= len(self._choices):
            return None
        picked = self._choices[idx]
        return {k: int(picked.get(k, 0) or 0) for k in STAT_KEYS}
