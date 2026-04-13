"""
Voicegroups Tab for the PorySuite-Z Sound Editor.

Phase 5: Browse, inspect, and edit all voicegroups (instrument banks).
Each voicegroup has 128 slots — this tab lets you view all slots,
change instrument types, assign samples, edit parameters, add/clone/
delete voicegroups, and see which songs use each one.
"""

from __future__ import annotations

import copy
import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView,
    QComboBox, QFrame, QSizePolicy, QScrollArea,
    QSpinBox, QSlider, QMessageBox, QInputDialog,
    QGridLayout, QMenu,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard

_log = logging.getLogger("SoundEditor.Voicegroups")

# ---------------------------------------------------------------------------
# Data roles
# ---------------------------------------------------------------------------
_ROLE_VG_NAME = Qt.ItemDataRole.UserRole + 20
_ROLE_SLOT_IDX = Qt.ItemDataRole.UserRole + 21

# ---------------------------------------------------------------------------
# Voice type display info
# ---------------------------------------------------------------------------

# The voice types users can assign to a slot (excluding cry types)
_EDITABLE_VOICE_TYPES = [
    ('voice_directsound',             'DirectSound (Sample)'),
    ('voice_directsound_no_resample', 'DirectSound (No Resample)'),
    ('voice_directsound_alt',         'DirectSound (Alt)'),
    ('voice_square_1',                'Square 1 (with sweep)'),
    ('voice_square_1_alt',            'Square 1 Alt'),
    ('voice_square_2',                'Square 2'),
    ('voice_square_2_alt',            'Square 2 Alt'),
    ('voice_programmable_wave',       'Programmable Wave'),
    ('voice_programmable_wave_alt',   'Programmable Wave Alt'),
    ('voice_noise',                   'Noise'),
    ('voice_noise_alt',               'Noise Alt'),
    ('voice_keysplit',                'Keysplit (table)'),
    ('voice_keysplit_all',            'Keysplit All'),
]

_VOICE_TYPE_TO_LABEL = {k: v for k, v in _EDITABLE_VOICE_TYPES}

# Short labels for slot grid display
_TYPE_SHORT = {
    'voice_directsound':             'DS',
    'voice_directsound_no_resample': 'DS-NR',
    'voice_directsound_alt':         'DS-A',
    'voice_square_1':                'SQ1',
    'voice_square_1_alt':            'SQ1-A',
    'voice_square_2':                'SQ2',
    'voice_square_2_alt':            'SQ2-A',
    'voice_programmable_wave':       'PW',
    'voice_programmable_wave_alt':   'PW-A',
    'voice_noise':                   'NS',
    'voice_noise_alt':               'NS-A',
    'voice_keysplit':                'KS',
    'voice_keysplit_all':            'KS-A',
}

# Colours for type badges in the slot list
_TYPE_COLORS = {
    'DS':   QColor(100, 160, 255),    # blue  — samples
    'SQ':   QColor(100, 220, 100),    # green — square waves
    'PW':   QColor(220, 180, 80),     # gold  — prog wave
    'NS':   QColor(180, 180, 180),    # grey  — noise
    'KS':   QColor(200, 130, 220),    # purple — keysplit
    'FILL': QColor(80, 80, 80),       # dark  — filler
}


def _type_color(voice_type: str) -> QColor:
    """Return a colour for a voice type string."""
    short = _TYPE_SHORT.get(voice_type, '?')
    prefix = short[:2]
    return _TYPE_COLORS.get(prefix, QColor(120, 120, 120))


def _is_filler_instrument(inst) -> bool:
    """Detect default filler (unused) instrument slots."""
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
# MIDI note names
# ---------------------------------------------------------------------------

_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F',
               'F#', 'G', 'G#', 'A', 'A#', 'B']


def _midi_to_name(n: int) -> str:
    return f"{_NOTE_NAMES[n % 12]}{n // 12}"


# ═══════════════════════════════════════════════════════════════════════════
# Voicegroups Tab
# ═══════════════════════════════════════════════════════════════════════════

