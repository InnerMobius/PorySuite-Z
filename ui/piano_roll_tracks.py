"""Track sidebar panel for the Piano Roll.

Shows per-track controls: instrument display, volume, pan, mute/solo toggles.
Also provides add/remove/duplicate track management, mute all/unmute all/clear
solo, and per-track instrument (VOICE slot) selection.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QScrollArea, QFrame, QMessageBox, QInputDialog,
    QSizePolicy, QComboBox,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


# Same track colors as the piano roll canvas
_TRACK_COLORS = [
    QColor(100, 160, 255), QColor(255, 120, 100),
    QColor(100, 220, 140), QColor(220, 160, 100),
    QColor(180, 120, 220), QColor(100, 200, 220),
    QColor(220, 100, 160), QColor(160, 200, 100),
]


class _ColorSwatch(QWidget):
    """Tiny colored square showing the track's color."""

    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(14, 14)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(0, 0, 14, 14, self._color)
        p.setPen(QPen(self._color.darker(150), 1))
        p.drawRect(0, 0, 13, 13)
        p.end()


class TrackRow(QFrame):
    """One row in the track sidebar representing a single track."""

    mute_toggled = pyqtSignal(int, bool)   # track_index, muted
    solo_toggled = pyqtSignal(int, bool)   # track_index, soloed
    volume_changed = pyqtSignal(int, int)  # track_index, volume (0-127)
    pan_changed = pyqtSignal(int, int)     # track_index, pan (0-127, 64=center)
    instrument_changed = pyqtSignal(int, int)  # track_index, new voice slot
    selected = pyqtSignal(int)             # track_index — user clicked this row

    def __init__(self, track_index: int, track_info: dict,
                 instrument_names: Optional[list] = None, parent=None):
        super().__init__(parent)
        self._index = track_index
        self._muted = False
        self._soloed = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            TrackRow { border: 1px solid #444; border-radius: 3px;
                       background: #2a2a2f; margin: 1px; }
            TrackRow:hover { background: #333338; }
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        # ── Row 1: color swatch + track name + mute/solo ──
        top_row = QHBoxLayout()
        color = _TRACK_COLORS[track_index % len(_TRACK_COLORS)]
        top_row.addWidget(_ColorSwatch(color))

        name = track_info.get('name', f'Track {track_index + 1}')
        name_lbl = QLabel(f"<b>{name}</b>")
        name_lbl.setStyleSheet("font-size: 11px; color: #ddd;")
        top_row.addWidget(name_lbl, 1)

        self._btn_mute = QPushButton("M")
        self._btn_mute.setFixedSize(22, 22)
        self._btn_mute.setToolTip("Mute this track")
        self._btn_mute.setCheckable(True)
        self._btn_mute.setStyleSheet("""
            QPushButton { background: #444; color: #aaa; border-radius: 3px; font-size: 10px; }
            QPushButton:checked { background: #a44; color: #fff; }
        """)
        self._btn_mute.toggled.connect(lambda v: self._on_mute(v))
        top_row.addWidget(self._btn_mute)

        self._btn_solo = QPushButton("S")
        self._btn_solo.setFixedSize(22, 22)
        self._btn_solo.setToolTip("Solo this track (hear only this one)")
        self._btn_solo.setCheckable(True)
        self._btn_solo.setStyleSheet("""
            QPushButton { background: #444; color: #aaa; border-radius: 3px; font-size: 10px; }
            QPushButton:checked { background: #4a4; color: #fff; }
        """)
        self._btn_solo.toggled.connect(lambda v: self._on_solo(v))
        top_row.addWidget(self._btn_solo)
        layout.addLayout(top_row)

        # ── Row 2: instrument selector (dropdown) ──
        self._inst_combo = QComboBox()
        self._inst_combo.setStyleSheet("font-size: 9px;")
        self._inst_combo.setToolTip("Pick an instrument from the voicegroup")
        self._inst_combo.setMaxVisibleItems(20)
        install_scroll_guard(self._inst_combo)

        # Store instrument names and populate the combo
        self._instrument_names = instrument_names or []
        self._populate_inst_combo()

        # Set initial selection
        inst_num = max(0, track_info.get('instrument', 0))
        if 0 <= inst_num < self._inst_combo.count():
            self._inst_combo.setCurrentIndex(inst_num)

        self._inst_combo.currentIndexChanged.connect(self._on_instrument_changed)
        layout.addWidget(self._inst_combo)

        # ── Row 3: channel info ──
        ch = track_info.get('channel', '')
        ch_text = f"Channel {ch}" if ch else ""
        notes = track_info.get('note_count', 0)
        if ch_text:
            ch_text += f"  |  {notes} notes"
        else:
            ch_text = f"{notes} notes"
        ch_lbl = QLabel(ch_text)
        ch_lbl.setStyleSheet("font-size: 9px; color: #777;")
        layout.addWidget(ch_lbl)

        # ── Row 4: Volume slider ──
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("Vol"))
        vol_row.itemAt(0).widget().setStyleSheet("font-size: 9px; color: #888;")
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        install_scroll_guard(self._vol_slider)
        self._vol_slider.setRange(0, 127)
        self._vol_slider.setValue(track_info.get('volume', 100))
        self._vol_slider.setFixedHeight(16)
        self._vol_slider.setToolTip("Track volume (0-127)")
        self._vol_slider.valueChanged.connect(
            lambda v: self.volume_changed.emit(self._index, v))
        vol_row.addWidget(self._vol_slider, 1)
        self._vol_label = QLabel(str(track_info.get('volume', 100)))
        self._vol_label.setFixedWidth(24)
        self._vol_label.setStyleSheet("font-size: 9px; color: #888;")
        self._vol_slider.valueChanged.connect(
            lambda v: self._vol_label.setText(str(v)))
        vol_row.addWidget(self._vol_label)
        layout.addLayout(vol_row)

        # ── Row 5: Pan slider ──
        pan_row = QHBoxLayout()
        pan_row.addWidget(QLabel("Pan"))
        pan_row.itemAt(0).widget().setStyleSheet("font-size: 9px; color: #888;")
        self._pan_slider = QSlider(Qt.Orientation.Horizontal)
        install_scroll_guard(self._pan_slider)
        self._pan_slider.setRange(0, 127)
        self._pan_slider.setValue(track_info.get('pan', 64))
        self._pan_slider.setFixedHeight(16)
        self._pan_slider.setToolTip("Track pan (0=left, 64=center, 127=right)")
        self._pan_slider.valueChanged.connect(
            lambda v: self.pan_changed.emit(self._index, v))
        pan_row.addWidget(self._pan_slider, 1)
        self._pan_label = QLabel(self._pan_text(track_info.get('pan', 64)))
        self._pan_label.setFixedWidth(24)
        self._pan_label.setStyleSheet("font-size: 9px; color: #888;")
        self._pan_slider.valueChanged.connect(
            lambda v: self._pan_label.setText(self._pan_text(v)))
        pan_row.addWidget(self._pan_label)
        layout.addLayout(pan_row)

    def _pan_text(self, v: int) -> str:
        if v < 60:
            return f"L{64 - v}"
        elif v > 68:
            return f"R{v - 64}"
        return "C"

    def _on_mute(self, checked: bool):
        self._muted = checked
        self.mute_toggled.emit(self._index, checked)

    def _on_solo(self, checked: bool):
        self._soloed = checked
        self.solo_toggled.emit(self._index, checked)

    def _populate_inst_combo(self):
        """Fill the instrument dropdown with numbered names."""
        self._inst_combo.blockSignals(True)
        old_idx = self._inst_combo.currentIndex()
        self._inst_combo.clear()
        for i in range(128):
            if self._instrument_names and i < len(self._instrument_names):
                name = self._instrument_names[i]
            else:
                name = ''
            label = f"{i}: {name}" if name else f"{i}: (empty)"
            self._inst_combo.addItem(label)
        if 0 <= old_idx < 128:
            self._inst_combo.setCurrentIndex(old_idx)
        self._inst_combo.blockSignals(False)

    def _on_instrument_changed(self, index: int):
        if index < 0:
            return
        self.instrument_changed.emit(self._index, index)

    def set_muted(self, muted: bool):
        """Programmatically set mute state (for Mute All / Unmute All)."""
        self._btn_mute.blockSignals(True)
        self._btn_mute.setChecked(muted)
        self._muted = muted
        self._btn_mute.blockSignals(False)

    def set_soloed(self, soloed: bool):
        """Programmatically set solo state (for Clear Solo)."""
        self._btn_solo.blockSignals(True)
        self._btn_solo.setChecked(soloed)
        self._soloed = soloed
        self._btn_solo.blockSignals(False)

    def update_instrument_names(self, names: list):
        """Update the instrument dropdown (when voicegroup changes)."""
        self._instrument_names = names
        self._populate_inst_combo()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._index)
        super().mousePressEvent(event)

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_soloed(self) -> bool:
        return self._soloed


