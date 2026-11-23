"""ui/stat_balance_panel.py — the Stat Balance Assistant panel.

Sits underneath the six Base Stats inputs on the Pokemon Stats tab. Pure
presentation: every number, label and note is computed by
``core/stat_balance.py``; this file only draws and persists editor-side
preferences.

Hard rules, enforced by construction:

* READ-ONLY toward species data. The panel never calls a species setter,
  never touches a base-stat spinbox, never emits into the window's dirty
  tracking. Changing a tier here records design INTENT in PorySuite's
  own data folder — the creature's actual stats are untouched.
* The panel's own widgets live on the panel, not on ``mainwindow.ui``, so
  the blanket ``_dirty`` auto-wiring in mainwindow can never see them.
* Every combo/spin in here gets the shared scroll guard (CLAUDE.md rule:
  a closed dropdown must ignore the mouse wheel).
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from core.app_info import get_data_dir, get_settings_path
from core.stat_balance import (
    BAND_EXPLANATION, GEN3_REFERENCE, MAX_BST,
    STAT_KEYS, STAT_LABELS, STAT_SOURCE_FIELDS, TIER_CUSTOM, TIER_NAMES,
    TIER_UNSPECIFIED, BalancePrefsStore, EvolutionEdges, RosterStats,
    advisory_notes, bst, classify_bst, classify_evolution_edges,
    evolution_paths, nearest_entries, regression_note, species_pref_key,
    stat_levels, target_feedback, tier_range, vanilla_comparison,
)
from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive


def _tip(text: str) -> str:
    """Tooltip text as rich text, so Qt word-wraps it at a readable width
    instead of letting one plain-text line spill across the window."""
    from html import escape
    return ("<qt style='white-space:normal'>"
            + escape(text).replace("\n", "<br>") + "</qt>")

_log = logging.getLogger("StatBalance")

_LEVEL_COLORS = {"low": "#64b5f6", "moderate": "#9e9e9e", "high": "#ffb74d"}
_STATUS_COLORS = {"above": "#ffb74d", "below": "#64b5f6",
                  "within": "#81c784", "none": "#9e9e9e"}

#: Settings keys (global editor preferences, settings.ini).
_S_ASSIST = "stat_balance/show_assistant"
_S_DOTS = "stat_balance/show_stat_indicators"
_S_WARN = "stat_balance/show_warnings"
_S_EVO = "stat_balance/show_evolution"
_S_EXP_DIST = "stat_balance/expand_distribution"
_S_EXP_EVO = "stat_balance/expand_evolution"


def _settings() -> QSettings:
    return QSettings(get_settings_path(), QSettings.Format.IniFormat)


def _get_bool(key: str, default: bool = True) -> bool:
    try:
        return _settings().value(key, default, type=bool)
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Small custom-painted widgets
# ─────────────────────────────────────────────────────────────────────────────

class _BstBar(QWidget):
    """Thin strip showing where the BST sits on a fixed 0–720 scale, filled
    with the classification band's color. Fixed scale = comparable between
    species; 720 comfortably covers every vanilla and most custom totals
    (higher totals simply pin the bar full)."""

    SCALE = 720

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._color = QColor("#9e9e9e")
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def set_value(self, total: int, color: str) -> None:
        self._value = max(0, int(total))
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _ev):  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(0, 0, w, h, 3, 3)
        frac = min(1.0, self._value / float(self.SCALE))
        if frac > 0:
            p.setBrush(self._color)
            p.drawRoundedRect(0, 0, max(4, int(w * frac)), h, 3, 3)
        p.end()


class _StatDistribution(QWidget):
    """Six fixed-scale (0–255) horizontal bars, one per stat, with the
    numeric value drawn beside each bar and an optional advisory dot.
    The spinboxes above keep the editable numbers; this is a read-only
    picture of the spread."""

    _ROW_H = 16
    _GAP = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stats: Dict[str, int] = {}
        self._levels: Dict[str, Tuple[str, str]] = {}
        self._show_dots = True
        n = len(STAT_KEYS)
        self.setMinimumHeight(n * self._ROW_H + (n - 1) * self._GAP + 4)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def set_data(self, stats: Dict[str, int],
                 levels: Dict[str, Tuple[str, str]],
                 show_dots: bool) -> None:
        self._stats = dict(stats)
        self._levels = dict(levels)
        self._show_dots = show_dots
        tips = []
        for k in STAT_KEYS:
            lvl, basis = self._levels.get(k, ("moderate", ""))
            if show_dots and basis:
                tips.append(f"{STAT_LABELS[k]}: {lvl} — {basis}")
        self.setToolTip(
            "Fixed 0–255 scale for every bar, so spreads compare fairly."
            + ("\n" + "\n".join(tips) if tips else ""))
        self.update()

    def paintEvent(self, _ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        fm = p.fontMetrics()
        label_w = max(fm.horizontalAdvance(STAT_LABELS[k])
                      for k in STAT_KEYS) + 8
        value_w = fm.horizontalAdvance("255") + 6
        dot_w = 10 if self._show_dots else 0
        bar_x = label_w
        bar_w = max(40, self.width() - label_w - value_w - dot_w - 4)
        text_col = self.palette().windowText().color()
        y = 2
        for k in STAT_KEYS:
            v = max(0, min(255, int(self._stats.get(k, 0) or 0)))
            lvl = self._levels.get(k, ("moderate", ""))[0]
            # label
            p.setPen(QPen(text_col))
            p.drawText(0, y, label_w - 6, self._ROW_H,
                       int(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter),
                       STAT_LABELS[k])
            # track + fill (one neutral fill color — the advisory dot and
            # tooltip carry the low/high reading, so color-blind users are
            # not asked to compare bar hues)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 22))
            p.drawRect(bar_x, y + 3, bar_w, self._ROW_H - 6)
            p.setBrush(QColor("#5c9ddf"))
            p.drawRect(bar_x, y + 3, int(bar_w * v / 255.0), self._ROW_H - 6)
            # value
            p.setPen(QPen(text_col))
            p.drawText(bar_x + bar_w + 4, y, value_w, self._ROW_H,
                       int(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter), str(v))
            # advisory dot (low/high only; moderate stays quiet)
            if self._show_dots and lvl in ("low", "high"):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(_LEVEL_COLORS[lvl]))
                d = 7
                p.drawEllipse(bar_x + bar_w + value_w + 2,
                              y + (self._ROW_H - d) // 2, d, d)
            y += self._ROW_H + self._GAP
        p.end()


class _Section(QWidget):
    """A compact collapsible section: arrow button header + body widget."""

    def __init__(self, title: str, settings_key: str, parent=None):
        super().__init__(parent)
        self._key = settings_key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._btn = QToolButton()
        self._btn.setText(title)
        self._btn.setCheckable(True)
        self._btn.setChecked(_get_bool(settings_key, False))
        self._btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._btn.setStyleSheet(
            "QToolButton { border: none; padding: 1px; }")
        self._btn.toggled.connect(self._on_toggled)
        lay.addWidget(self._btn)
        self.body = QWidget()
        lay.addWidget(self.body)
        self._apply()

    def _apply(self) -> None:
        open_ = self._btn.isChecked()
        self._btn.setArrowType(Qt.ArrowType.DownArrow if open_
                               else Qt.ArrowType.RightArrow)
        self.body.setVisible(open_)

    def _on_toggled(self, _checked: bool) -> None:
        self._apply()
        try:
            _settings().setValue(self._key, self._btn.isChecked())
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# The panel
# ─────────────────────────────────────────────────────────────────────────────

class StatBalancePanel(QWidget):
    """Lives under the Base Stats inputs. mainwindow drives it through two
    calls only:

    * ``update_context(project_dir, source_data, species, form)`` on species
      or form switch — reloads this species' stored intent, rebuilds the
      roster/evolution context.
    * ``update_stats(stats)`` whenever a base-stat spinbox changes — live
      recompute of everything derived.
    """

    def __init__(self, parent=None, prefs_store: Optional[BalancePrefsStore] = None):
        super().__init__(parent)
        self._store = prefs_store or BalancePrefsStore(
            os.path.join(get_data_dir(), "stat_balance.json"))
        self._loading = False           # guards pref writes during setup
        self._project_dir = ""
        self._source_data = None
        self._species = ""
        self._form: Optional[str] = None
        self._stats: Dict[str, int] = {k: 0 for k in STAT_KEYS}
        self._roster: Dict[str, Dict[str, int]] = {}
        self._raw_evos: Dict[str, List[dict]] = {}
        self._edges: EvolutionEdges = EvolutionEdges({}, [], [])
        self._names: Dict[str, str] = {}

        self._build_ui()
        install_scroll_guard_recursive(self)
        self._apply_visibility()

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(3)

        # BST header: always visible, even with the assistant disabled.
        head = QHBoxLayout()
        head.setSpacing(6)
        self._bst_label = QLabel("Base Stat Total: —")
        f = self._bst_label.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self._bst_label.setFont(f)
        self._bst_label.setToolTip(_tip(
            "The sum of the six base stats above — EV yields and every "
            "other field are excluded.\n\n" + BAND_EXPLANATION))
        head.addWidget(self._bst_label)
        head.addStretch(1)
        self._gear = QToolButton()
        self._gear.setText("⚙")
        self._gear.setToolTip("Stat Balance Assistant display options")
        self._gear.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._gear.setStyleSheet("QToolButton { border: none; }")
        self._gear.setMenu(self._build_gear_menu())
        head.addWidget(self._gear)
        root.addLayout(head)

        self._class_label = QLabel("")
        self._class_label.setToolTip(_tip(BAND_EXPLANATION))
        self._class_label.setWordWrap(True)
        root.addWidget(self._class_label)
        self._bst_bar = _BstBar()
        self._bst_bar.setToolTip(_tip(BAND_EXPLANATION))
        root.addWidget(self._bst_bar)

        # ── Balance Assistant (hideable as a whole) ──────────────────────
        self._assist = QWidget()
        a = QVBoxLayout(self._assist)
        a.setContentsMargins(0, 4, 0, 0)
        a.setSpacing(3)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #444;")
        a.addWidget(line)
        title = QLabel("Balance Assistant")
        tf = title.font()
        tf.setBold(True)
        title.setFont(tf)
        title.setToolTip(_tip(
            "Advisory only. Records design intent for this creature in "
            "PorySuite's own settings — never in the game source, and it "
            "never changes the creature's actual stats."))
        a.addWidget(title)

        # Intended strength
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Intended strength:"))
        self._tier_combo = QComboBox()
        self._tier_combo.addItems(list(TIER_NAMES))
        # Don't let the longest tier name dictate the whole column's
        # minimum width — the popup still shows full names.
        self._tier_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._tier_combo.setMinimumContentsLength(14)
        self._tier_combo.setToolTip(_tip(
            "YOUR design goal for this creature — the editor cannot know "
            "it, so it defaults to Unspecified and nothing is assumed. "
            "The colored classification above stays a neutral reference; "
            "only this selection produces above/within/below feedback. "
            "Suggested BST ranges, not rules."))
        self._tier_combo.currentIndexChanged.connect(self._on_pref_edited)
        row.addWidget(self._tier_combo, 1)
        a.addLayout(row)

        # Custom range (only visible for the Custom tier)
        self._custom_row = QWidget()
        cr = QHBoxLayout(self._custom_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.setSpacing(6)
        cr.addWidget(QLabel("Target BST:"))
        self._custom_min = QSpinBox()
        self._custom_max = QSpinBox()
        for sb in (self._custom_min, self._custom_max):
            sb.setRange(0, MAX_BST)
            sb.setToolTip("Custom target BST range (0–%d)." % MAX_BST)
        self._custom_max.setValue(MAX_BST)
        cr.addWidget(self._custom_min)
        cr.addWidget(QLabel("–"))
        cr.addWidget(self._custom_max)
        cr.addStretch(1)
        self._custom_min.valueChanged.connect(self._on_pref_edited)
        self._custom_max.valueChanged.connect(self._on_pref_edited)
        a.addWidget(self._custom_row)

        # (The Combat Role dropdown was removed on 2026-11-22: measured
        # against the full roster it almost never changed the output, and
        # specialisation-vs-accident is read from the stats themselves
        # now. See core/stat_balance.py for the numbers.)

        self._target_label = QLabel("")
        self._target_label.setWordWrap(True)
        a.addWidget(self._target_label)

        self._notes_label = QLabel("")
        self._notes_label.setWordWrap(True)
        self._notes_label.setTextFormat(Qt.TextFormat.RichText)
        a.addWidget(self._notes_label)

        # Stat distribution (collapsible)
        self._dist_section = _Section("Stat distribution", _S_EXP_DIST)
        db = QVBoxLayout(self._dist_section.body)
        db.setContentsMargins(8, 0, 0, 0)
        self._dist = _StatDistribution()
        db.addWidget(self._dist)
        a.addWidget(self._dist_section)

        # Evolution comparison (collapsible)
        self._evo_section = _Section("Evolution comparison", _S_EXP_EVO)
        eb = QVBoxLayout(self._evo_section.body)
        eb.setContentsMargins(8, 0, 0, 0)
        self._evo_label = QLabel("")
        self._evo_label.setWordWrap(True)
        self._evo_label.setToolTip(_tip(
            "Built from the project's real evolution data, with reverse "
            "evolutions (devolution) recognised from the project's own "
            "conventions and shown as information, not warnings. "
            "Alternate forms are not evolutions and are not shown here."))
        eb.addWidget(self._evo_label)
        a.addWidget(self._evo_section)

        # Reference comparisons
        self._ref_label = QLabel("")
        self._ref_label.setWordWrap(True)
        self._ref_label.setToolTip(_tip(
            "Closest totals among this project's own species come first — "
            "the project is always the primary reference. The vanilla "
            "line uses authentic Generation 3 (FireRed) values, not "
            "later-generation revisions, and only appears when it adds "
            "information the project line does not. Similar totals do "
            "not mean similar battle performance."))
        a.addWidget(self._ref_label)

        root.addWidget(self._assist)

    def _build_gear_menu(self) -> QMenu:
        m = QMenu(self)
        self._acts = {}
        for key, label in (
                (_S_ASSIST, "Show Balance Assistant"),
                (_S_DOTS, "Show per-stat indicators"),
                (_S_WARN, "Show contextual warnings"),
                (_S_EVO, "Show evolution comparison")):
            act = m.addAction(label)
            act.setCheckable(True)
            act.setChecked(_get_bool(key, True))
            act.toggled.connect(
                lambda on, k=key: self._on_setting_toggled(k, on))
            self._acts[key] = act
        return m

    # ── settings ─────────────────────────────────────────────────────────

    def _on_setting_toggled(self, key: str, on: bool) -> None:
        try:
            _settings().setValue(key, bool(on))
        except Exception:
            _log.exception("Could not persist %s", key)
        self._apply_visibility()
        self._recompute()

    def _apply_visibility(self) -> None:
        self._assist.setVisible(_get_bool(_S_ASSIST, True))
        self._evo_section.setVisible(_get_bool(_S_EVO, True))

    # ── preference (design intent) handling ─────────────────────────────

    def _pref_key(self) -> str:
        return species_pref_key(self._species, self._form)

    def _on_pref_edited(self, *_a) -> None:
        if self._loading or not self._species:
            self._sync_custom_row()
            return
        self._sync_custom_row()
        try:
            self._store.set(self._project_dir, self._pref_key(), {
                "tier": self._tier_combo.currentText(),
                "custom_min": self._custom_min.value(),
                "custom_max": self._custom_max.value(),
            })
        except Exception:
            _log.exception("Could not save balance prefs for %s",
                           self._pref_key())
        self._recompute()

    def _sync_custom_row(self) -> None:
        is_custom = self._tier_combo.currentText() == TIER_CUSTOM
        self._custom_row.setVisible(is_custom)

    def _current_range(self) -> Optional[Tuple[int, int]]:
        return tier_range(self._tier_combo.currentText(),
                          (self._custom_min.value(), self._custom_max.value()))

    # ── data plumbing (read-only toward the project) ─────────────────────

    def update_context(self, project_dir: str, source_data,
                       species: str, form: Optional[str] = None) -> None:
        """Species/form switch: load this species' stored intent and rebuild
        the roster + evolution context. Read-only throughout."""
        self._project_dir = project_dir or ""
        self._source_data = source_data
        self._species = species or ""
        self._form = form or None
        self._rebuild_project_context()
        self._loading = True
        try:
            prefs = (self._store.get(self._project_dir, self._pref_key())
                     if self._species else dict())
            tier = prefs.get("tier", TIER_UNSPECIFIED)
            self._tier_combo.setCurrentText(
                tier if tier in TIER_NAMES else TIER_UNSPECIFIED)
            cmin, cmax = prefs.get("custom_min"), prefs.get("custom_max")
            self._custom_min.setValue(int(cmin) if cmin is not None else 0)
            self._custom_max.setValue(int(cmax) if cmax is not None else MAX_BST)
            self._sync_custom_row()
        finally:
            self._loading = False
        self._recompute()

    def update_stats(self, stats: Dict[str, int]) -> None:
        """Live values from the six spinboxes (unsaved edits included)."""
        self._stats = {k: int(stats.get(k, 0) or 0) for k in STAT_KEYS}
        self._recompute()

    def clear(self) -> None:
        """Project closed / nothing selected."""
        self.update_context("", None, "", None)

    def _rebuild_project_context(self) -> None:
        """Roster stats, evolution edges and display names, read once per
        species switch from the live (unsaved-edits-included) data model."""
        self._roster = {}
        self._raw_evos = {}
        self._edges = EvolutionEdges({}, [], [])
        self._names = {}
        sd = self._source_data
        if sd is None:
            return
        try:
            all_species = list(sd.get_pokemon_data() or {})
        except Exception:
            _log.exception("Could not list species for balance context")
            return
        for sp in all_species:
            try:
                row = {}
                for key, field in STAT_SOURCE_FIELDS.items():
                    v = sd.get_species_info(sp, field)
                    row[key] = int(v) if isinstance(v, (int, float)) else 0
                self._roster[sp] = row
                name = sd.get_species_info(sp, "speciesName")
                self._names[sp] = (str(name) if name
                                   else sp.replace("SPECIES_", "").title())
                rows = [e for e in (sd.get_evolutions(sp) or [])
                        if isinstance(e, dict)]
                if rows:
                    self._raw_evos[sp] = rows
            except Exception:
                # One malformed species must not take the panel down.
                _log.debug("Skipping %s in balance context", sp,
                           exc_info=True)
        try:
            self._edges = classify_evolution_edges(self._raw_evos)
        except Exception:
            _log.exception("Could not classify evolution edges")
            self._edges = EvolutionEdges({}, [], [])

    def _display_name(self, sp: str) -> str:
        return self._names.get(sp) or sp.replace("SPECIES_", "").title()

    # ── the one recompute path ───────────────────────────────────────────

    def _recompute(self) -> None:
        total = bst(self._stats)
        label, color = classify_bst(total)
        if not self._species:
            self._bst_label.setText("Base Stat Total: —")
            self._class_label.setText("")
            self._bst_bar.set_value(0, "#9e9e9e")
            self._target_label.setText("")
            self._notes_label.setText("")
            self._evo_label.setText("")
            self._ref_label.setText("")
            self._dist.set_data({}, {}, False)
            return

        self._bst_label.setText(f"Base Stat Total: {total}")
        # Descriptive reference only — verdict-shaped feedback lives in the
        # target line below, where it is measured against the USER'S goal.
        self._class_label.setText(f"{label}")
        self._class_label.setStyleSheet(
            f"color: {color};")
        self._bst_bar.set_value(total, color)

        # Gate on the SETTING, never on isVisible(): a widget on a
        # non-current tab reports invisible, and skipping the recompute
        # there would leave stale content waiting when the user switches
        # to the Stats tab.
        if not _get_bool(_S_ASSIST, True):
            return

        rng = self._current_range()

        # Target feedback — the one place "stronger/weaker than intended"
        # language is allowed, because it compares against the target the
        # user chose themselves.
        status, _delta, text = target_feedback(total, rng)
        if status == "none":
            if self._tier_combo.currentText() == TIER_CUSTOM:
                self._target_label.setText(
                    "Enter a valid custom range (min ≤ max).")
                self._target_label.setStyleSheet("color: #e57373;")
            else:
                self._target_label.setText(
                    "No target range selected — nothing is assumed about "
                    "this creature's intended strength.")
                self._target_label.setStyleSheet("color: #9e9e9e;")
        else:
            extra = ""
            if status == "above":
                extra = (" Potentially stronger than intended for this "
                         "tier.")
            elif status == "below":
                extra = (" Potentially weaker than intended for this "
                         "tier.")
            self._target_label.setText(text + extra)
            self._target_label.setStyleSheet(
                f"color: {_STATUS_COLORS[status]};")

        # Per-stat levels + distribution
        show_dots = _get_bool(_S_DOTS, True)
        tier_roster = RosterStats(self._roster, rng) if rng else None
        levels = stat_levels(
            self._stats,
            tier_roster if (tier_roster and tier_roster.usable) else None)
        self._dist.set_data(self._stats, levels, show_dots)

        # Advisory notes (only with a chosen target range)
        if _get_bool(_S_WARN, True):
            notes = advisory_notes(self._stats, tier_roster)
            parts = []
            for kind, note in notes:
                c = "#ffb74d" if kind == "caution" else "#9e9e9e"
                parts.append(f'<span style="color:{c};">• {note}</span>')
            self._notes_label.setText("<br>".join(parts))
            self._notes_label.setVisible(bool(parts))
        else:
            self._notes_label.setText("")
            self._notes_label.setVisible(False)

        self._update_evolution(total)
        self._update_references(total)

    def _update_evolution(self, _total: int) -> None:
        if not _get_bool(_S_EVO, True):
            return
        bsts = {sp: bst(st) for sp, st in self._roster.items()}
        # The selected creature's UNSAVED edits count too.
        bsts[self._species] = bst(self._stats)
        lines: List[str] = []

        # Forward chains — only edges judged to be forward evolution.
        paths = evolution_paths(self._species, self._edges.forward, bsts)
        for path in paths:
            if len(path) < 2:
                continue
            bits = [f"{self._display_name(path[0][0])} {path[0][1]}"]
            for (prev_sp, prev_b), (sp, b) in zip(path, path[1:]):
                diff = b - prev_b
                bits.append(f"{self._display_name(sp)} {b} ({diff:+d})")
            lines.append("  →  ".join(bits))
            note = regression_note(path)
            if note:
                lines.append(note)

        # Reverse (devolution) edges touching this creature — shown as
        # information, never as a warning: the project's data says this
        # backward arrow is intentional.
        rev = self._edges.reverse_edges_touching(self._species)
        for src, tgt in rev[:4]:
            sb_, tb = bsts.get(src, 0), bsts.get(tgt, 0)
            diff = tb - sb_
            lines.append(
                f"{self._display_name(src)} {sb_}  →  "
                f"{self._display_name(tgt)} {tb} ({diff:+d}, reverse "
                f"evolution)")
        if len(rev) > 4:
            lines.append(f"…and {len(rev) - 4} more reverse-evolution "
                         f"links.")

        grp = self._edges.in_ambiguous_group(self._species)
        if grp:
            members = ", ".join(sorted(self._display_name(s) for s in grp))
            lines.append(
                f"{members} form an evolution cycle whose direction could "
                f"not be determined from the project's data — no stage or "
                f"direction is assumed.")

        self._evo_label.setText(
            "\n".join(lines) if lines
            else "No evolution data for this creature.")

    def _update_references(self, total: int) -> None:
        bits = []
        # The loaded project is always the primary reference.
        proj_table = [(self._display_name(sp), b)
                      for sp, b in ((s, bst(st))
                                    for s, st in self._roster.items())
                      if b > 0 and sp != self._species]
        proj = nearest_entries(total, proj_table, count=2)
        if proj:
            bits.append("Closest in this project: "
                        + ", ".join(f"{n} ({b})" for n, b in proj))

        # The vanilla Gen 3 line only appears when it ADDS something.
        kind, ref = vanilla_comparison(
            self._display_name(self._species), self._stats)
        if kind == "exact":
            bits.append(f"Stats match vanilla Gen 3 {ref.name} exactly "
                        f"(BST {ref.bst}).")
        elif kind == "modified":
            diff = total - ref.bst
            bits.append(f"Vanilla Gen 3 {ref.name} had BST {ref.bst}; "
                        f"this project's version is {total} ({diff:+d}).")
        else:
            van = nearest_entries(total, GEN3_REFERENCE, count=2)
            # Skip the vanilla line when it would just repeat the project
            # line (a mostly-vanilla roster shows the same names twice).
            proj_names = {n for n, _b in proj}
            van = [(n, b) for n, b in van if n not in proj_names]
            if van:
                bits.append("Similar Gen 3 totals (reference only): "
                            + ", ".join(f"{n} ({b})" for n, b in van))
        self._ref_label.setText("\n".join(bits))
        self._ref_label.setStyleSheet("color: #9e9e9e;")
