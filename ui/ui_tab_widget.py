"""
ui/ui_tab_widget.py
UI Content Editor — Name Pools · Location Names · Key Strings

Three sub-tabs for editing player-visible text content in a pokefirered project:
  1. Name Pools      — player/rival starter names (data/text/new_game_intro.inc)
  2. Location Names  — region map section names   (region_map_sections.json or .h)
  3. Key Strings     — misc game strings           (src/strings.c)
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QVBoxLayout, QWidget,
)


# ── stylesheet helpers ─────────────────────────────────────────────────────────

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
QLineEdit, QPlainTextEdit {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 7px;
    color: #e0e0e0;
    font-size: 12px;
    selection-background-color: #1565c0;
}
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #1976d2; }
"""

_TABLE_SS = """
QTableWidget {
    background-color: #1a1a1a;
    gridline-color: #2c2c2c;
    color: #d0d0d0;
    font-size: 12px;
    border: none;
}
QTableWidget::item:selected {
    background-color: #1565c0;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #2a2a2a;
    color: #888888;
    border: none;
    border-bottom: 1px solid #3a3a3a;
    padding: 4px 8px;
    font-size: 10px;
    text-transform: uppercase;
}
"""

_NOTE_SS = "color: #888888; font-size: 10px; font-style: italic;"


# ── file helpers ───────────────────────────────────────────────────────────────

def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()
    except OSError:
        return ""


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
        fh.write(content)


# ── Name Pools parsing ─────────────────────────────────────────────────────────

def _parse_name_labels_from_inc(inc_text: str) -> dict[str, str]:
    """
    Return {label: name_string} from an .inc file like:
        gNameChoice_Gary::
            .string "GARY$"
    Labels use GBA exported-symbol notation (::).
    The trailing $ GBA string-terminator is stripped from the returned value.
    """
    result: dict[str, str] = {}
    for m in re.finditer(
        r"^(\w+)::\s*\n\s*\.string\s+\"([^\"]*)\"\s*$",
        inc_text, re.MULTILINE
    ):
        name = m.group(2).rstrip("$")   # strip GBA null-terminator
        result[m.group(1)] = name
    return result


def _parse_name_choices_from_c(c_text: str, array_name: str) -> list[str]:
    """
    Return the list of label names from a C array like:
        static const u8 *const sMaleNameChoices[] = { gNameChoice_Red, ... };
    """
    m = re.search(
        r"\b" + re.escape(array_name) + r"\s*\[\s*\]\s*=\s*\{([^}]*)\}",
        c_text, re.DOTALL
    )
    if not m:
        return []
    body = m.group(1)
    labels = re.findall(r"\b(g\w+)\b", body)
    return labels


def _rebuild_inc_entry(label: str, name: str) -> str:
    return f"{label}::\n\t.string \"{name}$\"\n"


def _update_inc_names(inc_text: str, updates: dict[str, str]) -> str:
    """Replace .string values for matching labels in inc_text.
    The user-supplied value has no $ — we add it back on write.
    """
    def replacer(m: re.Match) -> str:
        label = m.group(1)
        if label in updates:
            val = updates[label].rstrip("$") + "$"   # ensure exactly one $
            return f"{label}::\n\t.string \"{val}\""
        return m.group(0)

    return re.sub(
        r"(\w+)::\s*\n\s*\.string\s+\"[^\"]*\"",
        replacer, inc_text
    )


# ── Location Names parsing ─────────────────────────────────────────────────────

