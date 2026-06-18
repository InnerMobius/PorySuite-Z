"""Import Song .s File Dialog for PorySuite-Z Sound Editor.

Multi-step wizard:
  Page 0 — Pick a .s file, preview song info, choose constant name + player type
  Page 1 — Voicegroup compatibility check & optional remap
  Page 2 — Progress / result
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QGroupBox, QFormLayout, QFileDialog,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QSpinBox,
    QProgressBar, QMessageBox, QFrame, QSizePolicy,
    QStackedWidget, QWidget, QTextEdit, QScrollArea, QGridLayout,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


_log = logging.getLogger("SoundEditor")

# Page indices
_PAGE_FILE = 0
_PAGE_VOICEGROUP = 1
_PAGE_MAPPING = 2
_PAGE_PROGRESS = 3


def _extract_voice_usage(song) -> dict:
    """Map each VOICE slot number used in the song to the track indices that
    use it.

    Returns ``{voice_num: [track_index, ...]}`` sorted. A song with no explicit
    VOICE commands yields an empty dict — the caller treats that as "voice 0".
    """
    usage: dict = {}
    for track in song.tracks:
        for cmd in track.commands:
            if cmd.cmd == 'VOICE' and cmd.value is not None:
                num = int(cmd.value)
                bucket = usage.setdefault(num, [])
                if track.index not in bucket:
                    bucket.append(track.index)
    for num in usage:
        usage[num].sort()
    return usage


def _type_tag(inst) -> str:
    """Short instrument-type tag for the slot dropdown ('square', 'sample'…).

    Lets the user spot a square/PSG voice when remapping, instead of having to
    guess from the friendly name alone.
    """
    if getattr(inst, 'is_keysplit', False):
        return 'keysplit'
    if getattr(inst, 'is_square', False):
        return 'square'
    if getattr(inst, 'is_programmable_wave', False):
        return 'wave'
    if getattr(inst, 'is_noise', False):
        return 'noise'
    if getattr(inst, 'is_directsound', False):
        return 'sample'
    return ''


# ── Worker thread ─────────────────────────────────────────────────────────

class _SImportWorker(QThread):
    """Copies .s file, optionally remaps voicegroup, and registers the song."""
    finished = pyqtSignal(bool, str, str)  # success, s_path, error

    def __init__(self, project_root: str, source_s_path: str,
                 constant: str, label: str, music_player: int,
                 target_vg_name: str, source_vg_name: str,
                 reverb: int, volume: int, priority: int,
                 voice_remap: Optional[dict] = None,
                 overwrite: bool = False,
                 skip_registration: bool = False):
        super().__init__()
        self._project_root = project_root
        self._source_s_path = source_s_path
        self._constant = constant
        self._label = label
        self._music_player = music_player
        self._target_vg_name = target_vg_name
        self._source_vg_name = source_vg_name
        self._reverb = reverb
        self._volume = volume
        self._priority = priority
        self._voice_remap = voice_remap or {}
        self._overwrite = overwrite
        self._skip_registration = skip_registration

    def run(self):
        try:
            dest_dir = os.path.join(self._project_root, "sound", "songs", "midi")
            dest_filename = self._label + ".s"
            dest_path = os.path.join(dest_dir, dest_filename)

            # Check destination doesn't already exist (unless overwrite was approved)
            if os.path.isfile(dest_path) and not self._overwrite:
                self.finished.emit(False, "",
                                   f"File already exists: sound/songs/midi/{dest_filename}")
                return

            # Copy the .s file (overwrites if approved)
            shutil.copy2(self._source_s_path, dest_path)

            # Rewrite label references BEFORE generating the companion .mid
            # so the .mid is rendered from the same parsed content the user
            # will see in the editor (correct label, voicegroup, etc).
            self._rewrite_labels(dest_path)
            if (self._target_vg_name and self._source_vg_name
                    and self._target_vg_name != self._source_vg_name):
                self._rewrite_voicegroup(dest_path)

            # Persist the wizard's priority / reverb / volume into the .s's
            # .equ equates. register_song only writes these to midi.cfg; without
            # this the copied .s keeps the source file's values (usually 0), so
            # the wizard's Priority box never took effect. Writing both the .s
            # equate AND midi.cfg keeps them consistent so a mid2agb regen can't
            # silently revert the edit.
            self._rewrite_properties(dest_path)

            # Remap VOICE commands to the voicegroup slots the user chose on
            # the Instruments page. The source .s keeps whatever slot it was
            # authored against (often 0); without this the song silently uses
            # whatever instrument happens to sit in that slot of the target
            # voicegroup. Reuses the MIDI importer's proven rewriter. Done
            # BEFORE the companion .mid render so the .mid reflects the remap.
            if self._voice_remap:
                from ui.dialogs.midi_import_dialog import _postprocess_voice_remap
                _postprocess_voice_remap(dest_path, self._voice_remap)

            # Generate a real, content-matching .mid alongside the .s.
            # This replaces the previous 26-byte placeholder approach,
            # which left the .s exposed to build-time wipe: any later
            # midi.cfg bump (rename, add-song, etc.) would fire the Make
            # rule `%.s: %.mid midi.cfg` and overwrite the user's .s with
            # the empty output of mid2agb running on the placeholder.
            # With a content-matching .mid the worst-case mid2agb re-run
            # produces an audibly-equivalent .s — no data loss.
            self._create_companion_mid(dest_dir, dest_path)

            # Ensure .s is newer than .mid — make's %.s:%.mid rule runs
            # mid2agb when .mid is newer, which would overwrite our .s.
            # 1-hour backdate (matching save_song_file) — 2 seconds was
            # inside filesystem-clock-jitter range.  Label / voicegroup
            # rewriting was already done above before the .mid render,
            # so the .s on disk is already in its final form.
            import time as _time
            now = _time.time()
            os.utime(dest_path, (now, now))
            mid_file = os.path.join(dest_dir, self._label + ".mid")
            if os.path.isfile(mid_file):
                far_past = now - 3600
                os.utime(mid_file, (far_past, far_past))

            # Register in song_table.inc, songs.h, midi.cfg
            # (skipped when reimporting — the song is already registered)
            if not self._skip_registration:
                from core.sound.midi_importer import register_song, Mid2AgbSettings

                # Extract voicegroup number for midi.cfg
                vg_num = 0
                m = re.search(r'(\d+)', self._target_vg_name or self._source_vg_name)
                if m:
                    vg_num = int(m.group(1))

                settings = Mid2AgbSettings(
                    voicegroup_num=vg_num,
                    reverb=self._reverb,
                    master_volume=self._volume,
                    priority=self._priority,
                )

                ok, err = register_song(
                    self._project_root,
                    self._label,
                    self._constant,
                    self._music_player,
                    settings,
                )
                if not ok:
                    # Clean up copied file on registration failure
                    try:
                        os.remove(dest_path)
                    except OSError:
                        pass
                    self.finished.emit(False, "", err)
                    return

            self.finished.emit(True, dest_path, "")

        except Exception as e:
            self.finished.emit(False, "", str(e))

    def _rewrite_labels(self, dest_path: str):
        """Rewrite the .s's internal naming to match our target label.

        A song .s uses one stem for its labels + equates (mus_x, mus_x_1,
        mus_x_grp, ...). The public ``.global`` label and that equate stem CAN
        differ when the source was itself imported/renamed (e.g. ``.global
        se_confirm`` but ``.equ se_sfx_minish_106_grp``). Rewrite EVERY internal
        stem to the target so the file is consistent — otherwise the parser
        can't match the voicegroup/properties and a later rename leaves orphaned
        ``<oldstem>_*`` garbage behind.
        """
        with open(dest_path, encoding="utf-8") as f:
            content = f.read()

        stems = set()
        gm = re.search(r'^\s*\.global\s+(\w+)\s*$', content, re.MULTILINE)
        if gm:
            stems.add(gm.group(1))
        # The per-song equates carry the .s's TRUE internal stem, which the
        # parser keys off — rewrite it even if the .global label already matches.
        for em in re.finditer(r'^\s*\.equ\s+(\w+)_grp\s*,', content, re.MULTILINE):
            stems.add(em.group(1))
        stems.discard(self._label)
        stems.discard('')
        if not stems:
            return

        # Longest first so a shorter stem can't partially clobber a longer one.
        # Anchor at a word start + allow a trailing `_suffix`: a bare `\b...\b`
        # misses `<stem>_grp` because `_` is a word character (no boundary).
        for stem in sorted(stems, key=len, reverse=True):
            content = re.sub(r'\b' + re.escape(stem) + r'(?=_|\b)',
                             self._label, content)

        with open(dest_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def _rewrite_voicegroup(self, dest_path: str):
        """Rewrite the voicegroup .equ reference in the .s file."""
        with open(dest_path, encoding="utf-8") as f:
            lines = f.readlines()

        grp_re = re.compile(
            r'^(\s*\.equ\s+\w+_grp\s*,\s*)(\w+)(.*)$'
        )
        changed = False
        for i, line in enumerate(lines):
            m = grp_re.match(line)
            if m:
                lines[i] = f"{m.group(1)}{self._target_vg_name}{m.group(3)}\n"
                changed = True
                break

        if changed:
            with open(dest_path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines)

    def _rewrite_properties(self, dest_path: str):
        """Write the wizard's priority / reverb / volume into the .s's
        ``.equ <label>_pri/_rev/_mvl`` equates. The priority struct byte at the
        bottom is emitted as ``.byte <label>_pri``, so updating the equate
        updates it too. Reverb keeps the engine's ``reverb_set+N`` form."""
        with open(dest_path, encoding="utf-8") as f:
            lines = f.readlines()

        pri_re = re.compile(r'^(\s*\.equ\s+\w+_pri\s*,\s*).*$')
        rev_re = re.compile(r'^(\s*\.equ\s+\w+_rev\s*,\s*).*$')
        mvl_re = re.compile(r'^(\s*\.equ\s+\w+_mvl\s*,\s*).*$')

        for i, line in enumerate(lines):
            stripped = line.rstrip('\n')
            if pri_re.match(stripped):
                lines[i] = pri_re.sub(rf'\g<1>{self._priority}', stripped) + '\n'
            elif rev_re.match(stripped):
                lines[i] = rev_re.sub(rf'\g<1>reverb_set+{self._reverb}', stripped) + '\n'
            elif mvl_re.match(stripped):
                lines[i] = mvl_re.sub(rf'\g<1>{self._volume}', stripped) + '\n'

        with open(dest_path, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines)

    @staticmethod
    def _make_placeholder_mid() -> bytes:
        """Return a 26-byte empty SMF.

        DEPRECATED — kept only so call sites that still reference it during
        a transition don't break.  Do NOT call this for new code: write a
        real, content-matching .mid via `_create_companion_mid` instead.

        Placeholder .mids are a regression vector: they sit on disk paired
        with a real-content .s, and any future midi.cfg touch (rename,
        add-song, etc.) makes midi.cfg newer than .s, which fires the
        Make rule `%.s: %.mid midi.cfg` and overwrites the user's .s
        with whatever the placeholder produces (empty 0-track output).
        """
        import struct
        header = b'MThd' + struct.pack('>I', 6) + struct.pack('>HHH', 0, 1, 48)
        track_data = b'\x00\xff\x2f\x00'
        track = b'MTrk' + struct.pack('>I', len(track_data)) + track_data
        return header + track

    def _create_companion_mid(self, dest_dir: str, s_path: str):
        """Generate a real, playable .mid alongside the just-imported .s.

        Parses the .s and runs `midi_exporter.write_midi_file` so the .mid
        contains the same composition as the .s.  This protects the .s
        from build-time wipe: even if midi.cfg gets bumped and mid2agb
        runs, the regenerated .s is audibly equivalent to the user's
        import — no silent data loss.

        Falls back to the legacy 26-byte placeholder ONLY if .s parsing
        or .mid rendering fails — in that case the placeholder is the
        least-bad option (the song would otherwise not build at all
        because the Makefile wildcard requires a .mid file to exist).
        """
        mid_path = os.path.join(dest_dir, self._label + ".mid")

        # If the user already has a real-content .mid at this path, leave
        # it alone — we don't want to nuke their DAW source.
        if os.path.isfile(mid_path):
            try:
                if os.path.getsize(mid_path) > 30:
                    _log.info(
                        ".mid already has content; leaving alone: %s",
                        mid_path)
                    return
            except OSError:
                pass

        try:
            from core.sound.song_parser import parse_song_file
            from core.sound.midi_exporter import write_midi_file
            song = parse_song_file(s_path)
            if song.tracks and write_midi_file(song, mid_path):
                _log.info(
                    "Generated content-matching .mid from %s",
                    os.path.basename(s_path))
                return
        except Exception as exc:
            _log.warning(
                "Could not render .mid from %s — falling back to "
                "placeholder. Reason: %s",
                os.path.basename(s_path), exc, exc_info=True)

        # Last-resort placeholder so the build doesn't fail completely.
        # The Makefile wildcard requires a .mid file to exist; an empty
        # SMF still satisfies the wildcard.  Save IS still risky if a
        # later midi.cfg bump triggers mid2agb on this empty .mid — but
        # the project-open integrity sweep (`song_integrity.run_sweep`)
        # will catch and regenerate this on the next project load.
        with open(mid_path, "wb") as f:
            f.write(self._make_placeholder_mid())
        _log.info(
            "Wrote placeholder .mid (parser/exporter unavailable): %s",
            mid_path)


