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
from ui.custom_widgets.scroll_guard import install_scroll_guard


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
        self._structure_dirty = False  # True if user edited Song Structure

        # Snapshot original track commands before any editing so save can
        # reference the original control/structure commands
        import copy
        self._original_track_commands = {
            i: copy.deepcopy(track.commands)
            for i, track in enumerate(song_data.tracks)
        }

        self.setWindowTitle(f"Piano Roll  --  {song_data.label}")
        self.setMinimumSize(900, 500)
        self.resize(1200, 700)

        self._build_ui()
        self._load_song()

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

        self._btn_save = QPushButton("Save")
        self._btn_save.setFixedWidth(60)
        self._btn_save.setToolTip("Save song to disk (Ctrl+S).")
        self._btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(self._btn_save)

        # ── Main area: sidebar + piano roll ──
        main_area = QHBoxLayout()

        self._track_sidebar = TrackSidebar()
        self._track_sidebar.track_selected.connect(self._on_sidebar_track_selected)
        self._track_sidebar.track_muted.connect(self._on_track_muted)
        self._track_sidebar.track_soloed.connect(self._on_track_soloed)
        self._track_sidebar.track_instrument.connect(self._on_track_instrument)
        self._track_sidebar.track_volume.connect(self._on_track_volume)
        self._track_sidebar.track_pan.connect(self._on_track_pan)
        self._track_sidebar.track_added.connect(self._on_add_track)
        self._track_sidebar.track_removed.connect(self._on_remove_track)
        self._track_sidebar.track_duplicated.connect(self._on_duplicate_track)
        self._track_sidebar.mute_solo_changed.connect(self._apply_mute_solo)
        self._track_sidebar.voicegroup_changed.connect(self._on_voicegroup_changed)
        main_area.addWidget(self._track_sidebar)

        self._piano_roll = PianoRollWidget()
        self._piano_roll.hovered_note_changed.connect(self._on_hover_note)
        self._piano_roll.notes_changed.connect(self._on_notes_changed)
        self._piano_roll.status_message.connect(self._on_status_msg)
        self._piano_roll.ruler_clicked.connect(self._on_ruler_seek)
        main_area.addWidget(self._piano_roll, 1)

        self._structure_panel = SongStructurePanel()
        self._structure_panel.structure_changed.connect(self._on_structure_changed)
        self._structure_panel.seek_to_tick.connect(self._on_ruler_seek)
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

        # Load structure panel (sections, loops, patterns, end markers)
        self._structure_panel.load_from_song(song, self._total_ticks)

        # Use the Song Structure panel's loop region as the authority.
        # It reads track 0's GOTO/LABEL items — the same values the user
        # sees and can edit.  The canvas computation (max across all
        # tracks) can disagree, and get_flattened_loop_info can miscalculate
        # loop_start for songs where the LABEL tick doesn't match the
        # WAIT-accumulated tick.  The structure panel's raw ticks are
        # always correct for what the user expects.
        sp_loop_s, sp_loop_e = self._structure_panel.get_loop_region()
        if sp_loop_s is not None and sp_loop_e is not None:
            loop_s, loop_e = sp_loop_s, sp_loop_e
            self._piano_roll.canvas.set_loop_region(loop_s, loop_e)

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

    def _on_structure_changed(self):
        """User edited song structure (sections, loops, etc.)."""
        self._mark_dirty()
        self._structure_dirty = True
        # Update loop region from the structure panel
        loop_s, loop_e = self._structure_panel.get_loop_region()
        self._piano_roll.canvas._loop_start = loop_s
        self._piano_roll.canvas._loop_end = loop_e
        self._piano_roll.canvas.update()
        if self._sequencer is not None and loop_s is not None and loop_e is not None:
            self._sequencer.set_loop(loop_s, loop_e)
        self._status.showMessage("Song structure updated", 3000)

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

        # 1) Master volume → 127 so track VOL=127 survives save/reload
        self._song.master_volume = 127

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
        canvas.push_undo()
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
        self._status.showMessage(
            f"Active track: {index + 1} (new notes go here)", 3000)

    def _on_track_muted(self, index: int, muted: bool):
        self._apply_mute_solo()

    def _on_track_soloed(self, index: int, soloed: bool):
        self._apply_mute_solo()

    def _on_track_instrument(self, track_index: int, voice_slot: int):
        """User changed the instrument for a track via the sidebar spinner."""
        import traceback, os, time
        log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'voice_debug.log')
        with open(log_path, 'a', encoding='utf-8') as _f:
            _f.write(f"[{time.strftime('%H:%M:%S')}] _on_track_instrument: track={track_index} voice={voice_slot}\n")
            traceback.print_stack(limit=8, file=_f)
            _f.write('\n')
        if 0 <= track_index < len(self._song.tracks):
            track = self._song.tracks[track_index]
            # Update the VOICE command in the track
            found = False
            for cmd in track.commands:
                if cmd.cmd == 'VOICE':
                    with open(log_path, 'a', encoding='utf-8') as _f:
                        _f.write(f"  overwriting VOICE {cmd.value} -> {voice_slot}\n")
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

    def _on_track_volume(self, track_index: int, volume: int):
        """User changed a track's volume slider."""
        if self._sequencer is not None:
            self._sequencer.set_track_volume(track_index, volume)
        # Also persist in song data so save captures it.
        #
        # ── Bump EVERY VOL event, not just the first ──────────────────
        # mid2agb often emits multiple VOL events throughout a track
        # (automation/fades). If we only bumped the first one, the track
        # would ramp back down to the imported low value a few ticks in,
        # leaving the SFX quieter than the user expected. Bump them all
        # to match the slider so the whole track plays at the requested
        # level. (If the user wants a fade, they can set per-note
        # velocity or edit individual VOL events via Note Properties.)
        if 0 <= track_index < len(self._song.tracks):
            track = self._song.tracks[track_index]
            found_any = False
            for cmd in track.commands:
                if cmd.cmd == 'VOL':
                    cmd.value = volume
                    found_any = True
            if not found_any:
                # Track had no VOL event at all — insert one at tick 0
                from core.sound.song_parser import TrackCommand
                track.commands.insert(0, TrackCommand(
                    cmd='VOL', tick=0, value=volume, raw_line=''))
            # ── Ceiling fix ───────────────────────────────────────────
            # The .s file writes VOL as `raw*mvl/mxv`, and the writer's
            # _raw_vol() reverse-computes raw = round(value * 127 / mvl),
            # then clamps to 127. If `volume` exceeds the current
            # `master_volume` (`_mvl` in the .s), the write clamps and
            # the next cold-load parse evaluates back to the ceiling
            # (e.g. user slides to 120 with mvl=90 → file says 127*mvl/mxv
            # → reload reads 90). Hoist master_volume up to 127 so the
            # full 0-127 VOL range can round-trip losslessly. A 127 mvl
            # means _raw_vol(v, 127) == v exactly — every track's VOL is
            # written faithfully and re-parses to the same number.
            if volume > getattr(self._song, 'master_volume', 127):
                self._song.master_volume = 127
            self._mark_dirty()

    def _on_track_pan(self, track_index: int, pan: int):
        """User changed a track's pan slider."""
        if self._sequencer is not None:
            self._sequencer.set_track_pan(track_index, pan)
        # Also persist in song data so save captures it
        if 0 <= track_index < len(self._song.tracks):
            track = self._song.tracks[track_index]
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
        self._song.voicegroup = vg_name
        # Update instrument names on all track rows
        inst_names = get_instrument_names(self._vg_data, vg_name)
        self._track_sidebar.update_instrument_names(inst_names)
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

    def _on_add_track(self):
        new_idx = len(self._song.tracks)
        from core.sound.song_parser import Track, TrackCommand
        new_track = Track(
            index=new_idx,
            label=f"{self._song.label}_{new_idx + 1}",
        )
        new_track.commands.append(TrackCommand(
            cmd='VOICE', tick=0, value=0, raw_line=''))
        self._song.tracks.append(new_track)
        self._song.num_tracks = len(self._song.tracks)
        self._reload_tracks()
        self._status.showMessage(f"Added Track {new_idx + 1}", 3000)

    def _on_remove_track(self, index: int):
        if len(self._song.tracks) <= 1:
            QMessageBox.warning(self, "Cannot Remove",
                                "A song must have at least one track.")
            return
        track = self._song.tracks[index]
        note_count = sum(1 for c in track.commands if c.cmd == 'NOTE')
        if note_count > 0:
            reply = QMessageBox.question(
                self, "Remove Track",
                f"Track {index + 1} has {note_count} notes.\n"
                f"Are you sure you want to remove it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._song.tracks.pop(index)
        self._song.num_tracks = len(self._song.tracks)
        for i, t in enumerate(self._song.tracks):
            t.index = i

        canvas_notes = self._piano_roll.canvas.get_notes()
        canvas_notes = [n for n in canvas_notes if n.get('track', 0) != index]
        for n in canvas_notes:
            if n.get('track', 0) > index:
                n['track'] -= 1

        self._reload_tracks()
        self._status.showMessage(f"Removed Track {index + 1}", 3000)

    def _on_duplicate_track(self, index: int):
        if index < 0 or index >= len(self._song.tracks):
            return
        import copy
        src = self._song.tracks[index]
        new_idx = len(self._song.tracks)
        dup = copy.deepcopy(src)
        dup.index = new_idx
        dup.label = f"{self._song.label}_{new_idx + 1}"
        self._song.tracks.append(dup)
        self._song.num_tracks = len(self._song.tracks)

        canvas_notes = self._piano_roll.canvas.get_notes()
        new_notes = [
            {**n, 'track': new_idx}
            for n in canvas_notes if n.get('track', 0) == index
        ]
        canvas_notes.extend(new_notes)

        self._reload_tracks()
        self._status.showMessage(
            f"Duplicated Track {index + 1} as Track {new_idx + 1}", 3000)

    def _reload_tracks(self):
        song = self._song
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
        self._piano_roll.load_song_data(song, track_index=-1)
        self._structure_panel.load_from_song(
            song, self._piano_roll.canvas._total_ticks)

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
        """Write edited notes to the song's .s file.

        Called by the main window's _on_save_all(), NOT by the user
        directly. All edits live in RAM until this is called.
        """
        if not self._song.file_path:
            raise ValueError(
                f"Cannot save — song '{self._song.label}' has no file path")

        self._sync_notes_to_song()

        from core.sound.song_writer import save_song_file
        path = save_song_file(self._song)

        self._is_dirty = False
        self.saved.emit()
        self.setWindowTitle(
            f"Piano Roll  --  {self._song.label}  "
            f"({self._bpm} BPM, {len(self._song.tracks)} tracks)")
        self._edit_label.setText("Saved")
        self._status.showMessage(f"Saved to {path}", 5000)
        return path

    def _sync_notes_to_song(self):
        """Push piano roll notes back into the song's track command lists.

        Preserves:
        - Mid-song control changes (VOL, PAN, MOD, BEND, TEMPO, etc.)
        - If the user didn't touch Song Structure: the original file's
          structure (PATT, GOTO, LABEL, PEND, FINE) is kept intact
        - If the user DID edit Song Structure: structure is rebuilt from
          the piano roll's loop region only
        """
        from core.sound.song_writer import notes_to_track_commands
        from core.sound.song_parser import TrackCommand
        from core.sound.realtime_sequencer import extract_track_play_states

        notes = self._piano_roll.canvas.get_notes()
        track_states = extract_track_play_states(self._song)

        # Get loop region from the piano roll canvas
        loop_start = self._piano_roll.canvas._loop_start
        loop_end = self._piano_roll.canvas._loop_end

        # Get user-edited control events from the canvas (BEND/BENDR/VOL/PAN)
        canvas_controls = self._piano_roll.canvas._control_events

        # Group canvas control events by track
        _canvas_by_track: dict[int, list[dict]] = {}
        for evt in canvas_controls:
            trk = evt.get('track', 0)
            _canvas_by_track.setdefault(trk, []).append(evt)

        _EDITABLE_CTRL = {'BEND', 'BENDR', 'VOL', 'PAN'}

        import os, time as _time
        _log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'voice_debug.log')
        with open(_log_path, 'a', encoding='utf-8') as _f:
            _f.write(f"\n[{_time.strftime('%H:%M:%S')}] === SAVE START ===\n")

        for i, track in enumerate(self._song.tracks):
            ts = track_states.get(i)
            voice = ts.voice if ts else 0
            volume = ts.volume if ts else 100
            pan = ts.pan if ts else 64
            with open(_log_path, 'a', encoding='utf-8') as _f:
                _f.write(f"  Track {i}: voice={voice} vol={volume} pan={pan}\n")

            # Use the ORIGINAL commands from when the song was loaded,
            # not the current (potentially already-synced) commands.
            orig_cmds = list(self._original_track_commands.get(i, []))

            # If the user edited Song Structure, strip structural commands
            # so only controls remain — loop comes from the piano roll.
            if self._structure_dirty:
                _CONTROL_ONLY = {
                    'VOICE', 'VOL', 'PAN', 'MOD', 'BEND', 'BENDR',
                    'LFOS', 'LFODL', 'MODT', 'TUNE', 'TEMPO', 'KEYSH',
                    'XCMD', 'PRIO',
                }
                orig_cmds = [c for c in orig_cmds if c.cmd in _CONTROL_ONLY]

            # Replace editable control events (BEND/BENDR/VOL/PAN) with
            # the canvas's version — this includes user edits from the
            # Note Properties dialog.
            if i in _canvas_by_track:
                # Strip original BEND/BENDR mid-song events (tick > 0)
                # Keep tick-0 setup and non-editable controls intact
                orig_cmds = [
                    c for c in orig_cmds
                    if not (c.cmd in _EDITABLE_CTRL and c.tick > 0)
                ]
                # Add canvas control events as TrackCommands
                for evt in _canvas_by_track[i]:
                    orig_cmds.append(TrackCommand(
                        cmd=evt['type'],
                        tick=evt['tick'],
                        value=evt.get('value', 64),
                    ))

            # Determine loop label for this track.
            # The user's friendly name (e.g. "intro") is stored once in
            # track 0.  Every track gets a UNIQUE assembly label by
            # prefixing the track label (e.g. mus_kakariko_1_intro,
            # mus_kakariko_2_intro).  Assembly labels must be file-global
            # unique — using the bare name in every track causes
            # "symbol already defined" errors.
            structure_label = self._structure_panel.get_loop_label()
            friendly_label = structure_label or track.loop_label
            if not friendly_label and loop_start is not None:
                friendly_label = 'B1'
            if friendly_label:
                track_loop_label = f'{track.label}_{friendly_label}'
            else:
                track_loop_label = None

            track.commands = notes_to_track_commands(
                notes, i, voice, volume, pan,
                loop_start_tick=loop_start,
                loop_end_tick=loop_end,
                loop_label=track_loop_label,
                original_commands=orig_cmds,
            )

    def has_unsaved_changes(self) -> bool:
        return self._is_dirty

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
            from core.sound.song_parser import parse_song_file
            fresh = parse_song_file(self._song.file_path)
            # Overwrite the in-memory song object's tracks so the main
            # window's reference sees the original data, not our edits.
            self._song.tracks = fresh.tracks
            self._song.num_tracks = fresh.num_tracks
            self._song.reverb = fresh.reverb
            self._song.master_volume = fresh.master_volume
            self._song.priority = fresh.priority
            self._song.tempo_base = fresh.tempo_base
            self._is_dirty = False
        except Exception:
            pass  # worst case, edits persist — user can still not save