def _parse_region_map_json(json_text: str) -> list[tuple[str, str]]:
    """
    Parse region_map_sections.json and return [(constant, display_name), ...].

    pokefirered's file has the shape:
        { "map_sections": [ {"id": "MAPSEC_PALLET_TOWN", "name": "PALLET TOWN", ...}, ... ] }

    Entries that have no "name" key are unnamed sections (interior areas, etc.)
    and are skipped — they have no in-game location banner to edit.
    """
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return []

    # Unwrap common wrapper keys: { "map_sections": [...] }
    if isinstance(data, dict):
        for wrapper_key in ("map_sections", "sections", "entries"):
            if wrapper_key in data and isinstance(data[wrapper_key], list):
                data = data[wrapper_key]
                break

    results: list[tuple[str, str]] = []

    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            constant = (
                entry.get("id") or entry.get("constant") or
                entry.get("mapsec") or ""
            )
            display = (
                entry.get("name") or entry.get("display_name") or
                entry.get("label") or ""
            )
            # Only include entries that have an actual display name
            if constant and display:
                results.append((str(constant), str(display)))
    elif isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, dict):
                display = (
                    val.get("name") or val.get("display_name") or
                    val.get("label") or ""
                )
                if display:
                    results.append((str(key), str(display)))
            elif isinstance(val, str):
                results.append((str(key), val))

    return results


def _update_region_map_json(json_text: str, updates: dict[str, str]) -> str:
    """
    Apply display-name updates back to the JSON.
    updates = {constant: new_display_name}
    Handles the pokefirered wrapper: { "map_sections": [...] }
    """
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return json_text

    # Find the list to modify, preserving the wrapper dict if present
    wrapper_key: str | None = None
    entries = data
    if isinstance(data, dict):
        for k in ("map_sections", "sections", "entries"):
            if k in data and isinstance(data[k], list):
                wrapper_key = k
                entries = data[k]
                break

    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = (
                entry.get("id") or entry.get("constant") or
                entry.get("mapsec") or ""
            )
            if key and key in updates:
                for field in ("name", "display_name", "label"):
                    if field in entry:
                        entry[field] = updates[key]
                        break
                else:
                    entry["name"] = updates[key]
    elif isinstance(entries, dict):
        for key, val in entries.items():
            if key not in updates:
                continue
            if isinstance(val, dict):
                for field in ("name", "display_name", "label"):
                    if field in val:
                        val[field] = updates[key]
                        break
                else:
                    val["name"] = updates[key]
            elif isinstance(val, str):
                entries[key] = updates[key]

    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _parse_mapsec_h(h_text: str) -> list[tuple[str, str]]:
    """
    Fallback: parse region_map_entry_strings.h or similar.
    Handles both ASM and C forms:
      sMapsecName_XXX: .string "VALUE"
      static const u8 sMapsecName_XXX[] = _("VALUE");
    Returns [(constant_suffix, display_name), ...].
    """
    results: list[tuple[str, str]] = []
    # ASM form
    for m in re.finditer(
        r"(sMapsecName_\w+):\s*\n\s*\.string\s+\"([^\"]*)\"\s*$",
        h_text, re.MULTILINE
    ):
        results.append((m.group(1), m.group(2)))
    if results:
        return results
    # C form
    for m in re.finditer(
        r"(sMapsecName_\w+)\s*\[\s*\]\s*=\s*_\(\s*\"([^\"]*)\"\s*\)\s*;",
        h_text
    ):
        results.append((m.group(1), m.group(2)))
    return results


def _update_mapsec_h(h_text: str, updates: dict[str, str]) -> str:
    """Apply name updates back to the .h fallback."""
    def asm_replacer(m: re.Match) -> str:
        label = m.group(1)
        if label in updates:
            return f"{label}:\n\t.string \"{updates[label]}\""
        return m.group(0)

    new_text = re.sub(
        r"(sMapsecName_\w+):\s*\n\s*\.string\s+\"[^\"]*\"",
        asm_replacer, h_text
    )
    if new_text != h_text:
        return new_text

    def c_replacer(m: re.Match) -> str:
        label = m.group(1)
        if label in updates:
            return f"{label}[] = _(\"{updates[label]}\");"
        return m.group(0)

    new_text = re.sub(
        r"(sMapsecName_\w+)\s*\[\s*\]\s*=\s*_\(\s*\"[^\"]*\"\s*\)\s*;",
        c_replacer, h_text
    )
    return new_text


