"""Piano Roll Window for PorySuite-Z Sound Editor.

Standalone window hosting the piano roll. Uses real-time sequencer playback:
notes are synthesized on-the-fly as the cursor crosses them on the timeline.
No pre-rendering — pause, edit, resume instantly from any position.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QToolBar, QSlider, QStatusBar,
    QComboBox, QSpinBox, QSizePolicy, QMessageBox,
)

from ui.piano_roll_widget import PianoRollWidget, SNAP_VALUES
from ui.piano_roll_tracks import TrackSidebar, extract_track_infos, get_instrument_names
from ui.piano_roll_structure import SongStructurePanel
from ui.piano_roll_history import EditHistory
from ui.custom_widgets.scroll_guard import install_scroll_guard

import logging
import os

# Propagates to the Sound Editor's file handler (porysuite/sound_editor.log).
_log = logging.getLogger("SoundEditor.PianoRoll")


class PianoRollWindow(QMainWindow):
    """Standalone Piano Roll editor window.

    Follows PorySuite's save pattern: all edits stay in RAM. The dirty
    flag propagates up via `modified` signal to the main window. Changes
    are only written to disk when the user hits File → Save on the main
    toolbar.
    """

    closed = pyqtSignal()
    modified = pyqtSignal()  # propagates to parent → main window dirty flag
    saved = pyqtSignal()     # emitted after save_to_disk() clears _is_dirty

    def __init__(self, song_data, voicegroup_data=None, sample_data=None,
                 vg_labels=None, project_root='', song_table=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._song = song_data
        self._vg_data = voicegroup_data
        self._sample_data = sample_data
        self._vg_labels = vg_labels or {}
        self._project_root = project_root
        self._song_table = song_table

        # Real-time sequencer (created on first play)
        self._sequencer = None
        self._cursor_tick = 0
        self._total_ticks = 0
        self._bpm = 120
        self._is_dirty = False

        # What the canvas showed when it was last filled from the song (notes,
        # drawn controls, loop) — the save compares against it to tell what the
        # user actually edited. Set by _capture_baseline().
        self._baseline = None

        self.setWindowTitle(f"Piano Roll  --  {song_data.label}")
        self.setMinimumSize(900, 500)
        self.resize(1200, 700)

        self._build_ui()
        self._load_song()
        self._watcher = None
        self._disk_stamp = None
        self._watch_song_files()

        # Timer updates cursor position during playback (~33fps)
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(30)
        self._playback_timer.timeout.connect(self._update_playback_cursor)

    # ═══════════════════════════════════════════════════════════════════
    # UI setup
    # ═══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ──
        toolbar = QToolBar("Piano Roll Tools")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._btn_play = QPushButton("Play")
        self._btn_play.setFixedWidth(60)
        self._btn_play.setToolTip(
            "Play from current position (Space).\n"
            "Click or drag on the ruler bar (top) to set position.")
        self._btn_play.clicked.connect(self._on_play)
        toolbar.addWidget(self._btn_play)

        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setFixedWidth(60)
        self._btn_stop.setToolTip("Stop and reset to start.")
        self._btn_stop.clicked.connect(lambda: self._on_stop())
        self._btn_stop.setEnabled(False)
        toolbar.addWidget(self._btn_stop)

        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" Track:"))
        self._track_combo = QComboBox()
        install_scroll_guard(self._track_combo)
        self._track_combo.setFixedWidth(140)
        self._track_combo.setToolTip("Filter to a single track or view all.")
        self._track_combo.currentIndexChanged.connect(self._on_track_changed)
        toolbar.addWidget(self._track_combo)

        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" Snap:"))
        self._snap_combo = QComboBox()
        install_scroll_guard(self._snap_combo)
        self._snap_combo.addItems(list(SNAP_VALUES.keys()))
        self._snap_combo.setCurrentText('1/16')  # default to 1/16 for better grid alignment
        self._snap_combo.setToolTip("Grid snap resolution.")
        self._snap_combo.setFixedWidth(90)
        self._snap_combo.currentTextChanged.connect(self._on_snap_changed)
        toolbar.addWidget(self._snap_combo)

        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" BPM:"))
        self._bpm_spin = QSpinBox()
        install_scroll_guard(self._bpm_spin)
        self._bpm_spin.setRange(20, 510)
        self._bpm_spin.setValue(self._bpm)
        self._bpm_spin.setFixedWidth(85)
        self._bpm_spin.setToolTip("Song tempo in beats per minute.")
        self._bpm_spin.valueChanged.connect(self._on_bpm_changed)
        toolbar.addWidget(self._bpm_spin)

        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" Zoom:"))
        self._zoom_x_slider = QSlider(Qt.Orientation.Horizontal)
        install_scroll_guard(self._zoom_x_slider)
        self._zoom_x_slider.setRange(10, 500)
        self._zoom_x_slider.setValue(100)
        self._zoom_x_slider.setFixedWidth(80)
        self._zoom_x_slider.setToolTip("Horizontal zoom (Ctrl+Scroll).")
        self._zoom_x_slider.valueChanged.connect(self._on_zoom_x)
        toolbar.addWidget(self._zoom_x_slider)

        self._zoom_y_slider = QSlider(Qt.Orientation.Horizontal)
        install_scroll_guard(self._zoom_y_slider)
        self._zoom_y_slider.setRange(50, 400)
        self._zoom_y_slider.setValue(100)
        self._zoom_y_slider.setFixedWidth(50)
        self._zoom_y_slider.setToolTip("Vertical zoom (Ctrl+Shift+Scroll).")
        self._zoom_y_slider.valueChanged.connect(self._on_zoom_y)
        toolbar.addWidget(self._zoom_y_slider)

        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" Preview Vol:"))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        install_scroll_guard(self._vol_slider)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setFixedWidth(60)
        self._vol_slider.setToolTip(
            "Editor preview gain only — does NOT affect the saved song.\n"
            "To change the song's volume on the ROM, use the per-track VOL\n"
            "sliders in the track sidebar on the left."
        )
        self._vol_slider.valueChanged.connect(self._on_volume)
        toolbar.addWidget(self._vol_slider)

        toolbar.addSeparator()

        self._btn_boost = QPushButton("🔊 Max Volume")
        self._btn_boost.setFixedWidth(130)
        self._btn_boost.setToolTip(
            "Boost the ACTIVE track's volume to the ceiling:\n"
            "  • every VOL event on the track → 127\n"
            "  • every note's velocity on the track → 127\n"
            "  • master volume → 127 (so track VOL=127 can round-trip)\n\n"
            "Use on SFX that stay quiet even with the track slider maxed —\n"
            "imported MIDIs often carry soft per-note velocities that keep\n"
            "output under the ceiling.  Other tracks are left alone.\n\n"
            "Select the track you want boosted in the sidebar first."
        )
        self._btn_boost.clicked.connect(self._on_boost_all)
        toolbar.addWidget(self._btn_boost)

        self._btn_undo = QPushButton("Undo")
        self._btn_undo.setFixedWidth(50)
        self._btn_undo.setToolTip("Undo the last edit (Ctrl+Z).")
        self._btn_undo.clicked.connect(self._on_undo)
        toolbar.addWidget(self._btn_undo)
        self._btn_redo = QPushButton("Redo")
        self._btn_redo.setFixedWidth(50)
        self._btn_redo.setToolTip("Redo the edit you just undid (Ctrl+Y or Ctrl+Shift+Z).")
        self._btn_redo.clicked.connect(self._on_redo)
        toolbar.addWidget(self._btn_redo)

        self._btn_save = QPushButton("Save")
        self._btn_save.setFixedWidth(60)
        self._btn_save.setToolTip("Save song to disk (Ctrl+S).")
        self._btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(self._btn_save)

        toolbar.addSeparator()

        self._btn_flatten = QPushButton("Flatten")
        self._btn_flatten.setFixedWidth(70)
        self._btn_flatten.setToolTip(
            "Show + remove HIDDEN mid-song dynamics.\n\n"
            "Imported MIDIs carry volume / tempo / vibrato / pan envelopes that\n"
            "the piano roll can't show — so notes can play quieter or the tempo\n"
            "can shift partway with no way to see or edit it. Click to see exactly\n"
            "what this song carries, then optionally flatten each parameter to its\n"
            "starting value for a constant song. Notes are never touched.")
        self._btn_flatten.clicked.connect(self._on_flatten_dynamics)
        toolbar.addWidget(self._btn_flatten)

        self._btn_midi_events = QPushButton("MIDI Events")
        self._btn_midi_events.setToolTip(
            "List every event in this song's .mid (read-only): where it is, which\n"
            "track it belongs to, and whether the game's build uses it.")
        self._btn_midi_events.clicked.connect(self._on_midi_events)
        toolbar.addWidget(self._btn_midi_events)

        # ── Main area: sidebar + piano roll ──
        main_area = QHBoxLayout()

        self._track_sidebar = TrackSidebar()
        self._track_sidebar.track_selected.connect(self._on_sidebar_track_selected)
        self._track_sidebar.track_muted.connect(self._on_track_muted)
        self._track_sidebar.track_soloed.connect(self._on_track_soloed)
        self._track_sidebar.track_instrument.connect(self._on_track_instrument)
        self._track_sidebar.track_volume.connect(self._on_track_volume)
        self._track_sidebar.track_pan.connect(self._on_track_pan)
        self._track_sidebar.track_modulation.connect(self._on_track_modulation)
        self._track_sidebar.track_added.connect(self._on_add_track)
        self._track_sidebar.track_removed.connect(self._on_remove_track)
        self._track_sidebar.track_duplicated.connect(self._on_duplicate_track)
        self._track_sidebar.track_moved.connect(self._on_move_track)
        self._track_sidebar.mute_solo_changed.connect(self._apply_mute_solo)
        self._track_sidebar.voicegroup_changed.connect(self._on_voicegroup_changed)
        main_area.addWidget(self._track_sidebar)

        self._piano_roll = PianoRollWidget()
        self._piano_roll.hovered_note_changed.connect(self._on_hover_note)
        self._piano_roll.notes_changed.connect(self._on_notes_changed)
        self._piano_roll.status_message.connect(self._on_status_msg)
        self._piano_roll.ruler_clicked.connect(self._on_ruler_seek)
        self._piano_roll.lane_start_value_changed.connect(self._on_lane_start_value)
        main_area.addWidget(self._piano_roll, 1)

        # One undo / redo history for every edit in this window.
        self._history = EditHistory(self._capture_state, self._restore_state,
                                    self._same_state)
        self._history.on_change = self._update_undo_buttons
        self._piano_roll.canvas.set_history(self._history)

        self._structure_panel = SongStructurePanel()
        self._structure_panel.loop_requested.connect(self._on_loop_requested)
        self._structure_panel.seek_to_tick.connect(self._on_ruler_seek)
        self._structure_panel.time_signatures_requested.connect(self._on_time_signatures)
        self._structure_panel.shift_time_requested.connect(self._on_shift_time)
        main_area.addWidget(self._structure_panel)

        layout.addLayout(main_area, 1)

        # ── Status bar ──
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._note_label = QLabel("")
        self._status.addWidget(self._note_label)
        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: #888;")
        self._status.addWidget(self._info_label, 1)
        self._time_label = QLabel("")
        self._status.addPermanentWidget(self._time_label)
        self._edit_label = QLabel("")
        self._status.addPermanentWidget(self._edit_label)

        # ── Shortcuts ──
        QShortcut(QKeySequence("Ctrl+0"), self, self._reset_zoom)
        QShortcut(QKeySequence("Ctrl+="), self, self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, self._zoom_out)
        QShortcut(QKeySequence("Space"), self, self._toggle_play)
        QShortcut(QKeySequence("Ctrl+S"), self, self._on_save)
        QShortcut(QKeySequence("Ctrl+Z"), self, self._on_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self._on_redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._on_redo)

    def _load_song(self):
        song = self._song
        from core.sound.song_parser import get_song_tempo

        self._bpm = get_song_tempo(song)
        self._bpm_spin.blockSignals(True)
        self._bpm_spin.setValue(self._bpm)
        self._bpm_spin.blockSignals(False)

        self._track_combo.blockSignals(True)
        self._track_combo.clear()
        self._track_combo.addItem("All Tracks")
        for i, track in enumerate(song.tracks):
            ch = track.midi_channel
            ch_text = f"Ch{ch}" if ch is not None else f"#{i+1}"
            inst_name = ""
            for cmd in track.commands:
                if cmd.cmd == 'VOICE' and cmd.value is not None:
                    inst_name = f" (inst {cmd.value})"
                    break
            self._track_combo.addItem(f"Trk {i+1} {ch_text}{inst_name}")
        self._track_combo.blockSignals(False)

        # load_song_data flattens PATT/PEND/GOTO and computes correct
        # loop points and duration from the flattened timeline
        self._piano_roll.load_song_data(song, track_index=-1)

        # Read back the loop region and total ticks from the canvas
        # (computed during load_song_data from flattened commands)
        loop_s = self._piano_roll.canvas._loop_start
        loop_e = self._piano_roll.canvas._loop_end
        self._total_ticks = self._piano_roll.canvas._total_ticks

        # Populate voicegroup selector with friendly labels
        if self._vg_data:
            self._track_sidebar.set_voicegroup_data(
                self._vg_data, self._vg_labels, self._project_root,
                self._song_table)
            from core.sound.voicegroup_labels import get_display_name
            vg_display = [get_display_name(n, self._vg_labels)
                          for n in sorted(self._vg_data.voicegroups.keys())]
            current_display = get_display_name(
                song.voicegroup, self._vg_labels)
            self._track_sidebar.set_voicegroup_list(
                vg_display, current=current_display)

        # Get instrument names for the current voicegroup
        inst_names = get_instrument_names(self._vg_data, song.voicegroup)

        track_infos = extract_track_infos(song, self._vg_data)
        self._track_sidebar.load_tracks(track_infos, instrument_names=inst_names)

        # Structure panel: patterns, markers and the end, where they play.
        self._structure_panel.load_from_song(song, self._total_ticks)
        from core.sound.midi_exporter import song_time_signatures
        self._time_sigs = song_time_signatures(song)
        self._apply_bar_map()
        self._piano_roll.lane.tempo_base = max(1, int(song.tempo_base or 1))
        self._piano_roll.lane.voice_names = list(inst_names or [])

        # The loop is drawn where it PLAYS. A loop label's position in the
        # song's text is earlier than that whenever pattern calls come before
        # it (Kakariko: text 2879, plays 3935), and the canvas draws notes
        # where they play.
        from core.sound.midi_exporter import song_loop_region
        played = song_loop_region(song)
        if played is not None:
            loop_s, loop_e = played
        if loop_s is not None and loop_e is not None:
            self._piano_roll.canvas.set_loop_region(loop_s, loop_e)
        self._structure_panel.set_loop(self._piano_roll.canvas._loop_start,
                                       self._piano_roll.canvas._loop_end)
        # What the user sees now is what "unedited" means for the next save,
        # and where the undo history starts.
        self._capture_baseline()
        self._history.clear()

        note_count = len(self._piano_roll.canvas.get_notes())
        loop_text = ""
        if loop_s is not None:
            loop_text = f"  |  Loop: tick {loop_s}-{loop_e}"

        self._info_label.setText(
            f"{song.label}  |  {self._bpm} BPM  |  "
            f"{note_count} notes  |  {len(song.tracks)} tracks"
            f"{loop_text}")
        self.setWindowTitle(
            f"Piano Roll  --  {song.label}  "
            f"({self._bpm} BPM, {len(song.tracks)} tracks)")

    # ═══════════════════════════════════════════════════════════════════
    # Sequencer management
    # ═══════════════════════════════════════════════════════════════════

    def _ensure_sequencer(self):
        """Create the real-time sequencer if it doesn't exist yet."""
        if self._sequencer is not None:
            return True

        if self._vg_data is None or self._sample_data is None:
            self._time_label.setText(
                "Cannot play — open the Sound Editor first to load audio data")
            return False

        vg = self._vg_data.get_voicegroup(self._song.voicegroup)
        if vg is None:
            self._time_label.setText(
                f"Cannot play — voicegroup '{self._song.voicegroup}' not found")
            return False

        from core.sound.realtime_sequencer import RealtimeSequencer
        tbs = self._song.tempo_base if self._song.tempo_base else 1
        self._sequencer = RealtimeSequencer(
            voicegroup=vg,
            sample_data=self._sample_data,
            voicegroup_data=self._vg_data,
            bpm=self._bpm,
            tbs=tbs,
        )
        self._sequencer.volume = self._vol_slider.value() / 100.0

        # Give live playback the song's own reverb. Without this the piano
        # roll was dry while the same song in the ROM runs reverb 50, which is
        # a large part of why previews never sounded like the game.
        self._sequencer.set_reverb(getattr(self._song, 'reverb', 0) or 0)

        # Set loop region from the piano roll's flattened loop points
        loop_s = self._piano_roll.canvas._loop_start
        loop_e = self._piano_roll.canvas._loop_end
        if loop_s is not None and loop_e is not None:
            self._sequencer.set_loop(loop_s, loop_e)

        # Feed it the current notes and track states
        self._push_notes_to_sequencer()
        return True

    def _push_notes_to_sequencer(self):
        """Send the current piano roll notes to the sequencer."""
        if self._sequencer is None:
            return
        from core.sound.realtime_sequencer import extract_track_play_states
        notes = self._piano_roll.canvas.get_sequencer_events()
        track_states = extract_track_play_states(self._song)

        # Apply mute/solo
        ms = self._track_sidebar.get_mute_solo_state()
        for idx, ts in track_states.items():
            ts.muted = idx in ms['muted']

        self._sequencer.set_notes(notes, track_states)

        # Visible tracks for mute/solo filtering
        soloed = ms['soloed']
        muted = ms['muted']
        if soloed:
            self._sequencer.set_visible_tracks(soloed)
        elif muted:
            all_tracks = set(range(len(self._song.tracks)))
            self._sequencer.set_visible_tracks(all_tracks - muted)
        else:
            self._sequencer.set_visible_tracks(None)

    # ═══════════════════════════════════════════════════════════════════
    # Playback controls
    # ═══════════════════════════════════════════════════════════════════

    def _on_play(self):
        """Start playing from the current cursor position."""
        if not self._ensure_sequencer():
            return

        self._push_notes_to_sequencer()
        self._sequencer.play(start_tick=self._cursor_tick)

        self._btn_play.setText("Pause")
        self._btn_stop.setEnabled(True)
        self._playback_timer.start()

        try:
            self._btn_play.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._btn_play.clicked.connect(self._on_pause_resume)

    def _on_pause_resume(self):
        if self._sequencer is None:
            self._on_play()
            return
        if self._sequencer.is_playing:
            self._sequencer.pause()
            self._cursor_tick = self._sequencer.current_tick
            self._btn_play.setText("Resume")
            self._playback_timer.stop()
            self._time_label.setText(
                f"Paused at tick {self._cursor_tick}")
        else:
            self._push_notes_to_sequencer()
            self._sequencer.resume()
            self._btn_play.setText("Pause")
            self._playback_timer.start()

    def _on_stop(self, reset_cursor: bool = True):
        self._playback_timer.stop()
        if self._sequencer:
            self._sequencer.stop()
        if reset_cursor:
            self._cursor_tick = 0
            self._piano_roll.set_playback_tick(0)
        self._btn_play.setText("Play")
        self._btn_stop.setEnabled(False)
        self._time_label.setText("")

        try:
            self._btn_play.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._btn_play.clicked.connect(self._on_play)

    def _toggle_play(self):
        """Space bar toggles play/pause."""
        if self._sequencer and self._sequencer.is_playing:
            self._on_pause_resume()
        elif self._sequencer and not self._sequencer.is_playing:
            # Paused — resume
            self._on_pause_resume()
        else:
            self._on_play()

    def _on_ruler_seek(self, tick: int):
        """User clicked or dragged the ruler — jump to that position."""
        self._cursor_tick = tick
        self._piano_roll.set_playback_tick(tick)
        self._structure_panel.set_cursor_tick(tick)

        if self._sequencer is not None:
            self._sequencer.seek(tick)
            if self._sequencer.is_playing:
                self._time_label.setText(f"Tick {tick}")
            else:
                self._time_label.setText(
                    f"Tick {tick}  (press Space to play)")
        else:
            self._time_label.setText(
                f"Tick {tick}  (press Space to play)")

    def _update_playback_cursor(self):
        """Timer callback: move the cursor to the sequencer's position."""
        if self._sequencer is None:
            return

        # Song finished (past end, no loop) — stop playback but keep
        # the cursor where it ended so the user can click the ruler to
        # reposition before playing again.
        if not self._sequencer.is_playing:
            self._on_stop(reset_cursor=False)
            return

        tick = self._sequencer.current_tick
        self._cursor_tick = tick
        self._structure_panel.set_cursor_tick(tick)
        self._piano_roll.set_playback_tick(tick)
        self._piano_roll.scroll_to_tick(tick)

        # Time display
        tbs = self._song.tempo_base if self._song.tempo_base else 1
        tpf = self._bpm * tbs / 150.0
        tps = tpf * 59.7275
        if tps > 0:
            pos_sec = tick / tps
            total_sec = self._total_ticks / tps
            pos_min, pos_s = divmod(int(pos_sec), 60)
            tot_min, tot_s = divmod(int(total_sec), 60)
            self._time_label.setText(
                f"{pos_min}:{pos_s:02d} / {tot_min}:{tot_s:02d}  "
                f"(tick {tick})")

    # ═══════════════════════════════════════════════════════════════════
    # Toolbar slots
    # ═══════════════════════════════════════════════════════════════════

    def _on_track_changed(self, index: int):
        self._piano_roll.canvas.set_track_filter(index - 1)

    def _on_zoom_x(self, value: int):
        zx = value / 100.0
        self._piano_roll.canvas.set_zoom(zx, self._piano_roll.canvas.zoom_y())

    def _on_zoom_y(self, value: int):
        zy = value / 100.0
        self._piano_roll.canvas.set_zoom(self._piano_roll.canvas.zoom_x(), zy)

    def _reset_zoom(self):
        self._zoom_x_slider.setValue(100)
        self._zoom_y_slider.setValue(100)

    def _zoom_in(self):
        self._zoom_x_slider.setValue(min(500, self._zoom_x_slider.value() + 20))

    def _zoom_out(self):
        self._zoom_x_slider.setValue(max(10, self._zoom_x_slider.value() - 20))

    def _on_snap_changed(self, text: str):
        self._piano_roll.canvas.set_snap(text)

    def _on_bpm_changed(self, value: int):
        """Update the song's tempo when the user changes the BPM spinbox."""
        self._history.checkpoint(merge_key='tempo')
        self._bpm = value

        if self._song and self._song.tracks:
            tbs = self._song.tempo_base if self._song.tempo_base else 1
            # cmd.value must store the evaluated expression BPM * tbs / 2.
            # Storing raw BPM caused _raw_tempo() to double the byte every
            # save, and the old tbs-upgrade logic further corrupted speed
            # by changing the scale factor without adjusting the display BPM.
            # The spinbox range (20-510) means BPM * tbs / 2 ≤ 255 for tbs=1
            # (510 / 2 = 255), so no byte overflow is possible without upgrading.
            for cmd in self._song.tracks[0].commands:
                if cmd.cmd == 'TEMPO':
                    cmd.value = int(value * tbs / 2)
                    break
            # The canvas's starting tempo event is what the preview plays from
            # tick 0; left alone it put the old tempo straight back.
            for e in self._piano_roll.canvas._control_events:
                if e.get('track', 0) == 0 and e.get('type') == 'TEMPO' and e.get('tick', 0) == 0:
                    e['value'] = int(value * tbs / 2)
            self._piano_roll.lane.update()

        # Update sequencer playback speed if playing
        if self._sequencer:
            self._sequencer.set_bpm(value)

        self._mark_dirty()

    def _on_volume(self, value: int):
        if self._sequencer:
            self._sequencer.volume = value / 100.0

    def _on_hover_note(self, name: str):
        self._note_label.setText(f"Note: {name}" if name else "")

    def _on_notes_changed(self):
        """User edited notes. Push the updated notes to the sequencer."""
        self._mark_dirty()
        # No re-rendering needed — just update the sequencer's note list
        self._push_notes_to_sequencer()

    def _mark_dirty(self):
        """Mark the song as having unsaved changes.

        Emits `modified` signal which propagates up to the main window
        so File → Save knows to write this song's .s file.
        """
        if not self._is_dirty:
            self._is_dirty = True
            self.setWindowTitle(
                f"Piano Roll  --  {self._song.label}  [modified]  "
                f"({self._bpm} BPM, {len(self._song.tracks)} tracks)")
            self.modified.emit()
        self._edit_label.setText("Modified")

    def _on_status_msg(self, msg: str):
        self._status.showMessage(msg, 3000)

    def _on_midi_events(self):
        """Show the song's .mid event by event (read-only)."""
        s_path = self._song.file_path or ''
        mid = s_path[:-2] + '.mid' if s_path.endswith('.s') else ''
        from core.sound.midi_ownership import is_placeholder_mid
        if not mid or not os.path.isfile(mid) or is_placeholder_mid(mid):
            QMessageBox.information(self, "MIDI Events",
                                    f"'{self._song.label}' has no MIDI file to show.")
            return
        from ui.midi_event_inspector import MidiEventInspector
        self._midi_inspector = MidiEventInspector(
            self._song.label, open(mid, 'rb').read(), self._song.tempo_base or 1, self)
        self._midi_inspector.show()

    def _on_shift_time(self, at: int, delta: int):
        """Insert (*delta* > 0) or remove (*delta* < 0, the range at..at-delta)
        time across every track: notes, drawn controls, the loop and the time
        signatures after it move together. In a removed range, events inside
        it go, and a note reaching into it is shortened to end where it starts.
        Tick 0 (the tracks' starting values) never moves. One undo step."""
        if at < 1 or delta == 0:
            return
        canvas = self._piano_roll.canvas
        cut_end = at - delta if delta < 0 else at

        def moved(t):
            if t >= cut_end:
                return t + delta
            return t if t < at else None

        self._history.checkpoint()
        notes = []
        for n in canvas._notes:
            t, d = n['tick'], int(n.get('duration') or 0)
            if t >= cut_end:
                notes.append({**n, 'tick': t + delta})
            elif t < at:
                if delta > 0 and t + d > at:
                    # Held across the insertion point: its end moves with
                    # everything else after the point (so insert + remove of
                    # the same range gives the song back exactly).
                    d += delta
                elif delta < 0 and t + d > at:
                    # Reaches into the removed range: it loses the part inside
                    # it (ends at the cut, or later by what lay past the range).
                    d = d + delta if t + d > cut_end else at - t
                notes.append({**n, 'duration': max(1, d)})
        canvas._notes = notes
        canvas._control_events = [{**e, 'tick': moved(e['tick'])} for e in canvas._control_events
                                  if moved(e['tick']) is not None]
        canvas._selected.clear()
        ls, le = canvas._loop_start, canvas._loop_end
        if ls is not None and le is not None:
            ls = moved(ls) if moved(ls) is not None else at
            le = moved(le) if moved(le) is not None else at
            canvas.set_loop_region(*((ls, le) if le > ls else (None, None)))
        self._time_sigs = [(moved(t), n, d) for t, n, d in self._time_sigs if moved(t) is not None]
        canvas._total_ticks = max(96, canvas._total_ticks + delta)
        canvas._recalc_size()
        self._apply_bar_map()
        self._structure_panel.set_loop(canvas._loop_start, canvas._loop_end)
        canvas.update()
        if self._sequencer is not None:
            self._sequencer.stop()
            self._sequencer = None
        self._mark_dirty()
        what = "Inserted" if delta > 0 else "Removed"
        self._status.showMessage(f"{what} {abs(delta)} ticks at tick {at}", 4000)

    def _apply_bar_map(self):
        """Draw bar lines (grid, ruler, lanes, structure panel) where the
        song's time signatures put them."""
        from core.sound.midi_exporter import bar_starts_for
        canvas = self._piano_roll.canvas
        bars = bar_starts_for(self._time_sigs, max(canvas._total_ticks, 96))
        canvas.set_bar_starts(bars)
        self._structure_panel.set_bar_starts(bars)
        self._piano_roll._ruler.update()
        self._piano_roll.lane.update()

    def _on_time_signatures(self):
        """Edit the song's time signatures. They decide where bars fall (the
        build lays the song out in bars from them); they do not change what
        plays. Saved as the .mid's time-signature events."""
        from ui.piano_roll_time_signatures import TimeSignatureDialog
        dlg = TimeSignatureDialog(self._time_sigs, self._piano_roll.canvas._total_ticks, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        new = dlg.signatures()
        if new == list(self._time_sigs):
            return
        self._history.checkpoint()
        self._time_sigs = new
        self._apply_bar_map()
        self._mark_dirty()
        self._status.showMessage("Time signatures changed", 4000)

    def _on_loop_requested(self, start, end):
        """The Song Structure panel asked for a new loop (playback ticks), or
        for none ((None, None)). The canvas loop is what the save writes."""
        canvas = self._piano_roll.canvas
        if (start, end) == (canvas._loop_start, canvas._loop_end):
            return
        if start is not None and (end is None or end <= start or start < 0):
            return
        self._history.checkpoint()
        canvas.set_loop_region(start, end)
        if end is not None and end > canvas._total_ticks:
            canvas._total_ticks = end + 96
            canvas._recalc_size()
        canvas.update()
        self._structure_panel.set_loop(start, end)
        # (None, None) must clear the sequencer's loop too.
        if self._sequencer is not None:
            self._sequencer.set_loop(start, end)
        self._mark_dirty()
        self._status.showMessage(
            "Loop removed" if start is None else
            f"Loop set: {self._structure_panel.where(start)} → "
            f"{self._structure_panel.where(end)}", 4000)

    def _on_boost_all(self):
        """🔊 Max Volume — boost the ACTIVE track only.

        Three multipliers govern M4A output level:
          output = (master_volume/127) × (track_volume/127) × (note_velocity/127) × envelope

        The track-volume slider only controls the middle term. Imported
        MIDIs usually carry per-note velocities in the 60-100 range, so
        even with the slider maxed a note tops out at ~55-80% of full
        output — quieter than music with multiple tracks summing in the
        mixer.

        This action maxes the SELECTED track's VOL events + every note's
        velocity ON THAT TRACK, and raises master_volume to 127 so the
        track's new VOL=127 round-trips losslessly through save/reload.
        Other tracks are left alone.
        """
        # Which track is currently active?
        track_idx = getattr(self._piano_roll.canvas, '_active_track', 0) or 0
        if not (0 <= track_idx < len(self._song.tracks)):
            QMessageBox.warning(
                self, "Max Volume",
                "No active track — click a track in the sidebar first."
            )
            return

        ans = QMessageBox.question(
            self, "Max Volume",
            f"Boost every note and every VOL event on <b>Track {track_idx + 1}</b> "
            f"to 127, and raise the master volume to 127 so the track plays "
            f"at its loudest?<br><br>"
            f"Other tracks will be left untouched. This is useful when an "
            f"SFX is too quiet even with the track slider maxed — imported "
            f"MIDIs often have soft per-note velocities that stay under the "
            f"ceiling.<br><br>"
            f"You can still undo note changes on the piano roll canvas "
            f"(Ctrl+Z) if you don't like the result.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self._history.checkpoint()
        # 1) Master volume → 127 so track VOL=127 survives save/reload
        self._song.master_volume = 127
        # Master is now the max — re-expand every track slider's ceiling to 127.
        self._track_sidebar.set_volume_ceiling(127)

        # 2) Every VOL event on THIS track → 127 (and add one if missing);
        #    also every note-command velocity on THIS track → 127.
        from core.sound.song_parser import TrackCommand
        track = self._song.tracks[track_idx]
        found = False
        for cmd in track.commands:
            if cmd.cmd == 'VOL':
                cmd.value = 127
                found = True
        if not found:
            track.commands.insert(0, TrackCommand(
                cmd='VOL', tick=0, value=127, raw_line=''))
        for cmd in track.commands:
            if getattr(cmd, 'velocity', None) is not None:
                cmd.velocity = 127

        # 3) Canvas notes on THIS track only
        canvas = self._piano_roll.canvas
        for n in canvas._notes:
            if n.get('track', 0) == track_idx:
                n['velocity'] = 127
        # Any VOL control events the canvas is using for playback on this track
        for e in canvas._control_events:
            if e.get('track', 0) == track_idx and e.get('type') == 'VOL':
                e['value'] = 127
        canvas.notes_changed.emit()
        canvas.update()

        # 4) Live sequencer update for THIS track
        if self._sequencer is not None:
            self._sequencer.set_track_volume(track_idx, 127)

        self._mark_dirty()
        self._status.showMessage(
            f"Maxed Track {track_idx + 1} — VOL events and note velocities "
            f"set to 127. Save and rebuild to hear the result.",
            6000,
        )

    def _on_save(self):
        """Save button / Ctrl+S — writes the song to disk directly."""
        ans = QMessageBox.question(
            self, "Save Song",
            "Save this song to disk?\n\n"
            "Unlike most edits in PorySuite, this writes the .s assembly "
            "file directly — the change happens immediately, not when you "
            "use File → Save.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        try:
            path = self.save_to_disk()
            self._status.showMessage(f"Saved to {path}", 5000)
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", str(e))

    # ═══════════════════════════════════════════════════════════════════
    # Track sidebar slots
    # ═══════════════════════════════════════════════════════════════════

    def _on_sidebar_track_selected(self, index: int):
        """User clicked a track in the sidebar — set it as the active track
        for placing new notes, but do NOT change the track filter/visibility.
        The Track dropdown and M/S buttons are for controlling visibility."""
        self._piano_roll.canvas._active_track = max(0, index)
        self._piano_roll.lane.update()
        self._status.showMessage(
            f"Active track: {index + 1} (new notes go here)", 3000)

    def _on_track_muted(self, index: int, muted: bool):
        self._apply_mute_solo()

    def _on_track_soloed(self, index: int, soloed: bool):
        self._apply_mute_solo()

    def _on_track_instrument(self, track_index: int, voice_slot: int):
        """User changed the instrument for a track via the sidebar spinner."""
        if 0 <= track_index < len(self._song.tracks):
            track = self._song.tracks[track_index]
            self._history.checkpoint(merge_key=('instrument', id(track)))
            # Update the VOICE command in the track
            found = False
            for cmd in track.commands:
                if cmd.cmd == 'VOICE':
                    cmd.value = voice_slot
                    found = True
                    break
            if not found:
                from core.sound.song_parser import TrackCommand
                track.commands.insert(0, TrackCommand(
                    cmd='VOICE', tick=0, value=voice_slot, raw_line=''))
            # Update live sequencer — no need to destroy and recreate
            if self._sequencer is not None:
                self._sequencer.set_track_instrument(track_index, voice_slot)
            self._mark_dirty()

    _OPENING_ENDS = frozenset(('WAIT', 'NOTE', 'TIE', 'EOT', 'PATT', 'GOTO', 'FINE'))

    def _on_lane_start_value(self, track_index: int, kind: str):
        """The lane's tick-0 point is the track's STARTING value, which is not a
        mid-song event but one of the track's opening commands (before its
        first wait or note). Write the lane's value there — the same command
        the sidebar sliders write to — or remove it if the point was deleted.
        The lane already took the undo checkpoint."""
        if not (0 <= track_index < len(self._song.tracks)):
            return
        canvas = self._piano_roll.canvas
        points = [e for e in canvas._control_events
                  if e.get('track', 0) == track_index and e.get('type') == kind
                  and e.get('tick', 0) == 0]
        track = self._song.tracks[track_index]
        opening = []
        for c in track.commands:
            if c.cmd in self._OPENING_ENDS:
                break
            if c.cmd == kind:
                opening.append(c)
        if not points:
            track.commands = [c for c in track.commands if not any(c is o for o in opening)]
        elif opening:
            opening[-1].value = points[-1]['value']
            opening[-1].raw_line = ''
        else:
            from core.sound.song_parser import TrackCommand
            track.commands.insert(0, TrackCommand(
                cmd=kind, tick=0, value=points[-1]['value'], raw_line=''))
        self._refresh_sidebar_values()
        if kind == 'TEMPO':
            from core.sound.song_parser import get_song_tempo
            self._bpm = get_song_tempo(self._song)
            self._bpm_spin.blockSignals(True)
            self._bpm_spin.setValue(self._bpm)
            self._bpm_spin.blockSignals(False)
            if self._sequencer is not None:
                self._sequencer.set_bpm(self._bpm)
        self._mark_dirty()

    def _refresh_sidebar_values(self):
        """Re-read the sidebar's per-track sliders from the song's commands,
        keeping mute / solo and the selected row."""
        state = self._track_sidebar.get_mute_solo_state()
        selected = self._track_sidebar._selected_index
        inst_names = get_instrument_names(self._vg_data, self._song.voicegroup)
        self._track_sidebar.load_tracks(extract_track_infos(self._song, self._vg_data),
                                        instrument_names=inst_names)
        self._track_sidebar.set_mute_solo_state(state['muted'], state['soloed'])
        self._track_sidebar.select_row(selected)

    def _on_track_modulation(self, track_index: int, depth: int):
        """User changed a track's vibrato/modulation (MOD) slider.

        Imported MIDIs often carry a leftover MOD depth that makes the track
        wobble regardless of the instrument. Set EVERY MOD event on the track to
        the slider value (0 = remove the vibrato) — like the volume slider,
        bumping all of them so a mid-track modulation envelope can't reintroduce
        the wobble. Only ADD a MOD event when depth > 0 (no point writing MOD 0
        into a track that never modulated).
        """
        if not (0 <= track_index < len(self._song.tracks)):
            return
        track = self._song.tracks[track_index]
        self._history.checkpoint(merge_key=('modulation', id(track)))
        found_any = False
        for cmd in track.commands:
            if cmd.cmd == 'MOD':
                cmd.value = depth
                found_any = True
        if not found_any and depth > 0:
            from core.sound.song_parser import TrackCommand
            track.commands.insert(0, TrackCommand(
                cmd='MOD', tick=0, value=depth, raw_line=''))
        if self._sequencer is not None and hasattr(
                self._sequencer, 'set_track_modulation'):
            self._sequencer.set_track_modulation(track_index, depth)
        self._mark_dirty()

    def _on_track_volume(self, track_index: int, volume: int):
        """User changed a track's volume slider.

        SCALE the track's whole VOL envelope by the slider's change so any
        fade / volume-automation SHAPE is preserved.

        The earlier code set EVERY VOL event to the same value, which flattened
        a decaying SFX into a constant level (the "no decay / ^v^v^v" bug); an
        even-earlier version bumped only the first, letting the track ramp back
        down. Scaling about the track's starting (first) VOL — the level the
        slider represents — does neither: the entire curve, including mid-song
        fade steps, moves together, exactly like raising the master volume.

        The save reconstructs a track's mid-song controls from a snapshot taken
        at piano-roll-open AND/OR the canvas control-event list, NOT from the
        live song object, so the same factor is applied to all three — otherwise
        the saved fade would disagree with the slider. raw_line is cleared on
        the scaled commands so the writer regenerates them from the new value
        (it correctly re-derives the `n*mvl/mxv` multiplier; verified no
        double-scaling).
        """
        if self._sequencer is not None:
            self._sequencer.set_track_volume(track_index, volume)
        if not (0 <= track_index < len(self._song.tracks)):
            return
        track = self._song.tracks[track_index]
        self._history.checkpoint(merge_key=('volume', id(track)))
        live_vols = [c for c in track.commands
                     if c.cmd == 'VOL' and c.value is not None]
        ref = live_vols[0].value if live_vols else None
        if not ref or ref <= 0:
            # No usable reference envelope — set/insert a single tick-0 VOL.
            if live_vols:
                live_vols[0].value = volume
                live_vols[0].raw_line = ''
            else:
                from core.sound.song_parser import TrackCommand
                track.commands.insert(0, TrackCommand(
                    cmd='VOL', tick=0, value=volume, raw_line=''))
            self._mark_dirty()
            return

        factor = volume / ref

        def _scale_cmds(cmds):
            for c in cmds:
                if c.cmd == 'VOL' and c.value is not None:
                    c.value = max(0, min(127, round(c.value * factor)))
                    c.raw_line = ''   # regenerate from the scaled value

        # 1) Live song — drives the preview and the saved VOL commands.
        _scale_cmds(track.commands)
        # 2) Canvas control-event list — the mid-song VOL the preview plays
        #    and the save takes when it differs from what was loaded
        #    (plain dicts, no raw_line to clear).
        try:
            for e in self._piano_roll.canvas._control_events:
                if (e.get('track') == track_index and e.get('type') == 'VOL'
                        and e.get('value') is not None):
                    e['value'] = max(0, min(127, round(e['value'] * factor)))
        except AttributeError:
            pass
        self._mark_dirty()

    def _on_track_pan(self, track_index: int, pan: int):
        """User changed a track's pan slider."""
        if self._sequencer is not None:
            self._sequencer.set_track_pan(track_index, pan)
        # Also persist in song data so save captures it
        if 0 <= track_index < len(self._song.tracks):
            track = self._song.tracks[track_index]
            self._history.checkpoint(merge_key=('pan', id(track)))
            found = False
            for cmd in track.commands:
                if cmd.cmd == 'PAN':
                    cmd.value = pan
                    found = True
                    break
            if not found:
                from core.sound.song_parser import TrackCommand
                track.commands.insert(0, TrackCommand(
                    cmd='PAN', tick=0, value=pan, raw_line=''))
            self._mark_dirty()

    def _on_voicegroup_changed(self, vg_display: str):
        """User picked a different voicegroup from the sidebar combo."""
        from core.sound.voicegroup_labels import vg_name_from_display
        vg_name = vg_name_from_display(vg_display)
        if vg_name == self._song.voicegroup:
            return
        self._history.checkpoint()
        self._song.voicegroup = vg_name
        # Update instrument names on all track rows
        inst_names = get_instrument_names(self._vg_data, vg_name)
        self._track_sidebar.update_instrument_names(inst_names)
        self._piano_roll.lane.voice_names = list(inst_names)
        # Update live sequencer in-place — keeps playing without interruption
        if self._sequencer is not None:
            vg = self._vg_data.get_voicegroup(vg_name)
            if vg is not None:
                self._sequencer.update_voicegroup(vg)
        self._mark_dirty()
        self._status.showMessage(f"Voicegroup changed to {vg_name}", 3000)

    def _apply_mute_solo(self):
        state = self._track_sidebar.get_mute_solo_state()
        soloed = state['soloed']
        muted = state['muted']

        if soloed:
            visible = soloed
        else:
            all_tracks = set(range(len(self._song.tracks)))
            visible = all_tracks - muted

        self._piano_roll.canvas._visible_tracks = visible
        self._piano_roll.canvas.update()
        self._push_notes_to_sequencer()

    # Track add / remove / duplicate.
    #
    # These change the canvas's own notes and drawn controls directly and then
    # refresh only the track LIST. They used to call a reload that re-filled
    # the canvas from the song's stored commands, so every unsaved note, drawn
    # control and loop edit was silently thrown away, and the "what was loaded"
    # baseline was re-taken so the next save could not see an edit had been
    # made (BUGS.md 2026-11-15).

    def _unique_track_label(self) -> str:
        """A track label no other track has. Numbering by count collided after
        a removal (tracks _1, _3, _4, then a new one was named _4 again)."""
        used = {t.label for t in self._song.tracks}
        n = len(self._song.tracks) + 1
        while f"{self._song.label}_{n}" in used:
            n += 1
        return f"{self._song.label}_{n}"

    def _on_add_track(self):
        self._history.checkpoint()
        new_idx = len(self._song.tracks)
        from core.sound.song_parser import Track, TrackCommand
        new_track = Track(
            index=new_idx,
            label=self._unique_track_label(),
        )
        new_track.commands.append(TrackCommand(
            cmd='VOICE', tick=0, value=0, raw_line=''))
        self._song.tracks.append(new_track)
        self._song.num_tracks = len(self._song.tracks)
        self._refresh_track_views()
        self._mark_dirty()
        self._status.showMessage(f"Added Track {new_idx + 1}", 3000)

    def _on_remove_track(self, index: int):
        if len(self._song.tracks) <= 1:
            QMessageBox.warning(self, "Cannot Remove",
                                "A song must have at least one track.")
            return
        canvas = self._piano_roll.canvas
        # Count what the canvas holds — that is what the user sees, unsaved
        # edits included.
        note_count = sum(1 for n in canvas.get_notes() if n.get('track', 0) == index)
        if note_count > 0:
            reply = QMessageBox.question(
                self, "Remove Track",
                f"Track {index + 1} has {note_count} notes.\n"
                f"Are you sure you want to remove it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._history.checkpoint()
        mute_solo = self._track_sidebar.get_mute_solo_state()
        mapping = {i: (None if i == index else (i - 1 if i > index else i))
                   for i in range(len(self._song.tracks))}
        self._song.tracks.pop(index)
        self._song.num_tracks = len(self._song.tracks)
        for i, t in enumerate(self._song.tracks):
            t.index = i
        # Renumber the canvas (and its undo history) the same way.
        canvas.remap_tracks(mapping)
        self._refresh_track_views(
            muted={mapping[i] for i in mute_solo['muted'] if mapping.get(i) is not None},
            soloed={mapping[i] for i in mute_solo['soloed'] if mapping.get(i) is not None})
        # Stage the change only — it persists when the user hits Save, never
        # auto-written to disk here.
        self._mark_dirty()
        self._status.showMessage(
            f"Removed Track {index + 1} (unsaved — use Save to keep it)", 4000)

    def _on_duplicate_track(self, index: int):
        if index < 0 or index >= len(self._song.tracks):
            return
        import copy
        self._history.checkpoint()
        src = self._song.tracks[index]
        new_idx = len(self._song.tracks)
        dup = copy.deepcopy(src)
        dup.index = new_idx
        dup.label = self._unique_track_label()
        # Tempo is song-wide (it lives on track 1); a copy of track 1 must not
        # carry a second set of tempo changes.
        dup.commands = [c for c in dup.commands if c.cmd != 'TEMPO']
        self._song.tracks.append(dup)
        self._song.num_tracks = len(self._song.tracks)
        # The copy is of the track AS IT IS NOW, unsaved canvas edits included.
        self._piano_roll.canvas.duplicate_track_content(index, new_idx)
        self._refresh_track_views()
        self._mark_dirty()
        self._status.showMessage(
            f"Duplicated Track {index + 1} as Track {new_idx + 1}", 3000)

    def _on_move_track(self, index: int, delta: int):
        """Move a track up (-1) or down (+1). Its notes, drawn controls,
        instrument, mute / solo and unsaved edits move with it; the save puts
        its MIDI track in the new place."""
        new = index + delta
        if not (0 <= index < len(self._song.tracks) and 0 <= new < len(self._song.tracks)):
            return
        self._history.checkpoint()
        ms = self._track_sidebar.get_mute_solo_state()
        mapping = {i: i for i in range(len(self._song.tracks))}
        mapping[index], mapping[new] = new, index
        tracks = self._song.tracks
        tracks[index], tracks[new] = tracks[new], tracks[index]
        for i, t in enumerate(tracks):
            t.index = i
        self._piano_roll.canvas.remap_tracks(mapping)
        self._refresh_track_views(muted={mapping[i] for i in ms['muted']},
                                  soloed={mapping[i] for i in ms['soloed']})
        self._track_sidebar.select_row(new)
        self._mark_dirty()
        self._status.showMessage(f"Moved Track {index + 1} to position {new + 1}", 3000)

    def _refresh_track_views(self, muted=None, soloed=None):
        """Rebuild the track list, sidebar and structure panel after a track
        operation. The canvas's notes, drawn controls, loop and undo history
        are NOT touched here — they hold the user's unsaved work."""
        song = self._song
        if muted is None or soloed is None:
            state = self._track_sidebar.get_mute_solo_state()
            muted = state['muted'] if muted is None else muted
            soloed = state['soloed'] if soloed is None else soloed
        self._track_combo.blockSignals(True)
        self._track_combo.clear()
        self._track_combo.addItem("All Tracks")
        for i, track in enumerate(song.tracks):
            ch = track.midi_channel
            ch_text = f"Ch{ch}" if ch is not None else f"#{i+1}"
            inst_name = ""
            for cmd in track.commands:
                if cmd.cmd == 'VOICE' and cmd.value is not None:
                    inst_name = f" (inst {cmd.value})"
                    break
            self._track_combo.addItem(f"Trk {i+1} {ch_text}{inst_name}")
        self._track_combo.setCurrentIndex(0)
        self._track_combo.blockSignals(False)

        inst_names = get_instrument_names(self._vg_data, song.voicegroup)
        track_infos = extract_track_infos(song, self._vg_data)
        self._track_sidebar.load_tracks(track_infos, instrument_names=inst_names)
        self._track_sidebar.set_mute_solo_state(muted, soloed)
        canvas = self._piano_roll.canvas
        canvas.set_track_filter(-1)
        self._structure_panel.load_from_song(song, canvas._total_ticks)
        self._structure_panel.set_loop(canvas._loop_start, canvas._loop_end)
        self._piano_roll.lane.update()
        self._apply_mute_solo()

        # Recreate sequencer with new track layout
        if self._sequencer is not None:
            self._sequencer.stop()
            self._sequencer = None

    # ═══════════════════════════════════════════════════════════════════
    # Save (called by the main window's File → Save pipeline)
    # ═══════════════════════════════════════════════════════════════════

    def has_unsaved_changes(self) -> bool:
        """Check if the piano roll has edits that haven't been saved."""
        return self._is_dirty

    def save_to_disk(self):
        """Write the edited song to disk — its .mid, its .s and midi.cfg.

        Called by the main window's _on_save_all(), the Save button and Flatten
        Dynamics. All edits live in RAM until this is called.

        The save goes through core.sound.safe_song_save.save_editor_song: the
        song is re-read from disk and only what was edited here is applied to
        it (the canvas notes / drawn controls / loop, when they differ from
        what was loaded; the sidebar, BPM, Max Volume and Flatten edits made
        to the song's commands). Everything else — instrument changes, echo,
        vibrato, tempo changes, timing — stays exactly as the file has it.
        mid2agb builds the result in a temp folder first, and the save is
        refused, with nothing written, if the build would not play what the
        editor shows or would change anything that was not edited. The
        current voicegroup is always passed, so a bank change can never be
        reverted by a stale -G in midi.cfg.
        """
        if not self._song.file_path:
            raise ValueError(
                f"Cannot save — song '{self._song.label}' has no file path")

        from core.sound.safe_song_save import save_editor_song, SongSaveRefused
        from core.sound.song_table_manager import voicegroup_index_from_name
        edit = self._build_song_edit()
        # <root>/sound/songs/midi/<song>.s — the project the song lives in.
        root = self._project_root or os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(self._song.file_path)))))
        try:
            path = save_editor_song(
                root, self._song, edit,
                voicegroup=voicegroup_index_from_name(
                    getattr(self._song, 'voicegroup', None)))
        except SongSaveRefused as e:
            _log.error("Piano Roll save refused for %s: %s", self._song.label, e)
            raise ValueError(
                f"'{self._song.label}' was NOT saved — nothing on disk was "
                f"changed.\n\n{e}") from e

        # The file is now the truth: read it back so the in-memory song (shared
        # with the Sound Editor) holds exactly what was written, and measure
        # the next save's edits from here.
        labels_before = [t.label for t in self._song.tracks]
        self._replace_song_from_disk()
        self._show_saved_song(labels_before)
        self._watch_song_files()          # our own write is not an outside change

        self._is_dirty = False
        self.saved.emit()
        self.setWindowTitle(
            f"Piano Roll  --  {self._song.label}  "
            f"({self._bpm} BPM, {len(self._song.tracks)} tracks)")
        self._edit_label.setText("Saved")
        self._status.showMessage(f"Saved to {path}", 5000)
        return path

    def _on_flatten_dynamics(self):
        """Show + remove HIDDEN mid-song dynamics (volume / tempo / vibrato /
        pan / bend envelopes the piano roll can't display). Imported MIDIs
        carry these, so a song can play quieter or shift tempo partway with no
        visible cause. Flattening locks each parameter to its tick-0 starting
        value; notes are never touched. Saves immediately."""
        from PyQt6.QtWidgets import QMessageBox
        from core.sound import dynamics as DYN

        lines = []
        total = 0
        for i, tr in enumerate(self._song.tracks):
            counts = DYN.count_track_dynamics(tr)
            n = DYN.total_mid_song_events(counts)
            total += n
            if n:
                lines.append(f"    Track {i + 1}:   {DYN.describe_dynamics(counts)}")

        if total == 0:
            QMessageBox.information(
                self, "Flatten Dynamics",
                "Good news — this song has no hidden mid-song volume, tempo, or "
                "vibrato changes. Nothing to flatten; what you see is what plays.")
            return

        body = "\n".join(lines)
        resp = QMessageBox.question(
            self, "Flatten Dynamics",
            "This song carries hidden mid-song changes the piano roll can't "
            "show (imported MIDIs bring these in):\n\n"
            f"{body}\n\n"
            "Flatten them? Each parameter locks to its STARTING value, so the "
            "song plays at a constant volume / tempo / vibrato — no surprise dips "
            "or shifts. Your notes are untouched. This saves immediately; use git "
            "to undo if needed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if resp != QMessageBox.StandardButton.Yes:
            return

        # Flatten the live song (the save replays its removals onto the file)
        # AND the canvas's control events, so the preview stops playing them.
        self._history.checkpoint()
        DYN.flatten_song_dynamics(self._song)
        canvas = self._piano_roll.canvas
        canvas._control_events = [
            e for e in canvas._control_events
            if not (e.get('tick', 0) > 0 and e.get('type') in DYN.DYNAMICS_CMDS)]

        try:
            self.save_to_disk()
        except Exception as exc:
            QMessageBox.warning(
                self, "Flatten Dynamics",
                f"Flattened in memory, but saving failed:\n{exc}\n\n"
                "Use File → Save to write it.")
            return

        QMessageBox.information(
            self, "Flatten Dynamics",
            f"Removed {total} hidden mid-song change(s) and saved. The song now "
            "plays at constant volume / tempo / vibrato, and the committed .mid "
            "matches what you see.")

    def _capture_baseline(self):
        """Remember what the canvas shows right after it was filled from the
        song, per track OBJECT (track add / remove / duplicate renumber rows,
        not tracks). The save treats any later difference as the user's edit."""
        canvas = self._piano_roll.canvas
        notes = self._notes_by_track(canvas.get_notes())
        controls = self._controls_by_track(canvas._control_events)
        self._baseline = {
            'notes': {id(t): notes.get(i, []) for i, t in enumerate(self._song.tracks)},
            'controls': {id(t): controls.get(i, []) for i, t in enumerate(self._song.tracks)},
            'loop': (canvas._loop_start, canvas._loop_end),
            'time_sigs': list(self._time_sigs),
        }

    @staticmethod
    def _notes_by_track(notes) -> dict:
        out: dict = {}
        for n in notes:
            out.setdefault(n.get('track', 0), []).append(
                (n.get('tick'), n.get('pitch'), n.get('duration'), n.get('velocity')))
        return {k: sorted(v) for k, v in out.items()}

    @staticmethod
    def _controls_by_track(events) -> dict:
        out: dict = {}
        for e in events:
            out.setdefault(e.get('track', 0), []).append(
                (e.get('tick'), e.get('type'), e.get('value')))
        return {k: sorted(v, key=repr) for k, v in out.items()}

    def _build_song_edit(self):
        """What was edited on the canvas since it was filled: per track, the
        notes and/or the drawn controls when they differ from what was loaded,
        and the loop when it moved. Unedited tracks are not mentioned, so the
        save keeps them exactly as the file has them."""
        from core.sound.midi_exporter import SongEdit, TrackEdit
        canvas = self._piano_roll.canvas
        all_notes = canvas.get_notes()
        notes_now = self._notes_by_track(all_notes)
        controls_now = self._controls_by_track(canvas._control_events)
        base = self._baseline or {'notes': {}, 'controls': {}, 'loop': None}
        edit = SongEdit()
        for i, track in enumerate(self._song.tracks):
            te = TrackEdit()
            if notes_now.get(i, []) != base['notes'].get(id(track)):
                te.notes = [n for n in all_notes if n.get('track', 0) == i]
            if controls_now.get(i, []) != base['controls'].get(id(track)):
                te.controls = [e for e in canvas._control_events
                               if e.get('track', 0) == i]
            if te.notes is not None or te.controls is not None:
                edit.tracks[i] = te
        loop_now = (canvas._loop_start, canvas._loop_end)
        if loop_now != base['loop']:
            edit.loop = loop_now
        if list(self._time_sigs) != base.get('time_sigs', list(self._time_sigs)):
            edit.time_signatures = list(self._time_sigs)
        return edit

    # ═══════════════════════════════════════════════════════════════════
    # Outside changes to the song's files while it is open
    # ═══════════════════════════════════════════════════════════════════

    def _song_files(self):
        s = self._song.file_path or ''
        return [p for p in (s, s[:-2] + '.mid' if s.endswith('.s') else '') if p]

    def _file_stamp(self):
        out = []
        for p in self._song_files():
            try:
                st = os.stat(p)
                out.append((p, st.st_size, st.st_mtime_ns))
            except OSError:
                out.append((p, None, None))
        return tuple(out)

    def _watch_song_files(self):
        """Watch the song's FOLDER and remember how its .s and .mid look now
        (called again after every save, so our own write is not news).

        The folder, not the files: on Windows a watch on a file intermittently
        blocks replacing that file (measured: 7 of 50 replaces refused), and
        every save here replaces its files all at once. A folder watch blocked
        none of 200. Other songs changing in the folder only cost a stat."""
        from PyQt6.QtCore import QFileSystemWatcher
        if self._watcher is None:
            self._watcher = QFileSystemWatcher(self)
            self._watcher.directoryChanged.connect(
                lambda _p: QTimer.singleShot(300, self._check_outside_change))
        folder = os.path.dirname(self._song.file_path or '')
        if folder and os.path.isdir(folder) and folder not in self._watcher.directories():
            self._watcher.addPath(folder)
        self._disk_stamp = self._file_stamp()

    def _check_outside_change(self):
        """The song's .s or .mid changed while it is open, and not by this
        window's own save. Offer to reload it (refreshing it from its MIDI
        when that is what changed); keeping the open version is safe, because
        its save is refused while it no longer matches the files."""
        if self._file_stamp() == self._disk_stamp:
            return
        self._watch_song_files()
        from core.sound.midi_refresh import midi_sync_status
        root = self._project_root or ''
        status = midi_sync_status(root, self._song.label) if root else None
        refresh = status is not None and status.state in ('midi_changed', 'no_s')
        _log.info("Piano Roll: %s changed on disk (%s)", self._song.label,
                  status.state if status else "?")
        edits = (" Your unsaved edits here would be discarded." if self._is_dirty else
                 " You have no unsaved edits here.")
        what = ("Its MIDI file was changed by another program." if refresh else
                "Its files were changed by another program.")
        ask = QMessageBox.question(
            self, "Song changed on disk",
            f"'{self._song.label}' was changed outside PorySuite. {what}\n\n"
            + ("Refresh it from the MIDI and reload it here?" if refresh else "Reload it here?")
            + edits + "\n\nIf you keep the current version, saving it is refused until "
            "it is reloaded, so nothing on disk can be overwritten.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ask != QMessageBox.StandardButton.Yes:
            self._status.showMessage(
                "The song changed on disk — reload it before saving.", 8000)
            return
        if refresh:
            from core.sound.midi_refresh import refresh_from_midi
            from core.sound.safe_song_save import SongSaveRefused
            try:
                refresh_from_midi(root, self._song.label)
            except SongSaveRefused as exc:
                QMessageBox.warning(self, "Song changed on disk", f"Not refreshed: {exc}")
                return
        self._replace_song_from_disk()
        self._load_song()
        self._is_dirty = False
        self._edit_label.setText("Reloaded")
        self._watch_song_files()
        if self._sequencer is not None:
            self._sequencer.stop()
            self._sequencer = None

    def _show_saved_song(self, old_labels):
        """Re-fill the window from the file just written. The save proved the
        build plays exactly what the editor held, so nothing visible changes —
        except that the tracks now number as the file has them (a new track
        with no notes is not written, and later ones move up), and the undo
        history starts again here (its snapshots describe the old file).
        Scroll position, mute and solo are kept."""
        h = self._piano_roll._scroll_area.horizontalScrollBar().value()
        v = self._piano_roll._scroll_area.verticalScrollBar().value()
        state = self._track_sidebar.get_mute_solo_state()
        by_label = {t.label: i for i, t in enumerate(self._song.tracks)}
        muted = {by_label[old_labels[i]] for i in state['muted']
                 if i < len(old_labels) and old_labels[i] in by_label}
        soloed = {by_label[old_labels[i]] for i in state['soloed']
                  if i < len(old_labels) and old_labels[i] in by_label}
        self._load_song()
        self._track_sidebar.set_mute_solo_state(muted, soloed)
        self._apply_mute_solo()
        self._piano_roll._scroll_area.horizontalScrollBar().setValue(h)
        self._piano_roll._scroll_area.verticalScrollBar().setValue(v)
        if self._sequencer is not None:
            self._sequencer.stop()
            self._sequencer = None

    # ═══════════════════════════════════════════════════════════════════
    # Undo / redo
    # ═══════════════════════════════════════════════════════════════════

    _HEADER_ATTRS = ('voicegroup', 'master_volume', 'reverb', 'priority')

    def _capture_state(self):
        canvas = self._piano_roll.canvas
        tracks = list(self._song.tracks)
        ms = self._track_sidebar.get_mute_solo_state()
        return {
            # Not edits: kept so a track that comes back (undoing its removal)
            # comes back muted / soloed as it was.
            'muted': {id(tracks[i]) for i in ms['muted'] if i < len(tracks)},
            'soloed': {id(tracks[i]) for i in ms['soloed'] if i < len(tracks)},
            'notes': [dict(n) for n in canvas._notes],
            'controls': [dict(e) for e in canvas._control_events],
            'loop': (canvas._loop_start, canvas._loop_end),
            'tracks': tracks,
            'commands': {id(t): (list(t.commands),
                                 [(c.value, c.raw_line, getattr(c, 'velocity', None))
                                  for c in t.commands])
                         for t in tracks},
            'header': {a: getattr(self._song, a, None) for a in self._HEADER_ATTRS},
            'time_sigs': list(self._time_sigs),
        }

    @staticmethod
    def _same_state(a, b) -> bool:
        if (a['notes'] != b['notes'] or a['controls'] != b['controls']
                or a['loop'] != b['loop'] or a['header'] != b['header']
                or a['time_sigs'] != b['time_sigs']
                or [id(t) for t in a['tracks']] != [id(t) for t in b['tracks']]):
            return False
        for t in a['tracks']:
            (ca, va), (cb, vb) = a['commands'][id(t)], b['commands'][id(t)]
            if va != vb or [id(c) for c in ca] != [id(c) for c in cb]:
                return False
        return True

    def _restore_state(self, st) -> None:
        canvas = self._piano_roll.canvas
        # Mute and solo are not edits: they stay with their track objects. A
        # track that is not here now (its removal is being undone) gets the
        # mute / solo it had then.
        ms = self._track_sidebar.get_mute_solo_state()
        cur = self._song.tracks
        present = {id(t) for t in cur}
        muted_ids = {id(cur[i]) for i in ms['muted'] if i < len(cur)}
        soloed_ids = {id(cur[i]) for i in ms['soloed'] if i < len(cur)}
        muted_ids |= {i for i in st['muted'] if i not in present}
        soloed_ids |= {i for i in st['soloed'] if i not in present}
        for t in st['tracks']:
            cmds, vals = st['commands'][id(t)]
            t.commands = list(cmds)
            for c, (value, raw, vel) in zip(t.commands, vals):
                c.value = value
                c.raw_line = raw
                if hasattr(c, 'velocity'):
                    c.velocity = vel
        self._song.tracks = list(st['tracks'])
        for i, t in enumerate(self._song.tracks):
            t.index = i
        self._song.num_tracks = len(self._song.tracks)
        for attr, value in st['header'].items():
            if hasattr(self._song, attr):
                setattr(self._song, attr, value)
        canvas._notes = [dict(n) for n in st['notes']]
        canvas._control_events = [dict(e) for e in st['controls']]
        canvas._selected.clear()
        canvas.set_loop_region(*st['loop'])
        self._time_sigs = list(st['time_sigs'])
        self._apply_bar_map()
        if canvas._active_track >= len(self._song.tracks):
            canvas._active_track = 0
        tracks = self._song.tracks
        self._refresh_track_views(
            muted={i for i, t in enumerate(tracks) if id(t) in muted_ids},
            soloed={i for i, t in enumerate(tracks) if id(t) in soloed_ids})
        self._track_sidebar.set_volume_ceiling(
            getattr(self._song, 'master_volume', None) or 127)
        self._show_voicegroup()
        from core.sound.song_parser import get_song_tempo
        self._bpm = get_song_tempo(self._song)
        self._bpm_spin.blockSignals(True)
        self._bpm_spin.setValue(self._bpm)
        self._bpm_spin.blockSignals(False)
        canvas.update()
        self._mark_dirty()

    def _show_voicegroup(self):
        if not self._vg_data:
            return
        from core.sound.voicegroup_labels import get_display_name
        vg_display = [get_display_name(n, self._vg_labels)
                      for n in sorted(self._vg_data.voicegroups.keys())]
        self._track_sidebar.set_voicegroup_list(
            vg_display, current=get_display_name(self._song.voicegroup, self._vg_labels))
        names = get_instrument_names(self._vg_data, self._song.voicegroup)
        self._track_sidebar.update_instrument_names(names)
        self._piano_roll.lane.voice_names = list(names)

    def _on_undo(self):
        self._piano_roll.canvas.undo()

    def _on_redo(self):
        self._piano_roll.canvas.redo()

    def _update_undo_buttons(self):
        if hasattr(self, '_btn_undo'):
            self._btn_undo.setEnabled(self._history.can_undo())
            self._btn_redo.setEnabled(self._history.can_redo())

    def _replace_song_from_disk(self):
        """Re-read the song's .s into the in-memory song object, in place (the
        Sound Editor holds the same object)."""
        from core.sound.song_parser import parse_song_file
        fresh = parse_song_file(self._song.file_path)
        self._song.tracks = fresh.tracks
        self._song.num_tracks = fresh.num_tracks
        self._song.reverb = fresh.reverb
        self._song.master_volume = fresh.master_volume
        self._song.priority = fresh.priority
        self._song.tempo_base = fresh.tempo_base
        self._song.voicegroup = fresh.voicegroup

    def closeEvent(self, event):
        if self._is_dirty:
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved Changes")
            box.setText(
                f"'{self._song.label}' has unsaved note edits.")
            box.setInformativeText(
                "Do you want to keep your changes for File > Save, "
                "or discard them?")
            btn_keep = box.addButton(
                "Keep && Close", QMessageBox.ButtonRole.AcceptRole)
            btn_discard = box.addButton(
                "Discard && Close", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(btn_cancel)
            box.exec()

            clicked = box.clickedButton()
            if clicked == btn_cancel:
                event.ignore()
                return
            elif clicked == btn_discard:
                # Reload the song from disk so in-memory edits are thrown away
                self._discard_changes()

        self._on_stop()
        if self._sequencer is not None:
            self._sequencer.stop()
            self._sequencer = None
        self.closed.emit()
        super().closeEvent(event)

    def _discard_changes(self):
        """Reload the song's .s file from disk, throwing away all edits."""
        if not self._song.file_path:
            return
        try:
            # Overwrite the in-memory song object's tracks so the main
            # window's reference sees the original data, not our edits.
            self._replace_song_from_disk()
            self._is_dirty = False
        except Exception:
            pass  # worst case, edits persist — user can still not save