class TrackSidebar(QWidget):
    """Scrollable sidebar showing all track rows with management buttons."""

    track_selected = pyqtSignal(int)         # user clicked a track row
    track_muted = pyqtSignal(int, bool)
    track_soloed = pyqtSignal(int, bool)
    track_volume = pyqtSignal(int, int)
    track_pan = pyqtSignal(int, int)
    track_instrument = pyqtSignal(int, int)  # track_index, voice slot
    track_added = pyqtSignal()
    track_removed = pyqtSignal(int)          # track index
    track_duplicated = pyqtSignal(int)       # track index
    mute_solo_changed = pyqtSignal()         # batch mute/solo changed (mute all, etc.)
    voicegroup_changed = pyqtSignal(str)     # new voicegroup name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)

        # Header
        header = QLabel("Tracks")
        header.setFont(QFont("", 10, QFont.Weight.Bold))
        header.setStyleSheet("color: #ccc; padding: 4px;")
        layout.addWidget(header)

        # Voicegroup selector
        vg_row = QHBoxLayout()
        vg_row.addWidget(QLabel("VG:"))
        vg_row.itemAt(0).widget().setStyleSheet("font-size: 9px; color: #999;")
        self._vg_combo = QComboBox()
        install_scroll_guard(self._vg_combo)
        self._vg_combo.setToolTip(
            "Voicegroup for this song.\n"
            "Changing this changes which instruments are available.")
        self._vg_combo.setStyleSheet("font-size: 10px;")
        self._vg_combo.currentTextChanged.connect(self._on_vg_changed)
        # Refresh the list from live data every time the dropdown opens
        _orig_show = self._vg_combo.showPopup
        def _show_and_refresh():
            self._refresh_vg_combo()
            _orig_show()
        self._vg_combo.showPopup = _show_and_refresh
        vg_row.addWidget(self._vg_combo, 1)

        self._btn_rename_vg = QPushButton("✎")
        self._btn_rename_vg.setFixedSize(22, 20)
        self._btn_rename_vg.setStyleSheet("font-size: 11px;")
        self._btn_rename_vg.setToolTip(
            "Rename this voicegroup's display label.\n"
            "Only affects PorySuite — doesn't touch your source code.")
        self._btn_rename_vg.clicked.connect(self._on_rename_vg)
        vg_row.addWidget(self._btn_rename_vg)

        self._vg_data = None  # live reference, set via set_voicegroup_data()
        self._vg_labels = {}
        self._project_root = ''
        self._song_table = None
        layout.addLayout(vg_row)

        # Mute/Solo batch controls
        ms_row = QHBoxLayout()
        ms_row.setSpacing(2)

        btn_mute_all = QPushButton("Mute All")
        btn_mute_all.setFixedHeight(20)
        btn_mute_all.setStyleSheet("font-size: 9px;")
        btn_mute_all.setToolTip("Mute every track")
        btn_mute_all.clicked.connect(self._on_mute_all)
        ms_row.addWidget(btn_mute_all)

        btn_unmute_all = QPushButton("Unmute All")
        btn_unmute_all.setFixedHeight(20)
        btn_unmute_all.setStyleSheet("font-size: 9px;")
        btn_unmute_all.setToolTip("Unmute every track")
        btn_unmute_all.clicked.connect(self._on_unmute_all)
        ms_row.addWidget(btn_unmute_all)

        btn_clear_solo = QPushButton("Clear Solo")
        btn_clear_solo.setFixedHeight(20)
        btn_clear_solo.setStyleSheet("font-size: 9px;")
        btn_clear_solo.setToolTip("Remove solo from all tracks")
        btn_clear_solo.clicked.connect(self._on_clear_solo)
        ms_row.addWidget(btn_clear_solo)

        layout.addLayout(ms_row)

        # Scrollable track list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_widget = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_widget)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(2)
        self._scroll_layout.addStretch()
        self._scroll.setWidget(self._scroll_widget)
        layout.addWidget(self._scroll, 1)

        # Management buttons
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("+")
        self._btn_add.setFixedSize(30, 24)
        self._btn_add.setToolTip("Add a new empty track")
        self._btn_add.clicked.connect(self.track_added.emit)
        btn_row.addWidget(self._btn_add)

        self._btn_remove = QPushButton("-")
        self._btn_remove.setFixedSize(30, 24)
        self._btn_remove.setToolTip("Remove the selected track")
        self._btn_remove.clicked.connect(self._on_remove)
        btn_row.addWidget(self._btn_remove)

        self._btn_dup = QPushButton("Dup")
        self._btn_dup.setFixedSize(40, 24)
        self._btn_dup.setToolTip("Duplicate the selected track")
        self._btn_dup.clicked.connect(self._on_duplicate)
        btn_row.addWidget(self._btn_dup)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._rows: list[TrackRow] = []
        self._selected_index: int = -1

    def set_voicegroup_data(self, vg_data, vg_labels=None,
                            project_root='', song_table=None):
        """Store live references for dynamic refresh and auto-labeling."""
        self._vg_data = vg_data
        self._vg_labels = vg_labels or {}
        self._project_root = project_root
        self._song_table = song_table

    def set_voicegroup_list(self, names: list[str], current: str = ''):
        """Populate the voicegroup combo box."""
        self._vg_combo.blockSignals(True)
        self._vg_combo.clear()
        for name in sorted(names):
            self._vg_combo.addItem(name)
        if current:
            idx = self._vg_combo.findText(current)
            if idx >= 0:
                self._vg_combo.setCurrentIndex(idx)
        self._vg_combo.blockSignals(False)

    def _refresh_vg_combo(self):
        """Re-read voicegroup names from the live data.

        Called when the dropdown is opened so newly created voicegroups
        (like a GM voicegroup) appear without needing to reopen the
        piano roll. Uses friendly labels when available.
        """
        if self._vg_data is None:
            return
        from core.sound.voicegroup_labels import get_display_name
        current = self._vg_combo.currentText()
        display_names = [get_display_name(n, self._vg_labels)
                         for n in sorted(self._vg_data.voicegroups.keys())]
        # Only update if the list actually changed
        existing = [self._vg_combo.itemText(i)
                    for i in range(self._vg_combo.count())]
        if existing == display_names:
            return
        self._vg_combo.blockSignals(True)
        self._vg_combo.clear()
        for name in display_names:
            self._vg_combo.addItem(name)
        idx = self._vg_combo.findText(current)
        if idx >= 0:
            self._vg_combo.setCurrentIndex(idx)
        self._vg_combo.blockSignals(False)

    def load_tracks(self, track_infos: list[dict],
                    instrument_names: Optional[list] = None):
        """Populate the sidebar from a list of track info dicts.

        Each dict: {name, channel, instrument, instrument_name,
                     volume, pan, note_count}
        instrument_names: list of 128 friendly names for the voicegroup slots
        """
        # Clear existing
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        for i, info in enumerate(track_infos):
            row = TrackRow(i, info, instrument_names=instrument_names)
            row.selected.connect(self._on_row_selected)
            row.mute_toggled.connect(self.track_muted.emit)
            row.solo_toggled.connect(self.track_soloed.emit)
            row.volume_changed.connect(self.track_volume.emit)
            row.pan_changed.connect(self.track_pan.emit)
            row.instrument_changed.connect(self.track_instrument.emit)
            # Insert before the stretch
            self._scroll_layout.insertWidget(
                self._scroll_layout.count() - 1, row)
            self._rows.append(row)

    def update_instrument_names(self, names: list):
        """Update instrument name lookups on all rows (voicegroup changed)."""
        for row in self._rows:
            row.update_instrument_names(names)

    def _on_row_selected(self, index: int):
        self._selected_index = index
        self.track_selected.emit(index)
        # Highlight the selected row
        for i, row in enumerate(self._rows):
            if i == index:
                row.setStyleSheet("""
                    TrackRow { border: 1px solid #68a; border-radius: 3px;
                               background: #333340; margin: 1px; }
                """)
            else:
                row.setStyleSheet("""
                    TrackRow { border: 1px solid #444; border-radius: 3px;
                               background: #2a2a2f; margin: 1px; }
                    TrackRow:hover { background: #333338; }
                """)

    def _on_remove(self):
        if self._selected_index >= 0:
            self.track_removed.emit(self._selected_index)

    def _on_duplicate(self):
        if self._selected_index >= 0:
            self.track_duplicated.emit(self._selected_index)

    def _on_mute_all(self):
        for row in self._rows:
            row.set_muted(True)
        self.mute_solo_changed.emit()

    def _on_unmute_all(self):
        for row in self._rows:
            row.set_muted(False)
        self.mute_solo_changed.emit()

    def _on_clear_solo(self):
        for row in self._rows:
            row.set_soloed(False)
        self.mute_solo_changed.emit()

    def _on_vg_changed(self, text: str):
        if text:
            self.voicegroup_changed.emit(text)

    def _on_rename_vg(self):
        """Let the user rename the current voicegroup's display label."""
        from core.sound.voicegroup_labels import (
            vg_name_from_display, get_display_name, save_labels,
        )
        display = self._vg_combo.currentText()
        if not display:
            return
        vg_name = vg_name_from_display(display)
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

        # Refresh the combo to show the new name
        new_display = get_display_name(vg_name, self._vg_labels)
        self._vg_combo.blockSignals(True)
        idx = self._vg_combo.currentIndex()
        self._vg_combo.setItemText(idx, new_display)
        self._vg_combo.blockSignals(False)

    def get_mute_solo_state(self) -> dict:
        """Return which tracks are muted/soloed.

        Returns dict with 'muted': set[int], 'soloed': set[int]
        """
        muted = set()
        soloed = set()
        for i, row in enumerate(self._rows):
            if row.is_muted:
                muted.add(i)
            if row.is_soloed:
                soloed.add(i)
        return {'muted': muted, 'soloed': soloed}