# ── Key Strings parsing ────────────────────────────────────────────────────────

# Each entry: (variable_name, label, max_chars, multiline, source_file_relative)
# source_file_relative is relative to the project root.
_INC = "data/text/new_game_intro.inc"
_KEY_STRINGS: list[tuple[str, str, int, bool, str]] = [
    # ── Main menu strings (src/strings.c) ────────────────────────────────────
    ("gText_EggNickname",  "Egg Nickname",             10, False, "src/strings.c"),
    ("gText_NewGame",      "New Game (menu)",           12, False, "src/strings.c"),
    ("gText_Continue",     "Continue (menu)",           12, False, "src/strings.c"),
    ("gText_Boy",          "Boy (gender label)",         7, False, "src/strings.c"),
    ("gText_Girl",         "Girl (gender label)",        7, False, "src/strings.c"),
    ("gText_Kanto",        "Kanto (region name)",       10, False, "src/strings.c"),
    ("gText_National",     "National (dex label)",      10, False, "src/strings.c"),
    # ── Pikachu/title-screen intro narration (data/text/new_game_intro.inc) ──
    ("gPikachuIntro_Text_Page1", "Title Intro — Page 1", 512, True, _INC),
    ("gPikachuIntro_Text_Page2", "Title Intro — Page 2", 512, True, _INC),
    ("gPikachuIntro_Text_Page3", "Title Intro — Page 3", 512, True, _INC),
    # ── Oak new-game intro dialogue (data/text/new_game_intro.inc) ───────────
    # Listed in the order they appear in the dialogue sequence.
    ("gOakSpeech_Text_WelcomeToTheWorld",
     "Oak (1) — Welcome",                512, True, _INC),
    ("gOakSpeech_Text_ThisWorld",
     "Oak (2) — This world\u2026",       128, True, _INC),
    ("gOakSpeech_Text_IsInhabitedFarAndWide",
     "Oak (3) — \u2026is inhabited",     256, True, _INC),
    ("gOakSpeech_Text_IStudyPokemon",
     "Oak (4) — I study Pok\u00e9mon",   512, True, _INC),
    ("gOakSpeech_Text_TellMeALittleAboutYourself",
     "Oak (5) — Tell me about yourself", 256, True, _INC),
    ("gOakSpeech_Text_AskPlayerGender",
     "Oak (6) — Boy or girl?",           128, True, _INC),
    ("gOakSpeech_Text_YourNameWhatIsIt",
     "Oak (7) — Your name?",             128, True, _INC),
    ("gOakSpeech_Text_SoYourNameIsPlayer",
     "Oak (8) — So your name is\u2026",  128, True, _INC),
    ("gOakSpeech_Text_WhatWasHisName",
     "Oak (9) — My grandson\u2026",      256, True, _INC),
    ("gOakSpeech_Text_YourRivalsNameWhatWasIt",
     "Oak (10) — Rival's name?",         128, True, _INC),
    ("gOakSpeech_Text_ConfirmRivalName",
     "Oak (11) — Was it {RIVAL}?",        64, True, _INC),
    ("gOakSpeech_Text_RememberRivalsName",
     "Oak (12) — I remember now!",       128, True, _INC),
    ("gOakSpeech_Text_LetsGo",
     "Oak (13) — Let's go!",             256, True, _INC),
]


def _parse_string_var(c_text: str, var_name: str) -> str | None:
    """
    Extract content between _(" and ") for:
        const u8 gText_X[] = _("VALUE");
    Also handles multiline strings that end with \\n"); or \\p");
    """
    # Match from _(" to the closing ");  — non-greedy, may span lines
    m = re.search(
        r"\b" + re.escape(var_name) + r'\s*\[\s*\]\s*=\s*_\(\s*"(.*?)"\s*\)\s*;',
        c_text, re.DOTALL
    )
    if m:
        return m.group(1)
    return None


