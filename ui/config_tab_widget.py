"""
ui/config_tab_widget.py
Game Configuration Editor

Edits the loaded project's new-game starting values, gameplay tweaks, and
build/debug settings:
  - src/new_game.c / src/player_pc.c / src/bike.c   (starting values + run-indoors)
  - config.mk             (VARIABLE := VALUE format)
  - include/config.h      (#define NAME VALUE / //#define NAME)

Sections are ordered most-relevant-first: Game Content, Gameplay Tweaks, then
the build/debug internals lower down.
"""
from __future__ import annotations

import os
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)


# ── stylesheet helpers (match project dark theme) ─────────────────────────────

_CARD_SS = """
QGroupBox {
    font-weight: bold;
    font-size: 10px;
    border: 1px solid #383838;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    background-color: #252525;
    color: #cccccc;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: #777777;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
"""

_FIELD_SS = """
QComboBox {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 7px;
    color: #e0e0e0;
    font-size: 12px;
}
QComboBox:focus { border: 1px solid #1976d2; }
QComboBox::drop-down { border: none; padding-right: 6px; }
QCheckBox { color: #cccccc; font-size: 12px; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #555555;
    border-radius: 3px;
    background-color: #1e1e1e;
}
QCheckBox::indicator:checked {
    background-color: #1976d2;
    border-color: #1976d2;
}
QSpinBox {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 3px 6px;
    color: #e0e0e0;
    font-size: 12px;
}
QSpinBox:focus { border: 1px solid #1976d2; }
"""

_NOTE_SS = "color: #888888; font-size: 10px; font-style: italic;"


# ── file / config helpers ─────────────────────────────────────────────────────

def _read_file(path: str) -> str:
    """Return file contents or empty string on error."""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()
    except OSError:
        return ""


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
        fh.write(content)


def _parse_mk_value(text: str, var: str) -> str:
    """Return current VALUE of  VAR := VALUE  in a Makefile snippet."""
    m = re.search(r"^\s*" + re.escape(var) + r"\s*:=\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _set_mk_value(text: str, var: str, value: str) -> str:
    """Replace VALUE in  VAR := OLD  → VAR := VALUE.  Adds line if missing."""
    pattern = r"(^\s*" + re.escape(var) + r"\s*:=\s*).+$"
    replacement = r"\g<1>" + value
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n == 0:
        new_text = text.rstrip("\n") + "\n" + var + " := " + value + "\n"
    return new_text


def _parse_define_value(text: str, name: str) -> str | None:
    """
    Return the value after #define NAME in config.h.
    Returns None if the define is commented out (//#define).
    """
    m = re.search(r"^#define\s+" + re.escape(name) + r"(?:\s+(\S+))?\s*$",
                  text, re.MULTILINE)
    if m:
        return m.group(1) or ""
    return None


def _is_define_active(text: str, name: str) -> bool:
    return _parse_define_value(text, name) is not None


def _set_define_active(text: str, name: str, active: bool) -> str:
    """Toggle the #define / //#define comment state."""
    active_pat = r"^(#define\s+" + re.escape(name) + r"(?:\s+\S+)?)\s*$"
    commented_pat = r"^(//\s*#define\s+" + re.escape(name) + r"(?:\s+\S+)?)\s*$"
    if active:
        new_text, n = re.subn(commented_pat, r"\1", text, flags=re.MULTILINE)
        if n == 0:
            new_text2, n2 = re.subn(active_pat, r"\1", text, flags=re.MULTILINE)
            if n2 == 0:
                new_text = text.rstrip("\n") + "\n#define " + name + "\n"
            else:
                new_text = new_text2
    else:
        new_text, n = re.subn(active_pat, r"//\1", text, flags=re.MULTILINE)
        if n == 0:
            new_text = text
    return new_text


def _set_define_value(text: str, name: str, value: str) -> str:
    """Replace the value in an active #define NAME OLD → #define NAME VALUE."""
    pattern = r"^(#define\s+" + re.escape(name) + r")\s+\S+"
    replacement = r"\1 " + value
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n == 0:
        new_text = text.rstrip("\n") + "\n#define " + name + " " + value + "\n"
    return new_text


# ── small reusable widgets ──────────────────────────────────────────────────

class _NoScrollComboBox(QComboBox):
    """Combo box that ignores the mouse wheel while closed.

    Project UX rule: scrolling the page must never silently change a dropdown
    value (the user scrolls via remote desktop two-finger gestures). The popup,
    once opened, scrolls normally because it's a separate widget.
    """

    def wheelEvent(self, e):  # noqa: N802
        e.ignore()


class _ItemRowDialog(QDialog):
    """Pick an item constant + quantity for a starting-items list."""

    def __init__(self, pairs, item="ITEM_NONE", qty=1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Starting Item")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self._pairs = [(str(c), str(d))
                       for c, d in (pairs or [("ITEM_NONE", "None")])]
        self._item = _NoScrollComboBox()
        self._item.setEditable(True)            # type-to-filter the long list
        self._item.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for const, display in self._pairs:
            self._item.addItem(display, const)  # show the name, store the const
        i = self._item.findData(item)
        if i >= 0:
            self._item.setCurrentIndex(i)
        form.addRow("Item:", self._item)
        self._qty = QSpinBox()
        self._qty.setRange(1, 999)
        self._qty.setValue(max(1, int(qty)))
        form.addRow("Quantity:", self._qty)
        lay.addLayout(form)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self):
        """Return (ITEM_const, qty). Resolves the selection back to a constant:
        the combo's stored data first, then a display-name or raw-constant match
        on the typed text."""
        const = self._item.currentData()
        if not const:
            txt = self._item.currentText().strip()
            low = txt.lower()
            for c, d in self._pairs:
                if d.lower() == low or c.lower() == low:
                    const = c
                    break
            if not const:
                const = txt          # last resort: a raw constant typed by hand
        return const, self._qty.value()


