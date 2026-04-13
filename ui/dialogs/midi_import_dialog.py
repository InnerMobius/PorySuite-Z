"""MIDI Import Dialog for PorySuite-Z Sound Editor.

Multi-step wizard:
  Page 0 — Pick a MIDI file, see track list and metadata, choose song name
  Page 1 — Voicegroup + conversion settings
  Page 2 — Instrument Mapping (per-track GM → voicegroup slot)
  Page 3 — Song Structure & Loop Points
  Page 4 — Progress / result
"""

from __future__ import annotations

import os
import re
from typing import Optional

from PyQt6.QtCore import Qt, QSettings, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QGroupBox, QFormLayout, QFileDialog,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QSpinBox,
    QCheckBox, QProgressBar, QMessageBox, QFrame, QSizePolicy,
    QStackedWidget, QWidget, QTextEdit, QRadioButton, QButtonGroup,
    QScrollArea, QGridLayout, QListWidget, QListWidgetItem,
    QAbstractItemView,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


# Page indices
_PAGE_FILE = 0
_PAGE_SETTINGS = 1
_PAGE_MAPPING = 2
_PAGE_STRUCTURE = 3
_PAGE_PROGRESS = 4


# ── Worker thread for the import ───────────────────────────────────────────

class _ImportWorker(QThread):
    """Runs the mid2agb conversion + post-processing off the main thread."""
    finished = pyqtSignal(bool, str, str)  # success, s_path, error

    def __init__(self, project_root, midi_path, constant, music_player,
                 settings, voice_remap, loop_config):
        super().__init__()
        self._project_root = project_root
        self._midi_path = midi_path
        self._constant = constant
        self._music_player = music_player
        self._settings = settings
        self._voice_remap = voice_remap      # dict[int, int] GM->VG slot
        self._loop_config = loop_config      # dict with loop settings

    def run(self):
        try:
            from core.sound.midi_importer import import_midi
            result = import_midi(
                self._project_root,
                self._midi_path,
                self._constant,
                self._music_player,
                self._settings,
            )
            if not result.success:
                self.finished.emit(False, "", result.error)
                return

            # Post-process: remap VOICE commands if user changed any
            if self._voice_remap:
                try:
                    _postprocess_voice_remap(result.s_file_path, self._voice_remap)
                except Exception as e:
                    self.finished.emit(False, result.s_file_path,
                                       f"Song imported but VOICE remap failed: {e}")
                    return

            # Post-process: apply section structure / loop config
            if self._loop_config and self._loop_config.get('mode') != 'automatic':
                try:
                    _postprocess_structure(result.s_file_path, self._loop_config)
                except Exception as e:
                    self.finished.emit(False, result.s_file_path,
                                       f"Song imported but structure edit failed: {e}")
                    return

            self.finished.emit(True, result.s_file_path, "")
        except Exception as e:
            self.finished.emit(False, "", str(e))


def _postprocess_voice_remap(s_path: str, remap: dict[int, int]):
    """Rewrite VOICE commands in the .s file based on the remap table.

    remap: {original_gm_number: new_vg_slot_number}
    Only entries where original != new are included.
    """
    with open(s_path, encoding="utf-8") as f:
        lines = f.readlines()

    voice_re = re.compile(r'^(\s*\.byte\s+VOICE\s*,\s*)(\d+)(.*)$')
    changed = False
    for i, line in enumerate(lines):
        m = voice_re.match(line)
        if m:
            old_num = int(m.group(2))
            if old_num in remap:
                new_num = remap[old_num]
                lines[i] = f"{m.group(1)}{new_num}{m.group(3)}\n"
                changed = True

    if changed:
        with open(s_path, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines)


