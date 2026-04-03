"""
credits_editor.py — Credits Editor for PorySuite-Z

Parses the credits text entries from src/strings.c and the credits script
from src/credits.c, presents them in an editable list, and writes changes
back to both files.

Each visible credits entry is a pair: a role/title line and a names line.
The script sequence controls the order, timing, and map backgrounds.
"""

import os
import re

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QColor, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QPlainTextEdit,
    QListWidget, QListWidgetItem, QGroupBox, QFrame,
    QSpinBox, QComboBox, QSplitter, QMessageBox, QScrollArea,
    QSizePolicy, QAbstractItemView,
)

from dex_description_edit import DexDescriptionEdit

# Credits window is 30 tiles wide (240 px, full GBA screen width).
# Variable-width font means ~30 characters per line in practice.
# Each credits screen shows 6 lines at a time.
_CREDITS_MAX_CHARS_PER_LINE = 30
_CREDITS_MAX_LINES = 6


# ── Parsing helpers ──────────────────────────────────────────────────────────

# Matches: ALIGNED(4) const u8 gCreditsString_SomeName[] = _("...");
_RE_CREDITS_STRING = re.compile(
    r'ALIGNED\(4\)\s+const\s+u8\s+(gCreditsString_\w+)\[\]\s*=\s*_\("(.*)"\)\s*;'
)

# Matches entries in sCreditsTexts[]: { gCreditsString_X, gCreditsString_Y, TRUE/FALSE }
_RE_CREDITS_TEXT_ENTRY = re.compile(
    r'\{\s*(gCreditsString_\w+|gString_Dummy)\s*,'
    r'\s*(gCreditsString_\w+|gString_Dummy)\s*,'
    r'\s*(TRUE|FALSE)\s*\}'
)

# Matches script commands like CREDITS_PRINT(NAME, 300)
_RE_SCRIPT_PRINT = re.compile(r'CREDITS_PRINT\(\s*(\w+)\s*,\s*(\d+)\s*\)')
_RE_SCRIPT_MAPNEXT = re.compile(r'CREDITS_MAPNEXT\(\s*(\w+)\s*,\s*(\d+)\s*\)')
_RE_SCRIPT_MAP = re.compile(r'CREDITS_MAP\(\s*(\w+)\s*,\s*(\d+)\s*\)')
_RE_SCRIPT_MON = re.compile(r'CREDITS_MON\(\s*(\w+)\s*\)')
_RE_SCRIPT_THEEND = re.compile(r'CREDITS_THEENDGFX\(\s*(\w+)\s*,\s*(\d+)\s*\)')
_RE_SCRIPT_WAIT = re.compile(r'CREDITS_WAITBUTTON\(\s*(\d+)\s*\)')


def _unescape_credits(raw: str) -> str:
    """Convert C escape sequences to readable text."""
    return raw.replace("\\n", "\n")


def _escape_credits(text: str) -> str:
    """Convert readable text back to C escape sequences.

    The GBA charmap has no double-quote or single-quote characters,
    so any quotes (straight or smart/curly) are replaced with
    parentheses to avoid build errors.
    """
    # Smart/curly quotes → parentheses (GBA font has no quote glyphs)
    text = text.replace("\u201c", "(").replace("\u201d", ")")  # " "
    text = text.replace("\u2018", "(").replace("\u2019", ")")  # ' '
    # Straight double quotes → parentheses (alternating open/close)
    while '"' in text:
        text = text.replace('"', "(", 1)
        text = text.replace('"', ")", 1)
    text = text.replace("\n", "\\n")
    return text


class CreditsEntry:
    """One credits text pair: title (role) + names."""
    def __init__(self, title_symbol: str, names_symbol: str,
                 title_text: str, names_text: str, unused: bool = False):
        self.title_symbol = title_symbol
        self.names_symbol = names_symbol
        self.title_text = title_text      # Human-readable (newlines as \n chars)
        self.names_text = names_text
        self.unused = unused


