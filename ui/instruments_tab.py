"""
Instruments Tab for the PorySuite-Z Sound Editor.

Phase 4: Browse all instruments across all voicegroups, view details
(type, ADSR, sample info, duty cycle, etc.), and preview how they
sound at any pitch via a clickable piano keyboard.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QPen, QMouseEvent, QDoubleValidator
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView,
    QComboBox, QFrame, QSizePolicy, QScrollArea,
    QSpinBox, QSlider, QFileDialog, QMessageBox,
    QInputDialog, QCheckBox,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard

_log = logging.getLogger("SoundEditor.Instruments")


# ---------------------------------------------------------------------------
# Data roles for tree items
# ---------------------------------------------------------------------------

_ROLE_VG_NAME = Qt.ItemDataRole.UserRole + 10
_ROLE_SLOT_IDX = Qt.ItemDataRole.UserRole + 11
_ROLE_INST_TYPE = Qt.ItemDataRole.UserRole + 12
_ROLE_INST_KEY = Qt.ItemDataRole.UserRole + 13

_DIRTY_BG = QColor("#3d2e00")
_DIRTY_SS = ("QGroupBox { border: 1px solid #ffb74d; border-radius: 4px; }"
             "QGroupBox::title { color: #ffb74d; }")


# ---------------------------------------------------------------------------
# Filler slot detection
# ---------------------------------------------------------------------------

def _is_filler_instrument(inst) -> bool:
    """Detect GBA 'empty slot' filler instruments.

    Most voicegroups pad unused slots with a default voice_square_1 entry
    (duty=2/50%, attack=0, decay=0, sustain=15, release=0, base=60, pan=0).
    These are never referenced by any song and just clutter the browser.
    """
    return (inst.voice_type == 'voice_square_1'
            and inst.duty_cycle == 2
            and inst.attack == 0
            and inst.decay == 0
            and inst.sustain == 15
            and inst.release == 0
            and inst.base_midi_key == 60
            and inst.pan == 0
            and inst.sweep == 0)


# ---------------------------------------------------------------------------
# Mini piano keyboard widget
# ---------------------------------------------------------------------------

class PianoKeyboard(QWidget):
    """A clickable 2-octave piano keyboard for previewing instruments."""

    note_clicked = pyqtSignal(int)   # emits MIDI note number on press
    note_released = pyqtSignal(int)  # emits MIDI note number on release

    # Which notes in an octave are black keys (sharps/flats)
    _BLACK_KEYS = {1, 3, 6, 8, 10}  # C#, D#, F#, G#, A#
    _BLACK_OFFSETS = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}

    def __init__(self, start_octave: int = 3, num_octaves: int = 3,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._start_note = start_octave * 12  # MIDI note of leftmost C
        self._num_octaves = num_octaves
        self._num_white = num_octaves * 7
        self._pressed_note: Optional[int] = None

        self._white_w = 24
        self._white_h = 80
        self._black_w = 14
        self._black_h = 50

        self.setFixedSize(
            self._num_white * self._white_w + 1,
            self._white_h + 1,
        )
        self.setMouseTracking(False)

    def set_start_octave(self, octave: int):
        """Shift the keyboard to start at a different octave (0-8)."""
        octave = max(0, min(8, octave))
        # Don't let the top end exceed MIDI 127
        max_start = (127 // 12) - self._num_octaves + 1
        octave = min(octave, max_start)
        self._start_note = octave * 12
        self.update()

    @property
    def start_octave(self) -> int:
        return self._start_note // 12

    def _white_key_midi(self, white_index: int) -> int:
        """Convert white-key index to MIDI note."""
        # White keys in an octave: C D E F G A B = scale degrees 0 2 4 5 7 9 11
        white_in_octave = [0, 2, 4, 5, 7, 9, 11]
        octave = white_index // 7
        pos = white_index % 7
        return self._start_note + octave * 12 + white_in_octave[pos]

    def _note_at_pos(self, x: int, y: int) -> Optional[int]:
        """Return the MIDI note under pixel (x, y), or None."""
        # Check black keys first (they overlap white keys)
        if y < self._black_h:
            for octave in range(self._num_octaves):
                for semi, bk_idx in self._BLACK_OFFSETS.items():
                    # Position of this black key
                    # Black keys sit between white keys
                    white_positions = [0, 1, 2, 3, 4, 5, 6]
                    # C#=between C and D, D#=between D and E,
                    # F#=between F and G, G#=between G and A, A#=between A and B
                    white_left_map = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}
                    wl = white_left_map[semi]
                    bx = (octave * 7 + wl + 1) * self._white_w - self._black_w // 2
                    if bx <= x <= bx + self._black_w:
                        return self._start_note + octave * 12 + semi

        # White key
        white_index = x // self._white_w
        if 0 <= white_index < self._num_white:
            return self._white_key_midi(white_index)
        return None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Draw white keys
        for i in range(self._num_white):
            x = i * self._white_w
            midi = self._white_key_midi(i)
            if midi == self._pressed_note:
                p.setBrush(QBrush(QColor(180, 200, 255)))
            else:
                p.setBrush(QBrush(QColor(255, 255, 255)))
            p.setPen(QPen(QColor(100, 100, 100)))
            p.drawRect(x, 0, self._white_w, self._white_h)

        # Draw black keys on top
        for octave in range(self._num_octaves):
            for semi, bk_idx in self._BLACK_OFFSETS.items():
                white_left_map = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}
                wl = white_left_map[semi]
                bx = (octave * 7 + wl + 1) * self._white_w - self._black_w // 2
                midi = self._start_note + octave * 12 + semi
                if midi == self._pressed_note:
                    p.setBrush(QBrush(QColor(80, 100, 200)))
                else:
                    p.setBrush(QBrush(QColor(30, 30, 30)))
                p.setPen(QPen(QColor(0, 0, 0)))
                p.drawRect(bx, 0, self._black_w, self._black_h)

        # Note name labels on C keys
        p.setPen(QPen(QColor(120, 120, 120)))
        small_font = QFont("", 7)
        p.setFont(small_font)
        for i in range(self._num_white):
            midi = self._white_key_midi(i)
            if midi % 12 == 0:  # C note
                octave = midi // 12
                x = i * self._white_w + 2
                p.drawText(x, self._white_h - 4, f"C{octave}")

        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        note = self._note_at_pos(int(event.position().x()),
                                  int(event.position().y()))
        if note is not None:
            self._pressed_note = note
            self.update()
            self.note_clicked.emit(note)

    def mouseReleaseEvent(self, event: QMouseEvent):
        released = self._pressed_note
        self._pressed_note = None
        self.update()
        if released is not None:
            self.note_released.emit(released)


# ---------------------------------------------------------------------------
# ADSR envelope display
# ---------------------------------------------------------------------------

class ADSRDisplay(QWidget):
    """Small visual display of an ADSR envelope curve."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._attack = 0
        self._decay = 0
        self._sustain = 0
        self._release = 0
        self._is_cgb = False
        self.setFixedSize(200, 60)

    def set_adsr(self, a: int, d: int, s: int, r: int, is_cgb: bool = False):
        self._attack = a
        self._decay = d
        self._sustain = s
        self._release = r
        self._is_cgb = is_cgb
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor(40, 40, 50))

        # Convert values to display-friendly proportions
        if self._is_cgb:
            # CGB: attack 0=instant, 1-7 slower; sustain 0-15
            a_frac = 0.0 if self._attack == 0 else self._attack / 7.0
            d_frac = self._decay / 7.0
            s_level = self._sustain / 15.0
            r_frac = 0.0 if self._release == 0 else self._release / 7.0
        else:
            # DirectSound: 0-255
            a_frac = (255 - self._attack) / 255.0  # 255=instant, 0=slow
            d_frac = (255 - self._decay) / 255.0 if self._decay > 0 else 0.0
            s_level = self._sustain / 255.0
            r_frac = self._release / 255.0

        margin = 6
        draw_w = w - margin * 2
        draw_h = h - margin * 2

        # Allocate horizontal space: attack | decay | sustain | release
        total_frac = max(0.01, a_frac + d_frac + 0.3 + r_frac)
        a_w = int(draw_w * a_frac / total_frac)
        d_w = int(draw_w * d_frac / total_frac)
        r_w = int(draw_w * r_frac / total_frac)
        s_w = draw_w - a_w - d_w - r_w
        if s_w < 10:
            s_w = 10

        # Draw envelope as a line path
        p.setPen(QPen(QColor(100, 200, 255), 2))
        x = margin
        bottom = h - margin

        # Start at zero
        points = [(x, bottom)]

        # Attack: ramp up to peak
        x += a_w
        points.append((x, margin))

        # Decay: ramp down to sustain level
        x += d_w
        sus_y = int(bottom - s_level * draw_h)
        points.append((x, sus_y))

        # Sustain: hold
        x += s_w
        points.append((x, sus_y))

        # Release: ramp down to zero
        x += r_w
        points.append((x, bottom))

        for i in range(len(points) - 1):
            p.drawLine(points[i][0], points[i][1],
                       points[i + 1][0], points[i + 1][1])

        # Labels
        p.setPen(QPen(QColor(150, 150, 150)))
        p.setFont(QFont("", 7))
        p.drawText(margin, h - 1, "A")
        p.drawText(margin + a_w, h - 1, "D")
        p.drawText(margin + a_w + d_w + s_w // 2, h - 1, "S")
        p.drawText(w - margin - 8, h - 1, "R")

        p.end()


# ---------------------------------------------------------------------------
# Note name helper
# ---------------------------------------------------------------------------

_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def _midi_to_name(midi: int) -> str:
    """Convert MIDI note number to readable name like 'C4' or 'F#5'."""
    if midi < 0 or midi > 127:
        return str(midi)
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12}"


# ---------------------------------------------------------------------------
# Loop waveform mini-view
# ---------------------------------------------------------------------------