def _postprocess_structure(s_path: str, loop_config: dict):
    """Rewrite the .s file structure based on the user's section sequencer.

    For 'automatic' mode, does nothing — mid2agb's output is kept as-is.
    For 'no_loop' mode, fully linearizes the file — removes ALL PATT/PEND/
      GOTO/internal labels and writes clean linear tracks ending with FINE.
    For 'custom' mode, rebuilds the track structure using the section
      sequencer's loop point.
    """
    mode = loop_config.get('mode', 'automatic')
    if mode == 'automatic':
        return

    if mode == 'no_loop':
        # Parse → flatten → rewrite as clean linear tracks.
        # This strips ALL mid2agb structure: PATT calls, PEND markers,
        # GOTO loops, and internal pattern labels (e.g. mus_name_3_007).
        # The result is a clean file with just notes, waits, and controls.
        from core.sound.song_parser import parse_song_file
        from core.sound.track_renderer import flatten_track_commands
        from core.sound.song_parser import TrackCommand, extract_tie_notes
        from core.sound.song_writer import notes_to_track_commands, write_song

        song = parse_song_file(s_path)

        for i, track in enumerate(song.tracks):
            flat_cmds = flatten_track_commands(track.commands, loop_count=0)

            # Extract notes from flattened commands
            notes = []
            for cmd in flat_cmds:
                if cmd.cmd == 'NOTE' and cmd.pitch is not None:
                    notes.append({
                        'tick': cmd.tick,
                        'pitch': cmd.pitch,
                        'duration': cmd.duration,
                        'velocity': cmd.velocity if cmd.velocity else 100,
                        'track': i,
                    })
            # Extract TIE/EOT notes
            from core.sound.song_parser import Track as _Track
            _flat_track = _Track(index=i, label=track.label)
            _flat_track.commands = flat_cmds
            for note_dict in extract_tie_notes(_flat_track):
                note_dict['track'] = i
                notes.append(note_dict)

            # Extract control commands (only non-duplicated)
            _CONTROL_CMDS = {
                'VOICE', 'VOL', 'PAN', 'MOD', 'BEND', 'BENDR',
                'LFOS', 'LFODL', 'MODT', 'TUNE', 'TEMPO', 'KEYSH',
                'XCMD', 'PRIO',
            }
            controls = []
            _seen: set[tuple[int, str, int]] = set()
            for cmd in flat_cmds:
                if cmd.cmd in _CONTROL_CMDS and cmd.value is not None:
                    key = (cmd.tick, cmd.cmd, cmd.value)
                    if key not in _seen:
                        _seen.add(key)
                        controls.append(cmd)

            # Get voice/vol/pan from tick-0 controls
            voice = 0
            volume = 100
            pan = 64
            for cmd in controls:
                if cmd.tick == 0:
                    if cmd.cmd == 'VOICE':
                        voice = cmd.value
                    elif cmd.cmd == 'VOL':
                        volume = cmd.value
                    elif cmd.cmd == 'PAN':
                        pan = cmd.value

            # Rebuild as clean linear track — no loops, no structure
            track.commands = notes_to_track_commands(
                notes, i, voice, volume, pan,
                loop_start_tick=None, loop_end_tick=None,
                loop_label=None, original_commands=controls,
            )

        content = write_song(song)
        with open(s_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)

        # Protect against mid2agb overwrite: backdate the .mid so it's
        # older than the .s we just wrote.  make's %.s:%.mid rule skips
        # mid2agb when .s is newer.
        mid_path = s_path.rsplit('.', 1)[0] + '.mid'
        if os.path.isfile(mid_path):
            s_mtime = os.stat(s_path).st_mtime
            os.utime(mid_path, (s_mtime - 2, s_mtime - 2))
        return

    # Custom mode: we need to understand measure boundaries in the .s file.
    # mid2agb outputs notes with tick-counted delays. We use the MIDI's
    # ticks_per_beat and time signature to figure out where measure
    # boundaries land, then we split the assembly into sections and
    # reconstruct with PATT/PEND/GOTO.
    #
    # For now, custom mode post-processes as intro+loop using the
    # section sequencer's loop point to determine where the GOTO goes.
    # Full PATT/PEND section extraction requires parsing .s tick offsets
    # which is complex — we handle the most common case: a linear
    # sequence of sections with a loop-back point.

    sections = loop_config.get('sections', [])
    play_order = loop_config.get('play_order', [])
    loop_point = loop_config.get('loop_point', 1)

    if not sections or not play_order:
        return

    # Find all track labels in the .s file (e.g. "mus_name_1:", "mus_name_2:")
    # Each track has its own sequence of commands ending in FINE or GOTO
    track_label_re = re.compile(r'^(\w+_\d+):$')
    track_starts = []
    for i, line in enumerate(lines):
        m = track_label_re.match(line.strip())
        if m:
            track_starts.append((i, m.group(1)))

    if not track_starts:
        return  # Can't find track labels

    # For each track, find the FINE line and insert a GOTO before it
    # that jumps to a calculated loop label
    if loop_point > len(play_order):
        # No loop — just strip existing GOTO and ensure FINE
        out = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('.byte') and 'GOTO' in stripped:
                continue
            if stripped.startswith('.word') and any(
                    ts[1] in stripped for ts in track_starts):
                continue
            out.append(line)
        with open(s_path, "w", encoding="utf-8", newline="\n") as f:
            f.write('\n'.join(out))
        _backdate_mid(s_path)
        return

    # Has a loop point: we need to insert loop-back labels and GOTO
    # The loop point position tells us: positions before it = intro (play once),
    # positions from loop_point onward = loop body (repeats).
    #
    # Strategy: For each track, find the existing label, add a _loop label
    # partway through (we approximate based on measure proportion), then
    # replace any existing GOTO/FINE with a GOTO to the _loop label.

    # Calculate what fraction of the song is intro vs loop
    total_measures_in_order = sum(
        sections[si]['end'] - sections[si]['start'] + 1
        for si in play_order if 0 <= si < len(sections))
    intro_measures = sum(
        sections[play_order[i]]['end'] - sections[play_order[i]]['start'] + 1
        for i in range(min(loop_point - 1, len(play_order)))
        if 0 <= play_order[i] < len(sections))

    if total_measures_in_order <= 0:
        return

    # For each track, count total non-directive lines (note/delay commands)
    # and insert the loop label at the proportional position
    out_lines = list(lines)
    offset_adjust = 0  # track how many lines we've inserted

    for track_idx, (track_line, track_name) in enumerate(track_starts):
        # Find the end of this track (next track start or end of file)
        if track_idx + 1 < len(track_starts):
            track_end = track_starts[track_idx + 1][0]
        else:
            track_end = len(lines)

        # Count note/command lines in this track (lines with .byte that aren't
        # structural commands like FINE/GOTO)
        cmd_lines = []
        for li in range(track_line + 1, track_end):
            stripped = lines[li].strip()
            if stripped.startswith('.byte') and 'FINE' not in stripped and 'GOTO' not in stripped:
                cmd_lines.append(li)

        if not cmd_lines:
            continue

        # Calculate where the loop label goes (proportional)
        loop_cmd_idx = int(len(cmd_lines) * intro_measures / total_measures_in_order)
        loop_cmd_idx = max(0, min(loop_cmd_idx, len(cmd_lines) - 1))
        loop_insert_line = cmd_lines[loop_cmd_idx]

        loop_label = f"{track_name}_loop"

        # Insert loop label
        adjusted_pos = loop_insert_line + offset_adjust
        out_lines.insert(adjusted_pos, f"{loop_label}:")
        offset_adjust += 1

        # Find and replace FINE/GOTO at end of this track with GOTO to loop label
        for li in range(track_line + 1 + offset_adjust,
                        track_end + offset_adjust):
            if li >= len(out_lines):
                break
            stripped = out_lines[li].strip()
            if stripped.startswith('.byte') and 'FINE' in stripped:
                out_lines[li] = f"\t.byte\tGOTO"
                out_lines.insert(li + 1, f"\t .word\t{loop_label}")
                offset_adjust += 1
                break
            if stripped.startswith('.byte') and 'GOTO' in stripped:
                # Replace existing GOTO target
                if li + 1 < len(out_lines) and '.word' in out_lines[li + 1]:
                    out_lines[li + 1] = f"\t .word\t{loop_label}"
                break

    with open(s_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('\n'.join(out_lines))
    _backdate_mid(s_path)


def _backdate_mid(s_path: str) -> None:
    """Backdate the .mid file so it's older than the .s we just wrote.

    pokefirered's Makefile has a %.s:%.mid rule — if .mid is newer than
    .s, make runs mid2agb which OVERWRITES the .s.  Backdating prevents
    that from wiping tool-edited assembly.
    """
    mid_path = s_path.rsplit('.', 1)[0] + '.mid'
    if os.path.isfile(mid_path):
        s_mtime = os.stat(s_path).st_mtime
        os.utime(mid_path, (s_mtime - 2, s_mtime - 2))


# ── Filler detection (same logic as voicegroups_tab) ───────────────────────

def _is_filler(inst) -> bool:
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


# ═══════════════════════════════════════════════════════════════════════════
# Main dialog
# ═══════════════════════════════════════════════════════════════════════════

class MidiImportDialog(QDialog):
    """MIDI Import wizard dialog."""

    song_imported = pyqtSignal(str)  # constant name

    def __init__(self, project_root: str, voicegroup_names: list[str],
                 voicegroup_data=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root = project_root
        self._voicegroup_names = voicegroup_names
        self._vg_data = voicegroup_data  # VoicegroupData or None
        self._midi_info = None
        self._worker = None
        self._mapping_combos: list[tuple[int, QComboBox]] = []  # (gm_num, combo)

        self.setWindowTitle("Import MIDI")
        self.setMinimumSize(750, 600)
        self.resize(800, 650)

        self._build_ui()

    # ═══════════════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Import MIDI as New Song")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # Step indicator
        self._step_label = QLabel("")
        self._step_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._step_label)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._build_page_file()       # 0
        self._build_page_settings()   # 1
        self._build_page_mapping()    # 2
        self._build_page_structure()  # 3
        self._build_page_progress()   # 4

        # Bottom buttons
        btn_bar = QHBoxLayout()

        self._btn_back = QPushButton("Back")
        self._btn_back.clicked.connect(self._go_back)
        self._btn_back.setVisible(False)
        btn_bar.addWidget(self._btn_back)

        btn_bar.addStretch()

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(self._btn_cancel)

        self._btn_next = QPushButton("Next")
        self._btn_next.clicked.connect(self._go_next)
        self._btn_next.setEnabled(False)
        btn_bar.addWidget(self._btn_next)

        layout.addLayout(btn_bar)
        self._update_step_label()

    def _update_step_label(self):
        page = self._stack.currentIndex()
        names = ["Select File", "Settings", "Instrument Mapping",
                 "Song Structure", "Import"]
        if 0 <= page < len(names):
            parts = []
            for i, n in enumerate(names):
                if i == page:
                    parts.append(f"[{n}]")
                else:
                    parts.append(n)
            self._step_label.setText("  >  ".join(parts))

    # ── Page 0: File selection & track preview ─────────────────────────

    def _build_page_file(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("MIDI file:"))
        self._file_edit = QLineEdit()
        self._file_edit.setReadOnly(True)
        self._file_edit.setPlaceholderText("Click Browse to select a .mid file...")
        file_row.addWidget(self._file_edit, 1)
        self._btn_browse = QPushButton("Browse...")
        self._btn_browse.clicked.connect(self._browse_midi)
        file_row.addWidget(self._btn_browse)
        layout.addLayout(file_row)

        self._midi_summary = QLabel("")
        self._midi_summary.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._midi_summary)

        tracks_group = QGroupBox("Tracks in this MIDI")
        tracks_layout = QVBoxLayout(tracks_group)

        self._track_tree = QTreeWidget()
        self._track_tree.setHeaderLabels(["Ch", "Name", "Instrument", "Notes", "Range"])
        self._track_tree.setRootIsDecorated(False)
        self._track_tree.setAlternatingRowColors(True)
        header = self._track_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        tracks_layout.addWidget(self._track_tree)
        layout.addWidget(tracks_group, 1)

        name_group = QGroupBox("Song Name")
        name_layout = QFormLayout(name_group)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("MUS_MY_SONG")
        self._name_edit.textChanged.connect(self._validate_name)
        name_layout.addRow("Constant name:", self._name_edit)
        self._name_status = QLabel("")
        self._name_status.setStyleSheet("font-size: 10px;")
        name_layout.addRow("", self._name_status)
        layout.addWidget(name_group)

        self._stack.addWidget(page)

    # ── Page 1: Voicegroup & conversion settings ───────────────────────

    def _build_page_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(
            "Choose which instrument bank (voicegroup) this song should use,\n"
            "and set the volume, reverb, and priority."
        ))

        vg_group = QGroupBox("Voicegroup")
        vg_layout = QFormLayout(vg_group)

        vg_row = QHBoxLayout()
        self._vg_combo = QComboBox()
        install_scroll_guard(self._vg_combo)
        for name in self._voicegroup_names:
            self._vg_combo.addItem(name)
        self._vg_combo.setToolTip(
            "The instrument bank this song will use.\n"
            "This determines which sounds play for each track.")
        vg_row.addWidget(self._vg_combo, 1)

        self._btn_gen_gm = QPushButton("Generate GM")
        self._btn_gen_gm.setToolTip(
            "Create a General MIDI voicegroup from the project's\n"
            "existing samples. Maps instruments to standard GM\n"
            "program numbers so MIDI tracks play correct sounds.")
        self._btn_gen_gm.clicked.connect(self._on_generate_gm_voicegroup)
        vg_row.addWidget(self._btn_gen_gm)

        vg_layout.addRow("Voicegroup:", vg_row)

        # Auto-select a GM voicegroup if one already exists
        self._auto_select_gm_voicegroup()

        layout.addWidget(vg_group)

        conv_group = QGroupBox("Conversion Settings")
        conv_layout = QFormLayout(conv_group)

        self._vol_spin = QSpinBox()
        self._vol_spin.setRange(0, 127)
        self._vol_spin.setValue(90)
        self._vol_spin.setToolTip("Master volume (0-127). Most songs use 70-100.")
        conv_layout.addRow("Master volume:", self._vol_spin)

        self._reverb_spin = QSpinBox()
        self._reverb_spin.setRange(0, 127)
        self._reverb_spin.setValue(50)
        self._reverb_spin.setToolTip("Reverb amount (0 = off, 50 = typical, 127 = maximum echo).")
        conv_layout.addRow("Reverb:", self._reverb_spin)

        self._priority_spin = QSpinBox()
        self._priority_spin.setRange(0, 127)
        self._priority_spin.setValue(0)
        self._priority_spin.setToolTip(
            "Playback priority. Higher = less likely to be interrupted.\n"
            "0 is fine for most songs. Fanfares often use 5.")
        conv_layout.addRow("Priority:", self._priority_spin)

        self._player_combo = QComboBox()
        install_scroll_guard(self._player_combo)
        self._player_combo.addItems([
            "BGM (Background Music)",
            "SE1 (Sound Effect 1)",
            "SE2 (Sound Effect 2)",
            "SE3 (Sound Effect 3)",
        ])
        self._player_combo.setToolTip(
            "Which audio channel to play on.\n"
            "BGM for music, SE for sound effects.")
        conv_layout.addRow("Player type:", self._player_combo)
        layout.addWidget(conv_group)

        adv_group = QGroupBox("Advanced")
        adv_layout = QVBoxLayout(adv_group)
        self._exact_gate_cb = QCheckBox("Exact gate time (-E)")
        self._exact_gate_cb.setChecked(True)
        self._exact_gate_cb.setToolTip(
            "Use exact note lengths from the MIDI.\n"
            "Almost always on — leave checked unless you have a reason.")
        adv_layout.addWidget(self._exact_gate_cb)
        self._high_res_cb = QCheckBox("High resolution timing (-X, 48 clocks/beat)")
        self._high_res_cb.setChecked(False)
        self._high_res_cb.setToolTip(
            "Use 48 clocks per beat instead of 24.\n"
            "More precise timing but uses more ROM. Usually not needed.")
        adv_layout.addWidget(self._high_res_cb)
        layout.addWidget(adv_group)

        layout.addStretch()
        self._stack.addWidget(page)

    # ── Page 2: Instrument Mapping ─────────────────────────────────────

    def _build_page_mapping(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(
            "Each MIDI track uses a General MIDI instrument.\n"
            "Pick which voicegroup instrument each track should use.\n"
            "The Auto-Match button tries to find the best match by name."
        ))

        self._mapping_info = QLabel("")
        self._mapping_info.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._mapping_info)

        # Scrollable mapping area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._mapping_container = QWidget()
        self._mapping_layout = QGridLayout(self._mapping_container)
        self._mapping_layout.setColumnStretch(0, 0)   # Channel
        self._mapping_layout.setColumnStretch(1, 2)   # MIDI says
        self._mapping_layout.setColumnStretch(2, 0)   # Arrow
        self._mapping_layout.setColumnStretch(3, 3)   # VG Instrument picker

        # Headers
        for col, text in enumerate(["Ch", "MIDI Instrument", "",
                                     "VG Instrument"]):
            lbl = QLabel(f"<b>{text}</b>")
            self._mapping_layout.addWidget(lbl, 0, col)

        scroll.setWidget(self._mapping_container)
        layout.addWidget(scroll, 1)

        # Auto-match button
        btn_row = QHBoxLayout()
        self._btn_auto_match = QPushButton("Auto-Match by Name")
        self._btn_auto_match.setToolTip(
            "Try to find the best matching voicegroup instrument\n"
            "for each MIDI track based on instrument name similarity.")
        self._btn_auto_match.clicked.connect(self._auto_match_instruments)
        btn_row.addWidget(self._btn_auto_match)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._stack.addWidget(page)

    def _populate_mapping_page(self):
        """Fill the instrument mapping grid based on current MIDI + voicegroup."""
        # Clear old rows (keep header row 0)
        while self._mapping_layout.rowCount() > 1:
            for col in range(self._mapping_layout.columnCount()):
                item = self._mapping_layout.itemAtPosition(
                    self._mapping_layout.rowCount() - 1, col)
                if item and item.widget():
                    item.widget().deleteLater()
            # Force row removal by removing from bottom
            break  # QGridLayout doesn't have removeRow; we clear widgets instead

        # Clear all widgets except header row
        for i in reversed(range(self._mapping_layout.count())):
            item = self._mapping_layout.itemAt(i)
            if item and item.widget():
                row, col, _, _ = self._mapping_layout.getItemPosition(i)
                if row > 0:
                    item.widget().deleteLater()

        self._mapping_combos.clear()

        if not self._midi_info:
            return

        # Get current voicegroup instruments
        vg_instruments = self._get_selected_vg_instruments()

        row = 1
        for t in self._midi_info.tracks:
            # Channel label
            ch_label = QLabel(str(t.channel))
            ch_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._mapping_layout.addWidget(ch_label, row, 0)

            # MIDI instrument
            gm_text = t.instrument_name
            if not t.is_drums and t.instrument_num >= 0:
                gm_text = f"#{t.instrument_num}: {t.instrument_name}"
            midi_label = QLabel(gm_text)
            self._mapping_layout.addWidget(midi_label, row, 1)

            # Arrow
            arrow = QLabel("  →  ")
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._mapping_layout.addWidget(arrow, row, 2)

            # VG slot dropdown — shows instrument names instead of raw numbers
            slot_combo = QComboBox()
            slot_combo.setMaxVisibleItems(20)
            slot_combo.setMinimumWidth(250)
            install_scroll_guard(slot_combo)

            # Populate with instrument names from the voicegroup
            for idx in range(128):
                if vg_instruments and idx < len(vg_instruments):
                    inst = vg_instruments[idx]
                    if _is_filler(inst):
                        label = f"{idx}: (empty)"
                    else:
                        label = f"{idx}: {inst.friendly_name}"
                else:
                    label = f"{idx}: (unknown)"
                slot_combo.addItem(label, idx)

            # Color-code the items: red for filler, green for real instruments
            for idx in range(slot_combo.count()):
                if vg_instruments and idx < len(vg_instruments):
                    inst = vg_instruments[idx]
                    if _is_filler(inst):
                        slot_combo.setItemData(
                            idx, QColor("#c44"), Qt.ItemDataRole.ForegroundRole)
                    else:
                        slot_combo.setItemData(
                            idx, QColor("#6a6"), Qt.ItemDataRole.ForegroundRole)

            if t.is_drums:
                slot_combo.setCurrentIndex(0)
                slot_combo.setToolTip("Drums — mid2agb maps drum hits by note number")
            else:
                slot_combo.setCurrentIndex(max(0, min(127, t.instrument_num)))
                slot_combo.setToolTip(
                    f"Which slot in the voicegroup to use for this track.\n"
                    f"Default: {t.instrument_num} (same as the MIDI program number).")

            self._mapping_layout.addWidget(slot_combo, row, 3)

            self._mapping_combos.append((t.instrument_num, slot_combo))
            row += 1

        if self._midi_info.tracks:
            self._mapping_info.setText(
                f"Voicegroup: {self._vg_combo.currentText()}")

    def _auto_select_gm_voicegroup(self):
        """If a GM voicegroup already exists, pre-select it."""
        # Look for a voicegroup that was generated by us — heuristic:
        # check if any voicegroup has GM-style coverage (>30 real instruments
        # across a wide range of slots). For simplicity, just check name hints
        # or pick the highest-numbered voicegroup with many real instruments.
        if not self._vg_data:
            return
        # Scan for voicegroups where >30 of 128 slots are real DirectSound
        best_name = None
        best_count = 0
        for vg_name, vg in self._vg_data.voicegroups.items():
            real = sum(
                1 for inst in vg.instruments
                if inst.is_directsound and inst.sample_label)
            if real > best_count and real >= 30:
                best_count = real
                best_name = vg_name
        if best_name:
            idx = self._vg_combo.findText(best_name)
            if idx >= 0:
                self._vg_combo.setCurrentIndex(idx)

    def _on_generate_gm_voicegroup(self):
        """Generate a GM voicegroup and add it to the combo box."""
        if not self._vg_data:
            QMessageBox.warning(
                self, "No Data",
                "Voicegroup data not loaded. Cannot generate GM voicegroup.")
            return

        from core.sound.gm_voicegroup import (
            generate_gm_voicegroup, get_gm_coverage_report,
        )

        report = get_gm_coverage_report(self._vg_data)
        mapped = report['mapped_slots']

        reply = QMessageBox.question(
            self, "Generate GM Voicegroup",
            f"Create a new voicegroup with General MIDI mapping?\n\n"
            f"{mapped} of 128 slots will have real instruments.\n"
            f"The rest get square wave fallback.\n\n"
            f"This voicegroup will be saved when you save the project.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        gm_vg = generate_gm_voicegroup(self._vg_data)
        self._vg_data.voicegroups[gm_vg.name] = gm_vg

        # Add to combo and select it
        self._vg_combo.addItem(gm_vg.name)
        idx = self._vg_combo.findText(gm_vg.name)
        if idx >= 0:
            self._vg_combo.setCurrentIndex(idx)

        QMessageBox.information(
            self, "GM Voicegroup Created",
            f"Created {gm_vg.name} with {mapped} real instruments.\n\n"
            f"It's now selected as the voicegroup for this import.")

    def _get_selected_vg_instruments(self) -> list:
        """Get the instrument list for the currently selected voicegroup."""
        if not self._vg_data:
            return []
        vg_text = self._vg_combo.currentText()
        m = re.search(r'(\d+)', vg_text)
        if not m:
            return []
        vg = self._vg_data.get_voicegroup_by_number(int(m.group(1)))
        if vg:
            return vg.instruments
        return []

    def _auto_match_instruments(self):
        """Try to match MIDI GM instruments to voicegroup slots by name."""
        insts = self._get_selected_vg_instruments()
        if not insts:
            return

        from core.sound.midi_importer import GM_INSTRUMENTS

        # Build a lowercase name lookup for VG instruments
        vg_names = []
        for inst in insts:
            vg_names.append(inst.friendly_name.lower() if not _is_filler(inst) else "")

        for gm_num, spin in self._mapping_combos:
            if gm_num < 0:  # drums
                continue

            gm_name = GM_INSTRUMENTS[gm_num].lower() if 0 <= gm_num < 128 else ""
            if not gm_name:
                continue

            # Try exact match first
            best_slot = gm_num  # default: same slot
            best_score = 0

            gm_words = set(gm_name.replace('(', '').replace(')', '').split())

            for slot_idx, vg_name in enumerate(vg_names):
                if not vg_name:
                    continue
                vg_words = set(vg_name.replace('(', '').replace(')', '').split())

                # Word overlap score
                overlap = len(gm_words & vg_words)
                if overlap > best_score:
                    best_score = overlap
                    best_slot = slot_idx

                # Substring check
                if gm_name in vg_name or vg_name in gm_name:
                    if len(vg_name) > best_score * 5:
                        best_score = overlap + 2
                        best_slot = slot_idx

            spin.setCurrentIndex(best_slot)

    # ── Page 3: Song Structure & Loop Points ───────────────────────────

    def _build_page_structure(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(
            "Define sections of the song, arrange them in play order,\n"
            "and set where the loop starts. Or use a quick preset."
        ))

        # ── Quick presets row ──
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Quick preset:"))
        self._preset_auto_btn = QPushButton("Automatic")
        self._preset_auto_btn.setToolTip(
            "Let mid2agb handle structure from the MIDI.\n"
            "Clears any custom sections you've defined.")
        self._preset_auto_btn.clicked.connect(self._preset_automatic)
        preset_row.addWidget(self._preset_auto_btn)
        self._preset_loop_all_btn = QPushButton("Loop All")
        self._preset_loop_all_btn.setToolTip(
            "One section covering the whole song, looping from the start.")
        self._preset_loop_all_btn.clicked.connect(self._preset_loop_all)
        preset_row.addWidget(self._preset_loop_all_btn)
        self._preset_no_loop_btn = QPushButton("No Loop")
        self._preset_no_loop_btn.setToolTip(
            "One section, no loop. For fanfares and sound effects.")
        self._preset_no_loop_btn.clicked.connect(self._preset_no_loop)
        preset_row.addWidget(self._preset_no_loop_btn)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        # ── Mode indicator ──
        self._structure_mode_label = QLabel("Mode: No Loop (clean)")
        self._structure_mode_label.setStyleSheet(
            "color: #888; font-size: 10px; margin-bottom: 4px;")
        layout.addWidget(self._structure_mode_label)

        # ── Section definitions (left) + Play order (right) ──
        halves = QHBoxLayout()

        # -- Left: define sections --
        sec_group = QGroupBox("Sections")
        sec_layout = QVBoxLayout(sec_group)

        self._section_list = QListWidget()
        self._section_list.setAlternatingRowColors(True)
        self._section_list.currentRowChanged.connect(
            self._on_section_selected)
        sec_layout.addWidget(self._section_list, 1)

        # Section edit fields
        edit_form = QFormLayout()
        self._sec_name_edit = QLineEdit()
        self._sec_name_edit.setPlaceholderText("e.g. Intro")
        self._sec_name_edit.setToolTip("A short name for this section.")
        self._sec_name_edit.textChanged.connect(self._on_section_name_edited)
        edit_form.addRow("Name:", self._sec_name_edit)

        meas_row = QHBoxLayout()
        self._sec_start_spin = QSpinBox()
        self._sec_start_spin.setRange(1, 9999)
        self._sec_start_spin.setValue(1)
        self._sec_start_spin.setToolTip("First measure of this section (1-based).")
        self._sec_start_spin.valueChanged.connect(self._on_section_range_edited)
        meas_row.addWidget(QLabel("From measure"))
        meas_row.addWidget(self._sec_start_spin)
        self._sec_end_spin = QSpinBox()
        self._sec_end_spin.setRange(1, 9999)
        self._sec_end_spin.setValue(4)
        self._sec_end_spin.setToolTip("Last measure of this section (inclusive).")
        self._sec_end_spin.valueChanged.connect(self._on_section_range_edited)
        meas_row.addWidget(QLabel("to"))
        meas_row.addWidget(self._sec_end_spin)
        edit_form.addRow("Measures:", meas_row)
        sec_layout.addLayout(edit_form)

        sec_btns = QHBoxLayout()
        self._btn_add_section = QPushButton("Add")
        self._btn_add_section.setToolTip("Add a new section definition.")
        self._btn_add_section.clicked.connect(self._add_section)
        sec_btns.addWidget(self._btn_add_section)
        self._btn_remove_section = QPushButton("Remove")
        self._btn_remove_section.setToolTip(
            "Remove the selected section (also removes it from play order).")
        self._btn_remove_section.clicked.connect(self._remove_section)
        self._btn_remove_section.setEnabled(False)
        sec_btns.addWidget(self._btn_remove_section)
        sec_btns.addStretch()
        sec_layout.addLayout(sec_btns)
        halves.addWidget(sec_group)

        # -- Right: play order sequencer --
        order_group = QGroupBox("Play Order")
        order_layout = QVBoxLayout(order_group)
        order_layout.addWidget(QLabel(
            "Arrange sections in the order they play.\n"
            "Mark where the loop starts (everything from\n"
            "that point onward repeats forever)."))
        order_layout.itemAt(0).widget().setStyleSheet(
            "color: #888; font-size: 10px;")

        self._order_list = QListWidget()
        self._order_list.setAlternatingRowColors(True)
        self._order_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self._order_list.currentRowChanged.connect(
            self._on_order_selected)
        order_layout.addWidget(self._order_list, 1)

        order_btns = QHBoxLayout()
        self._btn_add_to_order = QPushButton("Add ▶")
        self._btn_add_to_order.setToolTip(
            "Add the selected section to the play order.")
        self._btn_add_to_order.clicked.connect(self._add_to_order)
        order_btns.addWidget(self._btn_add_to_order)
        self._btn_remove_from_order = QPushButton("Remove")
        self._btn_remove_from_order.setToolTip(
            "Remove the selected entry from the play order.")
        self._btn_remove_from_order.clicked.connect(self._remove_from_order)
        self._btn_remove_from_order.setEnabled(False)
        order_btns.addWidget(self._btn_remove_from_order)
        order_btns.addStretch()
        self._btn_move_up = QPushButton("▲")
        self._btn_move_up.setToolTip("Move selected entry up in play order.")
        self._btn_move_up.clicked.connect(self._move_order_up)
        self._btn_move_up.setFixedWidth(30)
        order_btns.addWidget(self._btn_move_up)
        self._btn_move_down = QPushButton("▼")
        self._btn_move_down.setToolTip("Move selected entry down in play order.")
        self._btn_move_down.clicked.connect(self._move_order_down)
        self._btn_move_down.setFixedWidth(30)
        order_btns.addWidget(self._btn_move_down)
        order_layout.addLayout(order_btns)

        # Loop point
        loop_row = QHBoxLayout()
        loop_row.addWidget(QLabel("Loop starts at position:"))
        self._loop_point_spin = QSpinBox()
        self._loop_point_spin.setRange(1, 1)
        self._loop_point_spin.setValue(1)
        self._loop_point_spin.setToolTip(
            "Which position in the play order starts the loop.\n"
            "Everything before this plays once (intro).\n"
            "Everything from this position onward repeats.")
        self._loop_point_spin.valueChanged.connect(self._update_order_labels)
        loop_row.addWidget(self._loop_point_spin)
        self._loop_point_info = QLabel("")
        self._loop_point_info.setStyleSheet("color: #888; font-size: 10px;")
        loop_row.addWidget(self._loop_point_info)
        loop_row.addStretch()
        order_layout.addLayout(loop_row)

        halves.addWidget(order_group)
        layout.addLayout(halves, 1)

        # Internal data: list of section dicts
        # Each: {'name': str, 'start': int, 'end': int}
        self._sections: list[dict] = []
        # Play order: list of section indices into self._sections
        self._play_order: list[int] = []
        # 'custom' or 'automatic' or 'no_loop'
        # Default to 'no_loop' — most users don't want mid2agb's internal
        # PATT/PEND subroutine structure cluttering the Song Structure panel.
        # Users who want the raw mid2agb output can click "Automatic".
        self._structure_mode = 'no_loop'

        self._stack.addWidget(page)

    # ── Section management ────────────────────────────────────────────

    def _add_section(self):
        """Add a new section definition."""
        total = self._midi_info.total_measures if self._midi_info else 99
        idx = len(self._sections)
        # Default: next available measure range
        if self._sections:
            last_end = self._sections[-1]['end']
            start = last_end + 1
        else:
            start = 1
        end = min(start + 3, total)

        name = f"Section {chr(65 + idx % 26)}"  # A, B, C...
        sec = {'name': name, 'start': start, 'end': end}
        self._sections.append(sec)
        self._section_list.addItem(f"{name}  (m{start}–{end})")
        self._section_list.setCurrentRow(len(self._sections) - 1)
        self._set_structure_mode('custom')

    def _remove_section(self):
        """Remove the selected section and any play order entries referencing it."""
        row = self._section_list.currentRow()
        if row < 0 or row >= len(self._sections):
            return
        self._sections.pop(row)
        self._section_list.takeItem(row)
        # Remove from play order and adjust indices
        new_order = []
        for oi in self._play_order:
            if oi == row:
                continue
            new_order.append(oi - 1 if oi > row else oi)
        self._play_order = new_order
        self._rebuild_order_list()
        self._btn_remove_section.setEnabled(
            self._section_list.currentRow() >= 0)

    def _on_section_selected(self, row: int):
        """Update the edit fields when a section is selected."""
        self._btn_remove_section.setEnabled(row >= 0)
        if 0 <= row < len(self._sections):
            sec = self._sections[row]
            self._sec_name_edit.blockSignals(True)
            self._sec_name_edit.setText(sec['name'])
            self._sec_name_edit.blockSignals(False)
            self._sec_start_spin.blockSignals(True)
            self._sec_start_spin.setValue(sec['start'])
            self._sec_start_spin.blockSignals(False)
            self._sec_end_spin.blockSignals(True)
            self._sec_end_spin.setValue(sec['end'])
            self._sec_end_spin.blockSignals(False)

    def _on_section_name_edited(self, text: str):
        row = self._section_list.currentRow()
        if 0 <= row < len(self._sections):
            self._sections[row]['name'] = text
            sec = self._sections[row]
            self._section_list.item(row).setText(
                f"{text}  (m{sec['start']}–{sec['end']})")
            self._update_order_labels()

    def _on_section_range_edited(self):
        row = self._section_list.currentRow()
        if 0 <= row < len(self._sections):
            self._sections[row]['start'] = self._sec_start_spin.value()
            self._sections[row]['end'] = self._sec_end_spin.value()
            sec = self._sections[row]
            self._section_list.item(row).setText(
                f"{sec['name']}  (m{sec['start']}–{sec['end']})")
            self._update_order_labels()

    # ── Play order management ─────────────────────────────────────────

    def _add_to_order(self):
        """Add the currently selected section to the play order."""
        row = self._section_list.currentRow()
        if row < 0 or row >= len(self._sections):
            return
        self._play_order.append(row)
        self._rebuild_order_list()
        self._order_list.setCurrentRow(len(self._play_order) - 1)
        self._set_structure_mode('custom')

    def _remove_from_order(self):
        """Remove the selected entry from the play order."""
        row = self._order_list.currentRow()
        if 0 <= row < len(self._play_order):
            self._play_order.pop(row)
            self._rebuild_order_list()

    def _move_order_up(self):
        row = self._order_list.currentRow()
        if row > 0:
            self._play_order[row - 1], self._play_order[row] = \
                self._play_order[row], self._play_order[row - 1]
            self._rebuild_order_list()
            self._order_list.setCurrentRow(row - 1)

    def _move_order_down(self):
        row = self._order_list.currentRow()
        if 0 <= row < len(self._play_order) - 1:
            self._play_order[row], self._play_order[row + 1] = \
                self._play_order[row + 1], self._play_order[row]
            self._rebuild_order_list()
            self._order_list.setCurrentRow(row + 1)

    def _on_order_selected(self, row: int):
        self._btn_remove_from_order.setEnabled(
            0 <= row < len(self._play_order))

    def _rebuild_order_list(self):
        """Rebuild the play order list widget from self._play_order."""
        self._order_list.clear()
        loop_pos = self._loop_point_spin.value()
        for i, sec_idx in enumerate(self._play_order):
            pos = i + 1  # 1-based display
            sec = self._sections[sec_idx]
            prefix = "[LOOP] " if pos >= loop_pos else ""
            label = f"{pos}. {prefix}{sec['name']}  (m{sec['start']}–{sec['end']})"
            item = QListWidgetItem(label)
            if pos >= loop_pos:
                item.setForeground(QColor("#4a9"))
            self._order_list.addItem(item)
        self._loop_point_spin.setMaximum(max(1, len(self._play_order)))
        self._update_loop_info()

    def _update_order_labels(self):
        """Refresh play order labels (e.g. after rename or loop point change)."""
        self._rebuild_order_list()

    def _update_loop_info(self):
        """Update the loop point info label."""
        n = len(self._play_order)
        lp = self._loop_point_spin.value()
        if n == 0:
            self._loop_point_info.setText("(no sections in play order)")
        elif lp == 1:
            self._loop_point_info.setText("(loops entire sequence)")
        elif lp > n:
            self._loop_point_info.setText("(no loop — plays once)")
        else:
            intro_count = lp - 1
            loop_count = n - intro_count
            self._loop_point_info.setText(
                f"({intro_count} intro, {loop_count} looping)")

    # ── Presets ───────────────────────────────────────────────────────

    def _set_structure_mode(self, mode: str):
        self._structure_mode = mode
        labels = {
            'automatic': "Mode: Automatic — mid2agb handles structure from MIDI",
            'custom': "Mode: Custom — sections arranged manually",
            'no_loop': "Mode: No Loop (clean) — strips all labels, plays once then stops",
        }
        self._structure_mode_label.setText(labels.get(mode, f"Mode: {mode}"))

    def _preset_automatic(self):
        """Clear sections and use mid2agb's automatic structure."""
        self._sections.clear()
        self._play_order.clear()
        self._section_list.clear()
        self._order_list.clear()
        self._loop_point_spin.setValue(1)
        self._set_structure_mode('automatic')

    def _preset_loop_all(self):
        """One section = whole song, loop from position 1."""
        total = self._midi_info.total_measures if self._midi_info else 32
        self._sections.clear()
        self._play_order.clear()
        self._sections.append({'name': 'Full Song', 'start': 1, 'end': total})
        self._play_order.append(0)
        self._section_list.clear()
        self._section_list.addItem(f"Full Song  (m1–{total})")
        self._loop_point_spin.setValue(1)
        self._rebuild_order_list()
        self._set_structure_mode('custom')

    def _preset_no_loop(self):
        """One section = whole song, no loop."""
        total = self._midi_info.total_measures if self._midi_info else 32
        self._sections.clear()
        self._play_order.clear()
        self._sections.append({'name': 'Full Song', 'start': 1, 'end': total})
        self._play_order.append(0)
        self._section_list.clear()
        self._section_list.addItem(f"Full Song  (m1–{total})")
        self._loop_point_spin.setValue(2)  # past the end = no loop
        self._rebuild_order_list()
        self._set_structure_mode('no_loop')

    def _populate_structure_page(self):
        """Update structure page with MIDI info when entering the page."""
        if self._midi_info:
            total = self._midi_info.total_measures
            self._sec_end_spin.setMaximum(total)
            self._sec_start_spin.setMaximum(total)

            # If no sections defined yet (first visit), apply the default
            # no_loop preset so the data matches the default mode.
            if not self._sections:
                self._preset_no_loop()

    # ── Page 4: Progress & result ──────────────────────────────────────

    def _build_page_progress(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addStretch()

        self._progress_label = QLabel("Importing...")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_label.setFont(QFont("", 11))
        layout.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        layout.addWidget(self._progress_bar)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMaximumHeight(150)
        self._result_text.setVisible(False)
        layout.addWidget(self._result_text)

        layout.addStretch()
        self._stack.addWidget(page)

    # ═══════════════════════════════════════════════════════════════════════
    # Navigation
    # ═══════════════════════════════════════════════════════════════════════

    def _go_next(self):
        current = self._stack.currentIndex()

        if current == _PAGE_FILE:
            name = self._name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "Missing Name",
                                    "Please enter a constant name for the song.")
                return
            from core.sound.midi_importer import validate_constant_name
            valid, err = validate_constant_name(name, self._project_root)
            if not valid:
                QMessageBox.warning(self, "Invalid Name", err)
                return
            if self._midi_info is None:
                QMessageBox.warning(self, "No MIDI",
                                    "Please select a MIDI file first.")
                return
            self._stack.setCurrentIndex(_PAGE_SETTINGS)
            self._btn_back.setVisible(True)
            self._btn_next.setText("Next")

        elif current == _PAGE_SETTINGS:
            self._populate_mapping_page()
            self._stack.setCurrentIndex(_PAGE_MAPPING)
            self._btn_next.setText("Next")

        elif current == _PAGE_MAPPING:
            self._populate_structure_page()
            self._stack.setCurrentIndex(_PAGE_STRUCTURE)
            self._btn_next.setText("Import")

        elif current == _PAGE_STRUCTURE:
            self._start_import()

        self._update_step_label()

    def _go_back(self):
        current = self._stack.currentIndex()
        if current == _PAGE_SETTINGS:
            self._stack.setCurrentIndex(_PAGE_FILE)
            self._btn_back.setVisible(False)
            self._btn_next.setText("Next")
        elif current == _PAGE_MAPPING:
            self._stack.setCurrentIndex(_PAGE_SETTINGS)
            self._btn_next.setText("Next")
        elif current == _PAGE_STRUCTURE:
            self._stack.setCurrentIndex(_PAGE_MAPPING)
            self._btn_next.setText("Next")
        self._update_step_label()

    # ═══════════════════════════════════════════════════════════════════════
    # File browsing
    # ═══════════════════════════════════════════════════════════════════════

    def _browse_midi(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select MIDI File", "",
            "MIDI Files (*.mid *.midi);;All Files (*)",
        )
        if not path:
            return
        self._file_edit.setText(path)
        self._load_midi(path)

    def _load_midi(self, path: str):
        try:
            from core.sound.midi_importer import read_midi_info
            info = read_midi_info(path)
        except Exception as e:
            QMessageBox.critical(self, "MIDI Error",
                                 f"Could not read MIDI file:\n{e}")
            return

        self._midi_info = info

        self._midi_summary.setText(
            f"Type {info.midi_type} MIDI  |  "
            f"{info.tempo_bpm} BPM  |  "
            f"{info.duration_sec:.1f}s  |  "
            f"{len(info.tracks)} tracks  |  "
            f"{info.total_notes} notes"
        )

        self._track_tree.clear()
        note_names = ["C", "C#", "D", "D#", "E", "F",
                      "F#", "G", "G#", "A", "A#", "B"]
        for t in info.tracks:
            lo = f"{note_names[t.note_min % 12]}{t.note_min // 12 - 1}" if t.note_count else "-"
            hi = f"{note_names[t.note_max % 12]}{t.note_max // 12 - 1}" if t.note_count else "-"
            item = QTreeWidgetItem([
                str(t.channel), t.name, t.instrument_name,
                str(t.note_count),
                f"{lo} - {hi}" if t.note_count else "-",
            ])
            if t.is_drums:
                item.setToolTip(2, "Drums channel (MIDI channel 10)")
            self._track_tree.addTopLevelItem(item)

        # Auto-suggest name
        base = os.path.splitext(os.path.basename(path))[0]
        clean = re.sub(r'[^a-zA-Z0-9]', '_', base).upper()
        clean = re.sub(r'_+', '_', clean).strip('_')
        if not clean.startswith("MUS_") and not clean.startswith("SE_"):
            clean = "MUS_" + clean
        self._name_edit.setText(clean)
        self._btn_next.setEnabled(True)

    # ═══════════════════════════════════════════════════════════════════════
    # Name validation
    # ═══════════════════════════════════════════════════════════════════════

    def _validate_name(self, text: str):
        if not text:
            self._name_status.setText("")
            self._name_status.setStyleSheet("font-size: 10px;")
            return
        from core.sound.midi_importer import validate_constant_name
        valid, err = validate_constant_name(text, self._project_root)
        if valid:
            label = text.lower()
            self._name_status.setText(f"Label: {label}  |  File: {label}.s")
            self._name_status.setStyleSheet("color: #6a6; font-size: 10px;")
        else:
            self._name_status.setText(err)
            self._name_status.setStyleSheet("color: #c44; font-size: 10px;")

    # ═══════════════════════════════════════════════════════════════════════
    # Import execution
    # ═══════════════════════════════════════════════════════════════════════

    def _start_import(self):
        from core.sound.midi_importer import Mid2AgbSettings

        constant = self._name_edit.text().strip()

        vg_text = self._vg_combo.currentText()
        m = re.search(r'(\d+)', vg_text)
        vg_num = int(m.group(1)) if m else 0

        settings = Mid2AgbSettings(
            voicegroup_num=vg_num,
            reverb=self._reverb_spin.value(),
            master_volume=self._vol_spin.value(),
            priority=self._priority_spin.value(),
            exact_gate=self._exact_gate_cb.isChecked(),
            high_resolution=self._high_res_cb.isChecked(),
        )

        music_player = self._player_combo.currentIndex()

        # Build voice remap from the mapping page
        voice_remap = {}
        for gm_num, combo in self._mapping_combos:
            if gm_num >= 0 and combo.currentIndex() != gm_num:
                voice_remap[gm_num] = combo.currentIndex()

        # Loop / structure config
        loop_config = {
            'mode': self._structure_mode,   # 'automatic', 'custom', 'no_loop'
            'sections': list(self._sections),
            'play_order': list(self._play_order),
            'loop_point': self._loop_point_spin.value(),
        }

        # Switch to progress page
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._update_step_label()
        self._btn_next.setEnabled(False)
        self._btn_back.setVisible(False)
        self._btn_cancel.setEnabled(False)
        self._progress_label.setText("Converting MIDI and registering song...")
        self._progress_label.setStyleSheet("")
        self._result_text.setVisible(False)
        self._progress_bar.setRange(0, 0)

        self._worker = _ImportWorker(
            self._project_root,
            self._midi_info.path,
            constant,
            music_player,
            settings,
            voice_remap,
            loop_config,
        )
        self._worker.finished.connect(self._on_import_done)
        self._worker.start()

    def _on_import_done(self, success: bool, s_path: str, error: str):
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._btn_cancel.setEnabled(True)

        if success:
            constant = self._name_edit.text().strip()

            # Count remapped instruments
            remap_count = sum(1 for gm, combo in self._mapping_combos
                              if gm >= 0 and combo.currentIndex() != gm)

            self._progress_label.setText("Import successful!")
            self._progress_label.setStyleSheet("color: #6a6; font-size: 12px;")

            remap_note = ""
            if remap_count:
                remap_note = f"\n{remap_count} instrument(s) remapped to different voicegroup slots.\n"

            self._result_text.setVisible(True)
            self._result_text.setText(
                f"Song imported successfully.\n\n"
                f"Constant: {constant}\n"
                f"Label: {constant.lower()}\n"
                f"File: {os.path.basename(s_path)}\n"
                f"{remap_note}\n"
                f"The song has been added to:\n"
                f"  - include/constants/songs.h\n"
                f"  - sound/song_table.inc\n"
                f"  - sound/songs/midi/midi.cfg\n\n"
                f"You should build the project (Make) to verify it compiles."
            )

            self._btn_next.setText("Done")
            self._btn_next.setEnabled(True)
            try:
                self._btn_next.clicked.disconnect()
            except TypeError:
                pass
            self._btn_next.clicked.connect(self.accept)

            self.song_imported.emit(constant)
        else:
            self._progress_label.setText("Import failed")
            self._progress_label.setStyleSheet("color: #c44; font-size: 12px;")

            self._result_text.setVisible(True)
            self._result_text.setText(f"Error: {error}")

            self._btn_back.setVisible(True)
            self._btn_next.setText("Retry")
            self._btn_next.setEnabled(True)
            try:
                self._btn_next.clicked.disconnect()
            except TypeError:
                pass
            self._btn_next.clicked.connect(
                lambda: (self._stack.setCurrentIndex(_PAGE_STRUCTURE),
                         self._update_step_label()))