class ScriptCommand:
    """One command in the credits script sequence."""
    def __init__(self, cmd_type: str, param: str = "", duration: int = 0,
                 raw_line: str = ""):
        self.cmd_type = cmd_type   # "PRINT", "MAPNEXT", "MAP", "MON", "THEENDGFX", "WAITBUTTON"
        self.param = param         # The macro parameter (e.g. "DIRECTOR", "ROUTE23")
        self.duration = duration
        self.raw_line = raw_line


# ── Main Widget ──────────────────────────────────────────────────────────────

class CreditsEditorWidget(QWidget):
    """Credits editor — edit the game's ending credits text and sequence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_dir = ""
        self._strings: dict[str, str] = {}      # symbol -> raw C string content
        self._entries: list[CreditsEntry] = []   # parsed text pairs
        self._script: list[ScriptCommand] = []   # parsed script sequence
        self._dirty = False
        self._build_ui()

    # ═════════════════════════════════════════════════════════════════════════
    # UI Construction
    # ═════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Header ───────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Credits Editor")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self._status_label)

        self._save_btn = QPushButton("Save Credits")
        self._save_btn.setToolTip("Write changes back to strings.c and credits.c")
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setEnabled(False)
        header.addWidget(self._save_btn)

        root.addLayout(header)

        # ── Splitter: list on left, editor on right ──────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: credits entry list ─────────────────────────────────────────
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(4)

        list_label = QLabel("Credits Entries")
        list_label.setStyleSheet("font-weight: bold;")
        left_lay.addWidget(list_label)

        self._entry_list = QListWidget()
        self._entry_list.setAlternatingRowColors(True)
        self._entry_list.setFont(QFont("Source Code Pro", 10))
        self._entry_list.currentRowChanged.connect(self._on_entry_selected)
        self._entry_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._entry_list.model().rowsMoved.connect(self._on_rows_moved)
        left_lay.addWidget(self._entry_list, 1)

        # ── List buttons ─────────────────────────────────────────────────────
        list_btns = QHBoxLayout()
        self._add_btn = QPushButton("+ Add Entry")
        self._add_btn.clicked.connect(self._on_add_entry)
        self._remove_btn = QPushButton("- Remove Entry")
        self._remove_btn.clicked.connect(self._on_remove_entry)
        self._remove_btn.setEnabled(False)
        list_btns.addWidget(self._add_btn)
        list_btns.addWidget(self._remove_btn)
        list_btns.addStretch()
        left_lay.addLayout(list_btns)

        splitter.addWidget(left)

        # ── Middle: entry editor ─────────────────────────────────────────────
        mid = QWidget()
        mid_lay = QVBoxLayout(mid)
        mid_lay.setContentsMargins(8, 0, 0, 0)
        mid_lay.setSpacing(8)

        # ── Title (Role) ─────────────────────────────────────────────────────
        title_box = QGroupBox("Role / Title")
        title_box_lay = QVBoxLayout(title_box)
        title_box_lay.addWidget(QLabel(
            "The category or job title shown in the credits.\n"
            "Each line break (Enter) becomes a new line on screen.\n"
            "The game shows 6 lines per screen — pad with blank lines if needed."
        ))
        self._title_edit = DexDescriptionEdit(
            max_chars_per_line=_CREDITS_MAX_CHARS_PER_LINE,
            max_lines=_CREDITS_MAX_LINES,
        )
        self._title_edit.setFont(QFont("Source Code Pro", 11))
        self._title_edit.setMaximumHeight(140)
        self._title_edit.setPlaceholderText("e.g.\n\nDirector\n\n\n")
        self._title_counter = QLabel("")
        self._title_counter.setTextFormat(Qt.TextFormat.RichText)
        self._title_edit.set_counter_label(self._title_counter)
        self._title_edit.textChanged.connect(self._on_text_changed)
        title_box_lay.addWidget(self._title_edit)
        title_box_lay.addWidget(self._title_counter)
        mid_lay.addWidget(title_box)

        # ── Names ────────────────────────────────────────────────────────────
        names_box = QGroupBox("Names")
        names_box_lay = QVBoxLayout(names_box)
        names_box_lay.addWidget(QLabel(
            "The people listed under this role.\n"
            "One name per line. The game shows 6 lines per screen."
        ))
        self._names_edit = DexDescriptionEdit(
            max_chars_per_line=_CREDITS_MAX_CHARS_PER_LINE,
            max_lines=_CREDITS_MAX_LINES,
        )
        self._names_edit.setFont(QFont("Source Code Pro", 11))
        self._names_edit.setMaximumHeight(140)
        self._names_edit.setPlaceholderText("e.g.\n\n\nJohn Smith\n\n")
        self._names_counter = QLabel("")
        self._names_counter.setTextFormat(Qt.TextFormat.RichText)
        self._names_edit.set_counter_label(self._names_counter)
        self._names_edit.textChanged.connect(self._on_text_changed)
        names_box_lay.addWidget(self._names_edit)
        names_box_lay.addWidget(self._names_counter)
        mid_lay.addWidget(names_box)

        # ── Script info ──────────────────────────────────────────────────────
        script_box = QGroupBox("Timing")
        script_lay = QFormLayout(script_box)
        script_lay.addRow(QLabel(
            "How long this entry stays on screen (in frames, 60 = 1 second)."
        ))
        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 9999)
        self._duration_spin.setValue(210)
        self._duration_spin.setSuffix(" frames")
        self._duration_spin.valueChanged.connect(self._on_duration_changed)
        script_lay.addRow("Display duration:", self._duration_spin)
        mid_lay.addWidget(script_box)

        mid_lay.addStretch()
        splitter.addWidget(mid)

        # ── Right: preview column ────────────────────────────────────────────
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(8, 0, 0, 0)
        right_lay.setSpacing(8)

        preview_box = QGroupBox("Preview (how it looks in-game)")
        preview_box_lay = QVBoxLayout(preview_box)
        preview_box_lay.addWidget(QLabel(
            "Title (blue) and names (white) are drawn on the same screen.\n"
            "Use blank lines to position them so they don't overlap. 6 lines total."
        ))
        self._preview = QLabel("")
        self._preview.setFont(QFont("Source Code Pro", 11))
        self._preview.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._preview.setStyleSheet(
            "background: #1a1a2e; color: #e0e0e0; padding: 16px; "
            "border-radius: 4px;"
        )
        self._preview.setWordWrap(True)
        self._preview.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        preview_box_lay.addWidget(self._preview, 1)
        preview_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        right_lay.addWidget(preview_box, 1)

        splitter.addWidget(right)

        splitter.setSizes([250, 400, 300])

        # Disable editor until data is loaded
        self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled: bool):
        self._title_edit.setEnabled(enabled)
        self._names_edit.setEnabled(enabled)
        self._title_counter.setVisible(enabled)
        self._names_counter.setVisible(enabled)
        self._duration_spin.setEnabled(enabled)
        self._add_btn.setEnabled(enabled)

    # ═════════════════════════════════════════════════════════════════════════
    # Data Loading
    # ═════════════════════════════════════════════════════════════════════════

    def load_project(self, project_dir: str):
        """Parse credits data from the project's source files."""
        self._project_dir = project_dir
        self._strings.clear()
        self._entries.clear()
        self._script.clear()

        strings_path = os.path.join(project_dir, "src", "strings.c")
        credits_path = os.path.join(project_dir, "src", "credits.c")

        if not os.path.isfile(strings_path):
            self._status_label.setText("strings.c not found")
            return
        if not os.path.isfile(credits_path):
            self._status_label.setText("credits.c not found")
            return

        # ── Parse strings.c for gCreditsString_* definitions ─────────────────
        with open(strings_path, "r", encoding="utf-8") as f:
            for line in f:
                m = _RE_CREDITS_STRING.match(line.strip())
                if m:
                    self._strings[m.group(1)] = m.group(2)

        # ── Parse credits.c for sCreditsTexts[] ─────────────────────────────
        with open(credits_path, "r", encoding="utf-8") as f:
            credits_src = f.read()

        # Extract the sCreditsTexts array
        texts_match = re.search(
            r'sCreditsTexts\[\]\s*=\s*\{(.*?)\};',
            credits_src, re.DOTALL
        )
        if texts_match:
            for m in _RE_CREDITS_TEXT_ENTRY.finditer(texts_match.group(1)):
                title_sym = m.group(1)
                names_sym = m.group(2)
                unused = m.group(3) == "TRUE"
                title_text = _unescape_credits(self._strings.get(title_sym, ""))
                names_text = _unescape_credits(self._strings.get(names_sym, ""))
                # Skip the trailing dummy entry
                if title_sym == "gString_Dummy" and names_sym == "gString_Dummy":
                    continue
                self._entries.append(CreditsEntry(
                    title_sym, names_sym, title_text, names_text, unused
                ))

        # ── Parse the script sequence ────────────────────────────────────────
        script_match = re.search(
            r'sCreditsScript\[\]\s*=\s*\{(.*?)\};',
            credits_src, re.DOTALL
        )
        if script_match:
            for line in script_match.group(1).splitlines():
                line = line.strip().rstrip(",")
                if not line or line.startswith("//"):
                    continue
                m = _RE_SCRIPT_PRINT.search(line)
                if m:
                    self._script.append(ScriptCommand("PRINT", m.group(1), int(m.group(2)), line))
                    continue
                m = _RE_SCRIPT_MAPNEXT.search(line)
                if m:
                    self._script.append(ScriptCommand("MAPNEXT", m.group(1), int(m.group(2)), line))
                    continue
                m = _RE_SCRIPT_MAP.search(line)
                if m:
                    self._script.append(ScriptCommand("MAP", m.group(1), int(m.group(2)), line))
                    continue
                m = _RE_SCRIPT_MON.search(line)
                if m:
                    self._script.append(ScriptCommand("MON", m.group(1), 0, line))
                    continue
                m = _RE_SCRIPT_THEEND.search(line)
                if m:
                    self._script.append(ScriptCommand("THEENDGFX", m.group(1), int(m.group(2)), line))
                    continue
                m = _RE_SCRIPT_WAIT.search(line)
                if m:
                    self._script.append(ScriptCommand("WAITBUTTON", "", int(m.group(1)), line))
                    continue

        self._populate_list()
        self._set_editor_enabled(True)
        self._status_label.setText(f"{len(self._entries)} entries loaded")
        self._dirty = False
        self._save_btn.setEnabled(False)

    def _populate_list(self):
        self._entry_list.clear()
        for i, entry in enumerate(self._entries):
            # Show a clean display: "Role: Names" on one line
            title_clean = entry.title_text.strip().replace("\n", " ").strip()
            names_clean = entry.names_text.strip().replace("\n", ", ").strip()
            if not title_clean:
                title_clean = "(blank)"
            display = f"{title_clean}"
            if names_clean:
                display += f"  —  {names_clean}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._entry_list.addItem(item)

        if self._entries:
            self._entry_list.setCurrentRow(0)

    # ═════════════════════════════════════════════════════════════════════════
    # Entry Selection & Editing
    # ═════════════════════════════════════════════════════════════════════════

    def _on_entry_selected(self, row: int):
        self._remove_btn.setEnabled(row >= 0)
        if row < 0 or row >= len(self._entries):
            self._title_edit.blockSignals(True)
            self._names_edit.blockSignals(True)
            self._title_edit.clear()
            self._names_edit.clear()
            self._preview.setText("")
            self._title_edit.blockSignals(False)
            self._names_edit.blockSignals(False)
            return

        entry = self._entries[row]
        self._title_edit.blockSignals(True)
        self._names_edit.blockSignals(True)
        self._title_edit.setPlainText(entry.title_text)
        self._names_edit.setPlainText(entry.names_text)
        self._title_edit.blockSignals(False)
        self._names_edit.blockSignals(False)

        # Find duration from script
        self._duration_spin.blockSignals(True)
        duration = self._find_duration_for_entry(row)
        self._duration_spin.setValue(duration if duration > 0 else 210)
        self._duration_spin.blockSignals(False)

        self._update_preview()

    def _find_duration_for_entry(self, entry_index: int) -> int:
        """Find the script PRINT command duration for the given entry index."""
        # The sCreditsTexts array is indexed by CREDITS_STRING_* enum values.
        # PRINT commands reference enum names which map 1:1 to sCreditsTexts indices.
        # We match by counting PRINT commands in the script.
        print_idx = 0
        for cmd in self._script:
            if cmd.cmd_type == "PRINT":
                if cmd.param == "DUMMY":
                    print_idx += 1  # DUMMY entries don't map to sCreditsTexts
                    continue
                if print_idx == entry_index:
                    return cmd.duration
                print_idx += 1
        return 210  # default

    def _on_text_changed(self):
        row = self._entry_list.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]
        entry.title_text = self._title_edit.toPlainText()
        entry.names_text = self._names_edit.toPlainText()
        self._update_preview()
        self._mark_dirty()

        # Update list display
        title_clean = entry.title_text.strip().replace("\n", " ").strip()
        names_clean = entry.names_text.strip().replace("\n", ", ").strip()
        if not title_clean:
            title_clean = "(blank)"
        display = f"{title_clean}"
        if names_clean:
            display += f"  —  {names_clean}"
        item = self._entry_list.item(row)
        if item:
            item.setText(display)

    def _on_duration_changed(self, value: int):
        row = self._entry_list.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        # Update the script command's duration
        print_idx = 0
        for cmd in self._script:
            if cmd.cmd_type == "PRINT":
                if cmd.param == "DUMMY":
                    print_idx += 1
                    continue
                if print_idx == row:
                    cmd.duration = value
                    self._mark_dirty()
                    return
                print_idx += 1

    def _update_preview(self):
        title = self._title_edit.toPlainText()
        names = self._names_edit.toPlainText()

        # The GBA draws both title (blue) and names (white) on the SAME
        # screen simultaneously.  The \n padding in each string positions
        # them vertically so they don't overlap.  We merge both into one
        # 6-line grid, colouring each line by which string provided it.
        title_lines = (title.split("\n") if title else [])[:_CREDITS_MAX_LINES]
        names_lines = (names.split("\n") if names else [])[:_CREDITS_MAX_LINES]

        # Pad both to exactly 6 lines
        while len(title_lines) < _CREDITS_MAX_LINES:
            title_lines.append("")
        while len(names_lines) < _CREDITS_MAX_LINES:
            names_lines.append("")

        html_parts = []
        html_parts.append(
            "<div style='margin-bottom:4px; color:#667; font-size:9px; "
            "text-align:center;'>— In-Game Preview —</div>"
        )

        for i in range(_CREDITS_MAX_LINES):
            t = title_lines[i].strip()
            n = names_lines[i].strip()
            if t and n:
                # Both have text on this line — show title in blue, names in white
                html_parts.append(
                    f"<div style='text-align:center; line-height:1.6;'>"
                    f"<span style='color:#aaccff;'>{t}</span>"
                    f" / "
                    f"<span style='color:#ffffff;'>{n}</span></div>"
                )
            elif t:
                html_parts.append(
                    f"<div style='text-align:center; color:#aaccff; "
                    f"line-height:1.6;'>{t}</div>"
                )
            elif n:
                html_parts.append(
                    f"<div style='text-align:center; color:#ffffff; "
                    f"line-height:1.6;'>{n}</div>"
                )
            else:
                html_parts.append(
                    "<div style='line-height:1.6;'>&nbsp;</div>"
                )

        self._preview.setText("".join(html_parts))

    def _mark_dirty(self):
        self._dirty = True
        self._save_btn.setEnabled(True)
        self._status_label.setText("Unsaved changes")
        self._status_label.setStyleSheet("color: #e8a44a; font-size: 11px;")

    def _on_rows_moved(self):
        """Rebuild entries list to match new visual order after drag-drop."""
        new_entries = []
        for i in range(self._entry_list.count()):
            item = self._entry_list.item(i)
            old_idx = item.data(Qt.ItemDataRole.UserRole)
            new_entries.append(self._entries[old_idx])
            item.setData(Qt.ItemDataRole.UserRole, i)
        self._entries = new_entries
        self._mark_dirty()

    # ═════════════════════════════════════════════════════════════════════════
    # Add / Remove Entries
    # ═════════════════════════════════════════════════════════════════════════

    def _on_add_entry(self):
        """Add a new blank credits entry."""
        # Generate a unique symbol name
        idx = len(self._entries)
        base = f"gCreditsString_Custom_{idx}"
        while base in self._strings:
            idx += 1
            base = f"gCreditsString_Custom_{idx}"
        title_sym = f"{base}_Title"
        names_sym = f"{base}_Names"

        entry = CreditsEntry(
            title_sym, names_sym,
            "\n\nNew Role\n\n\n",
            "\n\n\nYour Name Here\n\n",
            False
        )
        self._entries.append(entry)
        self._strings[title_sym] = _escape_credits(entry.title_text)
        self._strings[names_sym] = _escape_credits(entry.names_text)

        # Add a PRINT command to the script (before THEENDGFX)
        insert_pos = len(self._script)
        for i, cmd in enumerate(self._script):
            if cmd.cmd_type in ("THEENDGFX", "WAITBUTTON"):
                insert_pos = i
                break
        enum_name = title_sym.replace("gCreditsString_", "").upper()
        self._script.insert(insert_pos, ScriptCommand("PRINT", enum_name, 210))

        self._populate_list()
        self._entry_list.setCurrentRow(len(self._entries) - 1)
        self._mark_dirty()

    def _on_remove_entry(self):
        row = self._entry_list.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]
        ret = QMessageBox.question(
            self, "Remove Entry",
            f"Remove the credits entry for:\n{entry.title_text.strip()[:60]}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self._entries.pop(row)

        # Remove corresponding PRINT from script
        print_idx = 0
        for i, cmd in enumerate(self._script):
            if cmd.cmd_type == "PRINT":
                if cmd.param == "DUMMY":
                    print_idx += 1
                    continue
                if print_idx == row:
                    self._script.pop(i)
                    break
                print_idx += 1

        self._populate_list()
        if self._entries:
            self._entry_list.setCurrentRow(min(row, len(self._entries) - 1))
        self._mark_dirty()

    # ═════════════════════════════════════════════════════════════════════════
    # Saving
    # ═════════════════════════════════════════════════════════════════════════

    def _on_save(self):
        if not self._project_dir:
            return
        try:
            self._write_strings()
            self._write_credits_c()
            self._dirty = False
            self._save_btn.setEnabled(False)
            self._status_label.setText("Saved successfully")
            self._status_label.setStyleSheet("color: #7cbb5e; font-size: 11px;")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save credits:\n{e}")

    def _write_strings(self):
        """Update gCreditsString_* definitions in strings.c."""
        strings_path = os.path.join(self._project_dir, "src", "strings.c")
        with open(strings_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Build a map of updated string values
        updated = {}
        for entry in self._entries:
            updated[entry.title_symbol] = _escape_credits(entry.title_text)
            updated[entry.names_symbol] = _escape_credits(entry.names_text)

        # Update existing lines in-place
        existing_symbols = set()
        for i, line in enumerate(lines):
            m = _RE_CREDITS_STRING.match(line.strip())
            if m:
                sym = m.group(1)
                existing_symbols.add(sym)
                if sym in updated:
                    lines[i] = f'ALIGNED(4) const u8 {sym}[] = _("{updated[sym]}");\n'

        # Append new strings that don't exist yet
        # Find the last gCreditsString line to insert after
        last_credits_line = -1
        for i, line in enumerate(lines):
            if "gCreditsString_" in line and "ALIGNED(4)" in line:
                last_credits_line = i

        new_lines = []
        for sym, val in updated.items():
            if sym not in existing_symbols:
                new_lines.append(f'ALIGNED(4) const u8 {sym}[] = _("{val}");\n')

        if new_lines and last_credits_line >= 0:
            for j, nl in enumerate(new_lines):
                lines.insert(last_credits_line + 1 + j, nl)

        with open(strings_path, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines)

    def _write_credits_c(self):
        """Update sCreditsTexts[] and sCreditsScript[] in credits.c."""
        credits_path = os.path.join(self._project_dir, "src", "credits.c")
        with open(credits_path, "r", encoding="utf-8") as f:
            content = f.read()

        # ── Rebuild sCreditsTexts[] ──────────────────────────────────────────
        texts_lines = []
        for entry in self._entries:
            unused_str = "TRUE " if entry.unused else "FALSE"
            texts_lines.append(
                f"    {{ {entry.title_symbol}, {entry.names_symbol}, {unused_str} }}"
            )
        texts_lines.append("    { gString_Dummy, gString_Dummy, FALSE }")
        new_texts = ",\n".join(texts_lines)

        content = re.sub(
            r'(sCreditsTexts\[\]\s*=\s*\{)\s*(.*?)\s*(\};)',
            rf'\1\n{new_texts}\n\3',
            content, flags=re.DOTALL
        )

        # ── Rebuild sCreditsScript[] ─────────────────────────────────────────
        script_lines = []
        for cmd in self._script:
            if cmd.cmd_type == "PRINT":
                script_lines.append(f"    CREDITS_PRINT({cmd.param}, {cmd.duration})")
            elif cmd.cmd_type == "MAPNEXT":
                script_lines.append(f"    CREDITS_MAPNEXT({cmd.param}, {cmd.duration})")
            elif cmd.cmd_type == "MAP":
                script_lines.append(f"    CREDITS_MAP({cmd.param}, {cmd.duration})")
            elif cmd.cmd_type == "MON":
                script_lines.append(f"    CREDITS_MON({cmd.param})")
            elif cmd.cmd_type == "THEENDGFX":
                script_lines.append(f"    CREDITS_THEENDGFX({cmd.param}, {cmd.duration})")
            elif cmd.cmd_type == "WAITBUTTON":
                script_lines.append(f"    CREDITS_WAITBUTTON({cmd.duration})")
        new_script = ",\n".join(script_lines)

        content = re.sub(
            r'(sCreditsScript\[\]\s*=\s*\{)\s*(.*?)\s*(\};)',
            rf'\1\n{new_script}\n\3',
            content, flags=re.DOTALL
        )

        # ── Update enum CreditsString to match entries ───────────────────────
        enum_entries = []
        for i, entry in enumerate(self._entries):
            name = entry.title_symbol.replace("gCreditsString_", "").upper()
            if i == 0:
                enum_entries.append(f"    CREDITS_STRING_{name} = 0")
            else:
                enum_entries.append(f"    CREDITS_STRING_{name}")
        enum_entries.append("    CREDITS_STRING_DUMMY")
        new_enum = ",\n".join(enum_entries)

        content = re.sub(
            r'(enum\s+CreditsString\s*\{)\s*(.*?)\s*(\};)',
            rf'\1\n{new_enum}\n\3',
            content, flags=re.DOTALL
        )

        with open(credits_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    # ═════════════════════════════════════════════════════════════════════════
    # Public API
    # ═════════════════════════════════════════════════════════════════════════

    def has_unsaved_changes(self) -> bool:
        return self._dirty