class _LoopWaveformWidget(QWidget):
    """Tiny waveform display with a draggable loop-start marker.

    Shows the sample's waveform as a compact visualization with a
    vertical orange line at the loop start position.  The user can
    click or drag anywhere on the widget to move the loop point.
    """

    loop_dragged = pyqtSignal(float)  # emits 0.0–1.0 fraction

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pcm_peaks: list[tuple[int, int]] = []  # (min, max) per column
        self._loop_frac: float = 0.0   # 0.0–1.0
        self._enabled = False
        self._dragging = False
        self.setFixedHeight(40)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            "Waveform with loop point.\n"
            "Click or drag to set the loop start position.\n"
            "Orange line = where the loop begins.\n"
            "Left of the line plays once (attack), right loops.")
        self.setVisible(False)

    def set_sample(self, pcm_data: bytes, loop_frac: float, enabled: bool):
        """Load sample data and configure the display."""
        self._enabled = enabled
        self._loop_frac = max(0.0, min(1.0, loop_frac))
        self._build_peaks(pcm_data)
        self.setVisible(True)
        self.update()

    def set_loop_frac(self, frac: float):
        """Update just the loop marker position."""
        self._loop_frac = max(0.0, min(1.0, frac))
        self.update()

    def clear(self):
        """Hide the waveform."""
        self._pcm_peaks = []
        self._enabled = False
        self.setVisible(False)

    def _build_peaks(self, pcm_data: bytes):
        """Downsample PCM to per-pixel min/max peaks."""
        w = max(self.width(), 200)
        n = len(pcm_data)
        if n == 0:
            self._pcm_peaks = []
            return
        peaks = []
        for col in range(w):
            start = col * n // w
            end = (col + 1) * n // w
            if start >= end:
                end = start + 1
            chunk = pcm_data[start:min(end, n)]
            if not chunk:
                peaks.append((0, 0))
                continue
            # Signed 8-bit: 0-127 = positive, 128-255 = negative
            vals = [b if b < 128 else b - 256 for b in chunk]
            peaks.append((min(vals), max(vals)))
        self._pcm_peaks = peaks

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        mid = h // 2

        # Background
        p.fillRect(0, 0, w, h, QColor(30, 30, 30))

        if not self._pcm_peaks:
            p.end()
            return

        # Waveform
        wave_color = QColor(80, 180, 80) if self._enabled else QColor(80, 80, 80)
        p.setPen(QPen(wave_color, 1))
        n_peaks = len(self._pcm_peaks)
        for col in range(min(w, n_peaks)):
            mn, mx = self._pcm_peaks[col]
            y_top = mid - int(mx * mid / 128)
            y_bot = mid - int(mn * mid / 128)
            if y_top == y_bot:
                y_bot = y_top + 1
            p.drawLine(col, y_top, col, y_bot)

        # Center line
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.drawLine(0, mid, w, mid)

        # Loop marker
        if self._enabled:
            lx = int(self._loop_frac * w)
            p.setPen(QPen(QColor(255, 160, 0), 2))
            p.drawLine(lx, 0, lx, h)
            # Label
            p.setPen(QColor(255, 160, 0))
            p.setFont(QFont("", 7))
            p.drawText(lx + 3, 10, "LOOP")

        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if not self._enabled or event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self._emit_position(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._emit_position(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False

    def _emit_position(self, x: float):
        w = self.width()
        if w <= 0:
            return
        frac = max(0.0, min(1.0, x / w))
        self._loop_frac = frac
        self.update()
        self.loop_dragged.emit(frac)

    def resizeEvent(self, event):
        # Rebuild peaks when widget resizes
        # (we don't have the raw PCM here, so peaks stay as-is until next set_sample)
        super().resizeEvent(event)


# ---------------------------------------------------------------------------
# Instruments Tab
# ---------------------------------------------------------------------------

class InstrumentsTab(QWidget):
    """Browse and preview all instruments across all voicegroups."""

    modified = pyqtSignal()  # emitted when any instrument property is edited
    _preview_done = pyqtSignal()
    _preview_failed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root: str = ""
        self._voicegroup_data = None
        self._sample_data = None
        self._audio_player = None
        self._voicegroups_tab_ref = None  # set by sound_editor_tab for dirty tracking
        self._current_instrument = None
        self._current_vg_name = ""
        self._editing = False  # guard against feedback loops
        self._dirty_inst_keys: set[str] = set()

        self._build_ui()

        self._preview_done.connect(self._on_preview_ready)
        self._preview_failed.connect(self._on_preview_error)

    # ═══════════════════════════════════════════════════════════════════════
    # UI Construction
    # ═══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Left: instrument browser ───────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Instruments")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        left_layout.addWidget(title)

        # Search
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search instruments...")
        self._search_box.setToolTip(
            "Filter by name — type part of the instrument or sample name.\n"
            "Works across all type groups.")
        self._search_box.textChanged.connect(self._apply_filter)
        left_layout.addWidget(self._search_box)

        # Count label
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: grey; font-size: 11px;")
        left_layout.addWidget(self._count_label)

        # Instrument tree — grouped by type
        self._inst_tree = QTreeWidget()
        self._inst_tree.setHeaderLabels(["Instrument", "Used In"])
        self._inst_tree.setRootIsDecorated(True)
        self._inst_tree.setAlternatingRowColors(True)
        self._inst_tree.setSortingEnabled(False)
        self._inst_tree.setSelectionMode(
            QTreeWidget.SelectionMode.SingleSelection)
        header = self._inst_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._inst_tree.currentItemChanged.connect(self._on_instrument_selected)
        left_layout.addWidget(self._inst_tree)

        # Action buttons at the bottom of the list (always visible)
        list_btn_row = QHBoxLayout()
        self._btn_import_sample = QPushButton("Import WAV")
        self._btn_import_sample.setToolTip(
            "Import a WAV file as a new GBA instrument.\n\n"
            "Creates the sample and adds it to a voicegroup\n"
            "as a new instrument you can use in songs.\n\n"
            "Requirements:\n"
            "• Mono or stereo (stereo mixed to mono)\n"
            "• Any bit depth or sample rate\n"
            "• Shorter is better — GBA has limited audio memory")
        self._btn_import_sample.clicked.connect(self._on_import_sample)
        self._btn_import_sample.setEnabled(False)
        list_btn_row.addWidget(self._btn_import_sample)

        self._btn_import_inst = QPushButton("Import Instrument")
        self._btn_import_inst.setToolTip(
            "Load a .psinst file exported from another project.\n"
            "Imports the sample audio, creates a new instrument\n"
            "with the saved settings (ADSR, base key, pan, loop).")
        self._btn_import_inst.clicked.connect(self._on_import_instrument)
        self._btn_import_inst.setEnabled(False)
        list_btn_row.addWidget(self._btn_import_inst)
        list_btn_row.addStretch()
        left_layout.addLayout(list_btn_row)

        splitter.addWidget(left)

        # ── Right: detail + preview ────────────────────────────────────────
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # -- Instrument info --
        self._info_group = QGroupBox("Instrument Details")
        self._info_group.setToolTip(
            "Properties of the selected instrument.\n"
            "Changes are applied to every copy of this instrument\n"
            "across all voicegroups — they're the same sound, shared.")
        info_layout = QVBoxLayout(self._info_group)

        self._inst_name_label = QLabel("No instrument selected")
        self._inst_name_label.setFont(QFont("", 14, QFont.Weight.Bold))
        self._inst_name_label.setWordWrap(True)
        info_layout.addWidget(self._inst_name_label)

        self._inst_type_label = QLabel("")
        self._inst_type_label.setStyleSheet("color: grey;")
        info_layout.addWidget(self._inst_type_label)

        # Read-only info line (sample name, voicegroup)
        self._detail_info = QLabel("")
        self._detail_info.setWordWrap(True)
        self._detail_info.setStyleSheet("font-size: 11px; color: grey;")
        info_layout.addWidget(self._detail_info)

        # --- Editable properties ---
        edit_frame = QFrame()
        edit_grid = QVBoxLayout(edit_frame)
        edit_grid.setContentsMargins(0, 4, 0, 0)

        # Base key
        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("Base Key:"))
        self._edit_base_key = QSpinBox()
        self._edit_base_key.setRange(0, 127)
        self._edit_base_key.setValue(60)
        self._edit_base_key.setToolTip(
            "MIDI note the sample is tuned to.\n"
            "For sample instruments, this is stored in the voicegroup\n"
            "but pitch comes from the sample file itself —\n"
            "changing this won't affect the preview sound.")
        install_scroll_guard(self._edit_base_key)
        self._edit_base_key.valueChanged.connect(self._on_base_key_changed)
        base_row.addWidget(self._edit_base_key)
        self._base_key_name = QLabel("C5")
        base_row.addWidget(self._base_key_name)
        base_row.addStretch()
        edit_grid.addLayout(base_row)

        # Info hint for DirectSound base key (hidden unless relevant)
        self._base_key_hint = QLabel(
            "Pitch comes from the sample file — this value is metadata only")
        self._base_key_hint.setStyleSheet(
            "color: grey; font-size: 10px; font-style: italic;")
        self._base_key_hint.setWordWrap(True)
        self._base_key_hint.hide()
        edit_grid.addWidget(self._base_key_hint)

        # Pan
        pan_row = QHBoxLayout()
        pan_row.addWidget(QLabel("Pan:"))
        self._edit_pan = QSpinBox()
        self._edit_pan.setRange(0, 127)
        self._edit_pan.setValue(0)
        self._edit_pan.setToolTip(
            "Stereo panning (0–127).\n"
            "0 = center, 1–63 = left, 64 = center, 65–127 = right.\n"
            "Controls where the sound sits in the left/right mix.")
        install_scroll_guard(self._edit_pan)
        self._edit_pan.valueChanged.connect(self._on_pan_changed)
        pan_row.addWidget(self._edit_pan)
        self._pan_desc = QLabel("Center")
        pan_row.addWidget(self._pan_desc)
        pan_row.addStretch()
        edit_grid.addLayout(pan_row)

        # --- Square wave specific ---
        self._square_frame = QFrame()
        sq_layout = QVBoxLayout(self._square_frame)
        sq_layout.setContentsMargins(0, 0, 0, 0)

        duty_row = QHBoxLayout()
        duty_row.addWidget(QLabel("Duty Cycle:"))
        self._edit_duty = QComboBox()
        self._edit_duty.addItems(["12.5%", "25%", "50%", "75%"])
        self._edit_duty.setToolTip(
            "Pulse width of the square wave.\n"
            "12.5% = thin/tinny, 25% = hollow, 50% = full/classic,\n"
            "75% = same as 25% (inverted). Changes the tone/timbre.")
        install_scroll_guard(self._edit_duty)
        self._edit_duty.currentIndexChanged.connect(self._on_duty_changed)
        duty_row.addWidget(self._edit_duty)
        duty_row.addStretch()
        sq_layout.addLayout(duty_row)

        sweep_row = QHBoxLayout()
        self._sweep_label = QLabel("Sweep:")
        sweep_row.addWidget(self._sweep_label)
        self._edit_sweep = QSpinBox()
        self._edit_sweep.setRange(0, 255)
        self._edit_sweep.setToolTip(
            "Frequency sweep (0–255). Only for Square 1 waves.\n"
            "Controls an automatic pitch slide effect.\n"
            "0 = no sweep. Higher values = faster pitch change.")
        install_scroll_guard(self._edit_sweep)
        self._edit_sweep.valueChanged.connect(self._on_sweep_changed)
        sweep_row.addWidget(self._edit_sweep)
        sweep_row.addStretch()
        sq_layout.addLayout(sweep_row)

        self._square_frame.hide()
        edit_grid.addWidget(self._square_frame)

        # --- Noise specific ---
        self._noise_frame = QFrame()
        ns_layout = QHBoxLayout(self._noise_frame)
        ns_layout.setContentsMargins(0, 0, 0, 0)
        ns_layout.addWidget(QLabel("Period:"))
        self._edit_period = QComboBox()
        self._edit_period.addItems(["0 — White noise", "1 — Metallic"])
        self._edit_period.setToolTip(
            "Noise generator mode.\n"
            "White noise = hissy/static (good for cymbals, wind).\n"
            "Metallic = buzzy/tonal (good for retro percussion).")
        install_scroll_guard(self._edit_period)
        self._edit_period.currentIndexChanged.connect(self._on_period_changed)
        ns_layout.addWidget(self._edit_period)
        ns_layout.addStretch()
        self._noise_frame.hide()
        edit_grid.addWidget(self._noise_frame)

        # --- Sample info (read-only) ---
        self._sample_frame = QFrame()
        samp_layout = QVBoxLayout(self._sample_frame)
        samp_layout.setContentsMargins(0, 0, 0, 0)
        self._sample_info_label = QLabel("")
        self._sample_info_label.setWordWrap(True)
        samp_layout.addWidget(self._sample_info_label)

        # Sample loop controls
        loop_row = QHBoxLayout()
        self._chk_loop = QCheckBox("Loop")
        self._chk_loop.setToolTip(
            "Enable sample looping.\n"
            "When ON, the GBA replays from the loop point to the end\n"
            "for as long as the note is held. Without this, the sample\n"
            "plays once and stops — long notes go silent.\n"
            "Essential for sustained instruments (strings, winds, pads).")
        self._chk_loop.stateChanged.connect(self._on_loop_toggled)
        loop_row.addWidget(self._chk_loop)

        loop_row.addWidget(QLabel("at"))

        # Loop point text field — plain QLineEdit so typing, paste, and
        # backspace all work normally.  QDoubleSpinBox with 4 decimal places
        # has intractable Qt validation bugs that prevent editing small values.
        self._spin_loop_seconds = QLineEdit()
        self._spin_loop_seconds.setFixedWidth(100)
        self._loop_validator = QDoubleValidator(0.0, 999.9999, 4)
        self._loop_validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self._spin_loop_seconds.setValidator(self._loop_validator)
        self._spin_loop_seconds.setToolTip(
            "Loop start time in seconds (4 decimal places).\n"
            "Matches Audacity's timeline — set this to the\n"
            "exact timestamp where you want the loop to begin.\n"
            "The GBA plays the full sample once, then jumps\n"
            "back to this point and repeats.\n\n"
            "Type the value and press Enter to apply.")
        # returnPressed fires unconditionally on Enter regardless of validator state.
        # textEdited fires on every keystroke; debounce timer applies 400 ms after
        # the user stops typing so the loop updates without needing to press Enter.
        self._spin_loop_seconds.returnPressed.connect(self._on_loop_seconds_changed)
        self._loop_debounce = QTimer(self)
        self._loop_debounce.setInterval(400)
        self._loop_debounce.setSingleShot(True)
        self._loop_debounce.timeout.connect(self._on_loop_seconds_changed)
        self._spin_loop_seconds.textEdited.connect(self._loop_debounce.start)
        loop_row.addWidget(self._spin_loop_seconds)
        loop_row.addWidget(QLabel("s"))

        self._loop_detail_label = QLabel("")
        self._loop_detail_label.setStyleSheet("color: grey; font-size: 10px;")
        self._loop_detail_label.setToolTip(
            "Byte offset and percentage.\n"
            "The GBA stores the loop point as a byte offset\n"
            "into the sample data. This shows the exact value.")
        loop_row.addWidget(self._loop_detail_label)
        loop_row.addStretch()
        samp_layout.addLayout(loop_row)


        # Waveform mini-view with loop marker
        self._loop_waveform = _LoopWaveformWidget()
        self._loop_waveform.loop_dragged.connect(self._on_loop_waveform_dragged)
        samp_layout.addWidget(self._loop_waveform)

        # Sample management buttons (Export / Replace / Delete)
        self._sample_btn_row = QHBoxLayout()
        self._btn_export_sample = QPushButton("Export WAV")
        self._btn_export_sample.setFixedWidth(90)
        self._btn_export_sample.setToolTip(
            "Save this sample as a standard .wav file.\n"
            "Exports as 8-bit mono WAV at the original sample rate.\n"
            "Can be opened in any audio editor (Audacity, etc).")
        self._btn_export_sample.clicked.connect(self._on_export_sample)
        self._sample_btn_row.addWidget(self._btn_export_sample)

        self._btn_replace_sample = QPushButton("Replace")
        self._btn_replace_sample.setFixedWidth(70)
        self._btn_replace_sample.setToolTip(
            "Replace this sample's audio with a new .wav file.\n"
            "Keeps the same name and all voicegroup references.\n"
            "Any WAV format works (mono/stereo, any bit depth/rate).\n\n"
            "The audio is automatically resampled to match the\n"
            "original sample's rate so pitch stays correct in-game.\n"
            "Loop settings are also preserved from the original.")
        self._btn_replace_sample.clicked.connect(self._on_replace_sample)
        self._sample_btn_row.addWidget(self._btn_replace_sample)

        self._btn_delete_sample = QPushButton("Delete")
        self._btn_delete_sample.setFixedWidth(60)
        self._btn_delete_sample.setToolTip(
            "Remove this sample from the project.\n"
            "Deletes the .bin file and its entry in direct_sound_data.inc.\n"
            "Only allowed if no voicegroup references this sample.")
        self._btn_delete_sample.clicked.connect(self._on_delete_sample)
        self._sample_btn_row.addWidget(self._btn_delete_sample)

        self._sample_btn_row.addStretch()
        samp_layout.addLayout(self._sample_btn_row)

        self._sample_frame.hide()
        edit_grid.addWidget(self._sample_frame)

        info_layout.addWidget(edit_frame)
        right_layout.addWidget(self._info_group)

        # -- Export instrument preset (needs a selected instrument) --
        preset_row = QHBoxLayout()
        self._btn_export_inst = QPushButton("Export Instrument")
        self._btn_export_inst.setToolTip(
            "Save this instrument as a .psinst file.\n"
            "Includes the sample audio, loop settings, ADSR,\n"
            "base key, pan — everything needed to recreate it\n"
            "in another project.")
        self._btn_export_inst.clicked.connect(self._on_export_instrument)
        self._btn_export_inst.setEnabled(False)
        preset_row.addWidget(self._btn_export_inst)
        preset_row.addStretch()
        right_layout.addLayout(preset_row)

        # -- ADSR envelope (editable) --
        adsr_group = QGroupBox("Envelope (ADSR)")
        adsr_group.setToolTip(
            "The volume envelope controls how the sound\n"
            "fades in and out over time.\n\n"
            "A = Attack (fade in), D = Decay (drop to sustain),\n"
            "S = Sustain (held level), R = Release (fade out).\n\n"
            "Changes here are audible in the preview immediately.")
        adsr_layout = QVBoxLayout(adsr_group)

        self._adsr_display = ADSRDisplay()
        adsr_layout.addWidget(self._adsr_display)

        # ADSR sliders
        _adsr_tips = {
            'Attack': "Attack — how quickly the sound reaches full volume.\n"
                      "0 = instant. Higher = slower fade in.\n"
                      "Synth (square/wave/noise): 0–7. Samples: 0–255.",
            'Decay': "Decay — how quickly it drops from peak to sustain level.\n"
                     "0 = instant drop. Higher = slower fade.\n"
                     "Synth: 0–7. Samples: 0–255.",
            'Sustain': "Sustain — the volume level held while a note is playing.\n"
                       "Higher = louder sustained sound.\n"
                       "Synth: 0–15. Samples: 0–255.",
            'Release': "Release — how quickly the sound fades after the note ends.\n"
                       "0 = instant cutoff. Higher = longer tail.\n"
                       "Synth: 0–7. Samples: 0–255.",
        }
        self._adsr_sliders = {}
        self._adsr_labels = {}
        for param in ('Attack', 'Decay', 'Sustain', 'Release'):
            row = QHBoxLayout()
            lbl = QLabel(f"{param[0]}:")
            lbl.setFixedWidth(16)
            row.addWidget(lbl)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 255)
            slider.setValue(0)
            slider.setToolTip(_adsr_tips[param])
            install_scroll_guard(slider)
            row.addWidget(slider)

            val_lbl = QLabel("0")
            val_lbl.setFixedWidth(35)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(val_lbl)

            self._adsr_sliders[param.lower()] = slider
            self._adsr_labels[param.lower()] = val_lbl

            slider.valueChanged.connect(
                lambda v, p=param.lower(): self._on_adsr_changed(p, v))

            adsr_layout.addLayout(row)

        self._adsr_scale_label = QLabel("")
        self._adsr_scale_label.setStyleSheet(
            "color: grey; font-size: 11px;")
        adsr_layout.addWidget(self._adsr_scale_label)

        right_layout.addWidget(adsr_group)

        # -- Used by (voicegroups) --
        usage_group = QGroupBox("Used By")
        usage_group.setToolTip(
            "Which voicegroups contain this instrument.\n"
            "Identical instruments shared across voicegroups are\n"
            "treated as one — editing any copy updates all of them.")
        usage_layout = QVBoxLayout(usage_group)
        self._usage_label = QLabel("—")
        self._usage_label.setWordWrap(True)
        self._usage_label.setStyleSheet("font-size: 11px;")
        usage_layout.addWidget(self._usage_label)
        right_layout.addWidget(usage_group)

        # -- Preview section --
        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)

        # Play button with note selector
        play_row = QHBoxLayout()
        self._btn_play = QPushButton("Play Note")
        self._btn_play.setFixedWidth(100)
        self._btn_play.setEnabled(False)
        self._btn_play.setToolTip(
            "Play a preview of this instrument at middle C.\n"
            "Or click a key on the piano below to hear any pitch.")
        self._btn_play.clicked.connect(self._on_play_preview)
        play_row.addWidget(self._btn_play)

        self._preview_note_label = QLabel("C4 (60)")
        play_row.addWidget(self._preview_note_label)

        play_row.addStretch()

        self._preview_status = QLabel("")
        self._preview_status.setStyleSheet("color: grey; font-size: 11px;")
        play_row.addWidget(self._preview_status)

        preview_layout.addLayout(play_row)

        # Piano keyboard with octave controls
        piano_row = QHBoxLayout()

        self._btn_octave_down = QPushButton("◀")
        self._btn_octave_down.setFixedSize(28, 80)
        self._btn_octave_down.setToolTip("Shift keyboard down one octave")
        self._btn_octave_down.clicked.connect(self._on_octave_down)
        piano_row.addWidget(self._btn_octave_down)

        self._piano = PianoKeyboard(start_octave=3, num_octaves=3)
        self._piano.setToolTip(
            "Click and HOLD a key to preview.\n"
            "Release to stop — hear how the loop sustains.")
        self._piano.note_clicked.connect(self._on_piano_key)
        self._piano.note_released.connect(self._on_piano_key_released)
        piano_row.addWidget(self._piano)

        self._btn_octave_up = QPushButton("▶")
        self._btn_octave_up.setFixedSize(28, 80)
        self._btn_octave_up.setToolTip("Shift keyboard up one octave")
        self._btn_octave_up.clicked.connect(self._on_octave_up)
        piano_row.addWidget(self._btn_octave_up)

        piano_row.addStretch()
        preview_layout.addLayout(piano_row)

        self._piano_range_label = QLabel("C3 – B5")
        self._piano_range_label.setStyleSheet("color: grey; font-size: 10px;")
        hint_row = QHBoxLayout()
        hint_row.addWidget(QLabel(""))  # spacer to align under piano
        hint_row.addWidget(self._piano_range_label)
        hint_row.addStretch()
        preview_layout.addLayout(hint_row)

        right_layout.addWidget(preview_group)
        right_layout.addStretch()

        right_scroll.setWidget(right)
        splitter.addWidget(right_scroll)
        splitter.setSizes([400, 500])

    # ═══════════════════════════════════════════════════════════════════════
    # Data loading
    # ═══════════════════════════════════════════════════════════════════════

    def load_data(self, project_root: str, voicegroup_data):
        """Load voicegroup data and populate the instrument list."""
        self._project_root = project_root
        self._voicegroup_data = voicegroup_data
        self._unique_instruments: dict = {}  # key -> (inst, vg_names)
        self.clear_dirty()
        self._populate_instrument_list()
        self._btn_import_sample.setEnabled(bool(project_root))
        self._btn_import_inst.setEnabled(bool(project_root))

    def set_sample_data(self, sample_data):
        """Receive sample data (lazy-loaded) for preview playback."""
        self._sample_data = sample_data
        # Refresh the current instrument display so loop controls populate
        if self._current_instrument and self._voicegroup_data:
            vg = self._voicegroup_data.get_voicegroup(self._current_vg_name)
            if vg:
                self._show_instrument_details(self._current_instrument, vg)

    @staticmethod
    def _inst_identity_key(inst) -> str:
        """Build a dedup key for an instrument based on what makes it unique."""
        if inst.is_directsound and inst.sample_label:
            return f"sample_{inst.sample_label}"
        if inst.is_square:
            return f"square_{inst.voice_type}_d{inst.duty_cycle}_s{inst.sweep}"
        if inst.is_programmable_wave and inst.wave_label:
            return f"wave_{inst.wave_label}"
        if inst.is_noise:
            return f"noise_p{inst.period}"
        if inst.is_keysplit:
            return f"keysplit_{inst.target_voicegroup}_{inst.keysplit_table}"
        return f"other_{inst.voice_type}_{inst.slot_index}"

    def _populate_instrument_list(self):
        """Build a deduplicated, type-grouped instrument tree."""
        self._inst_tree.clear()
        self._unique_instruments.clear()

        if not self._voicegroup_data:
            return

        # Pass 1: collect unique instruments and track which VGs use them
        from collections import OrderedDict
        unique: dict[str, dict] = OrderedDict()
        # key -> {inst, type_str, vg_set, first_vg, first_slot}

        for vg_name, vg in sorted(self._voicegroup_data.voicegroups.items()):
            for inst in vg.instruments:
                if _is_filler_instrument(inst):
                    continue

                key = self._inst_identity_key(inst)

                if key not in unique:
                    if inst.is_directsound:
                        type_str = "Samples"
                    elif inst.is_square:
                        type_str = "Square Waves"
                    elif inst.is_programmable_wave:
                        type_str = "Programmable Waves"
                    elif inst.is_noise:
                        type_str = "Noise"
                    elif inst.is_keysplit:
                        type_str = "Keysplits"
                    else:
                        type_str = "Other"

                    unique[key] = {
                        'inst': inst,
                        'type_str': type_str,
                        'vg_set': set(),
                        'first_vg': vg_name,
                        'first_slot': inst.slot_index,
                    }

                unique[key]['vg_set'].add(vg_name)

        self._unique_instruments = unique

        # Pass 2: build grouped tree
        # Group order
        group_order = [
            "Samples", "Square Waves", "Programmable Waves",
            "Noise", "Keysplits", "Other",
        ]
        group_items: dict[str, QTreeWidgetItem] = {}
        group_counts: dict[str, int] = {}

        for g in group_order:
            group_counts[g] = 0

        for key, info in unique.items():
            group_counts[info['type_str']] = (
                group_counts.get(info['type_str'], 0) + 1)

        for g in group_order:
            count = group_counts.get(g, 0)
            if count == 0:
                continue
            group_node = QTreeWidgetItem()
            group_node.setText(0, f"{g} ({count})")
            group_node.setData(0, _ROLE_INST_TYPE, g)
            font = group_node.font(0)
            font.setBold(True)
            group_node.setFont(0, font)
            # Group nodes are not selectable instruments
            group_node.setFlags(
                group_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._inst_tree.addTopLevelItem(group_node)
            group_items[g] = group_node

        # Add instrument items under their group
        for key, info in sorted(unique.items(),
                                key=lambda kv: kv[1]['inst'].friendly_name):
            inst = info['inst']
            type_str = info['type_str']
            vg_count = len(info['vg_set'])

            parent = group_items.get(type_str)
            if parent is None:
                continue

            item = QTreeWidgetItem(parent)
            item.setText(0, inst.friendly_name)
            if vg_count == 1:
                vg_name = next(iter(info['vg_set']))
                num = vg_name.replace('voicegroup', '')
                item.setText(1, f"VG {num}")
            else:
                item.setText(1, f"{vg_count} VGs")

            item.setData(0, _ROLE_VG_NAME, info['first_vg'])
            item.setData(0, _ROLE_SLOT_IDX, info['first_slot'])
            item.setData(0, _ROLE_INST_TYPE, type_str)
            item.setData(0, _ROLE_INST_KEY, key)
            item.setToolTip(0, f"{inst.voice_type} (0x{inst.type_byte:02X})")
            item.setToolTip(1, ', '.join(sorted(info['vg_set'])))

            if key in self._dirty_inst_keys:
                for col in range(self._inst_tree.columnCount()):
                    item.setBackground(col, _DIRTY_BG)

        # Expand all groups
        self._inst_tree.expandAll()
        self._update_count_label()

    # ═══════════════════════════════════════════════════════════════════════
    # Filtering
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_filter(self):
        """Filter instrument items by search text."""
        search = self._search_box.text().lower()

        for gi in range(self._inst_tree.topLevelItemCount()):
            group = self._inst_tree.topLevelItem(gi)
            visible_children = 0

            for ci in range(group.childCount()):
                child = group.child(ci)
                if search:
                    name = child.text(0).lower()
                    match = search in name
                else:
                    match = True
                child.setHidden(not match)
                if match:
                    visible_children += 1

            # Hide the entire group if no children match
            group.setHidden(visible_children == 0)

        self._update_count_label()

    def _update_count_label(self):
        visible = 0
        total = 0
        for gi in range(self._inst_tree.topLevelItemCount()):
            group = self._inst_tree.topLevelItem(gi)
            for ci in range(group.childCount()):
                total += 1
                if not group.child(ci).isHidden():
                    visible += 1
        if visible == total:
            self._count_label.setText(
                f"{total} unique instruments")
        else:
            self._count_label.setText(
                f"Showing {visible} of {total} instruments")

    # ═══════════════════════════════════════════════════════════════════════
    # Selection — show instrument details
    # ═══════════════════════════════════════════════════════════════════════

    def _on_instrument_selected(self, current, previous):
        """User clicked an instrument in the list."""
        if current is None:
            self._clear_details()
            return

        vg_name = current.data(0, _ROLE_VG_NAME)
        slot = current.data(0, _ROLE_SLOT_IDX)

        if not vg_name or slot is None or not self._voicegroup_data:
            self._clear_details()
            return

        vg = self._voicegroup_data.get_voicegroup(vg_name)
        if not vg:
            self._clear_details()
            return

        inst = vg.get_instrument(slot)
        if not inst:
            self._clear_details()
            return

        self._current_instrument = inst
        self._current_vg_name = vg_name
        key = self._inst_identity_key(inst)
        self._info_group.setStyleSheet(
            _DIRTY_SS if key in self._dirty_inst_keys else "")
        self._show_instrument_details(inst, vg)

    def _show_instrument_details(self, inst, vg):
        """Populate the right panel with instrument info and editable controls."""
        self._editing = True
        try:
            self._show_instrument_details_inner(inst, vg)
        finally:
            self._editing = False

    def _show_instrument_details_inner(self, inst, vg):

        self._inst_name_label.setText(inst.friendly_name)
        self._inst_type_label.setText(
            f"{inst.voice_type}  (type 0x{inst.type_byte:02X})")

        # Info line
        if vg:
            info_parts = [f"{vg.name} — Slot {inst.slot_index}"]
            self._detail_info.setText("  |  ".join(info_parts))

        # Base key and pan spinners
        self._edit_base_key.setValue(inst.base_midi_key)
        self._base_key_name.setText(_midi_to_name(inst.base_midi_key))
        self._edit_pan.setValue(inst.pan)
        self._update_pan_desc(inst.pan)

        # Show/hide type-specific controls
        self._square_frame.setVisible(inst.is_square)
        self._noise_frame.setVisible(inst.is_noise)
        self._sample_frame.setVisible(inst.is_directsound)
        self._base_key_hint.setVisible(inst.is_directsound)

        # Keysplits: disable editing (they're routing, not sound)
        is_editable = not inst.is_keysplit
        self._edit_base_key.setEnabled(is_editable)
        self._edit_pan.setEnabled(is_editable)

        if inst.is_directsound and inst.sample_label:
            short_name = inst.sample_label
            if short_name.startswith("DirectSoundWaveData_"):
                short_name = short_name[20:]
            sample_text = f"Sample: {short_name}"
            if 'no_resample' in inst.voice_type:
                sample_text += "  (fixed pitch)"

            # Populate loop controls from sample header
            sample = None
            if self._sample_data:
                sample = self._sample_data.direct_sound.get(inst.sample_label)
                if sample:
                    sample_text += (
                        f"\nRate: {sample.sample_rate} Hz  |  "
                        f"Length: {sample.duration_seconds:.2f}s")

                    rate = sample.header.sample_rate or 1
                    size = sample.header.size
                    loop_bytes = sample.header.loop_start
                    loop_sec = loop_bytes / rate if rate > 0 else 0.0

                    # Block signals to avoid feedback loops
                    self._loop_debounce.stop()
                    self._chk_loop.blockSignals(True)
                    self._spin_loop_seconds.blockSignals(True)

                    self._chk_loop.setChecked(sample.has_loop)
                    max_sec = size / rate if rate > 0 else 999.9999
                    self._loop_validator.setTop(max_sec)
                    self._spin_loop_seconds.setText(f"{loop_sec:.4f}")
                    self._spin_loop_seconds.setEnabled(sample.has_loop)

                    # Detail label: byte offset + percentage
                    if size > 0:
                        pct = loop_bytes * 100 / size
                        self._loop_detail_label.setText(
                            f"(byte {loop_bytes:,} — {pct:.0f}%)")
                    else:
                        self._loop_detail_label.setText("")

                    # Waveform with loop marker
                    loop_frac = loop_bytes / size if size > 0 else 0.0
                    self._loop_waveform.set_sample(
                        sample.pcm_data, loop_frac,
                        enabled=sample.has_loop)

                    self._chk_loop.blockSignals(False)
                    self._spin_loop_seconds.blockSignals(False)

            if sample is None:
                self._loop_waveform.clear()
            self._chk_loop.setEnabled(sample is not None)
            self._spin_loop_seconds.setEnabled(
                sample is not None and self._chk_loop.isChecked())
            self._sample_info_label.setText(sample_text)

        elif inst.is_square:
            self._edit_duty.setCurrentIndex(inst.duty_cycle)
            self._edit_sweep.setValue(inst.sweep)
            # Only square_1 has sweep
            is_sq1 = 'square_1' in inst.voice_type
            self._sweep_label.setVisible(is_sq1)
            self._edit_sweep.setVisible(is_sq1)

        elif inst.is_noise:
            self._edit_period.setCurrentIndex(
                min(inst.period, 1))

        elif inst.is_programmable_wave:
            self._sample_frame.setVisible(True)
            self._sample_info_label.setText(
                f"Waveform: {inst.wave_label or '—'}")

        elif inst.is_keysplit:
            self._sample_frame.setVisible(True)
            ks_text = f"Target: {inst.target_voicegroup or '—'}"
            if inst.keysplit_table:
                ks_text += f"\nTable: {inst.keysplit_table}"
            else:
                ks_text += "\nMode: All (direct note index)"
            self._sample_info_label.setText(ks_text)

        # ADSR sliders
        is_cgb = inst.is_square or inst.is_programmable_wave or inst.is_noise
        if is_cgb:
            # CGB scale: A 0-7, D 0-7, S 0-15, R 0-7
            self._adsr_sliders['attack'].setRange(0, 7)
            self._adsr_sliders['decay'].setRange(0, 7)
            self._adsr_sliders['sustain'].setRange(0, 15)
            self._adsr_sliders['release'].setRange(0, 7)
            self._adsr_scale_label.setText(
                "CGB scale: A 0-7, D 0-7, S 0-15, R 0-7")
        else:
            self._adsr_sliders['attack'].setRange(0, 255)
            self._adsr_sliders['decay'].setRange(0, 255)
            self._adsr_sliders['sustain'].setRange(0, 255)
            self._adsr_sliders['release'].setRange(0, 255)
            self._adsr_scale_label.setText(
                "DirectSound scale: 0-255")

        self._adsr_sliders['attack'].setValue(inst.attack)
        self._adsr_sliders['decay'].setValue(inst.decay)
        self._adsr_sliders['sustain'].setValue(inst.sustain)
        self._adsr_sliders['release'].setValue(inst.release)

        for p in ('attack', 'decay', 'sustain', 'release'):
            self._adsr_labels[p].setText(
                str(self._adsr_sliders[p].value()))
            self._adsr_sliders[p].setEnabled(is_editable)

        self._adsr_display.set_adsr(
            inst.attack, inst.decay, inst.sustain, inst.release,
            is_cgb=is_cgb)

        # Usage info
        self._update_usage_info(inst)

        # Preview
        can_preview = not inst.is_keysplit
        self._btn_play.setEnabled(can_preview)

        # Instrument export
        self._btn_export_inst.setEnabled(inst.is_directsound and bool(inst.sample_label))

    def _update_usage_info(self, inst):
        """Show which voicegroups contain this instrument."""
        key = self._inst_identity_key(inst)
        info = self._unique_instruments.get(key)

        if info:
            vg_list = sorted(info['vg_set'])
            if len(vg_list) == 1:
                self._usage_label.setText(
                    f"Used in: {vg_list[0]}")
            else:
                display = ', '.join(vg_list[:12])
                extra = f" ... and {len(vg_list) - 12} more" if len(vg_list) > 12 else ""
                self._usage_label.setText(
                    f"Used in {len(vg_list)} voicegroups: {display}{extra}")
        else:
            self._usage_label.setText("—")

    def _clear_details(self):
        """Reset the detail panel."""
        self._loop_debounce.stop()
        self._info_group.setStyleSheet("")
        self._editing = True
        self._current_instrument = None
        self._inst_name_label.setText("No instrument selected")
        self._inst_type_label.setText("")
        self._detail_info.setText("")
        self._edit_base_key.setValue(60)
        self._edit_base_key.setEnabled(False)
        self._base_key_name.setText("")
        self._edit_pan.setValue(0)
        self._edit_pan.setEnabled(False)
        self._pan_desc.setText("")
        self._square_frame.hide()
        self._noise_frame.hide()
        self._sample_frame.hide()
        self._base_key_hint.hide()
        self._sample_info_label.setText("")
        self._loop_waveform.clear()
        self._loop_detail_label.setText("")
        self._adsr_display.set_adsr(0, 0, 0, 0)
        for p in ('attack', 'decay', 'sustain', 'release'):
            self._adsr_sliders[p].setValue(0)
            self._adsr_sliders[p].setEnabled(False)
            self._adsr_labels[p].setText("0")
        self._adsr_scale_label.setText("")
        self._usage_label.setText("—")
        self._btn_play.setEnabled(False)
        self._btn_export_inst.setEnabled(False)
        self._preview_status.setText("")
        self._editing = False

    # ═══════════════════════════════════════════════════════════════════════
    # Editing — change handlers
    # ═══════════════════════════════════════════════════════════════════════

    def _tint_inst_row(self, key: str, dirty: bool) -> None:
        bg = _DIRTY_BG if dirty else QColor(0, 0, 0, 0)
        for gi in range(self._inst_tree.topLevelItemCount()):
            group = self._inst_tree.topLevelItem(gi)
            for ci in range(group.childCount()):
                child = group.child(ci)
                if child.data(0, _ROLE_INST_KEY) == key:
                    for col in range(self._inst_tree.columnCount()):
                        child.setBackground(col, bg)
                    return

    def _mark_inst_dirty(self, key: str) -> None:
        self._dirty_inst_keys.add(key)
        self.modified.emit()
        try:
            self._tint_inst_row(key, True)
            self._info_group.setStyleSheet(_DIRTY_SS)
        except Exception as e:
            _log.error("Dirty visual update failed: %s", e)

    def clear_dirty(self) -> None:
        self._dirty_inst_keys.clear()
        self._info_group.setStyleSheet("")
        for gi in range(self._inst_tree.topLevelItemCount()):
            group = self._inst_tree.topLevelItem(gi)
            for ci in range(group.childCount()):
                child = group.child(ci)
                for col in range(self._inst_tree.columnCount()):
                    child.setBackground(col, QColor(0, 0, 0, 0))

    def _apply_to_all_copies(self, attr: str, value):
        """Apply a property change to every copy of this instrument
        across all voicegroups (they're the same sound, just shared)."""
        inst = self._current_instrument
        if not inst:
            return

        key = self._inst_identity_key(inst)
        self._mark_inst_dirty(key)

        if not self._voicegroup_data:
            setattr(inst, attr, value)
            return

        info = self._unique_instruments.get(key)
        changed_vgs = []
        if info:
            for vg_name in info['vg_set']:
                vg = self._voicegroup_data.get_voicegroup(vg_name)
                if not vg:
                    continue
                for other in vg.instruments:
                    if self._inst_identity_key(other) == key:
                        setattr(other, attr, value)
                changed_vgs.append(vg_name)
        else:
            setattr(inst, attr, value)

        if changed_vgs and self._voicegroups_tab_ref:
            self._voicegroups_tab_ref.mark_voicegroups_dirty(changed_vgs)

    def _update_pan_desc(self, pan: int):
        """Update the pan description label."""
        if pan == 0:
            self._pan_desc.setText("Center")
        elif pan < 64:
            self._pan_desc.setText(f"Left {64 - pan}")
        elif pan > 64:
            self._pan_desc.setText(f"Right {pan - 64}")
        else:
            self._pan_desc.setText("Center")

    def _on_base_key_changed(self, value: int):
        if self._editing or not self._current_instrument:
            return
        self._base_key_name.setText(_midi_to_name(value))
        self._apply_to_all_copies('base_midi_key', value)

    def _on_pan_changed(self, value: int):
        if self._editing or not self._current_instrument:
            return
        self._update_pan_desc(value)
        self._apply_to_all_copies('pan', value)

    def _on_duty_changed(self, index: int):
        if self._editing or not self._current_instrument:
            return
        self._apply_to_all_copies('duty_cycle', index)

    def _on_sweep_changed(self, value: int):
        if self._editing or not self._current_instrument:
            return
        self._apply_to_all_copies('sweep', value)

    def _on_period_changed(self, index: int):
        if self._editing or not self._current_instrument:
            return
        self._apply_to_all_copies('period', index)

    def _on_adsr_changed(self, param: str, value: int):
        if self._editing or not self._current_instrument:
            return
        self._adsr_labels[param].setText(str(value))
        self._apply_to_all_copies(param, value)

        # Update the visual envelope
        inst = self._current_instrument
        is_cgb = inst.is_square or inst.is_programmable_wave or inst.is_noise
        self._adsr_display.set_adsr(
            self._adsr_sliders['attack'].value(),
            self._adsr_sliders['decay'].value(),
            self._adsr_sliders['sustain'].value(),
            self._adsr_sliders['release'].value(),
            is_cgb=is_cgb,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Sample loop controls
    # ═══════════════════════════════════════════════════════════════════════

    def _get_current_sample(self):
        """Get the DirectSoundSample for the current instrument, or None."""
        inst = self._current_instrument
        if not inst or not inst.sample_label or not self._sample_data:
            return None
        sample = self._sample_data.direct_sound.get(inst.sample_label)
        if not sample or not sample.pcm_data:
            return None
        return sample

    def _write_loop_to_bin(self, sample, loop_on: bool, loop_start: int):
        """Write updated loop settings to the .bin file and update memory."""
        from core.sound.sample_loader import _write_gba_bin
        _write_gba_bin(
            sample.file_path,
            sample.header.sample_rate,
            sample.pcm_data,
            loop=loop_on,
            loop_start=loop_start,
        )
        sample.header.status = 0x4000 if loop_on else 0
        sample.header.loop_start = loop_start

    def _update_loop_ui(self, sample, loop_on: bool, loop_bytes: int):
        """Sync all loop-related UI widgets after a change."""
        rate = sample.header.sample_rate or 1
        size = sample.header.size

        # Time field
        self._loop_debounce.stop()
        self._spin_loop_seconds.blockSignals(True)
        self._spin_loop_seconds.setText(f"{loop_bytes / rate:.4f}")
        self._spin_loop_seconds.setEnabled(loop_on)
        self._spin_loop_seconds.blockSignals(False)

        # Detail label
        if size > 0:
            pct = loop_bytes * 100 / size
            self._loop_detail_label.setText(
                f"(byte {loop_bytes:,} — {pct:.0f}%)")
        else:
            self._loop_detail_label.setText("")

        # Waveform marker
        frac = loop_bytes / size if size > 0 else 0.0
        self._loop_waveform.set_loop_frac(frac)
        self._loop_waveform._enabled = loop_on
        self._loop_waveform.update()

    def _mark_loop_dirty(self):
        """Mark all voicegroups that use the current instrument as dirty.
        Loop changes write to the .bin file but don't go through
        _apply_to_all_copies, so we need to mark dirty separately."""
        inst = self._current_instrument
        if not inst or not self._voicegroup_data:
            return
        key = self._inst_identity_key(inst)
        info = self._unique_instruments.get(key)
        if not info:
            return
        changed_vgs = list(info['vg_set'])
        if changed_vgs and self._voicegroups_tab_ref:
            self._voicegroups_tab_ref.mark_voicegroups_dirty(changed_vgs)

    def _on_loop_toggled(self, state):
        """User toggled the loop checkbox — update the .bin file header."""
        if self._editing:
            return
        sample = self._get_current_sample()
        if not sample:
            return

        loop_on = bool(state)
        loop_start = sample.header.loop_start if loop_on else 0

        try:
            self._write_loop_to_bin(sample, loop_on, loop_start)
            self._update_loop_ui(sample, loop_on, loop_start)
            _log.info("Loop %s for %s", "enabled" if loop_on else "disabled",
                       self._current_instrument.sample_label)
        except Exception as e:
            _log.error("Failed to update loop setting: %s", e)
            QMessageBox.warning(self, "Loop Error",
                                f"Failed to update sample loop:\n{e}")
            return
        self._mark_loop_dirty()
        inst = self._current_instrument
        if inst:
            self._mark_inst_dirty(self._inst_identity_key(inst))

    def _on_loop_seconds_changed(self):
        """User entered a loop time in seconds — convert to bytes and save."""
        if self._editing:
            return
        sample = self._get_current_sample()
        if not sample or not self._chk_loop.isChecked():
            return

        try:
            seconds = float(self._spin_loop_seconds.text())
        except (ValueError, AttributeError):
            return

        rate = sample.header.sample_rate or 1
        loop_bytes = max(0, min(round(seconds * rate), sample.header.size - 1))

        try:
            self._write_loop_to_bin(sample, True, loop_bytes)
            self._update_loop_ui(sample, True, loop_bytes)
        except Exception as e:
            _log.error("Failed to update loop start: %s", e)
            QMessageBox.warning(self, "Loop Error",
                                f"Failed to update loop point:\n{e}")
            return
        self._mark_loop_dirty()
        inst = self._current_instrument
        if inst:
            self._mark_inst_dirty(self._inst_identity_key(inst))

    def _on_loop_waveform_dragged(self, frac: float):
        """User dragged the loop marker on the waveform."""
        if self._editing:
            return
        sample = self._get_current_sample()
        if not sample or not self._chk_loop.isChecked():
            return

        size = sample.header.size
        loop_bytes = max(0, min(int(frac * size), size - 1))

        try:
            self._write_loop_to_bin(sample, True, loop_bytes)
            # Update time spinner and detail label (waveform already updated by drag)
            rate = sample.header.sample_rate or 1
            self._loop_debounce.stop()
            self._spin_loop_seconds.blockSignals(True)
            self._spin_loop_seconds.setText(f"{loop_bytes / rate:.4f}")
            self._spin_loop_seconds.blockSignals(False)
            if size > 0:
                pct = loop_bytes * 100 / size
                self._loop_detail_label.setText(
                    f"(byte {loop_bytes:,} — {pct:.0f}%)")
        except Exception as e:
            _log.error("Failed to update loop start: %s", e)
            return
        self._mark_loop_dirty()
        inst = self._current_instrument
        if inst:
            self._mark_inst_dirty(self._inst_identity_key(inst))

    # ═══════════════════════════════════════════════════════════════════════
    # Preview playback
    # ═══════════════════════════════════════════════════════════════════════

    def _on_piano_key(self, midi_note: int):
        """User pressed a piano key — start sustained preview."""
        self._preview_note_label.setText(
            f"{_midi_to_name(midi_note)} ({midi_note})")
        self._play_instrument_note(midi_note, sustain=True)

    def _on_piano_key_released(self, midi_note: int):
        """User released a piano key — stop playback."""
        if self._audio_player:
            self._audio_player.stop()
            self._audio_player = None

    def _on_play_preview(self):
        """Play button clicked — short preview at middle C (60)."""
        self._play_instrument_note(60, sustain=False)

    def _on_octave_down(self):
        """Shift piano keyboard down one octave."""
        cur = self._piano.start_octave
        if cur > 0:
            self._piano.set_start_octave(cur - 1)
            self._update_piano_range_label()

    def _on_octave_up(self):
        """Shift piano keyboard up one octave."""
        cur = self._piano.start_octave
        self._piano.set_start_octave(cur + 1)
        self._update_piano_range_label()

    def _update_piano_range_label(self):
        """Update the label showing the current piano range."""
        low = self._piano.start_octave
        high = low + self._piano._num_octaves - 1
        self._piano_range_label.setText(f"C{low} – B{high}")

    def _play_instrument_note(self, midi_note: int, sustain: bool = False):
        """Render and play the current instrument at the given MIDI note.

        If sustain=True, renders a long note (10 seconds) so the user can
        hear the loop sustain. Playback stops on key release.
        If sustain=False, renders a short 800ms preview.
        """
        inst = self._current_instrument
        if inst is None or inst.is_keysplit:
            return

        if not self._voicegroup_data:
            return

        # Lazy-load samples if needed
        if not self._sample_data and self._project_root:
            self._preview_status.setText("Loading samples...")
            try:
                from core.sound.sample_loader import load_sample_data
                self._sample_data = load_sample_data(
                    self._project_root, load_pcm=True)
                self._preview_status.setText("")
            except Exception as e:
                self._preview_status.setText(f"Failed: {e}")
                return

        if not self._sample_data:
            self._preview_status.setText("No sample data available")
            return

        # Stop any existing preview
        if self._audio_player:
            self._audio_player.stop()
            self._audio_player = None

        self._preview_status.setText("Rendering...")
        self._btn_play.setEnabled(False)

        # Sustain = long render so you can hear the loop
        duration_ms = 10000 if sustain else 800

        # Render in a thread to keep UI responsive
        sample_data = self._sample_data
        vg_data = self._voicegroup_data

        def _do_preview():
            try:
                from core.sound.track_renderer import render_instrument_preview
                audio = render_instrument_preview(
                    inst, midi_note, sample_data, vg_data,
                    duration_ms=duration_ms, velocity=100)
                self._preview_audio = audio
                self._preview_done.emit()
            except Exception as e:
                self._preview_failed.emit(str(e))

        t = threading.Thread(target=_do_preview, daemon=True)
        t.start()

    def _on_preview_ready(self):
        """Preview render finished — play it."""
        self._preview_status.setText("")
        self._btn_play.setEnabled(True)

        audio = getattr(self, '_preview_audio', None)
        if audio is None:
            return

        try:
            from core.sound.audio_engine import AudioPlayer, OUTPUT_SAMPLE_RATE
            self._audio_player = AudioPlayer()
            self._audio_player.volume = 0.8
            from PyQt6.QtCore import QSettings
            _mono = QSettings("PorySuite", "PorySuiteZ").value(
                "sound/output_mode", "Stereo") == "Mono"
            self._audio_player.play(audio, OUTPUT_SAMPLE_RATE, mono=_mono)
        except Exception as e:
            self._preview_status.setText(f"Playback failed: {e}")

    def _on_preview_error(self, msg: str):
        """Preview render failed."""
        self._preview_status.setText(f"Preview failed: {msg}")
        self._btn_play.setEnabled(True)

    # ═══════════════════════════════════════════════════════════════════════
    # Sample management
    # ═══════════════════════════════════════════════════════════════════════

    def _on_export_sample(self):
        """Export the current instrument's sample to a WAV file."""
        inst = self._current_instrument
        if not inst or not inst.is_directsound or not inst.sample_label:
            return
        if not self._sample_data:
            QMessageBox.warning(
                self, "No Sample Data",
                "Sample data hasn't been loaded yet. "
                "Try playing a note first to trigger loading.")
            return

        from core.sound.sample_loader import (
            get_sample_for_instrument, export_sample_to_wav)

        sample = get_sample_for_instrument(self._sample_data, inst.sample_label)
        if not sample or not sample.pcm_data:
            QMessageBox.warning(
                self, "No Audio Data",
                f"Sample '{inst.sample_label}' has no audio data loaded.")
            return

        # Suggest a filename
        short = inst.sample_label
        if short.startswith('DirectSoundWaveData_'):
            short = short[20:]

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sample as WAV",
            short + ".wav",
            "WAV Files (*.wav)")
        if not path:
            return

        try:
            export_sample_to_wav(sample, path)
            QMessageBox.information(
                self, "Exported",
                f"Sample exported to:\n{path}")
        except Exception as e:
            QMessageBox.critical(
                self, "Export Failed", str(e))

    def _on_replace_sample(self):
        """Replace the current sample's audio with a new WAV file."""
        inst = self._current_instrument
        if not inst or not inst.is_directsound or not inst.sample_label:
            return
        if not self._project_root:
            return
        if not self._sample_data:
            QMessageBox.warning(
                self, "No Sample Data",
                "Sample data hasn't been loaded yet.")
            return

        from core.sound.sample_loader import (
            get_sample_for_instrument, replace_sample_from_wav)

        sample = get_sample_for_instrument(self._sample_data, inst.sample_label)
        if not sample:
            QMessageBox.warning(
                self, "Sample Not Found",
                f"Could not find sample '{inst.sample_label}' in loaded data.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Replacement WAV",
            "", "WAV Files (*.wav)")
        if not path:
            return

        from core.sound.sample_loader import peek_wav_info

        try:
            info = peek_wav_info(path)
        except Exception as e:
            QMessageBox.critical(self, "Bad WAV", str(e))
            return

        wav_rate = info['rate']
        wav_dur = info['duration']
        orig_rate = sample.header.sample_rate

        # Build rate options — always let the user choose
        target_rate = 0  # 0 = keep original rate
        options = []

        # Offer to match original rate first (preserves pitch in-game)
        if wav_rate != orig_rate:
            match_size = int(wav_dur * orig_rate) + 16
            options.append(
                f"Match original ({orig_rate} Hz) — "
                f"{match_size / 1024:.1f} KB")

        # Keep WAV's native rate
        raw_size = info['mono_8bit_size'] + 16
        options.append(
            f"Keep WAV rate ({wav_rate} Hz) — "
            f"{raw_size / 1024:.1f} KB")

        # Standard downsample tiers
        for tier_rate, tier_desc in [
            (22050, "good quality"),
            (13379, "typical GBA"),
            (8000,  "small / lo-fi"),
        ]:
            if wav_rate > tier_rate and tier_rate != orig_rate:
                tier_size = int(wav_dur * tier_rate) + 16
                options.append(
                    f"Downsample to {tier_rate} Hz ({tier_desc}) — "
                    f"{tier_size / 1024:.1f} KB")

        choice, ok = QInputDialog.getItem(
            self, "Replace Sample — Rate & Size",
            f"Replacing '{sample.friendly_name}' audio.\n"
            f"Original rate: {orig_rate} Hz\n"
            f"New WAV: {wav_rate} Hz, {wav_dur:.2f}s\n\n"
            f"Loop settings will be preserved (scaled to new length).\n\n"
            f"Choose a sample rate:",
            options, 0, False)
        if not ok:
            return

        # Parse the chosen rate
        if "Match original" in choice:
            target_rate = orig_rate
        elif "Keep WAV rate" in choice:
            target_rate = wav_rate
        elif "22050" in choice:
            target_rate = 22050
        elif "13379" in choice:
            target_rate = 13379
        elif "8000" in choice:
            target_rate = 8000

        try:
            replace_sample_from_wav(path, sample, target_rate=target_rate)
            # Refresh the detail display
            self._show_instrument_details(inst, None)
            self._mark_inst_dirty(self._inst_identity_key(inst))
            QMessageBox.information(
                self, "Replaced",
                f"Sample '{sample.friendly_name}' audio replaced.\n\n"
                f"Rate: {sample.header.sample_rate} Hz\n"
                f"Size: {os.path.getsize(sample.file_path) / 1024:.1f} KB")
        except Exception as e:
            QMessageBox.critical(
                self, "Replace Failed", str(e))

    def _on_delete_sample(self):
        """Delete the current sample (only if unused)."""
        inst = self._current_instrument
        if not inst or not inst.is_directsound or not inst.sample_label:
            return
        if not self._project_root or not self._sample_data:
            return

        from core.sound.sample_loader import (
            get_sample_for_instrument, delete_sample, get_sample_references)

        sample = get_sample_for_instrument(self._sample_data, inst.sample_label)
        if not sample:
            return

        # Check references first
        refs = get_sample_references(
            inst.sample_label, self._voicegroup_data)
        if refs:
            vg_names = sorted(set(r[0] for r in refs))
            QMessageBox.warning(
                self, "Sample In Use",
                f"Cannot delete '{sample.friendly_name}' — it is used by "
                f"{len(refs)} instrument slot(s) in {len(vg_names)} "
                f"voicegroup(s):\n\n"
                + ', '.join(vg_names[:10])
                + (' ...' if len(vg_names) > 10 else ''))
            return

        reply = QMessageBox.question(
            self, "Delete Sample?",
            f"Delete '{sample.friendly_name}'?\n\n"
            "This will remove the .bin file and its entry in "
            "direct_sound_data.inc.\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            delete_sample(
                self._project_root, sample,
                self._sample_data, self._voicegroup_data)
            self._clear_details()
            self._populate_instrument_list()
            self.modified.emit()
            QMessageBox.information(
                self, "Deleted",
                f"Sample '{sample.friendly_name}' has been removed.")
        except Exception as e:
            QMessageBox.critical(
                self, "Delete Failed", str(e))

    def _assign_sample_to_slot(self, sample_label: str, new_instrument: bool = False):
        """Assign a sample to an instrument slot.

        If a DirectSound instrument is currently selected AND new_instrument is
        False, reassigns that slot's sample to sample_label.
        Otherwise, asks the user which voicegroup to add it to and finds
        a filler slot to replace.

        new_instrument=True is used when importing a brand-new sample — it must
        always find a fresh filler slot instead of clobbering the selected one.

        Returns the target Instrument or None.
        """
        # If a DirectSound instrument is selected, use that slot — but only
        # when reassigning, not when creating a new instrument (import path).
        inst = self._current_instrument
        if inst and inst.is_directsound and not new_instrument:
            self._apply_to_all_copies('sample_label', sample_label)
            return inst

        # No suitable slot selected — ask the user which voicegroup
        if not self._voicegroup_data:
            return None

        vg_names = sorted(self._voicegroup_data.voicegroups.keys())
        if not vg_names:
            return None

        vg_name, ok = QInputDialog.getItem(
            self, "Add to Voicegroup",
            "Which voicegroup should this new instrument be added to?",
            vg_names, 0, False)
        if not ok:
            return None

        vg = self._voicegroup_data.get_voicegroup(vg_name)
        if not vg:
            return None

        # Find a filler slot to replace
        filler_idx = None
        for i, slot_inst in enumerate(vg.instruments):
            if _is_filler_instrument(slot_inst):
                filler_idx = i
                break

        if filler_idx is None:
            QMessageBox.warning(
                self, "No Empty Slots",
                f"All 128 slots in {vg_name} are in use.\n"
                f"Pick a different voicegroup or replace an existing "
                f"instrument.")
            return None

        # Set up the filler slot as a new DirectSound instrument
        target = vg.instruments[filler_idx]
        target.voice_type = 'voice_directsound'
        target.type_byte = 0x00
        target.sample_label = sample_label
        target.base_midi_key = 60
        target.pan = 0
        target.attack = 255
        target.decay = 0
        target.sustain = 255
        target.release = 0

        # Mark the voicegroup dirty so it gets saved
        if self._voicegroups_tab_ref:
            self._voicegroups_tab_ref.mark_voicegroups_dirty([vg_name])

        return target

    def _on_import_sample(self):
        """Import a new WAV file as a GBA DirectSound sample."""
        if not self._project_root or not self._sample_data:
            QMessageBox.warning(
                self, "Not Ready",
                "Project data must be loaded first.")
            return

        # Pick the WAV file
        path, _ = QFileDialog.getOpenFileName(
            self, "Select WAV File to Import",
            "", "WAV Files (*.wav)")
        if not path:
            return

        from core.sound.sample_loader import peek_wav_info

        # ── Peek at the WAV early so we have rate/size for both paths ────
        try:
            info = peek_wav_info(path)
        except Exception as e:
            QMessageBox.critical(self, "Bad WAV", str(e))
            return

        wav_rate = info['rate']
        wav_dur = info['duration']
        raw_size = info['mono_8bit_size'] + 16  # +16 for header

        # ── If a DS instrument is selected, offer to replace it in-place ──
        inst = self._current_instrument
        has_ds_slot = (inst is not None
                       and inst.is_directsound
                       and bool(inst.sample_label)
                       and self._sample_data is not None
                       and inst.sample_label in self._sample_data.direct_sound)

        if has_ds_slot:
            msg = QMessageBox(self)
            msg.setWindowTitle("Replace or Add New?")
            msg.setText(
                f"A DirectSound instrument is already selected:\n"
                f"{inst.friendly_name}\n\n"
                f"Do you want to replace its audio with the new WAV, "
                f"or add it as a completely new instrument?")
            btn_replace = msg.addButton("Replace Selected",
                                        QMessageBox.ButtonRole.AcceptRole)
            btn_add = msg.addButton("Add as New Instrument",
                                    QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is None:
                return

            if clicked is btn_replace:
                # ── Replace in-place path ─────────────────────────────────
                existing = self._sample_data.direct_sound[inst.sample_label]
                orig_rate = existing.header.sample_rate
                orig_size = len(existing.pcm_data) + 16

                # Build rate options for the replace dialog
                replace_options = []
                if wav_rate != orig_rate:
                    match_size = int(wav_dur * orig_rate) + 16
                    replace_options.append(
                        f"Match original ({orig_rate} Hz) — "
                        f"{match_size / 1024:.1f} KB")
                replace_options.append(
                    f"Keep WAV rate ({wav_rate} Hz) — "
                    f"{raw_size / 1024:.1f} KB")
                for tier_rate, tier_desc in [
                    (22050, "good quality"),
                    (13379, "typical GBA"),
                    (8000,  "small / lo-fi"),
                ]:
                    if wav_rate > tier_rate and tier_rate != orig_rate:
                        tier_size = int(wav_dur * tier_rate) + 16
                        replace_options.append(
                            f"Downsample to {tier_rate} Hz ({tier_desc}) — "
                            f"{tier_size / 1024:.1f} KB")

                size_warn = ""
                if orig_size > 10 * 1024:
                    size_warn = (
                        f"\n⚠ Original sample is {orig_size / 1024:.1f} KB — "
                        f"large for a GBA instrument.\n")

                choice, ok = QInputDialog.getItem(
                    self, "Replace Sample — Rate & Size",
                    f"Replacing '{existing.friendly_name}' audio.\n"
                    f"Original rate: {orig_rate} Hz\n"
                    f"New WAV: {wav_rate} Hz, {wav_dur:.2f}s\n"
                    f"{size_warn}\n"
                    f"Loop settings will be preserved (scaled to new length).\n\n"
                    f"Choose a sample rate:",
                    replace_options, 0, False)
                if not ok:
                    return

                target_rate = orig_rate  # default: match original
                if "Match original" in choice:
                    target_rate = orig_rate
                elif "Keep WAV rate" in choice:
                    target_rate = wav_rate
                elif "22050" in choice:
                    target_rate = 22050
                elif "13379" in choice:
                    target_rate = 13379
                elif "8000" in choice:
                    target_rate = 8000

                try:
                    from core.sound.sample_loader import replace_sample_from_wav
                    replace_sample_from_wav(path, existing,
                                            target_rate=target_rate)
                    self._show_instrument_details(inst, None)
                    self._mark_inst_dirty(self._inst_identity_key(inst))
                    final_rate = existing.header.sample_rate
                    final_size = os.path.getsize(existing.file_path)
                    QMessageBox.information(
                        self, "Replaced",
                        f"Sample '{existing.friendly_name}' audio replaced.\n\n"
                        f"Rate: {final_rate} Hz\n"
                        f"Size: {final_size / 1024:.1f} KB")
                except Exception as e:
                    QMessageBox.critical(self, "Replace Failed", str(e))
                return
            # else: user chose "Add as New Instrument" — fall through below

        # ── Add as new instrument path ─────────────────────────────────────
        # Ask for a name
        suggested = os.path.splitext(os.path.basename(path))[0]
        # Clean up the name for GBA label safety
        suggested = re.sub(r'[^a-zA-Z0-9_]', '_', suggested).strip('_')

        name, ok = QInputDialog.getText(
            self, "Sample Name",
            "Enter a name for the new sample.\n"
            "This becomes the label suffix (letters, numbers, underscores).\n"
            "Example: 'my_trumpet' creates 'DirectSoundWaveData_my_trumpet'",
            text=suggested)
        if not ok or not name:
            return

        # Sanitize
        name = re.sub(r'[^a-zA-Z0-9_]', '_', name).strip('_')
        if not name:
            QMessageBox.warning(self, "Invalid Name",
                                "The name must contain at least one letter.")
            return

        from core.sound.sample_loader import import_wav_as_sample

        # Always show rate/size options — even "GBA-friendly" rates
        # can be wasteful for long samples.  Let the user decide.
        target_rate = 0  # 0 = keep original

        size_original = raw_size
        options = []
        options.append(
            f"Keep original ({wav_rate} Hz) — "
            f"{size_original / 1024:.1f} KB")

        # Offer standard downsample tiers (only those below current rate)
        for tier_rate, tier_desc in [
            (22050, "good quality"),
            (13379, "typical GBA"),
            (8000,  "small / lo-fi"),
        ]:
            if wav_rate > tier_rate:
                tier_size = int(wav_dur * tier_rate) + 16
                options.append(
                    f"Downsample to {tier_rate} Hz ({tier_desc}) — "
                    f"{tier_size / 1024:.1f} KB")

        # Size warning for large samples
        size_warn = ""
        if size_original > 10 * 1024:
            size_warn = (
                f"\n⚠ {size_original / 1024:.1f} KB is large for a GBA "
                f"instrument.\nMost built-in samples are 1–5 KB. "
                f"Consider a lower rate.\n")

        choice, ok = QInputDialog.getItem(
            self, "Sample Rate & ROM Space",
            f"'{os.path.basename(path)}' — "
            f"{wav_rate} Hz, {wav_dur:.2f}s\n"
            f"At full rate: {size_original / 1024:.1f} KB of ROM space.\n"
            f"{size_warn}\n"
            f"GBA instruments typically use 8,000–13,379 Hz.\n"
            f"Lower rates save ROM space but reduce audio quality.\n\n"
            f"Choose a sample rate:",
            options, 0, False)
        if not ok:
            return

        # Parse which rate they picked
        if "Keep original" in choice:
            target_rate = 0
        elif "22050" in choice:
            target_rate = 22050
        elif "13379" in choice:
            target_rate = 13379
        elif "8000" in choice:
            target_rate = 8000

        # ── Do the import ─────────────────────────────────────────────────
        try:
            full_label = f"DirectSoundWaveData_{name}"

            # Check if this sample already exists
            if full_label in self._sample_data.direct_sound:
                existing = self._sample_data.direct_sound[full_label]
                # Replace the old audio with the new WAV
                from core.sound.sample_loader import replace_sample_from_wav
                replace_sample_from_wav(
                    path, existing,
                    target_rate=target_rate if target_rate > 0
                    else existing.header.sample_rate)
                new_sample = existing
            else:
                new_sample = import_wav_as_sample(
                    self._project_root, path, name, self._sample_data,
                    target_rate=target_rate)

            final_size = os.path.getsize(new_sample.file_path)
            final_rate = new_sample.header.sample_rate

            # Assign the new sample to an instrument slot — new_instrument=True
            # so a fresh filler slot is found rather than clobbering the
            # currently selected instrument.
            target_inst = self._assign_sample_to_slot(new_sample.label,
                                                       new_instrument=True)

            self._populate_instrument_list()
            if target_inst:
                self._show_instrument_details(target_inst, None)
                self._mark_inst_dirty(self._inst_identity_key(target_inst))
            else:
                self.modified.emit()

            QMessageBox.information(
                self, "Imported",
                f"Sample '{new_sample.friendly_name}' imported.\n\n"
                f"Rate: {final_rate} Hz\n"
                f"ROM space: {final_size / 1024:.1f} KB")
        except Exception as e:
            QMessageBox.critical(
                self, "Import Failed", str(e))

    # ═══════════════════════════════════════════════════════════════════════
    # Instrument Export / Import (.psinst)
    # ═══════════════════════════════════════════════════════════════════════

    def _on_export_instrument(self):
        """Export the current instrument as a .psinst file.

        The .psinst file is a zip containing:
          - instrument.json — all instrument settings
          - the .bin sample file (GBA WaveData header + signed 8-bit PCM)
        """
        inst = self._current_instrument
        if not inst or not inst.is_directsound:
            return

        sample = self._get_current_sample()
        if not sample:
            QMessageBox.warning(
                self, "No Sample",
                "This instrument has no sample data to export.")
            return

        # Suggest a filename from the instrument's friendly name
        suggested_name = re.sub(r'[^a-zA-Z0-9_ -]', '_',
                                inst.friendly_name).strip('_ ')
        if not suggested_name:
            suggested_name = "instrument"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Instrument Preset",
            suggested_name + ".psinst",
            "PorySuite Instrument (*.psinst)")
        if not path:
            return

        import json
        import zipfile

        try:
            # Build the manifest
            manifest = {
                'format': 'psinst',
                'version': 1,
                'name': inst.friendly_name,
                'voice_type': inst.voice_type,
                'type_byte': inst.type_byte,
                'base_midi_key': inst.base_midi_key,
                'pan': inst.pan,
                'attack': inst.attack,
                'decay': inst.decay,
                'sustain': inst.sustain,
                'release': inst.release,
                'sample_label_suffix': sample.friendly_name.replace(' ', '_').lower(),
                'sample_rate': sample.sample_rate,
                'loop_enabled': sample.has_loop,
                'loop_start_bytes': sample.header.loop_start,
                'sample_size_bytes': sample.header.size,
            }

            # Read the raw .bin file
            bin_filename = os.path.basename(sample.file_path)
            with open(sample.file_path, 'rb') as f:
                bin_data = f.read()

            # Write the .psinst zip
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('instrument.json',
                            json.dumps(manifest, indent=2))
                zf.writestr(bin_filename, bin_data)

            QMessageBox.information(
                self, "Exported",
                f"Instrument preset saved to:\n{path}\n\n"
                f"Contains: {inst.friendly_name}\n"
                f"Sample: {bin_filename} "
                f"({len(bin_data) / 1024:.1f} KB)")

        except Exception as e:
            QMessageBox.critical(
                self, "Export Failed", str(e))

    def _on_import_instrument(self):
        """Import a .psinst file as a new instrument.

        Copies the .bin sample into the project, registers it in
        direct_sound_data.inc, and applies all instrument settings.
        If a DirectSound slot is selected, uses that. Otherwise,
        asks which voicegroup to add it to and finds a filler slot.
        """
        if not self._project_root or not self._sample_data:
            QMessageBox.warning(
                self, "Not Ready",
                "Project data must be loaded first.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Import Instrument Preset",
            "", "PorySuite Instrument (*.psinst)")
        if not path:
            return

        import json
        import zipfile
        from core.sound.sample_loader import (
            read_bin_sample, DirectSoundSample)

        try:
            with zipfile.ZipFile(path, 'r') as zf:
                # Read and validate the manifest
                if 'instrument.json' not in zf.namelist():
                    raise ValueError(
                        "Invalid .psinst file — missing instrument.json")

                manifest = json.loads(zf.read('instrument.json'))

                if manifest.get('format') != 'psinst':
                    raise ValueError(
                        "Invalid .psinst file — wrong format field")

                # Find the .bin file in the archive
                bin_files = [n for n in zf.namelist()
                             if n.endswith('.bin')]
                if not bin_files:
                    raise ValueError(
                        "Invalid .psinst file — no .bin sample found")
                bin_archive_name = bin_files[0]

                # Ask the user for a sample name (label suffix)
                suggested = manifest.get('sample_label_suffix', 'imported')
                suggested = re.sub(r'[^a-zA-Z0-9_]', '_', suggested).strip('_')

                name, ok = QInputDialog.getText(
                    self, "Sample Name",
                    f"Importing: {manifest.get('name', 'Unknown')}\n\n"
                    f"Enter a name for the sample in this project.\n"
                    f"(letters, numbers, underscores)\n"
                    f"Example: 'zoot_clarinet' creates "
                    f"'DirectSoundWaveData_zoot_clarinet'",
                    text=suggested)
                if not ok or not name:
                    return

                name = re.sub(r'[^a-zA-Z0-9_]', '_', name).strip('_')
                if not name:
                    QMessageBox.warning(
                        self, "Invalid Name",
                        "The name must contain at least one letter.")
                    return

                label = f"DirectSoundWaveData_{name}"

                # Check for conflicts
                if label in self._sample_data.direct_sound:
                    reply = QMessageBox.question(
                        self, "Sample Already Exists",
                        f"A sample named '{label}' already exists.\n\n"
                        f"Use the existing sample instead of importing "
                        f"a new copy?",
                        QMessageBox.StandardButton.Yes |
                        QMessageBox.StandardButton.No)
                    if reply == QMessageBox.StandardButton.Yes:
                        # Use existing — just apply settings below
                        new_sample = self._sample_data.direct_sound[label]
                    else:
                        return
                else:
                    # Write .bin to project
                    bin_name = f"{name}.bin"
                    bin_rel = f"sound/direct_sound_samples/{bin_name}"
                    bin_abs = os.path.join(
                        self._project_root, 'sound',
                        'direct_sound_samples', bin_name)

                    if os.path.exists(bin_abs):
                        raise ValueError(
                            f"File already exists: {bin_abs}\n"
                            f"Choose a different name.")

                    bin_data = zf.read(bin_archive_name)
                    with open(bin_abs, 'wb') as f:
                        f.write(bin_data)

                    # Register in direct_sound_data.inc
                    inc_path = os.path.join(
                        self._project_root, 'sound',
                        'direct_sound_data.inc')
                    entry = (f"\n\t.align 2\n{label}::\n"
                             f"\t.incbin \"{bin_rel}\"\n")
                    with open(inc_path, 'a', encoding='utf-8') as f:
                        f.write(entry)

                    # Load the new sample into memory
                    header, pcm_loaded = read_bin_sample(bin_abs)
                    new_sample = DirectSoundSample(
                        label=label,
                        file_path=bin_abs,
                        header=header,
                        pcm_data=pcm_loaded,
                    )
                    self._sample_data.direct_sound[label] = new_sample
                    self._sample_data._ds_label_to_path[label] = bin_rel

            # Assign the sample to an instrument slot — new_instrument=True
            # so a fresh filler slot is found rather than clobbering the
            # currently selected instrument.
            target_inst = self._assign_sample_to_slot(label, new_instrument=True)
            if not target_inst:
                # User cancelled voicegroup selection — sample is still
                # imported, they can assign it later
                self._populate_instrument_list()
                return

            # Apply full instrument settings from the manifest
            settings = {
                'base_midi_key': manifest.get('base_midi_key', 60),
                'pan': manifest.get('pan', 0),
                'attack': manifest.get('attack', 0),
                'decay': manifest.get('decay', 0),
                'sustain': manifest.get('sustain', 0),
                'release': manifest.get('release', 0),
            }
            # If we used _assign_sample_to_slot with existing instrument,
            # apply to all copies. Otherwise the target is already set up.
            if self._current_instrument and target_inst == self._current_instrument:
                for attr, val in settings.items():
                    self._apply_to_all_copies(attr, val)
            else:
                for attr, val in settings.items():
                    setattr(target_inst, attr, val)

            # Refresh the display to show the new instrument
            self._populate_instrument_list()
            self._show_instrument_details(target_inst, None)
            self._mark_inst_dirty(self._inst_identity_key(target_inst))

            QMessageBox.information(
                self, "Imported",
                f"Instrument preset applied!\n\n"
                f"Sample: {new_sample.friendly_name}\n"
                f"Rate: {new_sample.sample_rate} Hz\n"
                f"Loop: {'Yes' if manifest.get('loop_enabled') else 'No'}\n"
                f"ADSR: {manifest.get('attack', 0)} / "
                f"{manifest.get('decay', 0)} / "
                f"{manifest.get('sustain', 0)} / "
                f"{manifest.get('release', 0)}")

        except Exception as e:
            QMessageBox.critical(
                self, "Import Failed", str(e))

    # ═══════════════════════════════════════════════════════════════════════
    # Public API — programmatic selection
    # ═══════════════════════════════════════════════════════════════════════

    def select_instrument(self, vg_name: str, slot_index: int) -> bool:
        """Select an instrument in the tree by voicegroup name and slot index.

        Used by the Songs tab "Go to Instrument" feature.
        Returns True if the instrument was found and selected.
        """
        if not self._voicegroup_data:
            return False

        # Look up the instrument to get its identity key
        vg = self._voicegroup_data.get_voicegroup(vg_name)
        if not vg:
            return False
        inst = vg.get_instrument(slot_index)
        if not inst or _is_filler_instrument(inst):
            return False

        target_key = self._inst_identity_key(inst)

        # Search the tree for a matching item
        for gi in range(self._inst_tree.topLevelItemCount()):
            group = self._inst_tree.topLevelItem(gi)
            for ci in range(group.childCount()):
                child = group.child(ci)
                child_vg = child.data(0, _ROLE_VG_NAME)
                child_slot = child.data(0, _ROLE_SLOT_IDX)
                if child_vg and child_slot is not None:
                    child_inst_vg = self._voicegroup_data.get_voicegroup(child_vg)
                    if child_inst_vg:
                        child_inst = child_inst_vg.get_instrument(child_slot)
                        if child_inst:
                            child_key = self._inst_identity_key(child_inst)
                            if child_key == target_key:
                                self._inst_tree.setCurrentItem(child)
                                self._inst_tree.scrollToItem(child)
                                return True
        return False