def _update_string_var(c_text: str, var_name: str, new_value: str) -> str:
    """
    Replace the content between _(" and ") for var_name.
    new_value may contain literal \\n and \\p escape sequences.
    """
    # Escape backslashes in the replacement so re.sub doesn't interpret them
    safe_replacement = new_value.replace("\\", "\\\\")

    def replacer(m: re.Match) -> str:
        return m.group(0).replace(m.group(1), new_value, 1)

    new_text, n = re.subn(
        r"(\b" + re.escape(var_name) + r'\s*\[\s*\]\s*=\s*_\(\s*")(.*?)("\s*\)\s*;)',
        lambda m: m.group(1) + new_value + m.group(3),
        c_text, count=1, flags=re.DOTALL
    )
    return new_text if n > 0 else c_text


# ── GBA .inc assembly string parsing ──────────────────────────────────────────

def _parse_asm_string_label(inc_text: str, label_name: str) -> str:
    """
    Parse a multi-line GBA assembly string block:

        gOakSpeech_Text_WelcomeToTheWorld::
            .string "Hello, there!\\n"
            .string "Glad to meet you!\\p"
            .string "...last line.\\p$"

    Returns the concatenated string content with the trailing $ stripped.
    Returns "" if the label is not found.
    """
    pattern = (
        r"^" + re.escape(label_name) + r"::\s*\n"   # exported label line
        r"((?:[ \t]+\.string\s+\"[^\"]*\"[ \t]*\n?)+)"  # one or more .string lines
        # NOTE: trailing whitespace uses [ \t]* (not \s*) to avoid eating
        # the leading indent of the following .string line.
    )
    m = re.search(pattern, inc_text, re.MULTILINE)
    if not m:
        return ""
    block = m.group(1)
    parts = re.findall(r'\.string\s+"([^"]*)"', block)
    result = "".join(parts)
    return result.rstrip("$")


def _update_asm_string_label(inc_text: str, label_name: str, new_value: str) -> str:
    """
    Replace the .string block for label_name in an .inc file.

    The new_value has no trailing $ — we add it.  Long strings are split at
    GBA newline escape sequences (\\n, \\p, \\l) for readability, one
    .string line per segment.
    """
    # Split on GBA line-break boundaries, keeping the delimiter attached
    segments = re.split(r"(?<=\\[npl])", new_value)
    segments = [s for s in segments if s]
    if not segments:
        segments = [new_value or ""]

    # Add the GBA null-terminator to the last segment
    segments[-1] = segments[-1].rstrip("$") + "$"

    lines = "".join(f"\t.string \"{seg}\"\n" for seg in segments)
    new_block = f"{label_name}::\n{lines}"

    pattern = (
        r"^" + re.escape(label_name) + r"::\s*\n"
        r"(?:[ \t]+\.string\s+\"[^\"]*\"[ \t]*\n?)+"
    )
    new_text, n = re.subn(pattern, new_block, inc_text, count=1, flags=re.MULTILINE)
    return new_text if n > 0 else inc_text


# ── Name Pools sub-tab ────────────────────────────────────────────────────────