class VoicegroupsTab(QWidget):
    """Phase 5 — Voicegroup browser and editor."""

    modified = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root: str = ""
        self._voicegroup_data = None
        self._sample_data = None
        self._song_table = None
        self._current_vg = None        # selected Voicegroup object
        self._current_slot_item = None  # selected slot QTreeWidgetItem
        self._current_inst = None       # selected Instrument
        self._updating_ui = False       # suppress change handlers during load
        self._dirty_voicegroups: set[str] = set()  # names of modified VGs
        self._vg_labels: dict[str, str] = {}       # friendly display labels

        self._build_ui()

    # ═══════════════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Left panel: voicegroup list ───────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Voicegroups")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        left_layout.addWidget(title)

        # Search
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search voicegroups...")
        self._search_box.setToolTip(
            "Filter voicegroups by number or name.")
        self._search_box.textChanged.connect(self._apply_filter)
        left_layout.addWidget(self._search_box)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: grey; font-size: 11px;")
        left_layout.addWidget(self._count_label)

        # Voicegroup list
        self._vg_tree = QTreeWidget()
        self._vg_tree.setHeaderLabels(["Voicegroup", "Slots", "Songs"])
        self._vg_tree.setRootIsDecorated(False)
        self._vg_tree.setAlternatingRowColors(True)
        self._vg_tree.setSortingEnabled(True)
        self._vg_tree.setSelectionMode(
            QTreeWidget.SelectionMode.SingleSelection)
        header = self._vg_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._vg_tree.currentItemChanged.connect(self._on_vg_selected)
        self._vg_tree.itemDoubleClicked.connect(self._on_vg_double_clicked)
        left_layout.addWidget(self._vg_tree)

        # Management buttons
        btn_row = QHBoxLayout()
        self._btn_add_vg = QPushButton("+ Add")
        self._btn_add_vg.setToolTip(
            "Create a new voicegroup.\n"
            "You can start from scratch or clone an existing one.")
        self._btn_add_vg.clicked.connect(self._on_add_voicegroup)
        btn_row.addWidget(self._btn_add_vg)

        self._btn_clone_vg = QPushButton("Clone")
        self._btn_clone_vg.setToolTip(
            "Create a copy of the selected voicegroup\n"
            "with a new number. All 128 slots are duplicated.")
        self._btn_clone_vg.clicked.connect(self._on_clone_voicegroup)
        self._btn_clone_vg.setEnabled(False)
        btn_row.addWidget(self._btn_clone_vg)

        self._btn_delete_vg = QPushButton("Delete")
        self._btn_delete_vg.setToolTip(
            "Delete the selected voicegroup.\n"
            "Blocked if any songs still reference it.")
        self._btn_delete_vg.clicked.connect(self._on_delete_voicegroup)
        self._btn_delete_vg.setEnabled(False)
        btn_row.addWidget(self._btn_delete_vg)

        self._btn_gen_gm = QPushButton("Generate GM")
        self._btn_gen_gm.setToolTip(
            "Create a General MIDI voicegroup.\n"
            "Scans all existing instruments and maps them\n"
            "to standard GM program numbers (piano, strings,\n"
            "brass, etc.). Useful for MIDI imports.")
        self._btn_gen_gm.clicked.connect(self._on_generate_gm)
        btn_row.addWidget(self._btn_gen_gm)

        self._btn_auto_label = QPushButton("Auto-Label")
        self._btn_auto_label.setToolTip(
            "Generate friendly display names for all voicegroups\n"
            "based on which songs use them.\n\n"
            "Only affects PorySuite — doesn't touch your source code.\n"
            "You can rename any label individually afterwards.")
        self._btn_auto_label.clicked.connect(self._on_auto_label)
        btn_row.addWidget(self._btn_auto_label)

        self._btn_rename_label = QPushButton("✎ Rename")
        self._btn_rename_label.setToolTip(
            "Set a custom friendly name for the selected voicegroup.\n"
            "Double-click a voicegroup in the list to do the same thing.\n\n"
            "Only affects PorySuite — doesn't touch your source code.")
        self._btn_rename_label.clicked.connect(self._on_rename_label)
        btn_row.addWidget(self._btn_rename_label)

        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # ── Right panel: slot list + editor ───────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # VG info header
        self._vg_title = QLabel("No voicegroup selected")
        self._vg_title.setFont(QFont("", 14, QFont.Weight.Bold))
        right_layout.addWidget(self._vg_title)

        self._vg_info_label = QLabel("")
        self._vg_info_label.setStyleSheet("color: grey;")
        right_layout.addWidget(self._vg_info_label)

        # ── Slot list ─────────────────────────────────────────────────────
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        slots_group = QGroupBox("Instrument Slots (0–127)")
        slots_group.setToolTip(
            "All 128 instrument slots in this voicegroup.\n"
            "Click a slot to edit it below.\n"
            "Filler slots (default empty square waves) are shown in grey.")
        slots_layout = QVBoxLayout(slots_group)

        # Filter bar for slots
        slot_filter_row = QHBoxLayout()
        self._slot_filter = QComboBox()
        self._slot_filter.addItems([
            "All Slots",
            "Non-Filler Only",
            "Samples Only",
            "Square Waves Only",
            "Prog. Wave Only",
            "Noise Only",
            "Keysplits Only",
        ])
        install_scroll_guard(self._slot_filter)
        self._slot_filter.setToolTip("Filter which instrument slots are shown.")
        self._slot_filter.currentIndexChanged.connect(self._apply_slot_filter)
        slot_filter_row.addWidget(self._slot_filter)
        slot_filter_row.addStretch()

        self._slot_count_label = QLabel("")
        self._slot_count_label.setStyleSheet("color: grey; font-size: 11px;")
        slot_filter_row.addWidget(self._slot_count_label)

        slots_layout.addLayout(slot_filter_row)

        self._slot_tree = QTreeWidget()
        self._slot_tree.setHeaderLabels(["#", "Type", "Instrument", "Details"])
        self._slot_tree.setRootIsDecorated(False)
        self._slot_tree.setAlternatingRowColors(True)
        self._slot_tree.setSortingEnabled(False)
        self._slot_tree.setSelectionMode(
            QTreeWidget.SelectionMode.SingleSelection)
        slot_header = self._slot_tree.header()
        slot_header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        slot_header.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        slot_header.setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        slot_header.setStretchLastSection(True)
        self._slot_tree.currentItemChanged.connect(self._on_slot_selected)
        self._slot_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._slot_tree.customContextMenuRequested.connect(
            self._on_slot_context_menu)
        slots_layout.addWidget(self._slot_tree)

        right_splitter.addWidget(slots_group)

        # ── Slot editor ───────────────────────────────────────────────────
        editor_area = QScrollArea()
        editor_area.setWidgetResizable(True)
        editor_widget = QWidget()
        self._editor_layout = QVBoxLayout(editor_widget)
        self._editor_layout.setContentsMargins(4, 4, 4, 4)

        # Slot header
        self._slot_title = QLabel("No slot selected")
        self._slot_title.setFont(QFont("", 12, QFont.Weight.Bold))
        self._editor_layout.addWidget(self._slot_title)

        # Voice type selector
        type_group = QGroupBox("Voice Type")
        type_group.setToolTip(
            "Change what kind of instrument this slot uses.\n"
            "DirectSound = plays a sample (.bin file)\n"
            "Square = chiptune synth (Game Boy hardware)\n"
            "Prog. Wave = custom waveform\n"
            "Noise = percussion static/hiss\n"
            "Keysplit = routes note ranges to another voicegroup")
        type_layout = QVBoxLayout(type_group)

        self._type_combo = QComboBox()
        for macro, label in _EDITABLE_VOICE_TYPES:
            self._type_combo.addItem(label, macro)
        install_scroll_guard(self._type_combo)
        self._type_combo.setToolTip(
            "Select the instrument type for this slot.")
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self._type_combo)

        self._editor_layout.addWidget(type_group)

        # ── Type-specific editors ─────────────────────────────────────────

        # Sample picker (DirectSound)
        self._sample_group = QGroupBox("Sample")
        self._sample_group.setToolTip(
            "Which audio sample (.bin file) this instrument plays.")
        sample_layout = QVBoxLayout(self._sample_group)

        self._sample_combo = QComboBox()
        install_scroll_guard(self._sample_combo)
        self._sample_combo.setToolTip(
            "Pick which sample this instrument plays.\n"
            "Samples are .bin files in sound/direct_sound_samples/.")
        self._sample_combo.currentIndexChanged.connect(
            self._on_sample_changed)
        sample_layout.addWidget(self._sample_combo)

        self._sample_info_label = QLabel("")
        self._sample_info_label.setStyleSheet("color: grey; font-size: 11px;")
        sample_layout.addWidget(self._sample_info_label)

        self._editor_layout.addWidget(self._sample_group)

        # Wave picker (Programmable Wave)
        self._wave_group = QGroupBox("Waveform")
        self._wave_group.setToolTip(
            "Which programmable waveform this instrument uses.")
        wave_layout = QVBoxLayout(self._wave_group)

        self._wave_combo = QComboBox()
        install_scroll_guard(self._wave_combo)
        self._wave_combo.setToolTip(
            "Pick which programmable wave this instrument uses.")
        self._wave_combo.currentIndexChanged.connect(
            self._on_wave_changed)
        wave_layout.addWidget(self._wave_combo)

        self._editor_layout.addWidget(self._wave_group)

        # Keysplit target picker
        self._keysplit_group = QGroupBox("Keysplit Target")
        self._keysplit_group.setToolTip(
            "Which voicegroup the keysplit routes notes to.")
        ks_layout = QVBoxLayout(self._keysplit_group)

        ks_vg_row = QHBoxLayout()
        ks_vg_row.addWidget(QLabel("Target VG:"))
        self._ks_vg_combo = QComboBox()
        install_scroll_guard(self._ks_vg_combo)
        self._ks_vg_combo.setToolTip(
            "The voicegroup that keysplit routes notes to.")
        self._ks_vg_combo.currentIndexChanged.connect(
            self._on_ks_vg_changed)
        ks_vg_row.addWidget(self._ks_vg_combo)
        ks_layout.addLayout(ks_vg_row)

        ks_tbl_row = QHBoxLayout()
        ks_tbl_row.addWidget(QLabel("Table:"))
        self._ks_table_combo = QComboBox()
        install_scroll_guard(self._ks_table_combo)
        self._ks_table_combo.setToolTip(
            "The keysplit table that maps note ranges to instruments.\n"
            "Only used for 'voice_keysplit' (not keysplit_all).")
        self._ks_table_combo.currentIndexChanged.connect(
            self._on_ks_table_changed)
        ks_tbl_row.addWidget(self._ks_table_combo)
        ks_layout.addLayout(ks_tbl_row)

        self._editor_layout.addWidget(self._keysplit_group)

        # ── Common parameters ─────────────────────────────────────────────
        params_group = QGroupBox("Parameters")
        params_group.setToolTip(
            "Base key, pan, and type-specific settings.\n"
            "These apply to all instrument types except keysplits.")
        params_layout = QVBoxLayout(params_group)

        # Base key
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Base Key:"))
        self._edit_base_key = QSpinBox()
        self._edit_base_key.setRange(0, 127)
        self._edit_base_key.setToolTip(
            "The MIDI note at which the sample plays at its\n"
            "original speed. Higher = the engine pitch-shifts\n"
            "everything down to compensate. Range: 0–127.")
        install_scroll_guard(self._edit_base_key)
        self._edit_base_key.valueChanged.connect(self._on_base_key_changed)
        key_row.addWidget(self._edit_base_key)
        self._key_name_label = QLabel("C5")
        key_row.addWidget(self._key_name_label)
        key_row.addStretch()
        params_layout.addLayout(key_row)

        # Pan
        pan_row = QHBoxLayout()
        pan_row.addWidget(QLabel("Pan:"))
        self._edit_pan = QSpinBox()
        self._edit_pan.setRange(0, 127)
        self._edit_pan.setToolTip(
            "Stereo panning. 0 = centre, 1–63 = left, 64 = centre,\n"
            "65–127 = right.")
        install_scroll_guard(self._edit_pan)
        self._edit_pan.valueChanged.connect(self._on_pan_changed)
        pan_row.addWidget(self._edit_pan)
        self._pan_name_label = QLabel("Center")
        pan_row.addWidget(self._pan_name_label)
        pan_row.addStretch()
        params_layout.addLayout(pan_row)

        # Duty cycle (square only)
        duty_row = QHBoxLayout()
        self._duty_label = QLabel("Duty:")
        duty_row.addWidget(self._duty_label)
        self._edit_duty = QComboBox()
        self._edit_duty.addItems(["12.5%", "25%", "50%", "75%"])
        install_scroll_guard(self._edit_duty)
        self._edit_duty.setToolTip(
            "Pulse width for square wave instruments.\n"
            "12.5% = thin/nasal, 25% = reedy, 50% = hollow, 75% = full.")
        self._edit_duty.currentIndexChanged.connect(self._on_duty_changed)
        duty_row.addWidget(self._edit_duty)
        duty_row.addStretch()
        params_layout.addLayout(duty_row)

        # Sweep (square_1 only)
        sweep_row = QHBoxLayout()
        self._sweep_label = QLabel("Sweep:")
        sweep_row.addWidget(self._sweep_label)
        self._edit_sweep = QSpinBox()
        self._edit_sweep.setRange(0, 255)
        install_scroll_guard(self._edit_sweep)
        self._edit_sweep.setToolTip(
            "Frequency sweep for square_1 instruments.\n"
            "Non-zero = automatic pitch slide effect.")
        self._edit_sweep.valueChanged.connect(self._on_sweep_changed)
        sweep_row.addWidget(self._edit_sweep)
        sweep_row.addStretch()
        params_layout.addLayout(sweep_row)

        # Period (noise only)
        period_row = QHBoxLayout()
        self._period_label = QLabel("Period:")
        period_row.addWidget(self._period_label)
        self._edit_period = QComboBox()
        self._edit_period.addItems(["0 (White noise)", "1 (Metallic)"])
        install_scroll_guard(self._edit_period)
        self._edit_period.setToolTip(
            "Noise type. 0 = white noise (hissy), 1 = metallic (tonal).")
        self._edit_period.currentIndexChanged.connect(self._on_period_changed)
        period_row.addWidget(self._edit_period)
        period_row.addStretch()
        params_layout.addLayout(period_row)

        self._editor_layout.addWidget(params_group)

        # ── ADSR envelope ─────────────────────────────────────────────────
        adsr_group = QGroupBox("Envelope (ADSR)")
        adsr_group.setToolTip(
            "Volume envelope — how the sound fades in and out.\n"
            "Attack = fade in speed, Decay = initial drop,\n"
            "Sustain = held level, Release = fade after note ends.\n"
            "DirectSound uses 0–255, synth types use smaller ranges.")
        adsr_layout = QVBoxLayout(adsr_group)

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
        self._adsr_scale_label.setStyleSheet("color: grey; font-size: 11px;")
        adsr_layout.addWidget(self._adsr_scale_label)

        self._editor_layout.addWidget(adsr_group)

        # ── Songs using this voicegroup ───────────────────────────────────
        songs_group = QGroupBox("Songs Using This Voicegroup")
        songs_group.setToolTip(
            "All songs in the project that use this voicegroup.\n"
            "Changing instruments here affects all these songs.")
        songs_layout = QVBoxLayout(songs_group)

        self._songs_label = QLabel("—")
        self._songs_label.setWordWrap(True)
        songs_layout.addWidget(self._songs_label)

        self._editor_layout.addWidget(songs_group)

        self._editor_layout.addStretch()

        editor_area.setWidget(editor_widget)
        right_splitter.addWidget(editor_area)
        right_splitter.setSizes([350, 350])

        right_layout.addWidget(right_splitter)
        splitter.addWidget(right)
        splitter.setSizes([280, 620])

    # ═══════════════════════════════════════════════════════════════════════
    # Data loading
    # ═══════════════════════════════════════════════════════════════════════

    def load_data(self, project_root: str, voicegroup_data,
                  song_table=None, vg_labels=None):
        """Receive parsed voicegroup data and populate the browser."""
        self._project_root = project_root
        self._voicegroup_data = voicegroup_data
        self._song_table = song_table
        self._dirty_voicegroups.clear()

        # Use shared labels dict from sound editor tab (same object
        # reference, so edits here are visible to the piano roll)
        if vg_labels is not None:
            self._vg_labels = vg_labels
        else:
            from core.sound.voicegroup_labels import load_labels
            self._vg_labels = load_labels(project_root)

        self._populate_vg_list()

    def set_sample_data(self, sample_data):
        """Receive sample data for the sample picker dropdown."""
        self._sample_data = sample_data
        # Repopulate the sample combo if it's empty
        self._populate_sample_combo()
        self._populate_wave_combo()

    # ═══════════════════════════════════════════════════════════════════════
    # Voicegroup list
    # ═══════════════════════════════════════════════════════════════════════

    def _populate_vg_list(self):
        """Fill the left panel with all voicegroups."""
        self._vg_tree.clear()
        if not self._voicegroup_data:
            return

        # Build song usage map: vg_number -> list of song names
        song_usage: dict[int, list[str]] = {}
        if self._song_table:
            for entry in self._song_table.entries:
                if entry.voicegroup_index is not None:
                    song_usage.setdefault(
                        entry.voicegroup_index, []).append(
                        entry.friendly_name)

        self._vg_tree.setSortingEnabled(False)

        for vg_name in sorted(self._voicegroup_data.voicegroups.keys()):
            vg = self._voicegroup_data.voicegroups[vg_name]

            # Count non-filler slots
            real_slots = sum(
                1 for inst in vg.instruments
                if not _is_filler_instrument(inst))

            # Song count
            songs = song_usage.get(vg.number, [])

            item = QTreeWidgetItem()
            num_str = vg_name.replace('voicegroup', '')
            friendly = self._vg_labels.get(vg_name, '')
            if friendly:
                item.setText(0, f"VG {num_str} — {friendly}")
            else:
                item.setText(0, f"VG {num_str}")
            item.setText(1, f"{real_slots}/128")
            item.setText(2, str(len(songs)))
            item.setData(0, _ROLE_VG_NAME, vg_name)
            item.setToolTip(0, vg_name)
            if songs:
                item.setToolTip(2, '\n'.join(songs[:20])
                                + (f'\n... and {len(songs)-20} more'
                                   if len(songs) > 20 else ''))
            self._vg_tree.addTopLevelItem(item)

        self._vg_tree.setSortingEnabled(True)
        self._vg_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._update_count_label()

    def _update_count_label(self):
        """Update the visible/total count."""
        visible = 0
        total = self._vg_tree.topLevelItemCount()
        for i in range(total):
            if not self._vg_tree.topLevelItem(i).isHidden():
                visible += 1
        if visible == total:
            self._count_label.setText(f"{total} voicegroups")
        else:
            self._count_label.setText(f"Showing {visible} of {total}")

    def _apply_filter(self):
        """Filter voicegroup list by search text."""
        search = self._search_box.text().lower()
        for i in range(self._vg_tree.topLevelItemCount()):
            item = self._vg_tree.topLevelItem(i)
            vg_name = item.data(0, _ROLE_VG_NAME) or ""
            text = item.text(0).lower()
            item.setHidden(
                bool(search) and search not in text
                and search not in vg_name.lower())
        self._update_count_label()

    # ═══════════════════════════════════════════════════════════════════════
    # Voicegroup selection
    # ═══════════════════════════════════════════════════════════════════════

    def _on_vg_selected(self, current, previous):
        """User selected a voicegroup in the list."""
        if current is None:
            self._current_vg = None
            self._vg_title.setText("No voicegroup selected")
            self._vg_info_label.setText("")
            self._slot_tree.clear()
            self._clear_slot_editor()
            self._songs_label.setText("—")
            self._btn_clone_vg.setEnabled(False)
            self._btn_delete_vg.setEnabled(False)
            return

        vg_name = current.data(0, _ROLE_VG_NAME)
        if not vg_name or not self._voicegroup_data:
            return

        vg = self._voicegroup_data.get_voicegroup(vg_name)
        if not vg:
            return

        self._current_vg = vg
        num_str = vg_name.replace('voicegroup', '')
        self._vg_title.setText(f"Voicegroup {num_str}")

        # Summary line
        type_counts = {}
        filler_count = 0
        for inst in vg.instruments:
            if _is_filler_instrument(inst):
                filler_count += 1
                continue
            short = _TYPE_SHORT.get(inst.voice_type, '?')
            prefix = short.split('-')[0]
            type_counts[prefix] = type_counts.get(prefix, 0) + 1

        parts = []
        for t in ['DS', 'SQ1', 'SQ2', 'PW', 'NS', 'KS']:
            if t in type_counts:
                parts.append(f"{type_counts[t]} {t}")
        summary = ', '.join(parts)
        if filler_count:
            summary += f", {filler_count} filler"
        self._vg_info_label.setText(summary)

        self._populate_slot_list(vg)
        self._update_songs_using(vg)
        self._btn_clone_vg.setEnabled(True)
        self._btn_delete_vg.setEnabled(True)

    def _populate_slot_list(self, vg):
        """Fill the slot tree for the selected voicegroup."""
        self._slot_tree.clear()
        self._clear_slot_editor()

        for inst in vg.instruments:
            item = QTreeWidgetItem()
            item.setText(0, str(inst.slot_index))
            item.setData(0, _ROLE_SLOT_IDX, inst.slot_index)

            short_type = _TYPE_SHORT.get(inst.voice_type, '?')
            item.setText(1, short_type)

            filler = _is_filler_instrument(inst)
            if filler:
                item.setText(2, "(filler)")
                item.setText(3, "")
                for col in range(4):
                    item.setForeground(col, QColor(100, 100, 100))
            else:
                item.setText(2, inst.friendly_name)
                # Details column
                details = self._slot_detail_string(inst)
                item.setText(3, details)
                item.setForeground(1, _type_color(inst.voice_type))

            self._slot_tree.addTopLevelItem(item)

        self._apply_slot_filter()

    def _slot_detail_string(self, inst) -> str:
        """Build a short detail string for a slot."""
        parts = []
        if inst.is_directsound and inst.sample_label:
            parts.append(f"key={inst.base_midi_key}")
        elif inst.is_square:
            duty = {0: '12.5%', 1: '25%', 2: '50%', 3: '75%'}.get(
                inst.duty_cycle, '?')
            parts.append(f"duty={duty}")
            if inst.sweep:
                parts.append(f"sweep={inst.sweep}")
        elif inst.is_noise:
            parts.append(f"period={inst.period}")
        elif inst.is_keysplit:
            parts.append(inst.target_voicegroup or '?')

        if not inst.is_keysplit and not _is_filler_instrument(inst):
            parts.append(
                f"ADSR={inst.attack}/{inst.decay}/"
                f"{inst.sustain}/{inst.release}")
        return '  '.join(parts)

    def _apply_slot_filter(self):
        """Filter the slot list by type."""
        idx = self._slot_filter.currentIndex()

        visible = 0
        total = self._slot_tree.topLevelItemCount()

        for i in range(total):
            item = self._slot_tree.topLevelItem(i)
            slot_idx = item.data(0, _ROLE_SLOT_IDX)
            inst = self._current_vg.instruments[slot_idx] if self._current_vg else None

            show = True
            if inst:
                filler = _is_filler_instrument(inst)
                if idx == 1:    # Non-filler
                    show = not filler
                elif idx == 2:  # Samples
                    show = inst.is_directsound and not filler
                elif idx == 3:  # Square
                    show = inst.is_square and not filler
                elif idx == 4:  # Prog wave
                    show = inst.is_programmable_wave and not filler
                elif idx == 5:  # Noise
                    show = inst.is_noise and not filler
                elif idx == 6:  # Keysplit
                    show = inst.is_keysplit and not filler

            item.setHidden(not show)
            if show:
                visible += 1

        self._slot_count_label.setText(
            f"Showing {visible} of {total}" if visible != total
            else f"{total} slots")

    # ═══════════════════════════════════════════════════════════════════════
    # Slot selection — loads editor
    # ═══════════════════════════════════════════════════════════════════════

    def _on_slot_selected(self, current, previous):
        """User clicked a slot in the list."""
        if current is None or not self._current_vg:
            self._clear_slot_editor()
            return

        slot_idx = current.data(0, _ROLE_SLOT_IDX)
        if slot_idx is None:
            self._clear_slot_editor()
            return

        inst = self._current_vg.get_instrument(slot_idx)
        if not inst:
            self._clear_slot_editor()
            return

        self._current_slot_item = current
        self._current_inst = inst
        self._load_slot_editor(inst)

    def _clear_slot_editor(self):
        """Reset the slot editor to empty state."""
        self._current_inst = None
        self._current_slot_item = None
        self._slot_title.setText("No slot selected")
        self._sample_group.setVisible(False)
        self._wave_group.setVisible(False)
        self._keysplit_group.setVisible(False)

    def _load_slot_editor(self, inst):
        """Populate all editor widgets for the given instrument."""
        self._updating_ui = True
        try:
            filler = _is_filler_instrument(inst)
            self._slot_title.setText(
                f"Slot {inst.slot_index}: {inst.friendly_name}"
                + (" (filler)" if filler else ""))

            # Voice type combo
            macro = inst.voice_type
            for i in range(self._type_combo.count()):
                if self._type_combo.itemData(i) == macro:
                    self._type_combo.setCurrentIndex(i)
                    break

            # Show/hide type-specific groups
            is_ds = inst.is_directsound
            is_sq = inst.is_square
            is_pw = inst.is_programmable_wave
            is_ns = inst.is_noise
            is_ks = inst.is_keysplit

            self._sample_group.setVisible(is_ds)
            self._wave_group.setVisible(is_pw)
            self._keysplit_group.setVisible(is_ks)

            # Duty / sweep / period visibility
            self._duty_label.setVisible(is_sq)
            self._edit_duty.setVisible(is_sq)
            self._sweep_label.setVisible(is_sq and 'square_1' in macro)
            self._edit_sweep.setVisible(is_sq and 'square_1' in macro)
            self._period_label.setVisible(is_ns)
            self._edit_period.setVisible(is_ns)

            # Sample picker
            if is_ds:
                self._populate_sample_combo()
                if inst.sample_label:
                    idx = self._sample_combo.findData(inst.sample_label)
                    if idx >= 0:
                        self._sample_combo.setCurrentIndex(idx)
                self._update_sample_info()

            # Wave picker
            if is_pw:
                self._populate_wave_combo()
                if inst.wave_label:
                    idx = self._wave_combo.findData(inst.wave_label)
                    if idx >= 0:
                        self._wave_combo.setCurrentIndex(idx)

            # Keysplit pickers
            if is_ks:
                self._populate_keysplit_combos()
                if inst.target_voicegroup:
                    idx = self._ks_vg_combo.findData(inst.target_voicegroup)
                    if idx >= 0:
                        self._ks_vg_combo.setCurrentIndex(idx)
                if inst.keysplit_table:
                    idx = self._ks_table_combo.findData(inst.keysplit_table)
                    if idx >= 0:
                        self._ks_table_combo.setCurrentIndex(idx)
                # Hide table combo for keysplit_all
                self._ks_table_combo.setVisible(macro == 'voice_keysplit')

            # Common params
            self._edit_base_key.setValue(inst.base_midi_key)
            self._key_name_label.setText(_midi_to_name(inst.base_midi_key))
            self._edit_pan.setValue(inst.pan)
            self._update_pan_label(inst.pan)

            if is_sq:
                self._edit_duty.setCurrentIndex(inst.duty_cycle)
                self._edit_sweep.setValue(inst.sweep)

            if is_ns:
                self._edit_period.setCurrentIndex(
                    min(inst.period, self._edit_period.count() - 1))

            # ADSR
            is_cgb = is_sq or is_pw or is_ns
            if is_cgb:
                self._adsr_sliders['attack'].setRange(0, 7)
                self._adsr_sliders['decay'].setRange(0, 7)
                self._adsr_sliders['sustain'].setRange(0, 15)
                self._adsr_sliders['release'].setRange(0, 7)
                self._adsr_scale_label.setText("CGB scale: A 0–7, D 0–7, S 0–15, R 0–7")
            else:
                for s in self._adsr_sliders.values():
                    s.setRange(0, 255)
                self._adsr_scale_label.setText("DirectSound scale: 0–255")

            self._adsr_sliders['attack'].setValue(inst.attack)
            self._adsr_sliders['decay'].setValue(inst.decay)
            self._adsr_sliders['sustain'].setValue(inst.sustain)
            self._adsr_sliders['release'].setValue(inst.release)

            for p in ('attack', 'decay', 'sustain', 'release'):
                self._adsr_labels[p].setText(
                    str(self._adsr_sliders[p].value()))

            # Disable params for keysplit
            params_enabled = not is_ks
            self._edit_base_key.setEnabled(params_enabled)
            self._edit_pan.setEnabled(params_enabled)
            for s in self._adsr_sliders.values():
                s.setEnabled(params_enabled)

        finally:
            self._updating_ui = False

    # ═══════════════════════════════════════════════════════════════════════
    # Combo population helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _ensure_samples_loaded(self):
        """Lazy-load sample data on first need."""
        if self._sample_data or not self._project_root:
            return
        try:
            from core.sound.sample_loader import load_sample_data
            self._sample_data = load_sample_data(
                self._project_root, load_pcm=False)
            # Also share with instruments tab via parent
            se = getattr(self, '_sound_editor', None)
            if se and hasattr(se, '_voicegroups_tab'):
                # Don't load PCM — just labels for pickers
                pass
        except Exception as e:
            _log.error("Failed to load sample data: %s", e)

    def _populate_sample_combo(self):
        """Fill the sample picker with all available samples."""
        self._ensure_samples_loaded()
        self._sample_combo.blockSignals(True)
        self._sample_combo.clear()
        if self._sample_data:
            for label in sorted(self._sample_data.direct_sound.keys()):
                friendly = label
                if friendly.startswith('DirectSoundWaveData_'):
                    friendly = friendly[20:].replace('_', ' ').title()
                self._sample_combo.addItem(friendly, label)
        self._sample_combo.blockSignals(False)

    def _populate_wave_combo(self):
        """Fill the wave picker with all programmable waves."""
        self._wave_combo.blockSignals(True)
        self._wave_combo.clear()
        if self._sample_data:
            for label in sorted(self._sample_data.programmable_waves.keys()):
                num = label.split('_')[-1] if '_' in label else label
                self._wave_combo.addItem(f"Wave {num}", label)
        self._wave_combo.blockSignals(False)

    def _populate_keysplit_combos(self):
        """Fill keysplit target VG and table combos."""
        self._ks_vg_combo.blockSignals(True)
        self._ks_vg_combo.clear()
        if self._voicegroup_data:
            for name in sorted(self._voicegroup_data.voicegroups.keys()):
                num = name.replace('voicegroup', '')
                self._ks_vg_combo.addItem(f"VG {num}", name)
        self._ks_vg_combo.blockSignals(False)

        self._ks_table_combo.blockSignals(True)
        self._ks_table_combo.clear()
        if self._voicegroup_data:
            for name in sorted(self._voicegroup_data.keysplit_tables.keys()):
                self._ks_table_combo.addItem(name, name)
        self._ks_table_combo.blockSignals(False)

    def _update_sample_info(self):
        """Show info about the currently selected sample."""
        if not self._sample_data:
            self._sample_info_label.setText("")
            return
        label = self._sample_combo.currentData()
        if not label:
            self._sample_info_label.setText("")
            return
        sample = self._sample_data.direct_sound.get(label)
        if not sample:
            self._sample_info_label.setText("(sample not found)")
            return
        rate = sample.header.sample_rate
        dur = sample.duration_seconds
        loop = "Yes" if sample.has_loop else "No"
        self._sample_info_label.setText(
            f"Rate: {rate} Hz  |  Length: {dur:.2f}s  |  Loop: {loop}")

    def _update_pan_label(self, val):
        """Update the pan description label."""
        if val == 0 or val == 64:
            self._pan_name_label.setText("Center")
        elif val < 64:
            self._pan_name_label.setText(f"Left {val}")
        else:
            self._pan_name_label.setText(f"Right {val - 64}")

    # ═══════════════════════════════════════════════════════════════════════
    # Change handlers
    # ═══════════════════════════════════════════════════════════════════════

    def _on_type_changed(self, index):
        """User changed the voice type of the current slot."""
        if self._updating_ui or not self._current_inst or not self._current_vg:
            return

        new_macro = self._type_combo.itemData(index)
        if not new_macro or new_macro == self._current_inst.voice_type:
            return

        from core.sound.sound_constants import VOICE_MACRO_TYPES
        inst = self._current_inst
        inst.voice_type = new_macro
        inst.type_byte = VOICE_MACRO_TYPES.get(new_macro, 0)

        # Clear type-specific fields that don't apply
        if not inst.is_directsound:
            inst.sample_label = None
        if not inst.is_programmable_wave:
            inst.wave_label = None
        if not inst.is_keysplit:
            inst.target_voicegroup = None
            inst.keysplit_table = None
        if not inst.is_square:
            inst.duty_cycle = 0
            inst.sweep = 0
        if not inst.is_noise:
            inst.period = 0

        # Set defaults for the new type
        if inst.is_directsound and not inst.sample_label:
            if self._sample_data and self._sample_data.direct_sound:
                inst.sample_label = sorted(
                    self._sample_data.direct_sound.keys())[0]
        if inst.is_programmable_wave and not inst.wave_label:
            if self._sample_data and self._sample_data.programmable_waves:
                inst.wave_label = sorted(
                    self._sample_data.programmable_waves.keys())[0]
        if inst.is_keysplit and not inst.target_voicegroup:
            if self._voicegroup_data:
                names = sorted(self._voicegroup_data.voicegroups.keys())
                if names:
                    inst.target_voicegroup = names[0]

        self._mark_dirty()
        self._load_slot_editor(inst)
        self._refresh_slot_item(inst)

    def _on_sample_changed(self, index):
        """User picked a different sample."""
        if self._updating_ui or not self._current_inst:
            return
        label = self._sample_combo.itemData(index)
        if label and label != self._current_inst.sample_label:
            self._current_inst.sample_label = label
            self._mark_dirty()
            self._refresh_slot_item(self._current_inst)
            self._update_sample_info()

    def _on_wave_changed(self, index):
        """User picked a different programmable wave."""
        if self._updating_ui or not self._current_inst:
            return
        label = self._wave_combo.itemData(index)
        if label and label != self._current_inst.wave_label:
            self._current_inst.wave_label = label
            self._mark_dirty()
            self._refresh_slot_item(self._current_inst)

    def _on_ks_vg_changed(self, index):
        """User changed keysplit target voicegroup."""
        if self._updating_ui or not self._current_inst:
            return
        name = self._ks_vg_combo.itemData(index)
        if name and name != self._current_inst.target_voicegroup:
            self._current_inst.target_voicegroup = name
            self._mark_dirty()
            self._refresh_slot_item(self._current_inst)

    def _on_ks_table_changed(self, index):
        """User changed keysplit table."""
        if self._updating_ui or not self._current_inst:
            return
        name = self._ks_table_combo.itemData(index)
        if name and name != self._current_inst.keysplit_table:
            self._current_inst.keysplit_table = name
            self._mark_dirty()
            self._refresh_slot_item(self._current_inst)

    def _on_base_key_changed(self, val):
        if self._updating_ui or not self._current_inst:
            return
        self._key_name_label.setText(_midi_to_name(val))
        self._current_inst.base_midi_key = val
        self._mark_dirty()

    def _on_pan_changed(self, val):
        if self._updating_ui or not self._current_inst:
            return
        self._update_pan_label(val)
        self._current_inst.pan = val
        self._mark_dirty()

    def _on_duty_changed(self, idx):
        if self._updating_ui or not self._current_inst:
            return
        self._current_inst.duty_cycle = idx
        self._mark_dirty()
        self._refresh_slot_item(self._current_inst)

    def _on_sweep_changed(self, val):
        if self._updating_ui or not self._current_inst:
            return
        self._current_inst.sweep = val
        self._mark_dirty()

    def _on_period_changed(self, idx):
        if self._updating_ui or not self._current_inst:
            return
        self._current_inst.period = idx
        self._mark_dirty()
        self._refresh_slot_item(self._current_inst)

    def _on_adsr_changed(self, param, val):
        if self._updating_ui or not self._current_inst:
            return
        self._adsr_labels[param].setText(str(val))
        setattr(self._current_inst, param, val)
        self._mark_dirty()
        self._refresh_slot_item(self._current_inst)

    # ═══════════════════════════════════════════════════════════════════════
    # Dirty tracking / slot refresh
    # ═══════════════════════════════════════════════════════════════════════

    def _mark_dirty(self):
        """Mark the current voicegroup as modified."""
        if self._current_vg:
            self._dirty_voicegroups.add(self._current_vg.name)
        self.modified.emit()

    def _refresh_slot_item(self, inst):
        """Update the slot list item text after an edit."""
        if not self._current_slot_item:
            return
        item = self._current_slot_item
        short_type = _TYPE_SHORT.get(inst.voice_type, '?')
        item.setText(1, short_type)

        filler = _is_filler_instrument(inst)
        if filler:
            item.setText(2, "(filler)")
            item.setText(3, "")
            for col in range(4):
                item.setForeground(col, QColor(100, 100, 100))
        else:
            item.setText(2, inst.friendly_name)
            item.setText(3, self._slot_detail_string(inst))
            item.setForeground(1, _type_color(inst.voice_type))
            for col in (0, 2, 3):
                item.setForeground(col, QColor(220, 220, 220))

    # ═══════════════════════════════════════════════════════════════════════
    # Slot context menu
    # ═══════════════════════════════════════════════════════════════════════

    def _on_slot_context_menu(self, pos):
        """Right-click on a slot — copy from another voicegroup."""
        item = self._slot_tree.itemAt(pos)
        if not item or not self._current_vg:
            return

        slot_idx = item.data(0, _ROLE_SLOT_IDX)
        if slot_idx is None:
            return

        menu = QMenu(self)

        copy_action = menu.addAction("Copy Instrument From Another VG...")
        copy_action.setToolTip(
            "Replace this slot with an instrument definition\n"
            "copied from the same slot in another voicegroup.")

        go_inst_action = menu.addAction("Go to Instrument")
        go_inst_action.setToolTip(
            "Jump to this instrument in the Instruments tab.")

        chosen = menu.exec(
            self._slot_tree.viewport().mapToGlobal(pos))

        if chosen == copy_action:
            self._copy_instrument_from_vg(slot_idx)
        elif chosen == go_inst_action:
            self._go_to_instrument(slot_idx)

    def _copy_instrument_from_vg(self, slot_idx: int):
        """Copy an instrument definition from another voicegroup."""
        if not self._voicegroup_data or not self._current_vg:
            return

        vg_names = sorted(self._voicegroup_data.voicegroups.keys())
        display_names = [
            f"VG {n.replace('voicegroup', '')}" for n in vg_names]

        name, ok = QInputDialog.getItem(
            self, "Copy From Voicegroup",
            f"Copy slot {slot_idx} from which voicegroup?",
            display_names, 0, False)
        if not ok or not name:
            return

        # Find the source VG
        idx = display_names.index(name)
        src_vg = self._voicegroup_data.get_voicegroup(vg_names[idx])
        if not src_vg:
            return

        src_inst = src_vg.get_instrument(slot_idx)
        if not src_inst:
            QMessageBox.warning(
                self, "No Instrument",
                f"Slot {slot_idx} doesn't exist in {name}.")
            return

        # Deep copy the instrument
        new_inst = copy.deepcopy(src_inst)
        new_inst.slot_index = slot_idx
        self._current_vg.instruments[slot_idx] = new_inst

        self._mark_dirty()
        self._populate_slot_list(self._current_vg)

        # Reselect the slot
        for i in range(self._slot_tree.topLevelItemCount()):
            item = self._slot_tree.topLevelItem(i)
            if item.data(0, _ROLE_SLOT_IDX) == slot_idx:
                self._slot_tree.setCurrentItem(item)
                break

    def set_sound_editor_ref(self, sound_editor):
        """Store a reference to the parent SoundEditorTab for cross-tab nav."""
        self._sound_editor = sound_editor

    def _go_to_instrument(self, slot_idx: int):
        """Jump to the Instruments tab and select this instrument."""
        se = getattr(self, '_sound_editor', None)
        if se and hasattr(se, '_instruments_tab') and hasattr(se, '_tab_widget'):
            se._tab_widget.setCurrentWidget(se._instruments_tab)
            if self._current_vg:
                se._instruments_tab.select_instrument(
                    self._current_vg.name, slot_idx)

    # ═══════════════════════════════════════════════════════════════════════
    # Songs cross-reference
    # ═══════════════════════════════════════════════════════════════════════

    def _update_songs_using(self, vg):
        """Show which songs use this voicegroup."""
        if not self._song_table:
            self._songs_label.setText("(song data not loaded)")
            return

        songs = []
        for entry in self._song_table.entries:
            if entry.voicegroup_index == vg.number:
                songs.append(entry.friendly_name)

        if songs:
            self._songs_label.setText(
                f"Used by {len(songs)} song(s): "
                + ', '.join(songs[:15])
                + (f' ... and {len(songs)-15} more'
                   if len(songs) > 15 else ''))
        else:
            self._songs_label.setText("Not used by any songs.")

    # ═══════════════════════════════════════════════════════════════════════
    # Voicegroup management
    # ═══════════════════════════════════════════════════════════════════════

    def _on_add_voicegroup(self):
        """Create a new empty voicegroup."""
        if not self._voicegroup_data:
            return

        # Find the next available number
        used = set(self._voicegroup_data.voicegroup_numbers)
        new_num = 0
        for n in range(256):
            if n not in used:
                new_num = n
                break

        num_str, ok = QInputDialog.getText(
            self, "New Voicegroup",
            f"Enter the voicegroup number (0–255).\n"
            f"Next available: {new_num}",
            text=str(new_num))
        if not ok or not num_str:
            return

        try:
            num = int(num_str)
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Must be a number.")
            return

        if num < 0 or num > 255:
            QMessageBox.warning(self, "Invalid", "Must be 0–255.")
            return

        vg_name = f"voicegroup{num:03d}"
        if vg_name in self._voicegroup_data.voicegroups:
            QMessageBox.warning(
                self, "Already Exists",
                f"Voicegroup {num} already exists.")
            return

        # Create 128 filler slots
        from core.sound.voicegroup_parser import Instrument, Voicegroup
        instruments = []
        for i in range(128):
            instruments.append(Instrument(
                slot_index=i,
                voice_type='voice_square_1',
                type_byte=0x01,
                base_midi_key=60, pan=0,
                sweep=0, duty_cycle=2,
                attack=0, decay=0, sustain=15, release=0,
            ))

        vg = Voicegroup(name=vg_name, number=num, instruments=instruments)
        self._voicegroup_data.voicegroups[vg_name] = vg
        self._dirty_voicegroups.add(vg_name)
        self.modified.emit()
        self._populate_vg_list()

        # Select the new VG
        for i in range(self._vg_tree.topLevelItemCount()):
            item = self._vg_tree.topLevelItem(i)
            if item.data(0, _ROLE_VG_NAME) == vg_name:
                self._vg_tree.setCurrentItem(item)
                break

    def _on_clone_voicegroup(self):
        """Clone the selected voicegroup."""
        if not self._current_vg or not self._voicegroup_data:
            return

        used = set(self._voicegroup_data.voicegroup_numbers)
        new_num = 0
        for n in range(256):
            if n not in used:
                new_num = n
                break

        num_str, ok = QInputDialog.getText(
            self, "Clone Voicegroup",
            f"Clone VG {self._current_vg.number} as which number?\n"
            f"Next available: {new_num}",
            text=str(new_num))
        if not ok or not num_str:
            return

        try:
            num = int(num_str)
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Must be a number.")
            return

        if num < 0 or num > 255:
            QMessageBox.warning(self, "Invalid", "Must be 0–255.")
            return

        vg_name = f"voicegroup{num:03d}"
        if vg_name in self._voicegroup_data.voicegroups:
            QMessageBox.warning(
                self, "Already Exists",
                f"Voicegroup {num} already exists.")
            return

        # Deep copy instruments
        new_instruments = copy.deepcopy(self._current_vg.instruments)

        from core.sound.voicegroup_parser import Voicegroup
        new_vg = Voicegroup(
            name=vg_name, number=num, instruments=new_instruments)
        self._voicegroup_data.voicegroups[vg_name] = new_vg
        self._dirty_voicegroups.add(vg_name)
        self.modified.emit()
        self._populate_vg_list()

        # Select the new VG
        for i in range(self._vg_tree.topLevelItemCount()):
            item = self._vg_tree.topLevelItem(i)
            if item.data(0, _ROLE_VG_NAME) == vg_name:
                self._vg_tree.setCurrentItem(item)
                break

    def _on_generate_gm(self):
        """Generate a General MIDI voicegroup from available samples."""
        if not self._voicegroup_data:
            QMessageBox.warning(
                self, "No Data",
                "Load a project first before generating a GM voicegroup.")
            return

        from core.sound.gm_voicegroup import (
            generate_gm_voicegroup, get_gm_coverage_report,
        )

        # Show coverage preview
        report = get_gm_coverage_report(self._voicegroup_data)
        mapped = report['mapped_slots']
        total_ds = report['total_ds_samples']
        total_unique = report['total_unique']

        reply = QMessageBox.question(
            self, "Generate GM Voicegroup",
            f"This will create a new voicegroup with standard General MIDI\n"
            f"instrument mapping using your project's existing instruments.\n\n"
            f"DirectSound samples found: {total_ds}\n"
            f"Total unique instruments (all types): {total_unique}\n"
            f"GM slots with real instruments: {mapped}/128\n"
            f"Remaining slots will be empty (silent filler).\n\n"
            f"This is useful for MIDI imports — the imported song can\n"
            f"reference this voicegroup and all standard instruments\n"
            f"will play with real samples.\n\n"
            f"Create the GM voicegroup?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        gm_vg = generate_gm_voicegroup(self._voicegroup_data)

        # Add to data
        self._voicegroup_data.voicegroups[gm_vg.name] = gm_vg
        self._dirty_voicegroups.add(gm_vg.name)
        self.modified.emit()
        self._populate_vg_list()

        # Select the new VG
        for i in range(self._vg_tree.topLevelItemCount()):
            item = self._vg_tree.topLevelItem(i)
            if item.data(0, _ROLE_VG_NAME) == gm_vg.name:
                self._vg_tree.setCurrentItem(item)
                break

        QMessageBox.information(
            self, "GM Voicegroup Created",
            f"Created {gm_vg.name} (voicegroup {gm_vg.number})\n\n"
            f"{mapped} of 128 slots mapped to real instruments.\n"
            f"Empty slots are silent filler (no duplicates).\n\n"
            f"You can now use this voicegroup for MIDI imports\n"
            f"or assign it to any song.")

    def _on_auto_label(self):
        """Generate friendly labels for all voicegroups from song usage."""
        from core.sound.voicegroup_labels import (
            generate_labels_from_song_table, save_labels,
        )
        if not self._song_table:
            QMessageBox.information(
                self, "No Song Data",
                "Song table not loaded — can't generate labels.")
            return

        generated = generate_labels_from_song_table(self._song_table)
        if not generated:
            QMessageBox.information(
                self, "No Labels",
                "Couldn't find any song-to-voicegroup mappings.")
            return

        # Merge: auto-labels fill in blanks but don't overwrite
        # user-renamed ones
        count_new = 0
        for vg_name, label in generated.items():
            if vg_name not in self._vg_labels:
                self._vg_labels[vg_name] = label
                count_new += 1

        if self._project_root:
            save_labels(self._project_root, self._vg_labels)

        # Refresh the tree to show labels
        self._populate_vg_list()

        QMessageBox.information(
            self, "Labels Generated",
            f"Labeled {count_new} new voicegroups from song usage.\n"
            f"({len(self._vg_labels)} total labels.)\n\n"
            f"These are display-only — your source code is not modified.")

    def _on_rename_label(self):
        """Set a custom friendly name for the selected voicegroup."""
        item = self._vg_tree.currentItem()
        if not item:
            QMessageBox.information(
                self, "No Selection",
                "Select a voicegroup in the list first.")
            return
        self._rename_vg_label(item)

    def _on_vg_double_clicked(self, item, column):
        """Double-click on a voicegroup opens the rename dialog."""
        if item:
            self._rename_vg_label(item)

    def _rename_vg_label(self, item):
        """Open a dialog to rename the selected voicegroup's display label."""
        from core.sound.voicegroup_labels import save_labels

        vg_name = item.data(0, _ROLE_VG_NAME)
        if not vg_name:
            return

        current_label = self._vg_labels.get(vg_name, '')

        new_label, ok = QInputDialog.getText(
            self, "Rename Voicegroup",
            f"Friendly name for {vg_name}:\n"
            "(This only changes what you see in PorySuite,\n"
            "it doesn't modify your source code.)",
            text=current_label)
        if not ok:
            return

        new_label = new_label.strip()
        if new_label:
            self._vg_labels[vg_name] = new_label
        elif vg_name in self._vg_labels:
            del self._vg_labels[vg_name]

        # Save to disk
        if self._project_root:
            save_labels(self._project_root, self._vg_labels)

        # Refresh the tree to show the updated label
        num_str = vg_name.replace('voicegroup', '')
        if new_label:
            item.setText(0, f"VG {num_str} — {new_label}")
        else:
            item.setText(0, f"VG {num_str}")

    def _on_delete_voicegroup(self):
        """Delete the selected voicegroup."""
        if not self._current_vg or not self._voicegroup_data:
            return

        vg = self._current_vg

        # Check if any songs use it
        if self._song_table:
            songs_using = [
                e.friendly_name for e in self._song_table.entries
                if e.voicegroup_index == vg.number]
            if songs_using:
                QMessageBox.warning(
                    self, "Cannot Delete",
                    f"Voicegroup {vg.number} is used by "
                    f"{len(songs_using)} song(s):\n\n"
                    + ', '.join(songs_using[:10])
                    + ('\n...' if len(songs_using) > 10 else '')
                    + "\n\nReassign those songs first.")
                return

        reply = QMessageBox.question(
            self, "Delete Voicegroup?",
            f"Delete voicegroup {vg.number} ({vg.name})?\n\n"
            "This removes all 128 instrument slots.\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        del self._voicegroup_data.voicegroups[vg.name]
        self._dirty_voicegroups.add(vg.name)  # track for save
        self._current_vg = None
        self.modified.emit()
        self._populate_vg_list()
        self._clear_slot_editor()
        self._slot_tree.clear()

    # ═══════════════════════════════════════════════════════════════════════
    # Save — write voice_groups.inc
    # ═══════════════════════════════════════════════════════════════════════

    def mark_voicegroups_dirty(self, names):
        """Mark voicegroups as modified from an external source
        (e.g. Instruments tab editing shared instrument objects)."""
        for name in names:
            self._dirty_voicegroups.add(name)

    def has_unsaved_changes(self) -> bool:
        """Check if any voicegroups have been modified."""
        return len(self._dirty_voicegroups) > 0

    def save_to_disk(self):
        """Write the full voice_groups.inc file to disk."""
        if not self._voicegroup_data or not self._project_root:
            return

        out_path = os.path.join(
            self._project_root, 'sound', 'voice_groups.inc')

        # ── Preserve .include lines from the original file ──────────
        # The file may contain lines like:
        #   .include "sound/cry_tables.inc"
        # inserted between voicegroup blocks.  We record which VG label
        # each include appeared *after* so we can re-insert them.
        include_after: dict[str, list[str]] = {}   # vg_name -> [include lines]
        includes_before_first: list[str] = []       # includes before any VG
        if os.path.isfile(out_path):
            try:
                with open(out_path, 'r', encoding='utf-8') as f:
                    last_vg = None
                    for raw in f:
                        stripped = raw.strip()
                        if stripped.endswith('::'):
                            last_vg = stripped.rstrip(':')
                        elif stripped.startswith('.include'):
                            if last_vg is None:
                                includes_before_first.append(stripped)
                            else:
                                include_after.setdefault(
                                    last_vg, []).append(stripped)
            except OSError:
                pass

        # ── Build output ────────────────────────────────────────────
        lines = []
        if includes_before_first:
            lines.extend(includes_before_first)
            lines.append("")

        for vg_name in sorted(self._voicegroup_data.voicegroups.keys()):
            vg = self._voicegroup_data.voicegroups[vg_name]
            lines.append(f"\t.align 2")
            lines.append(f"{vg_name}::")

            for inst in vg.instruments:
                line = self._instrument_to_asm(inst)
                lines.append(f"\t{line}")

            lines.append("")  # blank line between voicegroups

            # Re-insert any .include lines that were after this VG
            if vg_name in include_after:
                for inc_line in include_after[vg_name]:
                    lines.append(inc_line)
                lines.append("")

        with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines))
            f.write('\n')

        self._dirty_voicegroups.clear()
        _log.info("Wrote voice_groups.inc (%d voicegroups)",
                  len(self._voicegroup_data.voicegroups))

    @staticmethod
    def _instrument_to_asm(inst) -> str:
        """Convert an Instrument back to its assembly macro line."""
        vt = inst.voice_type

        if vt in ('voice_directsound', 'voice_directsound_no_resample',
                   'voice_directsound_alt'):
            return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                    f"{inst.sample_label}, "
                    f"{inst.attack}, {inst.decay}, "
                    f"{inst.sustain}, {inst.release}")

        if vt in ('voice_square_1', 'voice_square_1_alt'):
            return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                    f"{inst.sweep}, {inst.duty_cycle}, "
                    f"{inst.attack}, {inst.decay}, "
                    f"{inst.sustain}, {inst.release}")

        if vt in ('voice_square_2', 'voice_square_2_alt'):
            return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                    f"{inst.duty_cycle}, "
                    f"{inst.attack}, {inst.decay}, "
                    f"{inst.sustain}, {inst.release}")

        if vt in ('voice_programmable_wave',
                   'voice_programmable_wave_alt'):
            return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                    f"{inst.wave_label}, "
                    f"{inst.attack}, {inst.decay}, "
                    f"{inst.sustain}, {inst.release}")

        if vt in ('voice_noise', 'voice_noise_alt'):
            return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                    f"{inst.period}, "
                    f"{inst.attack}, {inst.decay}, "
                    f"{inst.sustain}, {inst.release}")

        if vt == 'voice_keysplit':
            return (f"{vt} {inst.target_voicegroup}, "
                    f"{inst.keysplit_table}")

        if vt == 'voice_keysplit_all':
            return f"{vt} {inst.target_voicegroup}"

        # Fallback — shouldn't happen
        return f"@ unknown voice type: {vt}"

    # ═══════════════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════════════

    def select_voicegroup(self, vg_name: str) -> bool:
        """Programmatically select a voicegroup by name."""
        for i in range(self._vg_tree.topLevelItemCount()):
            item = self._vg_tree.topLevelItem(i)
            if item.data(0, _ROLE_VG_NAME) == vg_name:
                self._vg_tree.setCurrentItem(item)
                return True
        return False