def extract_track_infos(song_data, vg_data=None) -> list[dict]:
    """Extract per-track info dicts from a SongData for the sidebar.

    Reads the first VOICE, VOL, and PAN commands from each track.
    If vg_data is provided, looks up instrument names from the voicegroup.
    """
    infos = []
    for i, track in enumerate(song_data.tracks):
        info = {
            'name': f"Track {i + 1}",
            'channel': track.midi_channel,
            'instrument': -1,
            'instrument_name': '',
            'volume': 100,
            'pan': 64,
            'note_count': 0,
        }

        if track.midi_channel is not None:
            info['name'] = f"Track {i + 1} (Ch{track.midi_channel})"

        for cmd in track.commands:
            if cmd.cmd == 'VOICE' and cmd.value is not None and info['instrument'] < 0:
                info['instrument'] = cmd.value
            elif cmd.cmd == 'VOL' and cmd.value is not None:
                info['volume'] = cmd.value
                break  # Only read the first volume
            elif cmd.cmd == 'PAN' and cmd.value is not None:
                info['pan'] = cmd.value
            elif cmd.cmd == 'NOTE':
                info['note_count'] += 1

        # Count all notes
        info['note_count'] = sum(
            1 for c in track.commands if c.cmd == 'NOTE')

        # Look up instrument name from voicegroup
        if vg_data and info['instrument'] >= 0:
            import re
            vg_name = song_data.voicegroup
            if vg_name:
                m = re.search(r'(\d+)', vg_name)
                if m:
                    vg = vg_data.get_voicegroup_by_number(int(m.group(1)))
                    if vg and 0 <= info['instrument'] < len(vg.instruments):
                        inst = vg.instruments[info['instrument']]
                        info['instrument_name'] = inst.friendly_name

        infos.append(info)
    return infos


def get_instrument_names(vg_data, voicegroup_name: str) -> list[str]:
    """Get a list of 128 friendly instrument names for a voicegroup.

    Returns empty strings for filler/unknown slots.
    """
    if not vg_data or not voicegroup_name:
        return [''] * 128

    import re
    m = re.search(r'(\d+)', voicegroup_name)
    if not m:
        return [''] * 128

    vg = vg_data.get_voicegroup_by_number(int(m.group(1)))
    if not vg:
        return [''] * 128

    names = []
    for inst in vg.instruments:
        names.append(inst.friendly_name if inst else '')
    # Pad to 128
    while len(names) < 128:
        names.append('')
    return names