class _NamePoolSubTab(QWidget):
    """
    Displays three groups of editable name lists:
    Male Player Names / Female Player Names / Rival Names.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir: str = ""
        # label_name → current string value (mutable)
        self._label_values: dict[str, str] = {}
        # pool_key → list of labels
        self._pools: dict[str, list[str]] = {
            "male":   [],
            "female": [],
            "rival":  [],
        }
        self._pool_arrays = {
            "male":   "sMaleNameChoices",
            "female": "sFemaleNameChoices",
            "rival":  "sRivalNameChoices",
        }
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(14)

        self._groups: dict[str, QGroupBox] = {}
        self._field_maps: dict[str, dict[str, QLineEdit]] = {}

        titles = {
            "male":   "Male Player Names  (sMaleNameChoices)",
            "female": "Female Player Names  (sFemaleNameChoices)",
            "rival":  "Rival Names  (sRivalNameChoices)",
        }

        for pool_key, title in titles.items():
            grp = QGroupBox(title)
            grp.setStyleSheet(_CARD_SS + _FIELD_SS)
            grp_layout = QFormLayout(grp)
            grp_layout.setContentsMargins(12, 16, 12, 12)
            grp_layout.setSpacing(6)
            self._groups[pool_key] = grp
            self._field_maps[pool_key] = {}
            inner_layout.addWidget(grp)

        note = QLabel(
            "Max 7 characters per name (PLAYER_NAME_LENGTH).  "
            "Source: data/text/new_game_intro.inc + src/oak_speech.c.\n"
            "Some names appear in multiple pools (e.g. Green/Gary in both Male and Rival). "
            "Editing a shared name here updates it in every pool that references it."
        )
        note.setStyleSheet(_NOTE_SS)
        note.setWordWrap(True)
        inner_layout.addWidget(note)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    def load(self, project_dir: str) -> None:
        self._project_dir = project_dir

        inc_path = os.path.join(project_dir, "data", "text", "new_game_intro.inc")
        c_path = os.path.join(project_dir, "src", "oak_speech.c")
        inc_text = _read_file(inc_path)
        c_text = _read_file(c_path)

        self._label_values = _parse_name_labels_from_inc(inc_text)

        for pool_key, array_name in self._pool_arrays.items():
            self._pools[pool_key] = _parse_name_choices_from_c(c_text, array_name)

        self._rebuild_forms()

    def _rebuild_forms(self) -> None:
        for pool_key, grp in self._groups.items():
            layout = grp.layout()
            # Clear existing rows
            while layout.rowCount() > 0:
                layout.removeRow(0)
            self._field_maps[pool_key] = {}

            labels = self._pools[pool_key]
            if not labels:
                layout.addRow(QLabel("(no entries found — check src/oak_speech.c)"))
                continue

            # Build a reverse map: label → which other pools also reference it
            all_pools = self._pools
            shared: dict[str, list[str]] = {}
            pool_display_names = {
                "male": "Male", "female": "Female", "rival": "Rival"
            }
            for lbl in labels:
                others = [
                    pool_display_names[pk]
                    for pk, pl_list in all_pools.items()
                    if pk != pool_key and lbl in pl_list
                ]
                shared[lbl] = others

            for i, label in enumerate(labels, start=1):
                current_val = self._label_values.get(label, "")
                field = QLineEdit(current_val)
                field.setMaxLength(7)
                # Tooltip: internal symbol + shared-pool info
                shared_note = ""
                if shared[label]:
                    shared_note = (
                        f"\nAlso in: {', '.join(shared[label])} pool"
                        f"{'s' if len(shared[label]) > 1 else ''} — "
                        "editing this name updates all pools that share it."
                    )
                field.setToolTip(
                    f"Internal label: {label}\n"
                    f"Position {i} in this pool  ·  Max 7 characters"
                    f"{shared_note}"
                )
                field.textChanged.connect(self.changed)
                self._field_maps[pool_key][label] = field
                # Row label: "Name N" — clean and position-based.
                # If this label is shared with another pool, note it in grey.
                if shared[label]:
                    row_lbl = QLabel(
                        f"Name {i} "
                        f"<span style='color:#666; font-size:9px;'>"
                        f"(shared with {'/'.join(shared[label])})</span>"
                    )
                    row_lbl.setTextFormat(Qt.TextFormat.RichText)
                else:
                    row_lbl = QLabel(f"Name {i}")
                layout.addRow(row_lbl, field)

    def collect(self) -> dict[str, str]:
        """Return {label: new_value} for all edited fields."""
        result: dict[str, str] = {}
        for pool_key, fields in self._field_maps.items():
            for label, field in fields.items():
                result[label] = field.text()
        return result

    def save(self) -> None:
        if not self._project_dir:
            return
        updates = self.collect()
        if not updates:
            return

        inc_path = os.path.join(self._project_dir, "data", "text", "new_game_intro.inc")
        inc_text = _read_file(inc_path)
        if not inc_text:
            QMessageBox.warning(
                self, "Name Pools",
                f"Could not read:\n{inc_path}"
            )
            return
        new_text = _update_inc_names(inc_text, updates)
        _write_file(inc_path, new_text)


# ── Location Names sub-tab ────────────────────────────────────────────────────

class _LocationNamesSubTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir: str = ""
        self._use_json: bool = True   # False → use .h fallback
        self._json_path: str = ""
        self._h_path: str = ""
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self._source_label = QLabel()
        self._source_label.setStyleSheet(_NOTE_SS)
        root.addWidget(self._source_label)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Constant", "Display Name"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(_TABLE_SS)
        self._table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self._table, 1)

        note = QLabel("Max 16 characters per location name.")
        note.setStyleSheet(_NOTE_SS)
        root.addWidget(note)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 1:
            # Enforce 16-char limit
            if len(item.text()) > 16:
                item.setText(item.text()[:16])
            self.changed.emit()

    def load(self, project_dir: str) -> None:
        self._project_dir = project_dir

        json_path = os.path.join(
            project_dir, "src", "data", "region_map", "region_map_sections.json"
        )
        h_path = os.path.join(
            project_dir, "src", "data", "region_map", "region_map_entry_strings.h"
        )
        self._json_path = json_path
        self._h_path = h_path

        if os.path.isfile(json_path):
            self._use_json = True
            json_text = _read_file(json_path)
            entries = _parse_region_map_json(json_text)
            self._source_label.setText(f"Source: {json_path}")
        elif os.path.isfile(h_path):
            self._use_json = False
            h_text = _read_file(h_path)
            entries = _parse_mapsec_h(h_text)
            self._source_label.setText(f"Source (fallback): {h_path}")
        else:
            entries = []
            self._source_label.setText("No region map source file found.")

        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for constant, display in entries:
            row = self._table.rowCount()
            self._table.insertRow(row)
            const_item = QTableWidgetItem(constant)
            const_item.setFlags(const_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            const_item.setForeground(self._table.palette().color(self._table.foregroundRole()))
            self._table.setItem(row, 0, const_item)
            self._table.setItem(row, 1, QTableWidgetItem(display))
        self._table.blockSignals(False)

    def _collect_updates(self) -> dict[str, str]:
        updates: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            c_item = self._table.item(row, 0)
            d_item = self._table.item(row, 1)
            if c_item and d_item:
                updates[c_item.text()] = d_item.text()
        return updates

    def save(self) -> None:
        if not self._project_dir:
            return
        updates = self._collect_updates()
        if not updates:
            return

        if self._use_json:
            json_text = _read_file(self._json_path)
            new_text = _update_region_map_json(json_text, updates)
            _write_file(self._json_path, new_text)
        else:
            h_text = _read_file(self._h_path)
            new_text = _update_mapsec_h(h_text, updates)
            _write_file(self._h_path, new_text)


# ── Key Strings sub-tab ───────────────────────────────────────────────────────

class _KeyStringsSubTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir: str = ""
        self._fields: dict[str, QLineEdit | QPlainTextEdit] = {}
        self._counters: dict[str, tuple[QLabel, int]] = {}   # var_name → (label, max)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(14)

        # Group by source file so the user knows where changes go
        groups: dict[str, tuple[QGroupBox, QFormLayout]] = {}

        _source_titles = {
            "src/strings.c":                 "Game Strings  (src/strings.c)",
            "data/text/new_game_intro.inc":  "New Game Intro Dialogue  (data/text/new_game_intro.inc)",
        }

        for var_name, label, max_chars, multiline, src_rel in _KEY_STRINGS:
            if src_rel not in groups:
                title = _source_titles.get(src_rel, src_rel)
                grp = QGroupBox(title)
                grp.setStyleSheet(_CARD_SS + _FIELD_SS)
                form = QFormLayout(grp)
                form.setContentsMargins(12, 16, 12, 12)
                form.setSpacing(8)
                groups[src_rel] = (grp, form)
                inner_layout.addWidget(grp)

            _, form = groups[src_rel]

            if multiline:
                widget: QPlainTextEdit | QLineEdit = QPlainTextEdit()
                widget.setPlaceholderText(
                    "GBA escape codes: \\n = line break · \\p = page break · "
                    "{PLAYER} / {RIVAL} = name placeholders. "
                    "Enter escape codes literally as backslash-letter."
                )
                widget.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
                )
                widget.setMinimumHeight(100)

                # Character counter label (mirrors _make_lineedit_counter pattern)
                counter_lbl = QLabel(f"0/{max_chars}")
                counter_lbl.setAlignment(
                    counter_lbl.alignment() | Qt.AlignmentFlag.AlignRight
                )
                counter_lbl.setStyleSheet("color: #888; font-size: 11px;")
                self._counters[var_name] = (counter_lbl, max_chars)

                _vn = var_name  # capture for lambda closure
                def _on_text_changed(vn: str = _vn, lim: int = max_chars) -> None:
                    w = self._fields[vn]
                    assert isinstance(w, QPlainTextEdit)
                    txt = w.toPlainText()
                    length = len(txt)
                    lbl, _ = self._counters[vn]
                    lbl.setText(f"{length}/{lim}")
                    if length >= lim:
                        lbl.setStyleSheet("color: #e53935; font-size: 11px; font-weight: bold;")
                        # Hard-truncate to limit
                        if length > lim:
                            cursor = w.textCursor()
                            w.blockSignals(True)
                            w.setPlainText(txt[:lim])
                            w.blockSignals(False)
                            # Restore cursor to end of allowed text
                            cursor.setPosition(lim)
                            w.setTextCursor(cursor)
                    else:
                        lbl.setStyleSheet("color: #888; font-size: 11px;")
                    self.changed.emit()

                widget.textChanged.connect(_on_text_changed)

                # Wrap widget + counter in a small vertical container
                container = QWidget()
                container.setStyleSheet("background: transparent;")
                cv = QVBoxLayout(container)
                cv.setContentsMargins(0, 0, 0, 0)
                cv.setSpacing(2)
                cv.addWidget(widget)
                cv.addWidget(counter_lbl)
                form_widget: QWidget = container
            else:
                widget = QLineEdit()
                widget.setMaxLength(max_chars)
                widget.textChanged.connect(self.changed)
                form_widget = widget

            widget.setToolTip(
                f"Variable: {var_name}\nSource: {src_rel}\nMax {max_chars} characters"
            )
            self._fields[var_name] = widget
            form.addRow(label + ":", form_widget)

        note = QLabel(
            "GBA escape codes: \\n = line break  ·  \\p = page break (clears screen)  ·  "
            "\\l = left-align  ·  \\c = colour marker.  "
            "{PLAYER} and {RIVAL} expand to the chosen names in-game."
        )
        note.setStyleSheet(_NOTE_SS)
        note.setWordWrap(True)
        inner_layout.addWidget(note)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    def load(self, project_dir: str) -> None:
        self._project_dir = project_dir

        # Read each unique source file once
        file_cache: dict[str, str] = {}
        for _, _lbl, _max, _ml, src_rel in _KEY_STRINGS:
            if src_rel not in file_cache:
                file_cache[src_rel] = _read_file(
                    os.path.join(project_dir, src_rel)
                )

        for var_name, _label, _max, multiline, src_rel in _KEY_STRINGS:
            file_text = file_cache.get(src_rel, "")

            # Choose parser based on source file type
            if src_rel.endswith(".inc"):
                value = _parse_asm_string_label(file_text, var_name)
            else:
                value = _parse_string_var(file_text, var_name) or ""

            widget = self._fields[var_name]
            widget.blockSignals(True)
            if multiline:
                assert isinstance(widget, QPlainTextEdit)
                widget.setPlainText(value)
                # Update counter label manually (signals are blocked)
                if var_name in self._counters:
                    lbl, lim = self._counters[var_name]
                    length = len(value)
                    lbl.setText(f"{length}/{lim}")
                    if length >= lim:
                        lbl.setStyleSheet("color: #e53935; font-size: 11px; font-weight: bold;")
                    else:
                        lbl.setStyleSheet("color: #888; font-size: 11px;")
            else:
                assert isinstance(widget, QLineEdit)
                widget.setText(value)
            widget.blockSignals(False)

    def save(self) -> None:
        if not self._project_dir:
            return

        # Collect new values grouped by source file
        updates_by_file: dict[str, dict[str, tuple[str, bool]]] = {}
        for var_name, _label, _max, multiline, src_rel in _KEY_STRINGS:
            widget = self._fields[var_name]
            if multiline:
                assert isinstance(widget, QPlainTextEdit)
                new_val = widget.toPlainText()
            else:
                assert isinstance(widget, QLineEdit)
                new_val = widget.text()
            updates_by_file.setdefault(src_rel, {})[var_name] = (new_val, multiline)

        for src_rel, var_updates in updates_by_file.items():
            file_path = os.path.join(self._project_dir, src_rel)
            file_text = _read_file(file_path)
            if not file_text:
                QMessageBox.warning(
                    self, "Key Strings",
                    f"Could not read:\n{file_path}"
                )
                continue

            for var_name, (new_val, _ml) in var_updates.items():
                if src_rel.endswith(".inc"):
                    file_text = _update_asm_string_label(file_text, var_name, new_val)
                else:
                    file_text = _update_string_var(file_text, var_name, new_val)

            _write_file(file_path, file_text)


# ── Top-level UITabWidget ─────────────────────────────────────────────────────

class UITabWidget(QWidget):
    """
    Embedded widget for the UI tab.
    Contains three sub-tabs: Name Pools, Location Names, Key Strings.
    Exposes `modified` signal and load()/save() interface.
    """

    modified = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir: str = ""
        self._dirty: bool = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs, 1)

        self._name_pools = _NamePoolSubTab()
        self._location_names = _LocationNamesSubTab()
        self._key_strings = _KeyStringsSubTab()

        self._tabs.addTab(self._name_pools,    "Name Pools")
        self._tabs.addTab(self._location_names, "Location Names")
        self._tabs.addTab(self._key_strings,   "Key Strings")

        # Save button row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 6, 8, 8)
        btn_row.addStretch(1)
        self._save_btn = QPushButton("Save UI Content")
        self._save_btn.setMinimumWidth(140)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.save)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        # Forward changed signals
        for sub in (self._name_pools, self._location_names, self._key_strings):
            sub.changed.connect(self._mark_dirty)

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self._save_btn.setEnabled(True)
            self.modified.emit()

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, project_dir: str) -> None:
        """Load all sub-tabs from project_dir. Clears the dirty flag."""
        self._project_dir = project_dir
        self._dirty = False
        self._save_btn.setEnabled(False)

        self._name_pools.load(project_dir)
        self._location_names.load(project_dir)
        self._key_strings.load(project_dir)

    def has_changes(self) -> bool:
        return self._dirty

    def save(self) -> None:
        """Write all pending changes to disk."""
        if not self._project_dir:
            return
        self._name_pools.save()
        self._location_names.save()
        self._key_strings.save()
        self._dirty = False
        self._save_btn.setEnabled(False)
