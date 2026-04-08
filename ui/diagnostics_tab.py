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

# GBA ROM address range
_ROM_ADDR_START = 0x08000000
_ROM_ADDR_END   = 0x0A000000


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

        # Parse the .map file for memory section info (needed for ROM content size)
        map_data = self._parse_map_file(root)
        elf_data = self._parse_elf_sections(root)

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
            # The .gba is typically padded to a power-of-2 (often 32 MB).
            # Use the actual ROM content size from the .map file when available.
            content_size = map_data.get('rom_content_size', 0) if map_data else 0
            display_size = content_size if content_size > 0 else rom_size
            padded_note = ""
            if content_size > 0 and rom_size > content_size:
                padded_note = (f"  (file is {_sizeof_fmt(rom_size)} "
                               f"— padded by build system)")
            self._rom_file_label.setText(
                f"{os.path.basename(rom_path)}: "
                f"{_sizeof_fmt(display_size)}{padded_note}")
            self._rom_bar_16 = self._update_bar(
                self._rom_bar_16, display_size, _ROM_16MB,
                self.layout().itemAt(2).widget().layout(), 2)
            self._rom_bar_32 = self._update_bar(
                self._rom_bar_32, display_size, _ROM_32MB,
                self.layout().itemAt(2).widget().layout(), 4)
        else:
            self._rom_file_label.setText(
                "No .gba file found — build the project first.")

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

        # Section breakdown — prefer ELF data, fall back to .map file
        sec_src = elf_data if elf_data else (map_data if map_data else {})
        if sec_src:
            self._text_label.setText(_sizeof_fmt(sec_src.get('text', 0)))
            self._rodata_label.setText(_sizeof_fmt(sec_src.get('rodata', 0)))
            self._data_label.setText(_sizeof_fmt(sec_src.get('data', 0)))
            self._bss_label.setText(_sizeof_fmt(sec_src.get('bss', 0)))

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
        """Parse the linker .map file for EWRAM/IWRAM usage and ROM content size.

        pokefirered's .map has section header lines like:
            ewram           0x02000000    0x3f46e
            iwram           0x03000000     0x7398
            .text           0x08000000   0x170f10
            .rodata         0x081ed948   0x6faedc
        The third column is the section size.
        """
        result = {}
        for name in ("pokefirered_modern.map", "pokefirered.map"):
            map_path = os.path.join(root, name)
            if not os.path.isfile(map_path):
                continue
            try:
                with open(map_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                # Parse section headers: name  address  size
                # Lowercase lines (ewram, .text, .rodata) are actual content.
                # UPPERCASE lines (ROM, EWRAM, IWRAM) are memory region
                # definitions (capacity, not usage) — skip them.
                section_re = re.compile(
                    r'^([a-z.]\S*)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)',
                    re.MULTILINE)
                rom_end = 0
                for m in section_re.finditer(content):
                    sec_name = m.group(1)
                    sec_addr = int(m.group(2), 16)
                    sec_size = int(m.group(3), 16)

                    if sec_name == 'ewram':
                        result['ewram_used'] = sec_size
                    elif sec_name == 'iwram':
                        result['iwram_used'] = sec_size
                    elif sec_name == '.text':
                        result['text'] = sec_size
                    elif sec_name == '.rodata':
                        result['rodata'] = sec_size
                    elif sec_name == '.data':
                        result['data'] = sec_size
                    elif sec_name == '.bss':
                        result['bss'] = sec_size
                    if _ROM_ADDR_START <= sec_addr < _ROM_ADDR_END and sec_size > 0:
                        # ROM content section — track the highest end address
                        end = sec_addr + sec_size
                        if end > rom_end:
                            rom_end = end

                if rom_end > _ROM_ADDR_START:
                    result['rom_content_size'] = rom_end - _ROM_ADDR_START

            except Exception:
                pass
            break
        return result

    def _parse_elf_sections(self, root: str) -> dict:
        """Try to get section sizes from the ELF file using objdump or size."""
        result = {}
        for name in ("pokefirered_modern.elf", "pokefirered.elf"):
            elf_path = os.path.join(root, name)
            if not os.path.isfile(elf_path):
                continue

            # Try arm-none-eabi-size — check devkitARM path too
            size_cmds = ["arm-none-eabi-size", "size"]
            devkit = os.environ.get("DEVKITARM", "")
            if not devkit:
                # Common default install location on Windows
                for dkp in (r"C:\devkitpro\devkitARM",
                            r"C:\devkitPro\devkitARM"):
                    if os.path.isdir(dkp):
                        devkit = dkp
                        break
            if devkit:
                size_cmds.insert(0, os.path.join(devkit, "bin",
                                                 "arm-none-eabi-size"))
            for size_cmd in size_cmds:
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