# ═══════════════════════════════════════════════════════════════════════════
# Main dialog
# ═══════════════════════════════════════════════════════════════════════════

class SFileImportDialog(QDialog):
    """Import Song .s File wizard dialog."""

    song_imported = pyqtSignal(str)  # constant name

    def __init__(self, project_root: str, voicegroup_names: list[str],
                 voicegroup_data=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root = project_root
        self._voicegroup_names = voicegroup_names
        self._vg_data = voicegroup_data
        self._parsed_song = None  # SongData from parser
        self._source_path = ""
        self._worker = None
        # (old_voice_num, slot_combo) rows on the Instruments page
        self._voice_map_combos: list = []

        self.setWindowTitle("Import Song (.s File)")
        self.setMinimumSize(650, 500)
        self.resize(700, 550)

        self._build_ui()

    # ═══════════════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Import Song from .s File")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # Step indicator
        self._step_label = QLabel("")
        self._step_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._step_label)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._build_page_file()        # 0
        self._build_page_voicegroup()  # 1
        self._build_page_mapping()     # 2
        self._build_page_progress()    # 3

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
        names = ["Select File", "Voicegroup", "Instruments", "Import"]
        if 0 <= page < len(names):
            parts = []
            for i, n in enumerate(names):
                if i == page:
                    parts.append(f"[{n}]")
                else:
                    parts.append(n)
            self._step_label.setText("  >  ".join(parts))

    # ── Page 0: File selection & song preview ─────────────────────────────

    def _build_page_file(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)

        # File picker
        file_group = QGroupBox("Song File")
        file_layout = QHBoxLayout(file_group)
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("No file selected...")
        file_layout.addWidget(self._path_edit, 1)

        browse_btn = QPushButton("Browse...")
        browse_btn.setToolTip("Select a .s assembly song file from another project")
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(browse_btn)
        layout.addWidget(file_group)

        # Song info
        info_group = QGroupBox("Song Info")
        info_layout = QFormLayout(info_group)
        info_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._info_label = QLabel("—")
        info_layout.addRow("Label:", self._info_label)
        self._info_vg = QLabel("—")
        info_layout.addRow("Voicegroup:", self._info_vg)
        self._info_tracks = QLabel("—")
        info_layout.addRow("Tracks:", self._info_tracks)
        self._info_tempo = QLabel("—")
        info_layout.addRow("Tempo:", self._info_tempo)
        self._info_reverb = QLabel("—")
        info_layout.addRow("Reverb:", self._info_reverb)
        self._info_volume = QLabel("—")
        info_layout.addRow("Volume:", self._info_volume)

        layout.addWidget(info_group)

        # Track list
        self._track_tree = QTreeWidget()
        self._track_tree.setHeaderLabels(["Track", "Notes", "Loop"])
        self._track_tree.setRootIsDecorated(False)
        self._track_tree.setAlternatingRowColors(True)
        self._track_tree.setMaximumHeight(160)
        header = self._track_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._track_tree)

        # Song name
        name_group = QGroupBox("Song Registration")
        name_layout = QFormLayout(name_group)
        name_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. My Battle Theme")
        self._name_edit.setToolTip(
            "Display name for the song.\n"
            "The constant (MUS_MY_BATTLE_THEME) is derived automatically.")
        self._name_edit.textChanged.connect(self._validate_name)
        name_layout.addRow("Song Name:", self._name_edit)

        self._const_label = QLabel("—")
        self._const_label.setStyleSheet("font-size: 10px; color: #aaa;")
        name_layout.addRow("Constant:", self._const_label)

        self._name_status = QLabel("")
        self._name_status.setStyleSheet("font-size: 10px;")
        name_layout.addRow("", self._name_status)

        # Prefix selector (MUS_ or SE_)
        self._prefix_combo = QComboBox()
        self._prefix_combo.addItems(["MUS_ (Music)", "SE_ (Sound Effect)"])
        self._prefix_combo.setToolTip("Whether this is background music or a sound effect")
        install_scroll_guard(self._prefix_combo)
        self._prefix_combo.currentIndexChanged.connect(
            lambda: self._validate_name(self._name_edit.text()))
        name_layout.addRow("Type:", self._prefix_combo)

        self._player_combo = QComboBox()
        self._player_combo.addItems(["BGM (Background Music)", "SE1 (Sound Effect 1)",
                                     "SE2 (Sound Effect 2)", "SE3 (Sound Effect 3)"])
        self._player_combo.setToolTip("Which music player slot this song uses")
        install_scroll_guard(self._player_combo)
        name_layout.addRow("Player:", self._player_combo)

        layout.addWidget(name_group)
        layout.addStretch()

        self._stack.addWidget(page)

    # ── Page 1: Voicegroup compatibility ──────────────────────────────────

    def _build_page_voicegroup(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)

        # Source voicegroup info
        src_group = QGroupBox("Source Voicegroup")
        src_layout = QFormLayout(src_group)
        self._src_vg_label = QLabel("—")
        src_layout.addRow("The .s file uses:", self._src_vg_label)

        self._vg_status_label = QLabel("")
        self._vg_status_label.setWordWrap(True)
        src_layout.addRow("", self._vg_status_label)
        layout.addWidget(src_group)

        # Target voicegroup picker
        tgt_group = QGroupBox("Target Voicegroup")
        tgt_layout = QVBoxLayout(tgt_group)

        tgt_layout.addWidget(QLabel(
            "Choose which voicegroup this song should use in your project.\n"
            "If the source voicegroup exists in your project, it's pre-selected.\n"
            "Otherwise, pick the closest match — instruments may sound different."))

        row = QHBoxLayout()
        row.addWidget(QLabel("Voicegroup:"))
        self._vg_combo = QComboBox()
        self._vg_combo.setToolTip("Select the voicegroup for this song in your project")
        install_scroll_guard(self._vg_combo)
        row.addWidget(self._vg_combo, 1)
        tgt_layout.addLayout(row)

        # Editable reverb/volume/priority (pre-filled from parsed .s)
        params_layout = QFormLayout()

        self._reverb_spin = QSpinBox()
        self._reverb_spin.setRange(0, 127)
        self._reverb_spin.setToolTip("Reverb depth (0 = off, higher = more echo)")
        install_scroll_guard(self._reverb_spin)
        params_layout.addRow("Reverb:", self._reverb_spin)

        self._vol_spin = QSpinBox()
        self._vol_spin.setRange(0, 127)
        self._vol_spin.setValue(127)
        self._vol_spin.setToolTip("Master volume (0-127)")
        install_scroll_guard(self._vol_spin)
        params_layout.addRow("Volume:", self._vol_spin)

        self._priority_spin = QSpinBox()
        self._priority_spin.setRange(0, 127)
        self._priority_spin.setToolTip("Playback priority (higher = less likely to be cut off)")
        install_scroll_guard(self._priority_spin)
        params_layout.addRow("Priority:", self._priority_spin)

        tgt_layout.addLayout(params_layout)
        layout.addWidget(tgt_group)

        layout.addStretch()
        self._stack.addWidget(page)

    # ── Page 2: Instrument Mapping ────────────────────────────────────────

    def _build_page_mapping(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel(
            "This .s file plays each track on a numbered instrument slot of the\n"
            "voicegroup. Pick which slot each one should use here — the source\n"
            "file's instrument bank doesn't come with it, so a slot that's wrong\n"
            "in your project (e.g. a sampled piano where the original was a\n"
            "square) will sound wrong in-game even if the notes are right.\n"
            "Leave a row on its original number to keep the .s file's choice."))

        self._mapping_info = QLabel("")
        self._mapping_info.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._mapping_info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._mapping_container = QWidget()
        self._mapping_layout = QGridLayout(self._mapping_container)
        self._mapping_layout.setColumnStretch(0, 0)   # Voice #
        self._mapping_layout.setColumnStretch(1, 2)   # Used by
        self._mapping_layout.setColumnStretch(2, 0)   # Arrow
        self._mapping_layout.setColumnStretch(3, 3)   # VG slot picker

        for col, text in enumerate(["Voice", "Used by", "", "VG Instrument"]):
            self._mapping_layout.addWidget(QLabel(f"<b>{text}</b>"), 0, col)

        scroll.setWidget(self._mapping_container)
        layout.addWidget(scroll, 1)

        self._stack.addWidget(page)

    def _get_target_vg_instruments(self) -> list:
        """Instrument list for the voicegroup currently selected on page 1."""
        if not self._vg_data:
            return []
        m = re.search(r'(\d+)', self._vg_combo.currentText())
        if not m:
            return []
        vg = self._vg_data.get_voicegroup_by_number(int(m.group(1)))
        return vg.instruments if vg else []

    def _populate_mapping_page(self):
        """Build one row per VOICE slot the song uses, each with a dropdown of
        the target voicegroup's instruments."""
        from ui.dialogs.midi_import_dialog import _is_filler

        # Clear previous rows (keep the header row 0).
        for i in reversed(range(self._mapping_layout.count())):
            item = self._mapping_layout.itemAt(i)
            if item and item.widget():
                row, _c, _rs, _cs = self._mapping_layout.getItemPosition(i)
                if row > 0:
                    item.widget().deleteLater()
        self._voice_map_combos.clear()

        song = self._parsed_song
        if not song:
            return

        vg_instruments = self._get_target_vg_instruments()
        usage = _extract_voice_usage(song)
        if not usage:
            usage = {0: []}  # no explicit VOICE — the engine defaults to slot 0

        self._mapping_info.setText(
            f"Voicegroup: {self._vg_combo.currentText()}")

        row = 1
        for voice_num in sorted(usage):
            tracks = usage[voice_num]

            vlabel = QLabel(str(voice_num))
            vlabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._mapping_layout.addWidget(vlabel, row, 0)

            if tracks:
                used_by = ", ".join(f"Track {t + 1}" for t in tracks)
            else:
                used_by = "(default)"
            self._mapping_layout.addWidget(QLabel(used_by), row, 1)

            arrow = QLabel("  →  ")
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._mapping_layout.addWidget(arrow, row, 2)

            slot_combo = QComboBox()
            slot_combo.setMaxVisibleItems(20)
            slot_combo.setMinimumWidth(280)
            install_scroll_guard(slot_combo)
            for idx in range(128):
                if vg_instruments and idx < len(vg_instruments):
                    inst = vg_instruments[idx]
                    if _is_filler(inst):
                        label = f"{idx}: (empty)"
                    else:
                        tag = _type_tag(inst)
                        tagstr = f"[{tag}] " if tag else ""
                        label = f"{idx}: {tagstr}{inst.friendly_name}"
                else:
                    label = f"{idx}: (unknown)"
                slot_combo.addItem(label, idx)
            for idx in range(slot_combo.count()):
                if vg_instruments and idx < len(vg_instruments):
                    inst = vg_instruments[idx]
                    color = QColor("#c44") if _is_filler(inst) else QColor("#6a6")
                    slot_combo.setItemData(
                        idx, color, Qt.ItemDataRole.ForegroundRole)
            slot_combo.setCurrentIndex(max(0, min(127, voice_num)))
            slot_combo.setToolTip(
                f"Which voicegroup slot this voice plays.\n"
                f"Default {voice_num} keeps the .s file's original instrument.")
            self._mapping_layout.addWidget(slot_combo, row, 3)

            self._voice_map_combos.append((voice_num, slot_combo))
            row += 1

    def _build_voice_remap(self) -> dict:
        """Collect {old_voice: new_slot} for rows the user actually changed."""
        remap: dict = {}
        for old_voice, combo in self._voice_map_combos:
            data = combo.currentData()
            new_slot = int(data) if data is not None else combo.currentIndex()
            if new_slot != int(old_voice):
                remap[int(old_voice)] = new_slot
        return remap

    # ── Page 3: Progress / result ─────────────────────────────────────────

    def _build_page_progress(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)

        self._progress_label = QLabel("Importing...")
        self._progress_label.setFont(QFont("", 11))
        layout.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        layout.addWidget(self._progress_bar)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
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
            # Validate file
            if not self._parsed_song:
                QMessageBox.warning(self, "No File",
                                    "Please select a .s song file first.")
                return

            # Validate name
            display_name = self._name_edit.text().strip()
            if not display_name:
                QMessageBox.warning(self, "Missing Name",
                                    "Please enter a name for the song.")
                return
            constant = self._derive_constant(display_name)
            if not constant:
                QMessageBox.warning(self, "Invalid Name",
                                    "Could not derive a valid constant from that name.")
                return
            from core.sound.midi_importer import validate_constant_name
            valid, err = validate_constant_name(constant, self._project_root)
            if not valid and "already exists" not in err:
                QMessageBox.warning(self, "Invalid Name", err)
                return

            self._populate_voicegroup_page()
            self._stack.setCurrentIndex(_PAGE_VOICEGROUP)
            self._btn_back.setVisible(True)
            self._btn_next.setText("Next")

        elif current == _PAGE_VOICEGROUP:
            # Build the instrument-mapping rows against the chosen voicegroup.
            self._populate_mapping_page()
            self._stack.setCurrentIndex(_PAGE_MAPPING)
            self._btn_next.setText("Import")

        elif current == _PAGE_MAPPING:
            self._start_import()

        self._update_step_label()

    def _go_back(self):
        current = self._stack.currentIndex()
        if current == _PAGE_VOICEGROUP:
            self._stack.setCurrentIndex(_PAGE_FILE)
            self._btn_back.setVisible(False)
            self._btn_next.setText("Next")
        elif current == _PAGE_MAPPING:
            self._stack.setCurrentIndex(_PAGE_VOICEGROUP)
            self._btn_next.setText("Next")
        self._update_step_label()

    # ═══════════════════════════════════════════════════════════════════════
    # File browsing
    # ═══════════════════════════════════════════════════════════════════════

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Song .s File", "",
            "GBA Song Assembly (*.s);;All Files (*)",
        )
        if not path:
            return

        self._source_path = path
        self._path_edit.setText(path)
        self._parse_file(path)

    def _parse_file(self, path: str):
        """Parse the selected .s file and populate the preview."""
        try:
            from core.sound.song_parser import parse_song_file, get_song_tempo
            song = parse_song_file(path)
            self._parsed_song = song

            # Fill info fields
            self._info_label.setText(song.label or "(unknown)")
            self._info_vg.setText(song.voicegroup or "(none)")
            self._info_tracks.setText(str(song.num_tracks))

            tempo = get_song_tempo(song)
            self._info_tempo.setText(f"{tempo} BPM" if tempo else "—")
            self._info_reverb.setText(str(song.reverb))
            self._info_volume.setText(str(song.master_volume))

            # Populate track tree
            self._track_tree.clear()
            for track in song.tracks:
                note_count = sum(1 for c in track.commands if c.cmd == 'NOTE')
                has_loop = "Yes" if track.loop_label else "No"
                item = QTreeWidgetItem([
                    f"Track {track.index + 1} ({track.label})",
                    str(note_count),
                    has_loop,
                ])
                self._track_tree.addTopLevelItem(item)

            # Auto-suggest display name from label
            if song.label and not self._name_edit.text().strip():
                # Convert mus_battle_theme -> Battle Theme
                name = song.label
                for prefix in ('mus_', 'se_'):
                    if name.lower().startswith(prefix):
                        name = name[len(prefix):]
                        if prefix == 'se_':
                            self._prefix_combo.setCurrentIndex(1)
                        break
                friendly = name.replace('_', ' ').title()
                self._name_edit.setText(friendly)

            self._btn_next.setEnabled(True)
            _log.info("Parsed .s file: %s (%d tracks, vg=%s)",
                      song.label, song.num_tracks, song.voicegroup)

        except Exception as e:
            self._parsed_song = None
            self._btn_next.setEnabled(False)
            QMessageBox.warning(self, "Parse Error",
                                f"Could not parse the .s file:\n\n{e}")
            _log.error("Failed to parse .s file %s: %s", path, e)

    def _derive_constant(self, display_name: str) -> str:
        """Convert a display name to a constant: 'My Battle Theme' -> 'MUS_MY_BATTLE_THEME'."""
        prefix = "MUS_" if self._prefix_combo.currentIndex() == 0 else "SE_"
        # Strip non-alphanumeric, replace spaces/hyphens with underscore, uppercase
        clean = re.sub(r'[^a-zA-Z0-9\s_-]', '', display_name.strip())
        clean = re.sub(r'[\s-]+', '_', clean).upper()
        clean = re.sub(r'_+', '_', clean).strip('_')
        if not clean:
            return ""
        return prefix + clean

    def _validate_name(self, text: str):
        """Live validation of the display name field."""
        if not text.strip():
            self._const_label.setText("—")
            self._name_status.setText("")
            self._name_status.setStyleSheet("font-size: 10px;")
            return

        constant = self._derive_constant(text)
        self._const_label.setText(constant)

        if not constant:
            self._name_status.setText("Enter a valid name")
            self._name_status.setStyleSheet("color: #c66; font-size: 10px;")
            return

        from core.sound.midi_importer import validate_constant_name
        valid, err = validate_constant_name(constant, self._project_root)
        if valid:
            self._name_status.setText("Name is available")
            self._name_status.setStyleSheet("color: #6a6; font-size: 10px;")
        elif "already exists" in err:
            # Existing song — reimport is allowed (will overwrite)
            self._name_status.setText(f"{constant} exists — will reimport/overwrite")
            self._name_status.setStyleSheet("color: #c90; font-size: 10px;")
        else:
            self._name_status.setText(err)
            self._name_status.setStyleSheet("color: #c66; font-size: 10px;")

    # ═══════════════════════════════════════════════════════════════════════
    # Voicegroup page population
    # ═══════════════════════════════════════════════════════════════════════

    def _populate_voicegroup_page(self):
        """Fill the voicegroup page with data from the parsed song."""
        song = self._parsed_song
        if not song:
            return

        source_vg = song.voicegroup or "(none)"
        self._src_vg_label.setText(source_vg)

        # Pre-fill reverb/volume/priority from the parsed song
        self._reverb_spin.setValue(song.reverb)
        self._vol_spin.setValue(song.master_volume)
        self._priority_spin.setValue(song.priority)

        # Populate voicegroup dropdown
        self._vg_combo.clear()
        for name in self._voicegroup_names:
            self._vg_combo.addItem(name)

        # Check if source voicegroup exists in our project
        vg_exists = False
        if song.voicegroup and self._vg_data:
            vg_exists = song.voicegroup in self._vg_data.voicegroups

        if vg_exists:
            # Pre-select the matching voicegroup
            for i in range(self._vg_combo.count()):
                if self._vg_combo.itemText(i).startswith(song.voicegroup):
                    self._vg_combo.setCurrentIndex(i)
                    break
            self._vg_status_label.setText(
                f"This voicegroup exists in your project. "
                f"The song should sound as intended.")
            self._vg_status_label.setStyleSheet("color: #6a6;")
        elif song.voicegroup:
            self._vg_status_label.setText(
                f"This voicegroup does NOT exist in your project.\n"
                f"Pick the closest match below — instruments may sound different.\n"
                f"You can also create a matching voicegroup in the Voicegroups tab later.")
            self._vg_status_label.setStyleSheet("color: #c90;")
        else:
            self._vg_status_label.setText(
                "No voicegroup reference found in the .s file.\n"
                "Pick a voicegroup for this song to use.")
            self._vg_status_label.setStyleSheet("color: #c90;")

    # ═══════════════════════════════════════════════════════════════════════
    # Import execution
    # ═══════════════════════════════════════════════════════════════════════

    def _start_import(self):
        song = self._parsed_song
        constant = self._derive_constant(self._name_edit.text())
        label = constant.lower()
        music_player = self._player_combo.currentIndex()

        # Get target voicegroup name from combo
        vg_text = self._vg_combo.currentText()
        m = re.match(r'(voicegroup\d+)', vg_text)
        target_vg = m.group(1) if m else ""

        source_vg = song.voicegroup or ""

        # Check if file and/or constant already exist (reimport scenario)
        dest_path = os.path.join(
            self._project_root, "sound", "songs", "midi", label + ".s")
        file_exists = os.path.isfile(dest_path)

        from core.sound.midi_importer import validate_constant_name
        _, const_err = validate_constant_name(constant, self._project_root)
        constant_exists = "already exists" in const_err

        overwrite = False
        skip_registration = False

        if file_exists or constant_exists:
            # Ask the user before overwriting
            parts = []
            if file_exists:
                parts.append(f"The file {label}.s already exists on disk.")
            if constant_exists:
                parts.append(f"The constant {constant} is already registered.")
            parts.append("\nDo you want to overwrite and reimport?")
            ans = QMessageBox.question(
                self, "Song Already Exists",
                "\n".join(parts),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
            skip_registration = constant_exists

        # Switch to progress page
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._update_step_label()
        self._btn_next.setEnabled(False)
        self._btn_back.setVisible(False)
        self._btn_cancel.setEnabled(False)
        self._progress_label.setText("Copying song file and registering...")
        self._progress_label.setStyleSheet("")
        self._result_text.setVisible(False)
        self._progress_bar.setRange(0, 0)

        self._worker = _SImportWorker(
            self._project_root,
            self._source_path,
            constant,
            label,
            music_player,
            target_vg,
            source_vg,
            self._reverb_spin.value(),
            self._vol_spin.value(),
            self._priority_spin.value(),
            voice_remap=self._build_voice_remap(),
            overwrite=overwrite,
            skip_registration=skip_registration,
        )
        self._worker.finished.connect(self._on_import_done)
        self._worker.start()

    def _on_import_done(self, success: bool, s_path: str, error: str):
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._btn_cancel.setEnabled(True)

        if success:
            constant = self._derive_constant(self._name_edit.text())
            song = self._parsed_song

            vg_text = self._vg_combo.currentText()
            vg_changed = (song.voicegroup and
                          not vg_text.startswith(song.voicegroup or ""))
            vg_note = ""
            if vg_changed:
                vg_note = (f"\nVoicegroup remapped: {song.voicegroup} → "
                           f"{vg_text.split(' ')[0]}\n")

            self._progress_label.setText("Import successful!")
            self._progress_label.setStyleSheet("color: #6a6; font-size: 12px;")

            self._result_text.setVisible(True)
            self._result_text.setText(
                f"Song imported successfully.\n\n"
                f"Constant: {constant}\n"
                f"Label: {constant.lower()}\n"
                f"File: {os.path.basename(s_path)}\n"
                f"{vg_note}\n"
                f"The song has been added to:\n"
                f"  - include/constants/songs.h\n"
                f"  - sound/song_table.inc\n"
                f"  - sound/songs/midi/midi.cfg\n\n"
                f"Build the project (Make) to verify it compiles."
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
            self._progress_label.setStyleSheet("color: #c66; font-size: 12px;")

            self._result_text.setVisible(True)
            self._result_text.setText(f"Error:\n{error}")

            self._btn_next.setText("Done")
            self._btn_next.setEnabled(True)
            try:
                self._btn_next.clicked.disconnect()
            except TypeError:
                pass
            self._btn_next.clicked.connect(self.reject)

        _log.info("S import result: success=%s, path=%s, error=%s",
                  success, s_path, error)
