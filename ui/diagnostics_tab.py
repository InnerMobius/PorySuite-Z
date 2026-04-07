"""ROM Diagnostics Tab for PorySuite-Z.

Shows ROM size, data usage, EWRAM consumption, and other build info.
All data is read fresh from the project's build outputs each time
the tab is opened or the Refresh button is clicked.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QProgressBar, QFrame, QTextEdit,
    QSizePolicy,
)


# GBA ROM size tiers
_ROM_16MB = 16 * 1024 * 1024   # 16,777,216 bytes
_ROM_32MB = 32 * 1024 * 1024   # 33,554,432 bytes
_GBA_EWRAM = 256 * 1024        # 256 KB

# pokefirered map file patterns
_SYM_EWRAM_START = '__ewram_start'
_SYM_EWRAM_END   = '__ewram_end'
_SYM_IWRAM_START = '__iwram_start'
_SYM_IWRAM_END   = '__iwram_end'
_SYM_ROM_START   = '__rom_start'  # typically 0x08000000


def _sizeof_fmt(num: int) -> str:
    """Human-readable file size."""
    if num < 1024:
        return f"{num} B"
    elif num < 1024 * 1024:
        return f"{num / 1024:.1f} KB"
    else:
        return f"{num / (1024 * 1024):.2f} MB"


def _pct(used: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{used / total * 100:.1f}%"


def _make_bar(used: int, total: int, warn_pct: float = 85.0) -> QProgressBar:
    """Create a progress bar showing usage."""
    bar = QProgressBar()
    bar.setRange(0, total)
    bar.setValue(min(used, total))
    bar.setTextVisible(True)
    bar.setFormat(f"{_sizeof_fmt(used)} / {_sizeof_fmt(total)}  ({_pct(used, total)})")
    bar.setFixedHeight(22)

    pct = (used / total * 100) if total > 0 else 0
    if pct >= 100:
        bar.setStyleSheet("QProgressBar::chunk { background: #c44; }")
    elif pct >= warn_pct:
        bar.setStyleSheet("QProgressBar::chunk { background: #ca6; }")
    else:
        bar.setStyleSheet("QProgressBar::chunk { background: #4a8; }")
    return bar


class DiagnosticsTab(QWidget):
    """ROM diagnostics and project info panel."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root = ""
        self._build_ui()

    def set_project(self, project_root: str):
        self._project_root = project_root
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header_row = QHBoxLayout()
        title = QLabel("ROM Diagnostics")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        header_row.addWidget(title)
        header_row.addStretch()

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setToolTip("Re-read build outputs and recalculate all values.")
        self._btn_refresh.clicked.connect(self._refresh)
        header_row.addWidget(self._btn_refresh)
        layout.addLayout(header_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._status_label)

        # ── ROM Size ────────────────────────────────────────────────────
        rom_box = QGroupBox("ROM Size")
        rom_lay = QVBoxLayout(rom_box)

        self._rom_file_label = QLabel("—")
        rom_lay.addWidget(self._rom_file_label)

        self._rom_bar_16 = _make_bar(0, _ROM_16MB)
        rom_lay.addWidget(QLabel("16 MB ROM limit:"))
        rom_lay.addWidget(self._rom_bar_16)

        self._rom_bar_32 = _make_bar(0, _ROM_32MB)
        rom_lay.addWidget(QLabel("32 MB ROM limit:"))
        rom_lay.addWidget(self._rom_bar_32)

        layout.addWidget(rom_box)

        # ── Memory ──────────────────────────────────────────────────────
        mem_box = QGroupBox("GBA Memory")
        mem_lay = QVBoxLayout(mem_box)

        mem_lay.addWidget(QLabel("EWRAM (256 KB — main working RAM):"))
        self._ewram_bar = _make_bar(0, _GBA_EWRAM)
        mem_lay.addWidget(self._ewram_bar)

        self._ewram_detail = QLabel("—")
        self._ewram_detail.setStyleSheet("font-size: 10px; color: #aaa;")
        mem_lay.addWidget(self._ewram_detail)

        mem_lay.addWidget(QLabel("IWRAM (32 KB — fast internal RAM):"))
        self._iwram_bar = _make_bar(0, 32 * 1024)
        mem_lay.addWidget(self._iwram_bar)

        self._iwram_detail = QLabel("—")
        self._iwram_detail.setStyleSheet("font-size: 10px; color: #aaa;")
        mem_lay.addWidget(self._iwram_detail)

        layout.addWidget(mem_box)

        # ── Section Breakdown ───────────────────────────────────────────
        section_box = QGroupBox("Section Breakdown")
        self._section_form = QFormLayout(section_box)
        self._section_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._text_label = QLabel("—")
        self._section_form.addRow("Code (.text):", self._text_label)
        self._rodata_label = QLabel("—")
        self._section_form.addRow("Read-only data (.rodata):", self._rodata_label)
        self._data_label = QLabel("—")
        self._section_form.addRow("Initialized data (.data):", self._data_label)
        self._bss_label = QLabel("—")
        self._section_form.addRow("Uninitialized data (.bss):", self._bss_label)

        layout.addWidget(section_box)

        # ── Build Info ──────────────────────────────────────────────────
        build_box = QGroupBox("Build Info")
        build_form = QFormLayout(build_box)
        build_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._build_type_label = QLabel("—")
        build_form.addRow("Build type:", self._build_type_label)
        self._song_count_label = QLabel("—")
        build_form.addRow("Songs:", self._song_count_label)
        self._map_count_label = QLabel("—")
        build_form.addRow("Maps:", self._map_count_label)
        self._species_count_label = QLabel("—")
        build_form.addRow("Species:", self._species_count_label)

        layout.addWidget(build_box)

        layout.addStretch()

    def _refresh(self):
        """Read the latest build outputs and update all displays."""
        if not self._project_root:
            self._status_label.setText("No project loaded.")
            return

        root = self._project_root

        # Find the ROM file
        rom_size = 0
        rom_path = ""
        for name in ("pokefirered_modern.gba", "pokefirered.gba"):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                rom_size = os.path.getsize(p)
                rom_path = p
                break

        if rom_size:
            self._rom_file_label.setText(
                f"{os.path.basename(rom_path)}: {_sizeof_fmt(rom_size)}")
            self._rom_bar_16 = self._update_bar(
                self._rom_bar_16, rom_size, _ROM_16MB,
                self.layout().itemAt(2).widget().layout(), 2)
            self._rom_bar_32 = self._update_bar(
                self._rom_bar_32, rom_size, _ROM_32MB,
                self.layout().itemAt(2).widget().layout(), 4)
        else:
            self._rom_file_label.setText(
                "No .gba file found — build the project first.")

        # Parse the .map file for memory section info
        map_data = self._parse_map_file(root)
        elf_data = self._parse_elf_sections(root)

        if map_data:
            ewram_used = map_data.get('ewram_used', 0)
            iwram_used = map_data.get('iwram_used', 0)

            self._ewram_bar = self._update_bar(
                self._ewram_bar, ewram_used, _GBA_EWRAM,
                self.layout().itemAt(3).widget().layout(), 1)
            self._ewram_detail.setText(
                f"{_sizeof_fmt(ewram_used)} used, "
                f"{_sizeof_fmt(_GBA_EWRAM - ewram_used)} free")

            self._iwram_bar = self._update_bar(
                self._iwram_bar, iwram_used, 32 * 1024,
                self.layout().itemAt(3).widget().layout(), 4)
            self._iwram_detail.setText(
                f"{_sizeof_fmt(iwram_used)} used, "
                f"{_sizeof_fmt(32 * 1024 - iwram_used)} free")

        if elf_data:
            self._text_label.setText(_sizeof_fmt(elf_data.get('text', 0)))
            self._rodata_label.setText(_sizeof_fmt(elf_data.get('rodata', 0)))
            self._data_label.setText(_sizeof_fmt(elf_data.get('data', 0)))
            self._bss_label.setText(_sizeof_fmt(elf_data.get('bss', 0)))

        # Build type
        if os.path.isfile(os.path.join(root, "pokefirered_modern.gba")):
            self._build_type_label.setText("Modern (MODERN=1)")
        elif os.path.isfile(os.path.join(root, "pokefirered.gba")):
            self._build_type_label.setText("Legacy (agbcc)")
        else:
            self._build_type_label.setText("Not built yet")

        # Counts
        self._song_count_label.setText(str(self._count_songs(root)))
        self._map_count_label.setText(str(self._count_maps(root)))
        self._species_count_label.setText(str(self._count_species(root)))

        self._status_label.setText("Last refreshed just now.")

    def _update_bar(self, old_bar: QProgressBar, used: int, total: int,
                    parent_layout, index: int) -> QProgressBar:
        """Replace a progress bar in-place with updated values."""
        new_bar = _make_bar(used, total)
        parent_layout.replaceWidget(old_bar, new_bar)
        old_bar.deleteLater()
        return new_bar

    def _parse_map_file(self, root: str) -> dict:
        """Parse the linker .map file for EWRAM/IWRAM usage."""
        result = {}
        for name in ("pokefirered_modern.map", "pokefirered.map"):
            map_path = os.path.join(root, name)
            if os.path.isfile(map_path):
                try:
                    with open(map_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()

                    # Look for EWRAM section boundaries
                    ewram_start = self._find_symbol(content, '__ewram_start')
                    ewram_end = self._find_symbol(content, '__ewram_end')
                    if ewram_start and ewram_end:
                        result['ewram_used'] = ewram_end - ewram_start

                    iwram_start = self._find_symbol(content, '__iwram_start')
                    iwram_end = self._find_symbol(content, '__iwram_end')
                    if iwram_start and iwram_end:
                        result['iwram_used'] = iwram_end - iwram_start

                    # Try alternate patterns for bss-based EWRAM
                    if 'ewram_used' not in result:
                        ewram_start = self._find_symbol(content, '__ewram_bss_start')
                        ewram_end = self._find_symbol(content, '__ewram_bss_end')
                        if ewram_start is None:
                            ewram_start = self._find_symbol(content, '__edata')
                        if ewram_end is None:
                            ewram_end = self._find_symbol(content, '__end__')
                        if ewram_start and ewram_end:
                            result['ewram_used'] = ewram_end - ewram_start

                except Exception:
                    pass
                break
        return result

    def _find_symbol(self, map_content: str, symbol: str) -> Optional[int]:
        """Find a symbol's address in a linker .map file."""
        # Pattern: 0x0800abcd  symbol_name
        m = re.search(
            rf'^\s*(0x[0-9a-fA-F]+)\s+{re.escape(symbol)}\s*$',
            map_content, re.MULTILINE)
        if m:
            return int(m.group(1), 16)
        return None

    def _parse_elf_sections(self, root: str) -> dict:
        """Try to get section sizes from the ELF file using objdump or size."""
        result = {}
        for name in ("pokefirered_modern.elf", "pokefirered.elf"):
            elf_path = os.path.join(root, name)
            if not os.path.isfile(elf_path):
                continue

            # Try arm-none-eabi-size first
            for size_cmd in ("arm-none-eabi-size", "size"):
                try:
                    r = subprocess.run(
                        [size_cmd, "-A", elf_path],
                        capture_output=True, text=True, timeout=10)
                    if r.returncode == 0:
                        for line in r.stdout.splitlines():
                            parts = line.split()
                            if len(parts) >= 2:
                                name_col = parts[0]
                                try:
                                    size_val = int(parts[1])
                                except ValueError:
                                    continue
                                if name_col == '.text':
                                    result['text'] = size_val
                                elif name_col == '.rodata':
                                    result['rodata'] = size_val
                                elif name_col == '.data':
                                    result['data'] = size_val
                                elif name_col == '.bss':
                                    result['bss'] = size_val
                        if result:
                            return result
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            break
        return result

    def _count_songs(self, root: str) -> int:
        """Count songs from song_table.inc."""
        path = os.path.join(root, "sound", "song_table.inc")
        if not os.path.isfile(path):
            return 0
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return sum(1 for line in f
                           if line.strip().startswith('song '))
        except Exception:
            return 0

    def _count_maps(self, root: str) -> int:
        """Count map directories."""
        maps_dir = os.path.join(root, "data", "maps")
        if not os.path.isdir(maps_dir):
            return 0
        return sum(1 for d in os.listdir(maps_dir)
                   if os.path.isdir(os.path.join(maps_dir, d))
                   and os.path.isfile(os.path.join(maps_dir, d, "map.json")))

    def _count_species(self, root: str) -> int:
        """Count species from species.h."""
        path = os.path.join(root, "include", "constants", "species.h")
        if not os.path.isfile(path):
            return 0
        try:
            with open(path, 'r', encoding='utf-8') as f:
                count = 0
                for line in f:
                    if line.strip().startswith('#define SPECIES_'):
                        name = line.split()[1]
                        if name not in ('SPECIES_NONE', 'SPECIES_EGG',
                                        'SPECIES_UNOWN_B'):
                            count += 1
                return count
        except Exception:
            return 0
