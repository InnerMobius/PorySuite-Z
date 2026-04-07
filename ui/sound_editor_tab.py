"""
Sound Editor tab for PorySuite-Z.

Phases 3-4 of the Sound Editor roadmap. Contains sub-tabs:
  - Songs: browse, preview, and play all songs
  - Instruments: browse, inspect, and preview all instruments across voicegroups
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QIcon, QAction
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QPushButton, QSlider, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView,
    QComboBox, QFrame, QProgressBar, QSizePolicy,
    QToolButton, QTabWidget, QMenu,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


# ---------------------------------------------------------------------------
# File logger — writes to porysuite/sound_editor.log
# ---------------------------------------------------------------------------

_log = logging.getLogger("SoundEditor")
_log.setLevel(logging.DEBUG)
_log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sound_editor.log")
_fh = logging.FileHandler(_log_path, mode="w", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
_log.addHandler(_fh)


# ---------------------------------------------------------------------------
# Song list item data role
# ---------------------------------------------------------------------------

_ROLE_SONG_KEY = Qt.ItemDataRole.UserRole + 1
_ROLE_SONG_TYPE = Qt.ItemDataRole.UserRole + 2
_ROLE_TRACK_VG = Qt.ItemDataRole.UserRole + 3
_ROLE_TRACK_SLOT = Qt.ItemDataRole.UserRole + 4


class SoundEditorTab(QWidget):
    """Main Sound Editor widget — hosts Songs and Instruments sub-tabs."""

    modified = pyqtSignal()
    # Signal emitted from render thread to safely trigger playback on main thread
    _render_done = pyqtSignal()
    _render_failed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root: str = ""
        self._song_table = None
        self._voicegroup_data = None
        self._sample_data = None
        self._samples_loaded = False
        self._all_songs: dict = {}       # label -> SongData (parsed)
        self._vg_labels: dict = {}       # voicegroup name -> friendly label
        self._current_song = None        # currently selected SongData
        self._current_song_key = ""
        self._audio_player = None
        self._rendered_audio = None
        self._render_thread = None
        self._is_rendering = False

        self._build_ui()
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(100)
        self._playback_timer.timeout.connect(self._update_playback_position)

        # Connect thread-safe signals
        self._render_done.connect(self._start_playback)
        self._render_failed.connect(self._on_render_error)

        _log.info("SoundEditorTab created")

    # ═════════════════════════════════════════════════════════════════════════
    # UI Construction
    # ═════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Sub-tab bar ────────────────────────────────────────────────────
        self._tab_widget = QTabWidget()
        layout.addWidget(self._tab_widget)

        # ── Songs sub-tab ──────────────────────────────────────────────────
        songs_page = QWidget()
        songs_layout = QVBoxLayout(songs_page)
        songs_layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        songs_layout.addWidget(splitter)

        # ── Left panel: Song browser ────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Title
        title = QLabel("Songs")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        left_layout.addWidget(title)

        # Filter row
        filter_row = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "BGM (Music)", "SE (Sound Effects)"])
        install_scroll_guard(self._filter_combo)
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_combo)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search songs...")
        self._search_box.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search_box)
        left_layout.addLayout(filter_row)

        # Song count + Import button row
        count_row = QHBoxLayout()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: grey; font-size: 11px;")
        count_row.addWidget(self._count_label, 1)

        self._btn_import_midi = QPushButton("Import MIDI...")
        self._btn_import_midi.setToolTip(
            "Import a MIDI file as a new song.\n"
            "Converts it to GBA format and registers it in the project.")
        self._btn_import_midi.setEnabled(False)
        self._btn_import_midi.clicked.connect(self._open_midi_import)
        count_row.addWidget(self._btn_import_midi)

        self._btn_import_s = QPushButton("Import .s...")
        self._btn_import_s.setToolTip(
            "Import a .s assembly song file from another project.\n"
            "Copies it into this project and registers it in the song table.")
        self._btn_import_s.setEnabled(False)
        self._btn_import_s.clicked.connect(self._open_s_import)
        count_row.addWidget(self._btn_import_s)
        left_layout.addLayout(count_row)

        # Song tree
        self._song_tree = QTreeWidget()
        self._song_tree.setHeaderLabels(["Name", "Type", "Tracks", "VG"])
        self._song_tree.setRootIsDecorated(False)
        self._song_tree.setAlternatingRowColors(True)
        self._song_tree.setSortingEnabled(True)
        self._song_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        header = self._song_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._song_tree.currentItemChanged.connect(self._on_song_selected)
        self._song_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._song_tree.customContextMenuRequested.connect(
            self._on_song_context_menu)
        left_layout.addWidget(self._song_tree)

        splitter.addWidget(left)

        # ── Right panel: Song details and playback ──────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Song info section
        info_group = QGroupBox("Song Details")
        info_layout = QVBoxLayout(info_group)

        self._song_name_label = QLabel("No song selected")
        self._song_name_label.setFont(QFont("", 14, QFont.Weight.Bold))
        self._song_name_label.setWordWrap(True)
        info_layout.addWidget(self._song_name_label)

        self._song_const_label = QLabel("")
        self._song_const_label.setStyleSheet("color: grey;")
        info_layout.addWidget(self._song_const_label)

        # Properties grid
        props_frame = QFrame()
        props_layout = QHBoxLayout(props_frame)
        props_layout.setContentsMargins(0, 8, 0, 0)

        # Left column
        props_left = QVBoxLayout()
        vg_row = QHBoxLayout()
        self._detail_voicegroup = QLabel("Voicegroup: —")
        vg_row.addWidget(self._detail_voicegroup)
        self._btn_goto_vg = QPushButton("Open ▶")
        self._btn_goto_vg.setFixedSize(55, 22)
        self._btn_goto_vg.setToolTip(
            "Open this voicegroup in the Voicegroups tab\n"
            "to view and edit its instrument slots.")
        self._btn_goto_vg.setVisible(False)
        self._btn_goto_vg.clicked.connect(self._on_goto_voicegroup)
        vg_row.addWidget(self._btn_goto_vg)
        vg_row.addStretch()
        self._detail_tracks = QLabel("Tracks: —")
        self._detail_tempo = QLabel("Tempo: —")
        props_left.addLayout(vg_row)
        props_left.addWidget(self._detail_tracks)
        props_left.addWidget(self._detail_tempo)

        # Right column
        props_right = QVBoxLayout()
        self._detail_reverb = QLabel("Reverb: —")
        self._detail_volume = QLabel("Volume: —")
        self._detail_loop = QLabel("Loop: —")
        props_right.addWidget(self._detail_reverb)
        props_right.addWidget(self._detail_volume)
        props_right.addWidget(self._detail_loop)

        props_layout.addLayout(props_left)
        props_layout.addLayout(props_right)
        info_layout.addWidget(props_frame)

        right_layout.addWidget(info_group)

        # ── Transport controls ──────────────────────────────────────────────
        transport_group = QGroupBox("Playback")
        transport_layout = QVBoxLayout(transport_group)

        # Buttons row
        btn_row = QHBoxLayout()

        self._btn_play = QPushButton("Play")
        self._btn_play.setFixedWidth(80)
        self._btn_play.clicked.connect(self._on_play)
        self._btn_play.setEnabled(False)
        btn_row.addWidget(self._btn_play)

        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setFixedWidth(80)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)
        btn_row.addWidget(self._btn_stop)

        self._btn_piano_roll = QPushButton("Piano Roll")
        self._btn_piano_roll.setFixedWidth(90)
        self._btn_piano_roll.setToolTip(
            "Open a visual piano roll view of this song.\n"
            "Shows all notes as colored bars on a grid.")
        self._btn_piano_roll.clicked.connect(self._open_piano_roll)
        self._btn_piano_roll.setEnabled(False)
        btn_row.addWidget(self._btn_piano_roll)

        btn_row.addStretch()

        # Volume slider
        vol_label = QLabel("Vol:")
        btn_row.addWidget(vol_label)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        # Read default volume from settings
        try:
            from app_info import get_settings_path
            from PyQt6.QtCore import QSettings as _QS
            _sv = int(_QS(get_settings_path(), _QS.Format.IniFormat).value(
                "sound/preview_volume", 80))
        except Exception:
            _sv = 80
        self._vol_slider.setValue(_sv)
        self._vol_slider.setFixedWidth(100)
        install_scroll_guard(self._vol_slider)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        btn_row.addWidget(self._vol_slider)

        transport_layout.addLayout(btn_row)

        # Progress bar (doubles as timeline)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("0:00 / 0:00")
        transport_layout.addWidget(self._progress_bar)

        # Render status
        self._render_label = QLabel("")
        self._render_label.setStyleSheet("color: grey; font-size: 11px;")
        transport_layout.addWidget(self._render_label)

        right_layout.addWidget(transport_group)

        # ── Track list (mute/solo per track) ────────────────────────────────
        tracks_group = QGroupBox("Tracks")
        tracks_layout = QVBoxLayout(tracks_group)

        self._track_tree = QTreeWidget()
        self._track_tree.setHeaderLabels(["#", "Channel", "Notes", "Instrument"])
        self._track_tree.setRootIsDecorated(False)
        self._track_tree.setAlternatingRowColors(True)
        header = self._track_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._track_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._track_tree.customContextMenuRequested.connect(
            self._on_track_context_menu)
        tracks_layout.addWidget(self._track_tree)

        right_layout.addWidget(tracks_group)
        right_layout.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([350, 550])

        self._tab_widget.addTab(songs_page, "Songs")

        # ── Instruments sub-tab ────────────────────────────────────────────
        from ui.instruments_tab import InstrumentsTab
        self._instruments_tab = InstrumentsTab()
        self._tab_widget.addTab(self._instruments_tab, "Instruments")
        self._instruments_tab.modified.connect(self.modified.emit)

        # ── Voicegroups sub-tab ────────────────────────────────────────────
        from ui.voicegroups_tab import VoicegroupsTab
        self._voicegroups_tab = VoicegroupsTab()
        self._voicegroups_tab.set_sound_editor_ref(self)
        self._tab_widget.addTab(self._voicegroups_tab, "Voicegroups")
        self._voicegroups_tab.modified.connect(self.modified.emit)

    # ═════════════════════════════════════════════════════════════════════════
    # Project loading
    # ═════════════════════════════════════════════════════════════════════════

    def load_project(self, project_root: str):
        """Load all sound data from the project. Called when project opens."""
        self._project_root = project_root
        self._song_tree.clear()
        self._track_tree.clear()
        self._samples_loaded = False

        try:
            from core.sound.song_table_manager import load_song_table
            from core.sound.voicegroup_parser import load_voicegroup_data

            self._song_table = load_song_table(project_root)
            self._voicegroup_data = load_voicegroup_data(project_root)
            # Defer sample loading until playback — keeps startup fast
            self._sample_data = None

            # Load any saved friendly voicegroup labels (UI-only)
            from core.sound.voicegroup_labels import load_labels
            self._vg_labels = load_labels(project_root)

            self._populate_song_list()

            self._btn_import_midi.setEnabled(True)
            self._btn_import_s.setEnabled(True)

            # Pass data to sub-tabs
            if hasattr(self, '_instruments_tab'):
                self._instruments_tab.load_data(
                    project_root, self._voicegroup_data)
            if hasattr(self, '_voicegroups_tab'):
                self._voicegroups_tab.load_data(
                    project_root, self._voicegroup_data,
                    self._song_table, self._vg_labels)
        except Exception as e:
            self._song_name_label.setText(f"Failed to load sound data: {e}")
            import traceback
            traceback.print_exc()

    def _check_audio_deps(self) -> bool:
        """Check if numpy and sounddevice are installed."""
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
            return True
        except ImportError as e:
            _log.error("Missing audio dependency: %s", e)
            self._render_label.setText(
                f"Missing package: {e.name}. Open the Setup Wizard from the "
                f"launcher to install it, then restart.")
            self._btn_play.setEnabled(False)
            return False

    def _ensure_samples_loaded(self):
        """Load PCM sample data on first use (deferred from startup)."""
        if self._samples_loaded:
            _log.info("Samples already loaded")
            return
        if not self._project_root:
            _log.warning("No project root set")
            return
        try:
            _log.info("Loading samples from %s ...", self._project_root)
            from core.sound.sample_loader import load_sample_data
            self._sample_data = load_sample_data(self._project_root, load_pcm=True)
            self._samples_loaded = True
            _log.info("Loaded %d DS samples, %d waves",
                       len(self._sample_data.direct_sound),
                       len(self._sample_data.programmable_waves))
            # Share samples with instruments tab for preview
            if hasattr(self, '_instruments_tab'):
                self._instruments_tab.set_sample_data(self._sample_data)
            # Share samples with voicegroups tab for sample picker
            if hasattr(self, '_voicegroups_tab'):
                self._voicegroups_tab.set_sample_data(self._sample_data)
        except Exception as e:
            _log.error("Failed to load samples: %s", e, exc_info=True)

    def _populate_song_list(self):
        """Fill the song tree with all entries from the song table."""
        if not self._song_table:
            return

        self._song_tree.clear()
        self._song_tree.setSortingEnabled(False)

        for entry in self._song_table.entries:
            # Determine type
            if entry.constant.startswith("MUS_"):
                song_type = "BGM"
            elif entry.constant.startswith("SE_"):
                song_type = "SE"
            else:
                song_type = "?"

            item = QTreeWidgetItem()
            item.setText(0, entry.friendly_name)
            item.setText(1, song_type)
            item.setText(2, "")  # track count filled after parse
            item.setText(3, f"VG{entry.voicegroup_index}" if entry.voicegroup_index is not None else "—")
            item.setData(0, _ROLE_SONG_KEY, entry.label)
            item.setData(0, _ROLE_SONG_TYPE, song_type)
            item.setToolTip(0, f"{entry.constant} (#{entry.index})")
            self._song_tree.addTopLevelItem(item)

        self._song_tree.setSortingEnabled(True)
        self._song_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._update_count_label()

    def _update_count_label(self):
        """Update the visible/total count label."""
        visible = 0
        total = self._song_tree.topLevelItemCount()
        for i in range(total):
            if not self._song_tree.topLevelItem(i).isHidden():
                visible += 1
        if visible == total:
            self._count_label.setText(f"{total} songs")
        else:
            self._count_label.setText(f"Showing {visible} of {total} songs")

    # ═════════════════════════════════════════════════════════════════════════
    # Filtering
    # ═════════════════════════════════════════════════════════════════════════

    def _apply_filter(self):
        """Filter the song list by type and search text."""
        filter_idx = self._filter_combo.currentIndex()
        search = self._search_box.text().lower()

        for i in range(self._song_tree.topLevelItemCount()):
            item = self._song_tree.topLevelItem(i)
            song_type = item.data(0, _ROLE_SONG_TYPE)

            # Type filter
            type_ok = True
            if filter_idx == 1 and song_type != "BGM":
                type_ok = False
            elif filter_idx == 2 and song_type != "SE":
                type_ok = False

            # Text filter
            text_ok = True
            if search:
                name = item.text(0).lower()
                key = (item.data(0, _ROLE_SONG_KEY) or "").lower()
                text_ok = search in name or search in key

            item.setHidden(not (type_ok and text_ok))

        self._update_count_label()

    # ═════════════════════════════════════════════════════════════════════════
    # Song selection
    # ═════════════════════════════════════════════════════════════════════════

    def _on_song_selected(self, current, previous):
        """User clicked a song in the list."""
        if current is None:
            self._clear_details()
            return

        song_label = current.data(0, _ROLE_SONG_KEY)
        if not song_label:
            return

        self._current_song_key = song_label

        # Find the song table entry
        entry = self._song_table.by_label(song_label) if self._song_table else None
        if entry is None:
            self._song_name_label.setText(song_label)
            return

        # Parse the song if not cached
        if song_label not in self._all_songs:
            self._parse_song(entry)

        song = self._all_songs.get(song_label)

        # Update details panel
        self._song_name_label.setText(entry.friendly_name)
        self._song_const_label.setText(f"{entry.constant}  (#{entry.index})")

        if song:
            from core.sound.song_parser import get_song_tempo, get_loop_info
            bpm = get_song_tempo(song)
            loop_start, loop_end = get_loop_info(song)

            self._detail_voicegroup.setText(f"Voicegroup: {song.voicegroup}")
            self._btn_goto_vg.setVisible(bool(song.voicegroup))
            self._detail_tracks.setText(f"Tracks: {len(song.tracks)}")
            self._detail_tempo.setText(f"Tempo: {bpm} BPM")
            self._detail_reverb.setText(f"Reverb: {song.reverb}")
            self._detail_volume.setText(f"Volume: {song.master_volume}")

            if loop_start is not None:
                self._detail_loop.setText(f"Loop: tick {loop_start} -> {loop_end}")
            else:
                self._detail_loop.setText("Loop: None")

            # Update track count in the tree item
            current.setText(2, str(len(song.tracks)))

            # Populate track list
            self._populate_track_list(song)
            self._btn_play.setEnabled(True)
            self._btn_piano_roll.setEnabled(True)
        else:
            self._detail_voicegroup.setText(f"Voicegroup: VG{entry.voicegroup_index}")
            self._btn_goto_vg.setVisible(False)
            self._detail_tracks.setText("Tracks: (not parsed)")
            self._detail_tempo.setText("Tempo: —")
            self._detail_reverb.setText("Reverb: —")
            self._detail_volume.setText("Volume: —")
            self._detail_loop.setText("Loop: —")
            self._btn_play.setEnabled(False)
            self._btn_piano_roll.setEnabled(False)

    # ═════════════════════════════════════════════════════════════════════════
    # Song context menu (rename / delete)
    # ═════════════════════════════════════════════════════════════════════════

    def _on_song_context_menu(self, pos):
        """Right-click on a song — Rename / Delete."""
        item = self._song_tree.itemAt(pos)
        if item is None:
            return

        song_label = item.data(0, _ROLE_SONG_KEY)
        if not song_label or not self._song_table:
            return

        entry = self._song_table.by_label(song_label)
        if not entry:
            return

        menu = QMenu(self)
        rename_action = menu.addAction("Rename Song...")
        replace_action = menu.addAction("Replace with .s File...")
        export_action = menu.addAction("Export .s File...")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Song")

        chosen = menu.exec(self._song_tree.viewport().mapToGlobal(pos))
        if not chosen:
            return

        if chosen == rename_action:
            self._rename_song(entry)
        elif chosen == replace_action:
            self._replace_song(entry)
        elif chosen == export_action:
            self._export_song(entry)
        elif chosen == delete_action:
            self._delete_song(entry)

    def _rename_song(self, entry):
        """Rename a song — ask for new display name, update everything."""
        from PyQt6.QtWidgets import QInputDialog, QMessageBox
        import re

        old_name = entry.friendly_name
        new_name, ok = QInputDialog.getText(
            self, "Rename Song", "New song name:",
            text=old_name)

        if not ok or not new_name or not new_name.strip():
            return

        new_name = new_name.strip()

        # Derive constant from display name
        prefix = "MUS_" if entry.constant.startswith("MUS_") else "SE_"
        clean = re.sub(r'[^a-zA-Z0-9\s_-]', '', new_name)
        clean = re.sub(r'[\s-]+', '_', clean).upper()
        clean = re.sub(r'_+', '_', clean).strip('_')
        if not clean:
            QMessageBox.warning(self, "Invalid Name",
                                "Could not derive a valid constant from that name.")
            return

        new_constant = prefix + clean

        # Check for duplicates (skip self)
        if new_constant != entry.constant:
            from core.sound.midi_importer import validate_constant_name
            valid, err = validate_constant_name(new_constant, self._project_root)
            if not valid:
                QMessageBox.warning(self, "Invalid Name", err)
                return

        if new_constant == entry.constant:
            return  # No change

        try:
            from core.sound.song_table_manager import (
                rename_song, load_song_table)

            old_label = entry.label
            new_label = rename_song(
                self._project_root, entry.constant, new_constant)

            # Clear cached parse for old label
            self._all_songs.pop(old_label, None)

            # Reload
            self._song_table = load_song_table(self._project_root)
            self._populate_song_list()
            self.select_song_by_constant(new_constant)
            self.modified.emit()

            _log.info("Renamed song %s -> %s", old_label, new_label)
        except Exception as e:
            QMessageBox.critical(self, "Rename Failed", str(e))
            _log.error("Rename failed: %s", e)

    def _delete_song(self, entry):
        """Delete a song after confirmation."""
        from PyQt6.QtWidgets import QMessageBox
        from core.sound.song_table_manager import (
            find_song_references, delete_song, load_song_table)

        # Check for references
        refs = find_song_references(self._project_root, entry.constant)

        msg = f"Delete '{entry.friendly_name}' ({entry.constant})?\n\n"
        msg += "This will remove the song from songs.h, song_table.inc, "
        msg += "midi.cfg, and delete the .s file.\n\n"

        if refs:
            msg += "⚠ This song is referenced in:\n"
            for r in refs[:10]:
                msg += f"  • {r}\n"
            if len(refs) > 10:
                msg += f"  ... and {len(refs) - 10} more\n"
            msg += "\nThose references will break. Continue anyway?"
        else:
            msg += "No references found in project source."

        btn = QMessageBox.question(
            self, "Delete Song", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)

        if btn != QMessageBox.StandardButton.Yes:
            return

        try:
            label = entry.label

            # Stop playback if this song is playing
            if self._current_song_key == label:
                self._on_stop()

            delete_song(self._project_root, entry.constant)

            # Clear cached parse
            self._all_songs.pop(label, None)
            self._current_song = None
            self._current_song_key = ""

            # Reload
            self._song_table = load_song_table(self._project_root)
            self._populate_song_list()
            self._clear_details()
            self.modified.emit()

            _log.info("Deleted song %s (%s)", label, entry.constant)
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", str(e))
            _log.error("Delete failed: %s", e)

    def _export_song(self, entry):
        """Export a song's .s file to a user-chosen location."""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        import shutil

        midi_dir = os.path.join(self._project_root, "sound", "songs", "midi")
        s_file = os.path.join(midi_dir, f"{entry.label}.s")

        if not os.path.isfile(s_file):
            QMessageBox.warning(self, "Export Failed",
                                f"Song file not found:\n{s_file}")
            return

        dest, _ = QFileDialog.getSaveFileName(
            self, "Export Song .s File",
            f"{entry.label}.s",
            "Assembly Files (*.s);;All Files (*)")
        if not dest:
            return

        try:
            shutil.copy2(s_file, dest)
            QMessageBox.information(self, "Export Complete",
                                    f"Exported to:\n{dest}")
            _log.info("Exported song %s to %s", entry.label, dest)
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _replace_song(self, entry):
        """Replace a song's .s file with another .s file.

        Keeps the same constant, registration, and song table entry.
        Rewrites labels in the new file to match the existing song.
        """
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        import re
        import shutil

        source, _ = QFileDialog.getOpenFileName(
            self, f"Replace '{entry.friendly_name}' with...",
            "", "Assembly Files (*.s);;All Files (*)")
        if not source:
            return

        # Preview the source file
        try:
            from core.sound.song_parser import parse_song_file
            source_song = parse_song_file(source)
            track_info = f"{len(source_song.tracks)} tracks"
        except Exception:
            track_info = "could not preview"

        ans = QMessageBox.question(
            self, "Replace Song",
            f"Replace '{entry.friendly_name}' ({entry.constant}) with the "
            f"contents of:\n\n"
            f"  {os.path.basename(source)}\n"
            f"  ({track_info})\n\n"
            f"The existing .s file will be overwritten. The song's constant "
            f"name and registration stay the same — only the music data "
            f"changes.\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        try:
            midi_dir = os.path.join(
                self._project_root, "sound", "songs", "midi")
            dest_path = os.path.join(midi_dir, f"{entry.label}.s")

            # Read the source file
            with open(source, encoding="utf-8") as f:
                content = f.read()

            # Detect the source file's original label
            m = re.search(r'\.global\s+(\w+)\s*$', content, re.MULTILINE)
            if m:
                original_label = m.group(1)
                if original_label != entry.label:
                    content = content.replace(original_label, entry.label)

            # Write the replaced file
            with open(dest_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)

            # Clear cached parse so it re-reads from disk
            self._all_songs.pop(entry.label, None)

            # Re-select to refresh the details panel
            self._on_song_selected(self._song_tree.currentItem(), None)
            self.modified.emit()

            _log.info("Replaced song %s with %s", entry.label, source)
        except Exception as e:
            QMessageBox.critical(self, "Replace Failed", str(e))
            _log.error("Replace failed: %s", e)

    def _parse_song(self, entry):
        """Parse a song's .s file on demand."""
        from core.sound.song_parser import parse_song_file

        # Construct the .s file path
        midi_dir = os.path.join(self._project_root, "sound", "songs", "midi")
        s_file = os.path.join(midi_dir, f"{entry.label}.s")

        if not os.path.isfile(s_file):
            return

        try:
            song = parse_song_file(s_file)
            self._all_songs[entry.label] = song
        except Exception as e:
            print(f"Failed to parse {entry.label}: {e}")

    def _populate_track_list(self, song):
        """Fill the track detail tree with track info."""
        self._track_tree.clear()

        for track in song.tracks:
            note_count = sum(1 for c in track.commands if c.cmd == "NOTE")

            # Find the first VOICE command to show instrument name
            first_voice = None
            for cmd in track.commands:
                if cmd.cmd == "VOICE" and cmd.value is not None:
                    first_voice = cmd.value
                    break

            # Look up instrument name from voicegroup
            inst_name = ""
            track_vg_name = song.voicegroup or ""
            if first_voice is not None and self._voicegroup_data and song.voicegroup:
                vg = self._voicegroup_data.get_voicegroup(song.voicegroup)
                if vg:
                    inst = vg.get_instrument(first_voice)
                    if inst:
                        inst_name = inst.friendly_name

            item = QTreeWidgetItem()
            item.setText(0, str(track.index + 1))
            item.setText(1, str(track.midi_channel) if track.midi_channel is not None else "—")
            item.setText(2, str(note_count))
            item.setText(3, inst_name or f"Voice {first_voice}" if first_voice is not None else "—")

            # Store voicegroup + slot for "Go to Instrument" context menu
            if first_voice is not None and track_vg_name:
                item.setData(0, _ROLE_TRACK_VG, track_vg_name)
                item.setData(0, _ROLE_TRACK_SLOT, first_voice)

            self._track_tree.addTopLevelItem(item)

    def _clear_details(self):
        """Reset the details panel."""
        self._song_name_label.setText("No song selected")
        self._song_const_label.setText("")
        self._detail_voicegroup.setText("Voicegroup: —")
        self._btn_goto_vg.setVisible(False)
        self._detail_tracks.setText("Tracks: —")
        self._detail_tempo.setText("Tempo: —")
        self._detail_reverb.setText("Reverb: —")
        self._detail_volume.setText("Volume: —")
        self._detail_loop.setText("Loop: —")
        self._track_tree.clear()
        self._btn_play.setEnabled(False)
        self._btn_piano_roll.setEnabled(False)

    def _on_goto_voicegroup(self):
        """Click the 'Open' button next to the voicegroup label."""
        if not self._current_song_key:
            return
        song = self._all_songs.get(self._current_song_key)
        if not song or not song.voicegroup:
            return
        self._tab_widget.setCurrentWidget(self._voicegroups_tab)
        self._voicegroups_tab.select_voicegroup(song.voicegroup)

    def _open_piano_roll(self):
        """Open the Piano Roll window for the currently selected song."""
        if not self._current_song_key:
            return
        song = self._all_songs.get(self._current_song_key)
        if not song:
            return

        # Make sure samples are loaded for playback
        self._ensure_samples_loaded()

        from ui.piano_roll_window import PianoRollWindow
        # Close any existing piano roll window
        if hasattr(self, '_piano_roll_window') and self._piano_roll_window is not None:
            try:
                self._piano_roll_window.close()
            except RuntimeError:
                pass
        win = PianoRollWindow(
            song,
            voicegroup_data=self._voicegroup_data,
            sample_data=self._sample_data,
            vg_labels=self._vg_labels,
            project_root=self._project_root,
            song_table=self._song_table,
        )
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win.modified.connect(self.modified.emit)
        self._piano_roll_window = win
        win.show()

    # ═════════════════════════════════════════════════════════════════════════
    # Track context menu
    # ═════════════════════════════════════════════════════════════════════════

    def _on_track_context_menu(self, pos):
        """Right-click on a track — Go to Instrument / Go to Voicegroup."""
        item = self._track_tree.itemAt(pos)
        if item is None:
            return

        vg_name = item.data(0, _ROLE_TRACK_VG)
        slot_idx = item.data(0, _ROLE_TRACK_SLOT)

        # Also get the song's voicegroup for "Go to Voicegroup"
        song_vg = ""
        if self._current_song_key:
            song = self._all_songs.get(self._current_song_key)
            if song:
                song_vg = song.voicegroup or ""

        # Need at least one valid target
        if not vg_name and not song_vg:
            return

        menu = QMenu(self)

        # "Go to Instrument" — only if we know the specific slot
        go_inst_action = None
        if vg_name and slot_idx is not None:
            go_inst_action = menu.addAction("Go to Instrument")

        # "Go to Voicegroup" — always available if the song has a VG
        go_vg_action = None
        target_vg = vg_name or song_vg
        if target_vg:
            go_vg_action = menu.addAction("Go to Voicegroup")

        chosen = menu.exec(self._track_tree.viewport().mapToGlobal(pos))
        if not chosen:
            return

        if chosen == go_inst_action and vg_name and slot_idx is not None:
            self._tab_widget.setCurrentWidget(self._instruments_tab)
            if not self._instruments_tab.select_instrument(vg_name, slot_idx):
                _log.warning("Could not find instrument vg=%s slot=%d",
                             vg_name, slot_idx)

        elif chosen == go_vg_action and target_vg:
            self._tab_widget.setCurrentWidget(self._voicegroups_tab)
            self._voicegroups_tab.select_voicegroup(target_vg)

    # ═════════════════════════════════════════════════════════════════════════
    # Playback
    # ═════════════════════════════════════════════════════════════════════════

    def _on_play(self):
        """Render the selected song and play it."""
        _log.info("Play clicked")
        song_label = self._current_song_key
        song = self._all_songs.get(song_label)
        if not song or not self._voicegroup_data:
            _log.warning("Cannot play: song=%s, vg_data=%s", song is not None, self._voicegroup_data is not None)
            return

        # Check dependencies first
        if not self._check_audio_deps():
            return

        # Load samples on first play
        _log.info("Ensuring samples loaded...")
        self._ensure_samples_loaded()
        if not self._sample_data:
            _log.error("Sample data is None after loading attempt")
            self._render_label.setText("Failed to load audio samples")
            return
        _log.info("Samples OK: %d DirectSound, %d waves",
                   len(self._sample_data.direct_sound),
                   len(self._sample_data.programmable_waves))

        # If already playing, stop audio but don't reset rendering flag
        self._playback_timer.stop()
        if self._audio_player:
            self._audio_player.stop()
            self._audio_player = None

        self._btn_play.setEnabled(False)
        self._btn_play.setText("Rendering...")
        self._render_label.setText("Rendering song — this may take a few seconds...")
        self._progress_bar.setValue(0)
        self._is_rendering = True

        # Capture references for the thread (avoid accessing self from thread)
        vg_data = self._voicegroup_data
        sample_data = self._sample_data

        def _do_render():
            _log.info("Render thread started for %s", song_label)
            try:
                from core.sound.track_renderer import render_song, OUTPUT_SAMPLE_RATE
                _log.info("Calling render_song: %d tracks, vg=%s", len(song.tracks), song.voicegroup)
                # Read loop count from settings (default 2)
                from app_info import get_settings_path
                from PyQt6.QtCore import QSettings
                _s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
                _loops = int(_s.value("sound/loop_count", 2))
                audio = render_song(song, vg_data, sample_data,
                                    loop_count=_loops)
                _log.info("Render complete: shape=%s, peak=%.3f", audio.shape, float(audio.max()))

                if self._is_rendering:
                    self._rendered_audio = audio
                    self._render_sample_rate = OUTPUT_SAMPLE_RATE
                    _log.info("Emitting _render_done signal")
                    self._render_done.emit()
                else:
                    _log.warning("Render cancelled (_is_rendering=False)")
            except Exception as e:
                _log.error("Render FAILED: %s", e, exc_info=True)
                self._render_failed.emit(str(e))

        self._render_thread = threading.Thread(target=_do_render, daemon=True)
        self._render_thread.start()
        _log.info("Render thread launched")

    def _start_playback(self):
        """Called on the main thread via _render_done signal after rendering completes."""
        _log.info("_start_playback called, audio=%s",
                   "present" if self._rendered_audio is not None else "None")
        if self._rendered_audio is None:
            _log.warning("No rendered audio — aborting playback")
            self._btn_play.setText("Play")
            self._btn_play.setEnabled(True)
            return

        self._is_rendering = False
        self._btn_play.setEnabled(True)
        self._btn_stop.setEnabled(True)
        self._render_label.setText("")

        # Calculate duration
        from core.sound.track_renderer import OUTPUT_SAMPLE_RATE
        duration_sec = len(self._rendered_audio) / OUTPUT_SAMPLE_RATE
        self._playback_duration = duration_sec
        _log.info("Audio duration: %.1fs", duration_sec)

        # Initialize audio player
        try:
            from core.sound.audio_engine import AudioPlayer
            self._audio_player = AudioPlayer()
            self._audio_player.volume = self._vol_slider.value() / 100.0
            _log.info("Starting AudioPlayer at %dHz, vol=%.2f",
                       OUTPUT_SAMPLE_RATE, self._audio_player.volume)
            from PyQt6.QtCore import QSettings
            _mono = QSettings("PorySuite", "PorySuiteZ").value(
                "sound/output_mode", "Stereo") == "Mono"
            self._audio_player.play(self._rendered_audio, OUTPUT_SAMPLE_RATE,
                                    mono=_mono)
            _log.info("AudioPlayer started, is_playing=%s", self._audio_player.is_playing)
        except Exception as e:
            _log.error("AudioPlayer FAILED: %s", e, exc_info=True)
            self._render_label.setText(f"Playback failed: {e}")
            self._btn_play.setText("Play")
            return

        # Start position timer
        self._playback_timer.start()

        self._btn_play.setText("Pause")
        try:
            self._btn_play.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._btn_play.clicked.connect(self._on_pause_resume)

    def _on_pause_resume(self):
        """Toggle pause/resume."""
        if self._audio_player is None:
            return

        if self._audio_player.is_playing:
            self._audio_player.pause()
            self._btn_play.setText("Resume")
        else:
            self._audio_player.resume()
            self._btn_play.setText("Pause")

    def _on_stop(self):
        """Stop playback."""
        _log.info("Stop")
        self._is_rendering = False
        self._playback_timer.stop()

        if self._audio_player:
            self._audio_player.stop()
            self._audio_player = None

        self._rendered_audio = None
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("0:00 / 0:00")
        self._btn_stop.setEnabled(False)
        self._render_label.setText("")

        # Reset play button
        self._btn_play.setText("Play")
        self._btn_play.setEnabled(self._current_song_key in self._all_songs)
        try:
            self._btn_play.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._btn_play.clicked.connect(self._on_play)

    def _on_render_error(self, error_msg: str):
        """Called when rendering fails."""
        _log.error("Render error: %s", error_msg)
        self._is_rendering = False
        self._btn_play.setText("Play")
        self._btn_play.setEnabled(True)
        self._render_label.setText(f"Render failed: {error_msg}")

    def _on_volume_changed(self, value: int):
        """Volume slider moved."""
        if self._audio_player:
            self._audio_player.volume = value / 100.0

    def _update_playback_position(self):
        """Update the progress bar during playback."""
        if self._audio_player is None or self._rendered_audio is None:
            return

        from core.sound.track_renderer import OUTPUT_SAMPLE_RATE

        pos_samples = self._audio_player.position
        pos_sec = pos_samples / OUTPUT_SAMPLE_RATE
        total_sec = self._playback_duration

        if total_sec > 0:
            progress = int((pos_sec / total_sec) * 1000)
            self._progress_bar.setValue(min(progress, 1000))

        pos_min, pos_s = divmod(int(pos_sec), 60)
        tot_min, tot_s = divmod(int(total_sec), 60)
        self._progress_bar.setFormat(f"{pos_min}:{pos_s:02d} / {tot_min}:{tot_s:02d}")

        # Check if playback finished
        if not self._audio_player.is_playing and pos_samples > 0:
            self._on_stop()

    # ═════════════════════════════════════════════════════════════════════════
    # Public API — cross-editor integration
    # ═════════════════════════════════════════════════════════════════════════

    def preview_song_by_constant(self, constant: str) -> bool:
        """Play a song by its MUS_*/SE_* constant name **without switching tabs**.

        Called from EVENTide command widgets.  Renders in a background thread
        and plays audio silently — the user stays on whatever page they're on.
        Returns True if rendering was started.
        """
        if not self._song_table or not self._voicegroup_data:
            return False
        entry = self._song_table.by_constant(constant)
        if not entry:
            return False

        # Parse the song if not already loaded
        song_label = entry.label
        song = self._all_songs.get(song_label)
        if not song:
            try:
                from core.sound.song_parser import parse_song_file
                import os
                s_path = os.path.join(
                    self._project_root, 'sound', 'songs', 'midi',
                    song_label + '.s')
                if os.path.isfile(s_path):
                    song = parse_song_file(s_path)
                    self._all_songs[song_label] = song
            except Exception:
                pass
        if not song:
            return False

        # Check deps and load samples
        if not self._check_audio_deps():
            return False
        self._ensure_samples_loaded()
        if not self._sample_data:
            return False

        # Stop any current playback
        self._playback_timer.stop()
        if self._audio_player:
            self._audio_player.stop()
            self._audio_player = None

        # Render in background thread, play when done
        vg_data = self._voicegroup_data
        sample_data = self._sample_data

        def _bg_render():
            try:
                from core.sound.track_renderer import render_song, OUTPUT_SAMPLE_RATE
                from app_info import get_settings_path
                from PyQt6.QtCore import QSettings
                _s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
                _loops = int(_s.value("sound/loop_count", 2))
                audio = render_song(song, vg_data, sample_data,
                                    loop_count=_loops)
                if audio is not None and len(audio) > 0:
                    self._rendered_audio = audio
                    self._render_sample_rate = OUTPUT_SAMPLE_RATE
                    self._render_done.emit()
            except Exception as e:
                _log.error("Background preview render failed: %s", e)

        self._is_rendering = True
        self._render_thread = threading.Thread(target=_bg_render, daemon=True)
        self._render_thread.start()
        return True

    def select_song_by_constant(self, constant: str) -> bool:
        """Select (but don't play) a song by its constant name.

        Used by "Open in Sound Editor" buttons.  Returns True if found.
        """
        if not self._song_table:
            return False
        entry = self._song_table.by_constant(constant)
        if not entry:
            return False

        self._tab_widget.setCurrentIndex(0)

        for i in range(self._song_tree.topLevelItemCount()):
            item = self._song_tree.topLevelItem(i)
            if item.data(0, _ROLE_SONG_KEY) == entry.label:
                self._song_tree.setCurrentItem(item)
                self._song_tree.scrollToItem(item)
                return True
        return False

    def stop_preview(self):
        """Stop any currently playing preview.  Safe to call from anywhere."""
        self._on_stop()

    @property
    def is_playing(self) -> bool:
        """True if audio is currently playing."""
        return self._audio_player is not None and self._audio_player.is_playing

    @property
    def song_table(self):
        """Expose the song table for EVENTide integration."""
        return self._song_table

    # ═════════════════════════════════════════════════════════════════════════
    # MIDI Import
    # ═════════════════════════════════════════════════════════════════════════

    def _open_midi_import(self):
        """Open the MIDI Import wizard dialog."""
        if not self._project_root:
            return

        # Build voicegroup name list for the dialog
        vg_names = []
        if self._voicegroup_data:
            for name in sorted(self._voicegroup_data.voicegroups.keys()):
                vg = self._voicegroup_data.voicegroups[name]
                # Count non-filler slots
                non_filler = sum(
                    1 for inst in vg.instruments
                    if inst.voice_type not in (0x00,) or inst.sample_label
                )
                vg_names.append(f"voicegroup{vg.number:03d} ({non_filler} instruments)")

        from ui.dialogs.midi_import_dialog import MidiImportDialog
        dlg = MidiImportDialog(self._project_root, vg_names,
                               voicegroup_data=self._voicegroup_data,
                               parent=self)
        dlg.song_imported.connect(self._on_midi_imported)
        dlg.exec()

    def _on_midi_imported(self, constant: str):
        """Called after a MIDI was successfully imported."""
        # Reload the song table to pick up the new entry
        try:
            from core.sound.song_table_manager import load_song_table
            self._song_table = load_song_table(self._project_root)
            self._populate_song_list()

            # Select the new song
            self.select_song_by_constant(constant)

            # Mark dirty so the user sees there are unsaved changes
            self.modified.emit()
        except Exception as e:
            _log.error("Failed to reload after MIDI import: %s", e)

    def _open_s_import(self):
        """Open the .s file import wizard dialog."""
        if not self._project_root:
            return

        # Build voicegroup name list for the dialog
        vg_names = []
        if self._voicegroup_data:
            for name in sorted(self._voicegroup_data.voicegroups.keys()):
                vg = self._voicegroup_data.voicegroups[name]
                non_filler = sum(
                    1 for inst in vg.instruments
                    if inst.voice_type not in (0x00,) or inst.sample_label
                )
                vg_names.append(f"voicegroup{vg.number:03d} ({non_filler} instruments)")

        from ui.dialogs.s_file_import_dialog import SFileImportDialog
        dlg = SFileImportDialog(self._project_root, vg_names,
                                voicegroup_data=self._voicegroup_data,
                                parent=self)
        dlg.song_imported.connect(self._on_s_imported)
        dlg.exec()

    def _on_s_imported(self, constant: str):
        """Called after a .s file was successfully imported."""
        try:
            from core.sound.song_table_manager import load_song_table
            self._song_table = load_song_table(self._project_root)
            self._populate_song_list()

            # Select the new song
            self.select_song_by_constant(constant)

            # Mark dirty so the user sees there are unsaved changes
            self.modified.emit()
        except Exception as e:
            _log.error("Failed to reload after .s import: %s", e)