class _ItemSlotEditor(QWidget):
    """Compact add / edit / remove list of (ITEM_*, quantity) rows used for the
    starting PC and bag contents."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, int]] = []
        self._pairs: list[tuple[str, str]] = [("ITEM_NONE", "None")]
        self._display: dict[str, str] = {"ITEM_NONE": "None"}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:#1e1e1e;border:1px solid #3a3a3a;"
            "border-radius:4px;color:#dddddd;font-size:11px;}")
        self._list.setMaximumHeight(96)
        self._list.itemDoubleClicked.connect(lambda *_: self._edit())
        lay.addWidget(self._list, 1)
        col = QVBoxLayout()
        col.setSpacing(4)
        for label, slot in (("Add", self._add), ("Edit", self._edit),
                            ("Remove", self._remove)):
            b = QPushButton(label)
            b.setFixedWidth(72)
            b.clicked.connect(slot)
            col.addWidget(b)
        col.addStretch(1)
        lay.addLayout(col)

    def set_choices(self, choices):
        """Accept [(const, display)] pairs OR a bare [const] list (display ==
        const). Stores the picker pairs + a const→display map for the rows."""
        pairs = []
        for c in (choices or []):
            if isinstance(c, (tuple, list)) and len(c) >= 2:
                pairs.append((str(c[0]), str(c[1])))
            else:
                pairs.append((str(c), str(c)))
        self._pairs = pairs or [("ITEM_NONE", "None")]
        self._display = {c: d for c, d in self._pairs}
        self._refresh()

    def set_items(self, items):
        self._items = [(str(c), int(q)) for c, q in (items or [])]
        self._refresh()

    def get_items(self):
        return list(self._items)

    def _refresh(self):
        self._list.clear()
        if not self._items:
            it = QListWidgetItem("(empty)")
            it.setForeground(Qt.GlobalColor.gray)
            self._list.addItem(it)
            return
        for c, q in self._items:
            name = self._display.get(c, c)
            it = QListWidgetItem(f"{name}   ×{q}")
            it.setToolTip(c)            # underlying ITEM_* constant on hover
            self._list.addItem(it)

    def _add(self):
        dlg = _ItemRowDialog(self._pairs, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            c, q = dlg.values()
            if c and c != "ITEM_NONE":
                self._items.append((c, q))
                self._refresh()
                self.changed.emit()

    def _edit(self):
        row = self._list.currentRow()
        if not (0 <= row < len(self._items)):
            return
        c0, q0 = self._items[row]
        dlg = _ItemRowDialog(self._pairs, c0, q0, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            c, q = dlg.values()
            if c and c != "ITEM_NONE":
                self._items[row] = (c, q)
                self._refresh()
                self.changed.emit()

    def _remove(self):
        row = self._list.currentRow()
        if 0 <= row < len(self._items):
            del self._items[row]
            self._refresh()
            self.changed.emit()


# ── main widget ───────────────────────────────────────────────────────────────

class ConfigTabWidget(QWidget):
    """
    Embedded widget for the Config tab.
    Exposes a `modified` signal and load()/save() interface matching the
    pattern used by ItemsTabWidget / MovesTabWidget.
    """

    modified = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir: str = ""
        self._dirty: bool = False
        self._build_ui()

    # ── construction ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(14)

        def _desc(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #666666; font-size: 10px;")
            lbl.setWordWrap(True)
            return lbl

        def _summary(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #8f8f8f; font-size: 11px;")
            lbl.setWordWrap(True)
            return lbl

        # ════════ Game Content (new-game starting values) ════════
        gc_group = QGroupBox("Game Content  (new game starting values)")
        gc_group.setStyleSheet(_CARD_SS + _FIELD_SS)
        gc = QVBoxLayout(gc_group)
        gc.setContentsMargins(12, 16, 12, 12)
        gc.setSpacing(6)
        gc.addWidget(_summary(
            "What a brand-new save starts with — money, where the player wakes "
            "up, whether the National Dex is unlocked, and the items already in "
            "their bag and PC. Written straight into the new-game init code."))
        gc_form = QFormLayout()
        gc_form.setSpacing(4)

        self._money_spin = QSpinBox()
        self._money_spin.setRange(0, 999999)
        self._money_spin.setValue(3000)
        self._money_spin.setGroupSeparatorShown(True)
        gc_form.addRow("Starting Money:", self._money_spin)

        loc_row = QHBoxLayout()
        loc_row.setSpacing(6)
        self._loc_map = _NoScrollComboBox()
        self._loc_map.setMinimumWidth(220)
        self._loc_x = QSpinBox()
        self._loc_x.setRange(0, 255)
        self._loc_x.setPrefix("X ")
        self._loc_y = QSpinBox()
        self._loc_y.setRange(0, 255)
        self._loc_y.setPrefix("Y ")
        loc_row.addWidget(self._loc_map, 1)
        loc_row.addWidget(self._loc_x)
        loc_row.addWidget(self._loc_y)
        gc_form.addRow("Starting Location:", loc_row)
        gc.addLayout(gc_form)
        gc.addWidget(_desc(
            "The map a new game begins on, plus the X/Y tile within it. "
            "Pick the map from the list; set the tile with X and Y."))

        self._dex_cb = QCheckBox("National Dex unlocked from the start")
        gc.addWidget(self._dex_cb)

        gc.addWidget(_desc(
            "Starting PC items — what's waiting in the player's PC on a new game:"))
        self._pc_items = _ItemSlotEditor()
        gc.addWidget(self._pc_items)
        gc.addWidget(_desc(
            "Starting bag items — added to the bag on a new game "
            "(vanilla starts empty):"))
        self._bag_items = _ItemSlotEditor()
        gc.addWidget(self._bag_items)
        inner_layout.addWidget(gc_group)

        # ════════ Gameplay Tweaks (engine patches) ════════
        tw_group = QGroupBox("Gameplay Tweaks  (engine patches)")
        tw_group.setStyleSheet(_CARD_SS + _FIELD_SS)
        tw = QFormLayout(tw_group)
        tw.setContentsMargins(12, 16, 12, 12)
        tw.setSpacing(4)
        tw.addRow("", _summary(
            "Quality-of-life and balance changes applied by patching the engine. "
            "Each is idempotent — saving the same value twice changes nothing."))

        self._run_indoors_cb = QCheckBox("Allow running indoors")
        tw.addRow("Running:", self._run_indoors_cb)
        tw.addRow("", _desc(
            "Lets the dash work inside buildings (vanilla blocks running on "
            "indoor maps). Per-tile 'no running' spots like warps still apply."))

        self._text_speed = _NoScrollComboBox()
        for label, const in (("Slow", "OPTIONS_TEXT_SPEED_SLOW"),
                             ("Mid", "OPTIONS_TEXT_SPEED_MID"),
                             ("Fast", "OPTIONS_TEXT_SPEED_FAST")):
            self._text_speed.addItem(label, const)
        tw.addRow("Default Text Speed:", self._text_speed)
        tw.addRow("", _desc(
            "The text-speed a new save defaults to (players can still change it "
            "in Options)."))

        self._battle_style = _NoScrollComboBox()
        for label, const in (("Shift", "OPTIONS_BATTLE_STYLE_SHIFT"),
                             ("Set", "OPTIONS_BATTLE_STYLE_SET")):
            self._battle_style.addItem(label, const)
        tw.addRow("Default Battle Style:", self._battle_style)
        tw.addRow("", _desc(
            "SHIFT offers a free switch when the foe sends out a new Pokémon; "
            "SET does not. The new-game default."))

        self._prize_multiplier = QSpinBox()
        self._prize_multiplier.setRange(0, 65535)
        self._prize_multiplier.setValue(4)
        tw.addRow("Trainer Prize Multiplier:", self._prize_multiplier)
        tw.addRow("", _desc(
            "Constant in the trainer prize formula "
            "(prize = base × level × class × bonuses). Vanilla is 4; lower it "
            "for small-currency economies. Writes a config.h macro and patches "
            "the engine line once."))

        self._gender_dialogue_cb = QCheckBox("Enable")
        tw.addRow("Gender-Tinted Dialogue:", self._gender_dialogue_cb)
        tw.addRow("", _desc(
            "When on (vanilla), male NPC sprites speak in blue and female in "
            "red; neutral/object sprites stay dark gray. When off, all dialogue "
            "is dark gray. Explicit {COLOR} tokens in text always apply."))
        inner_layout.addWidget(tw_group)

        # ════════ Build & Compilation (config.mk) — moved lower ════════
        mk_group = QGroupBox("Build & Compilation  (config.mk)")
        mk_group.setStyleSheet(_CARD_SS + _FIELD_SS)
        mk_form = QFormLayout(mk_group)
        mk_form.setContentsMargins(12, 16, 12, 12)
        mk_form.setSpacing(4)
        mk_form.addRow("", _summary(
            "How the ROM is compiled. You rarely need to touch these — the "
            "defaults build a standard FireRed ROM."))

        self._game_version = _NoScrollComboBox()
        self._game_version.addItems(["FIRERED", "LEAFGREEN"])
        mk_form.addRow("Game Version:", self._game_version)
        mk_form.addRow("", _desc(
            "Build as FireRed or LeafGreen. Affects the ROM title, game code "
            "(BPRE / BPGE), and version-specific maps and events."))

        self._game_revision = _NoScrollComboBox()
        self._game_revision.addItems(["0", "1"])
        mk_form.addRow("Game Revision:", self._game_revision)
        mk_form.addRow("", _desc(
            "ROM revision: 0 = original launch, 1 = bug-fix re-release. Use 0 "
            "unless you specifically need the revision-1 binary."))

        self._game_language = _NoScrollComboBox()
        self._game_language.addItems(["ENGLISH"])
        mk_form.addRow("Game Language:", self._game_language)
        mk_form.addRow("", _desc(
            "Target language. Only ENGLISH is currently supported by pokefirered."))

        self._modern_cb = QCheckBox("Enable")
        mk_form.addRow("Modern Mode:", self._modern_cb)
        self._modern_note = QLabel(
            "Uses arm-none-eabi-gcc instead of agbcc. Enables modern C features, "
            "BUGFIX, and UBFIX automatically. ROM will NOT match the original binary.")
        self._modern_note.setStyleSheet("color: #bb8800; font-size: 10px;")
        self._modern_note.setWordWrap(True)
        self._modern_note.setVisible(False)
        mk_form.addRow("", self._modern_note)
        mk_form.addRow("", _desc(
            "Compiles with modern GCC instead of the original agbcc. Produces a "
            "larger but more feature-rich ROM."))

        self._compare_cb = QCheckBox("Enable")
        mk_form.addRow("Compare Build:", self._compare_cb)
        mk_form.addRow("", _desc(
            "Also builds an unmodified reference ROM and compares the two. Only "
            "useful for verifying your changes differ exactly as expected."))

        self._keep_temps_cb = QCheckBox("Enable")
        mk_form.addRow("Keep Temps:", self._keep_temps_cb)
        mk_form.addRow("", _desc(
            "Retains intermediate .i / .s files after compilation. Developer "
            "tool for debugging compiler output."))
        inner_layout.addWidget(mk_group)

        # ════════ Debug & Logging (include/config.h) — lowest ════════
        h_group = QGroupBox("Debug & Logging  (include/config.h)")
        h_group.setStyleSheet(_CARD_SS + _FIELD_SS)
        h_form = QFormLayout(h_group)
        h_form.setContentsMargins(12, 16, 12, 12)
        h_form.setSpacing(4)
        h_form.addRow("", _summary(
            "Developer logging options. Leave at the defaults unless you're "
            "debugging on an emulator."))

        self._ndebug_cb = QCheckBox("Enable  (release mode — disables all debug output)")
        h_form.addRow("NDEBUG:", self._ndebug_cb)
        h_form.addRow("", _desc(
            "When enabled, all debug logging is compiled out (no overhead). "
            "Enable for final release builds; disable when testing on an emulator."))

        self._log_handler = _NoScrollComboBox()
        self._log_handler.addItems([
            "LOG_HANDLER_AGB_PRINT",
            "LOG_HANDLER_NOCASH_PRINT",
            "LOG_HANDLER_MGBA_PRINT",
        ])
        h_form.addRow("Log Handler:", self._log_handler)
        h_form.addRow("", _desc(
            "AGB_PRINT: hardware cartridge printer (rare). NOCASH_PRINT: no$gba. "
            "MGBA_PRINT: mGBA console (recommended for most users)."))

        self._pretty_print = _NoScrollComboBox()
        self._pretty_print.addItems([
            "PRETTY_PRINT_OFF",
            "PRETTY_PRINT_MINI_PRINTF",
            "PRETTY_PRINT_LIBC",
        ])
        h_form.addRow("Pretty Print:", self._pretty_print)
        h_form.addRow("", _desc(
            "Formatting library for debug strings. OFF: raw (smallest). "
            "MINI_PRINTF: lightweight printf. LIBC: full newlib printf (largest)."))
        inner_layout.addWidget(h_group)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # ── Bottom button bar ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._save_btn = QPushButton("Save Config")
        self._save_btn.setMinimumWidth(120)
        self._save_btn.setEnabled(False)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        # ── Signal wiring ─────────────────────────────────────────────────────
        self._modern_cb.toggled.connect(self._on_modern_toggled)
        self._ndebug_cb.toggled.connect(self._on_ndebug_toggled)
        self._save_btn.clicked.connect(self.save)

        for combo in (
            self._game_version, self._game_revision, self._game_language,
            self._log_handler, self._pretty_print,
            self._loc_map, self._text_speed, self._battle_style,
        ):
            combo.currentIndexChanged.connect(self._mark_dirty)

        for cb in (self._modern_cb, self._compare_cb, self._keep_temps_cb,
                   self._ndebug_cb, self._dex_cb, self._run_indoors_cb,
                   self._gender_dialogue_cb):
            cb.toggled.connect(self._mark_dirty)

        for spin in (self._prize_multiplier, self._money_spin,
                     self._loc_x, self._loc_y):
            spin.valueChanged.connect(self._mark_dirty)

        self._pc_items.changed.connect(self._mark_dirty)
        self._bag_items.changed.connect(self._mark_dirty)

    # ── internal slots ────────────────────────────────────────────────────────

    def _on_modern_toggled(self, checked: bool) -> None:
        self._modern_note.setVisible(checked)

    def _on_ndebug_toggled(self, checked: bool) -> None:
        """Grey out log/print combos when NDEBUG is checked."""
        self._log_handler.setEnabled(not checked)
        self._pretty_print.setEnabled(not checked)

    def _mark_dirty(self, *_) -> None:
        if not self._dirty:
            self._dirty = True
            self._save_btn.setEnabled(True)
            self.modified.emit()

    @staticmethod
    def _set_combo_data(combo: QComboBox, data: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.setCurrentIndex(i)
                return

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, project_dir: str) -> None:
        """Populate all widgets from disk. Clears the dirty flag."""
        self._project_dir = project_dir
        self._dirty = False
        self._save_btn.setEnabled(False)

        mk_text = _read_file(os.path.join(project_dir, "config.mk"))
        h_text = _read_file(os.path.join(project_dir, "include", "config.h"))

        signal_widgets = (
            self._game_version, self._game_revision, self._game_language,
            self._log_handler, self._pretty_print,
            self._modern_cb, self._compare_cb, self._keep_temps_cb, self._ndebug_cb,
            self._prize_multiplier, self._gender_dialogue_cb,
            self._money_spin, self._loc_map, self._loc_x, self._loc_y,
            self._dex_cb, self._run_indoors_cb, self._text_speed, self._battle_style,
        )
        for widget in signal_widgets:
            widget.blockSignals(True)

        try:
            def _set_combo(combo: QComboBox, value: str) -> None:
                idx = combo.findText(value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

            # config.mk
            _set_combo(self._game_version,  _parse_mk_value(mk_text, "GAME_VERSION"))
            _set_combo(self._game_revision, _parse_mk_value(mk_text, "GAME_REVISION"))
            _set_combo(self._game_language, _parse_mk_value(mk_text, "GAME_LANGUAGE"))
            self._modern_cb.setChecked(_parse_mk_value(mk_text, "MODERN") == "1")
            self._compare_cb.setChecked(_parse_mk_value(mk_text, "COMPARE") == "1")
            self._keep_temps_cb.setChecked(_parse_mk_value(mk_text, "KEEP_TEMPS") == "1")

            # include/config.h
            ndebug_on = _is_define_active(h_text, "NDEBUG")
            self._ndebug_cb.setChecked(ndebug_on)
            self._log_handler.setEnabled(not ndebug_on)
            self._pretty_print.setEnabled(not ndebug_on)
            log_val = _parse_define_value(h_text, "LOG_HANDLER")
            if log_val is not None:
                _set_combo(self._log_handler, log_val)
            pp_val = _parse_define_value(h_text, "PRETTY_PRINT_HANDLER")
            if pp_val is not None:
                _set_combo(self._pretty_print, pp_val)

            # Battle economy
            try:
                from core.battle_economy_patch import read_prize_multiplier
                self._prize_multiplier.setValue(read_prize_multiplier(project_dir))
            except Exception:
                self._prize_multiplier.setValue(4)

            # Gender-tinted dialogue
            try:
                from core.text_coloring_patch import read_gender_dialogue_enabled
                self._gender_dialogue_cb.setChecked(
                    read_gender_dialogue_enabled(project_dir))
            except Exception:
                self._gender_dialogue_cb.setChecked(True)

            # New-game content + run-indoors
            try:
                import core.new_game_config as ngc
                maps = ngc.parse_map_constants(project_dir)
                items = ngc.parse_item_choices(project_dir)   # [(const, name)]
                vals = ngc.read_all(project_dir)
            except Exception:
                maps, items, vals = [], [("ITEM_NONE", "None")], {}

            self._money_spin.setValue(int(vals.get("money", 3000)))

            self._loc_map.clear()
            if maps:
                self._loc_map.addItems(maps)
            mp, mx, my = vals.get(
                "location", ("MAP_PALLET_TOWN_PLAYERS_HOUSE_2F", 6, 6))
            idx = self._loc_map.findText(mp)
            if idx < 0 and mp:
                self._loc_map.insertItem(0, mp)
                idx = 0
            if idx >= 0:
                self._loc_map.setCurrentIndex(idx)
            self._loc_x.setValue(int(mx))
            self._loc_y.setValue(int(my))

            self._dex_cb.setChecked(bool(vals.get("national_dex", True)))
            self._run_indoors_cb.setChecked(bool(vals.get("run_indoors", False)))
            self._set_combo_data(
                self._text_speed, vals.get("text_speed", "OPTIONS_TEXT_SPEED_MID"))
            self._set_combo_data(
                self._battle_style, vals.get("battle_style", "OPTIONS_BATTLE_STYLE_SHIFT"))
            self._pc_items.set_choices(items)
            self._pc_items.set_items(vals.get("pc_items", []))
            self._bag_items.set_choices(items)
            self._bag_items.set_items(vals.get("bag_items", []))

        finally:
            for widget in signal_widgets:
                widget.blockSignals(False)

        self._modern_note.setVisible(self._modern_cb.isChecked())

    def has_changes(self) -> bool:
        return self._dirty

    def save(self) -> None:
        """Write all config back to disk."""
        if not self._project_dir:
            return

        # ── config.mk ────────────────────────────────────────────────────────
        mk_path = os.path.join(self._project_dir, "config.mk")
        mk_text = _read_file(mk_path)
        if not mk_text and not os.path.isfile(mk_path):
            QMessageBox.warning(
                self, "Config",
                f"config.mk not found at:\n{mk_path}\n\nLoad a project first.")
            return
        mk_text = _set_mk_value(mk_text, "GAME_VERSION",  self._game_version.currentText())
        mk_text = _set_mk_value(mk_text, "GAME_REVISION", self._game_revision.currentText())
        mk_text = _set_mk_value(mk_text, "GAME_LANGUAGE", self._game_language.currentText())
        mk_text = _set_mk_value(mk_text, "MODERN",   "1" if self._modern_cb.isChecked() else "0")
        mk_text = _set_mk_value(mk_text, "COMPARE",  "1" if self._compare_cb.isChecked() else "0")
        mk_text = _set_mk_value(mk_text, "KEEP_TEMPS", "1" if self._keep_temps_cb.isChecked() else "0")
        _write_file(mk_path, mk_text)

        # ── include/config.h ─────────────────────────────────────────────────
        h_path = os.path.join(self._project_dir, "include", "config.h")
        h_text = _read_file(h_path)
        if not h_text and not os.path.isfile(h_path):
            QMessageBox.warning(self, "Config",
                                f"include/config.h not found at:\n{h_path}")
            return
        h_text = _set_define_active(h_text, "NDEBUG", self._ndebug_cb.isChecked())
        h_text = _set_define_value(h_text, "LOG_HANDLER", self._log_handler.currentText())
        h_text = _set_define_value(h_text, "PRETTY_PRINT_HANDLER", self._pretty_print.currentText())
        _write_file(h_path, h_text)

        # ── Battle economy macro + engine patch ──────────────────────────────
        try:
            from core.battle_economy_patch import write_prize_multiplier
            ok, msg = write_prize_multiplier(
                self._project_dir, self._prize_multiplier.value())
            if not ok:
                QMessageBox.warning(self, "Battle Economy Patch", msg)
        except Exception as exc:
            QMessageBox.warning(self, "Battle Economy Patch",
                                f"Failed to apply prize-multiplier patch:\n{exc}")

        # ── Gender-tinted dialogue patch ──────────────────────────────────────
        try:
            from core.text_coloring_patch import write_gender_dialogue_enabled
            ok, msg = write_gender_dialogue_enabled(
                self._project_dir, self._gender_dialogue_cb.isChecked())
            if not ok:
                QMessageBox.warning(self, "Text & Dialogue Patch", msg)
        except Exception as exc:
            QMessageBox.warning(self, "Text & Dialogue Patch",
                                f"Failed to apply NPC dialogue color patch:\n{exc}")

        # ── New-game starting values + run-indoors ────────────────────────────
        try:
            import core.new_game_config as ngc
            ngc.set_starting_money(self._project_dir, self._money_spin.value())
            map_const = self._loc_map.currentText().strip()
            if map_const:
                ngc.set_start_location(
                    self._project_dir, map_const,
                    self._loc_x.value(), self._loc_y.value())
            ngc.set_national_dex(self._project_dir, self._dex_cb.isChecked())
            ngc.set_run_indoors(self._project_dir, self._run_indoors_cb.isChecked())
            ts = self._text_speed.currentData()
            if ts:
                ngc.set_default_text_speed(self._project_dir, ts)
            bs = self._battle_style.currentData()
            if bs:
                ngc.set_default_battle_style(self._project_dir, bs)
            ngc.set_pc_items(self._project_dir, self._pc_items.get_items())
            ngc.set_bag_items(self._project_dir, self._bag_items.get_items())
        except Exception as exc:
            QMessageBox.warning(self, "New Game Config",
                                f"Failed to write starting values:\n{exc}")

        self._dirty = False
        self._save_btn.setEnabled(False)
