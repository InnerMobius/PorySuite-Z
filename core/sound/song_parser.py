"""
Parser for GBA M4A song .s (assembly) files.

Reads the mid2agb-generated .s files in sound/songs/midi/ and extracts
structured data: song metadata, per-track command sequences, and loop points.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from core.sound.sound_constants import (
    WAIT_TICKS, NOTE_TICKS, NOTE_NAMES, COMMAND_NAMES,
    POINTER_COMMANDS, REVERB_SET, MXV, C_V,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

STRUCTURE_CMDS = frozenset(('LABEL', 'GOTO', 'PATT', 'PEND', 'FINE', 'WAIT'))
NOTE_CMDS = frozenset(('NOTE', 'TIE', 'EOT'))


def is_value_command(cmd: str) -> bool:
    """True for a command whose VALUE an editor can change.

    Everything else is either the song's STRUCTURE (labels, jumps, and WAIT —
    the song's clock, which no editor edits and every length of which would
    look alike when matching) or a NOTE, which comes from the editor's canvas
    rather than from the file.
    """
    return cmd not in STRUCTURE_CMDS and cmd not in NOTE_CMDS


@dataclass
class TrackCommand:
    """A single command in a track's bytecode sequence."""
    cmd: str                     # e.g. 'NOTE', 'WAIT', 'VOICE', 'GOTO', 'FINE', ...
    tick: int = 0                # absolute tick position in the track
    duration: int = 0            # for notes/waits: tick count
    pitch: Optional[int] = None  # MIDI note number (0-127) for NOTE/TIE
    velocity: Optional[int] = None
    gate_time: Optional[int] = None
    value: Optional[int] = None  # generic integer arg (VOICE number, VOL, etc.)
    target_label: Optional[str] = None  # for GOTO/PATT: the label reference
    raw_line: str = ''           # original source line for round-trip fidelity
    parsed_value: Optional[int] = None  # value captured at parse time (see __post_init__)
    sub: Optional[str] = None    # XCMD sub-command ('xIECV' / 'xIECL') or MEMACC operation ('mem_set', ...)
    arg1: Optional[int] = None   # MEMACC's first operand (the memory slot); value is the second
    # Which command this IS: (track index, ordinal among that track's value
    # commands). Two reads of the same music give every command the same
    # origin, and a command an editor creates has none — which is how a save
    # tells exactly what an editor changed (safe_song_save.apply_command_edits).
    #
    # It deliberately does NOT say where in the file the command sat. It used
    # to — (line number, position on the line) — and that made a command's
    # identity depend on the file's layout, so anything that moved lines threw
    # an open editor's save away. See docs/BUGS.md → "Piano Roll: a save is
    # refused as 'changed on disk'".
    #
    # None for notes and structure: an editor's notes come from its canvas,
    # not from the file, so they are never matched up this way.
    origin: Optional[tuple] = None

    def __post_init__(self):
        # Snapshot the parse-time value. If the UI later edits `value`, this
        # stays put — letting the writer emit the EXACT original `raw_line` for
        # an UNCHANGED editable control (so VOL/PAN/etc. don't drift on a no-op
        # save) and regenerate only when the value actually changed.
        if self.parsed_value is None:
            self.parsed_value = self.value


@dataclass
class Track:
    """One track within a song."""
    index: int                   # 0-based track number
    label: str                   # e.g. 'mus_cycling_1'
    midi_channel: Optional[int] = None  # from comment "Midi-Chn.N"
    commands: list[TrackCommand] = field(default_factory=list)
    loop_label: Optional[str] = None    # GOTO target label (loop start)
    loop_tick: Optional[int] = None     # tick position of the loop start
    # Bar comments mid2agb writes ("@ 005   ----"), as (bar number, index of
    # the next command). They record where the source MIDI's bars fell, which
    # decides how mid2agb splits waits; the MIDI exporter uses them to write
    # the time signature back.
    bar_marks: list = field(default_factory=list)


@dataclass
class SongData:
    """Complete parsed representation of a song .s file."""
    label: str                  # e.g. 'mus_cycling'
    file_path: str              # full path to the .s file
    voicegroup: str = ''        # e.g. 'voicegroup141'
    priority: int = 0
    reverb: int = 0             # raw value (after reverb_set addition)
    master_volume: int = 127
    key_shift: int = 0
    tempo_base: int = 1         # tbs (always 1 in practice)
    num_tracks: int = 0
    tracks: list[Track] = field(default_factory=list)

    # Raw .equ values for round-trip writing
    _raw_equs: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Expression evaluator for simple assembler math
# ---------------------------------------------------------------------------

def _eval_expr(expr: str, equs: dict[str, int]) -> int:
    """Evaluate a simple assembler expression like '134*mus_cycling_tbs/2'."""
    # Strip whitespace
    expr = expr.strip()

    # Direct integer
    if expr.lstrip('-').isdigit():
        return int(expr)

    # Hex literal
    if expr.startswith('0x') or expr.startswith('0X'):
        return int(expr, 16)

    # Known constant substitution
    subs = {
        'reverb_set': str(REVERB_SET),
        'mxv': str(MXV),
        'c_v': str(C_V),
    }
    subs.update({k: str(v) for k, v in equs.items()})

    resolved = expr
    # Sort by length descending so longer names match first
    for name in sorted(subs, key=len, reverse=True):
        resolved = resolved.replace(name, subs[name])

    # Now evaluate the arithmetic (only +, -, *, / are used)
    try:
        # Safety: only allow digits, operators, parens, spaces
        if re.match(r'^[\d\s+\-*/()]+$', resolved):
            return int(eval(resolved))  # noqa: S307
    except Exception:
        pass

    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_RE_EQU = re.compile(r'\.equ\s+(\w+)\s*,\s*(.+)')
_RE_LABEL = re.compile(r'^(\w+):')
_RE_TRACK_COMMENT = re.compile(r'Track\s+(\d+)\s+\(Midi-Chn\.(\d+)\)')
_RE_BAR_COMMENT = re.compile(r'^@\s*(\d{3,})\s+-{4,}')

# Commands the M4A engine (and mid2agb's PrintOp) allow to be repeated by
# "running status": a later line may give just the value(s). TEMPO, KEYSH,
# PRIO and the flow commands are never repeated this way (mid2agb prints them
# with PrintByte, which leaves the running command alone).
_RUNNING_VALUE_CMDS = frozenset(
    ('VOICE', 'VOL', 'PAN', 'BEND', 'BENDR', 'LFOS', 'LFODL', 'MOD', 'MODT', 'TUNE'))
_RE_BYTE = re.compile(r'\.byte\s+(.*)')
_RE_WORD = re.compile(r'\.word\s+(\w+)')
_RE_GLOBAL = re.compile(r'\.global\s+(\w+)')
_RE_FOOTER_BYTE = re.compile(r'\.byte\s+(\d+)\s+@\s*NumTrks')


def extract_tie_notes(track) -> list[dict]:
    """Extract note dicts from TIE/EOT pairs WITHOUT modifying the track.

    mid2agb uses TIE to start a sustained note and EOT to end it.  This
    function reads TIE/EOT pairs and returns them as note dicts compatible
    with the piano roll's note format, leaving track.commands untouched
    so the song writer can round-trip the original assembly faithfully.

    Which EOT ends which TIE is decided by ``core.sound.tie_pairing``, the
    one rule the whole program uses -- see its docstring. It used to be
    decided here, differently from the save check and from the MIDI
    exporter, and a song whose readings disagreed could not be saved at all.

    Returns list of dicts with keys: tick, pitch, duration, velocity.
    (Caller must add the 'track' key.)
    """
    from core.sound.tie_pairing import tie_notes
    return tie_notes(track.commands)


def parse_song_file(filepath: str) -> SongData:
    """Parse a single .s song file into a SongData structure."""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    song_label = ''
    equs: dict[str, int] = {}
    raw_equs: dict[str, str] = {}

    # Pre-load constants from MPlayDef.s that songs reference
    equs['c_v'] = C_V
    equs['mxv'] = MXV
    # XCMD sub-command IDs (from MPlayDef.s)
    equs['xIECV'] = 0x08
    equs['xIECL'] = 0x09

    # --- Pass 1: extract .equ constants and song label ---
    for line in lines:
        stripped = line.strip()
        m = _RE_EQU.match(stripped)
        if m:
            name, val_expr = m.group(1), m.group(2).split('@')[0].strip()
            raw_equs[name] = val_expr
            equs[name] = _eval_expr(val_expr, equs)

        m = _RE_GLOBAL.match(stripped)
        if m:
            song_label = m.group(1)

    # Extract metadata from .equ names.
    # Convention: <song_label>_grp, _pri, _rev, _mvl, _key, _tbs — all sharing
    # ONE prefix, usually the song label. But an imported/renamed .s can keep
    # the source's internal prefix (e.g. public label `se_confirm` with
    # `se_sfx_minish_106_*` equates). If the label-derived prefix has no _grp
    # equate, fall back to the prefix the equates actually use, so the
    # voicegroup + properties still resolve instead of showing blank.
    prefix = song_label + '_' if song_label else ''
    if not prefix or (prefix + 'grp') not in raw_equs:
        for name in raw_equs:
            if name.endswith('_grp'):
                prefix = name[:-3]  # drop 'grp', keep the trailing '_'
                break

    song = SongData(
        label=song_label,
        file_path=filepath,
        _raw_equs=raw_equs,
    )

    if prefix:
        grp_key = prefix + 'grp'
        if grp_key in raw_equs:
            song.voicegroup = raw_equs[grp_key].strip()
        song.priority = equs.get(prefix + 'pri', 0)
        # Reverb: stored as reverb_set+N, we want just N
        rev_raw = raw_equs.get(prefix + 'rev', '')
        if 'reverb_set+' in rev_raw:
            try:
                song.reverb = int(rev_raw.split('+')[1])
            except (ValueError, IndexError):
                song.reverb = equs.get(prefix + 'rev', 0) - REVERB_SET
        else:
            song.reverb = equs.get(prefix + 'rev', 0)
        song.master_volume = equs.get(prefix + 'mvl', 127)
        song.key_shift = equs.get(prefix + 'key', 0)
        song.tempo_base = equs.get(prefix + 'tbs', 1)

    # Track labels share the equate stem (resolved above), which can differ from
    # the public .global label on an imported/renamed .s (e.g. .global se_confirm
    # but tracks se_sfx_minish_106_1). Match on the stem, not song_label, or no
    # tracks parse and the song shows "Tracks: 0" with an empty track list.
    track_stem = prefix.rstrip('_') if prefix else song_label

    # --- Pass 2: parse tracks ---
    tracks: list[Track] = []
    current_track: Optional[Track] = None
    current_tick = 0
    pending_note_cmd: Optional[str] = None  # last Nxx seen (sticky duration)
    pending_pitch: Optional[int] = None     # last pitch seen (sticky)
    pending_velocity: Optional[int] = None  # last velocity seen (sticky)
    last_control_cmd: Optional[str] = None  # last VOL/MOD/PAN/etc. for running status
    # The command a bare value / bare pitch line repeats ("running status"):
    # 'NOTE', 'TIE', 'EOT' or one of _RUNNING_VALUE_CMDS. A bare pitch after
    # an EOT is another EOT, after a TIE another TIE — not a new note.
    run_op: Optional[str] = None
    in_footer = False
    awaiting_pointer_for: Optional[str] = None  # 'GOTO' or 'PATT' waiting for .word

    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        # Remove comments
        code = stripped.split('@')[0].strip()
        comment = stripped[stripped.index('@'):] if '@' in stripped else ''

        if not code:
            # Check for track header comment
            m = _RE_TRACK_COMMENT.search(comment)
            if m:
                # Starting a new track section
                pass
            mb = _RE_BAR_COMMENT.match(comment)
            if mb and current_track is not None and not in_footer:
                current_track.bar_marks.append(
                    (int(mb.group(1)), len(current_track.commands)))
            continue

        # --- Footer detection: the song header at the bottom ---
        if code.startswith('.align') or code == '.end':
            continue

        # Check for the song footer label
        m = _RE_LABEL.match(code)
        if m:
            label_name = m.group(1)

            # Is this the main song label (footer)?
            if label_name == song_label and current_track is not None:
                tracks.append(current_track)
                current_track = None
                in_footer = True
                continue

            # Is this a track label?
            if current_track is None or label_name != current_track.label:
                # Check if this is starting a new track (matches <song>_N pattern)
                track_match = re.match(
                    re.escape(track_stem) + r'_(\d+)$', label_name
                ) if track_stem else None

                if track_match:
                    # Save previous track
                    if current_track is not None:
                        tracks.append(current_track)

                    track_idx = int(track_match.group(1)) - 1  # 1-based -> 0-based
                    # Look back for midi channel comment
                    midi_ch = None
                    for back in range(max(0, line_idx - 3), line_idx):
                        mc = _RE_TRACK_COMMENT.search(lines[back])
                        if mc:
                            midi_ch = int(mc.group(2))
                            break

                    current_track = Track(
                        index=track_idx,
                        label=label_name,
                        midi_channel=midi_ch,
                    )
                    current_tick = 0
                    pending_note_cmd = None
                    pending_pitch = None
                    pending_velocity = None
                    last_control_cmd = None
                    run_op = None
                    awaiting_pointer_for = None
                    continue

            # Any other label within a track is a potential loop/pattern target
            if current_track is not None and not in_footer:
                # Record the label with its tick position for loop detection
                current_track.commands.append(TrackCommand(
                    cmd='LABEL',
                    tick=current_tick,
                    target_label=label_name,
                    raw_line=stripped,
                ))
            continue

        if in_footer:
            # Parse footer for track count — need to check raw line since
            # the @ NumTrks comment is stripped from 'code'
            m = _RE_FOOTER_BYTE.search(stripped)
            if m:
                song.num_tracks = int(m.group(1))
            continue

        if current_track is None:
            continue

        # --- Handle .word lines (pointer args for GOTO/PATT) ---
        if awaiting_pointer_for:
            m = _RE_WORD.match(code)
            if m:
                target = m.group(1)
                # Find the command we're attaching this to
                if current_track.commands:
                    current_track.commands[-1].target_label = target

                # Track loop point
                if awaiting_pointer_for == 'GOTO':
                    current_track.loop_label = target
                    # Find the tick of the target label
                    for cmd in current_track.commands:
                        if cmd.cmd == 'LABEL' and cmd.target_label == target:
                            current_track.loop_tick = cmd.tick
                            break

                awaiting_pointer_for = None
            continue

        # --- Parse .byte lines ---
        m = _RE_BYTE.match(code)
        if not m:
            continue

        # The .byte line can have multiple comma-separated tokens
        byte_content = m.group(1)
        tokens = [t.strip() for t in byte_content.split(',')]

        i = 0
        while i < len(tokens):
            token = tokens[i]

            # --- Wait command (W00-W96) ---
            if token in WAIT_TICKS:
                ticks = WAIT_TICKS[token]
                current_track.commands.append(TrackCommand(
                    cmd='WAIT', tick=current_tick, duration=ticks,
                    raw_line=stripped,
                ))
                current_tick += ticks
                i += 1
                continue

            # --- Note command (N01-N96) ---
            if token in NOTE_TICKS:
                pending_note_cmd = token
                duration = NOTE_TICKS[token]

                # Next tokens may be pitch and velocity
                pitch = pending_pitch
                velocity = pending_velocity
                gate = None

                if i + 1 < len(tokens) and tokens[i + 1] in NOTE_NAMES:
                    pitch = NOTE_NAMES[tokens[i + 1]]
                    pending_pitch = pitch
                    i += 1

                    if i + 1 < len(tokens):
                        next_t = tokens[i + 1]
                        if next_t.startswith('v') and next_t[1:].isdigit():
                            velocity = int(next_t[1:])
                            pending_velocity = velocity
                            i += 1

                            if i + 1 < len(tokens):
                                gt = tokens[i + 1]
                                if gt in ('gtp1', 'gtp2', 'gtp3'):
                                    gate = int(gt[-1])
                                    i += 1

                current_track.commands.append(TrackCommand(
                    cmd='NOTE', tick=current_tick, duration=duration,
                    pitch=pitch, velocity=velocity, gate_time=gate,
                    raw_line=stripped,
                ))
                run_op = 'NOTE'
                i += 1
                continue

            # --- Named control commands ---
            if token in COMMAND_NAMES:
                cmd_name = COMMAND_NAMES[token]

                if cmd_name in POINTER_COMMANDS:
                    # GOTO / PATT — pointer follows on next .word line
                    current_track.commands.append(TrackCommand(
                        cmd=cmd_name, tick=current_tick,
                        raw_line=stripped,
                    ))
                    awaiting_pointer_for = cmd_name
                    i += 1
                    continue

                if cmd_name == 'FINE':
                    current_track.commands.append(TrackCommand(
                        cmd='FINE', tick=current_tick,
                        raw_line=stripped,
                    ))
                    i += 1
                    continue

                if cmd_name == 'PEND':
                    current_track.commands.append(TrackCommand(
                        cmd='PEND', tick=current_tick,
                        raw_line=stripped,
                    ))
                    i += 1
                    continue

                if cmd_name == 'EOT':
                    # May have optional pitch arg
                    pitch = None
                    if i + 1 < len(tokens) and tokens[i + 1] in NOTE_NAMES:
                        pitch = NOTE_NAMES[tokens[i + 1]]
                        i += 1
                    current_track.commands.append(TrackCommand(
                        cmd='EOT', tick=current_tick, pitch=pitch,
                        raw_line=stripped,
                    ))
                    if pitch is not None:
                        pending_pitch = pitch
                    run_op = 'EOT'
                    i += 1
                    continue

                if cmd_name == 'TIE':
                    # TIE [pitch] [velocity]
                    pitch = pending_pitch
                    velocity = pending_velocity
                    if i + 1 < len(tokens) and tokens[i + 1] in NOTE_NAMES:
                        pitch = NOTE_NAMES[tokens[i + 1]]
                        pending_pitch = pitch
                        i += 1
                        if i + 1 < len(tokens):
                            next_t = tokens[i + 1]
                            if next_t.startswith('v') and next_t[1:].isdigit():
                                velocity = int(next_t[1:])
                                pending_velocity = velocity
                                i += 1
                    current_track.commands.append(TrackCommand(
                        cmd='TIE', tick=current_tick,
                        pitch=pitch, velocity=velocity,
                        raw_line=stripped,
                    ))
                    run_op = 'TIE'
                    i += 1
                    continue

                # XCMD carries a sub-command and ITS value:
                #   .byte  XCMD , xIECV , 8
                # Record both. The old code evaluated the sub-command name as
                # the value and threw the real value away, so the echo setting
                # could never be written back out.
                if cmd_name == 'XCMD':
                    sub = tokens[i + 1] if i + 1 < len(tokens) else None
                    xval = None
                    if i + 2 < len(tokens):
                        try:
                            xval = _eval_expr(tokens[i + 2], equs)
                        except Exception:
                            xval = None
                    current_track.commands.append(TrackCommand(
                        cmd='XCMD', tick=current_tick, value=xval, sub=sub,
                        raw_line=stripped,
                    ))
                    run_op = 'XCMD'
                    i += 3
                    continue

                # MEMACC has an operation and two operands:
                #   .byte  MEMACC, mem_set, 0x00, 117
                # It used to be read as a one-value command, and the two
                # operands then fell through as "running status" values of the
                # previous command (two phantom PAN events, in mus_rs_vs_trainer).
                if cmd_name == 'MEMACC':
                    def _num(k):
                        try:
                            return _eval_expr(tokens[k], equs) if k < len(tokens) else None
                        except Exception:
                            return None
                    current_track.commands.append(TrackCommand(
                        cmd='MEMACC', tick=current_tick,
                        sub=tokens[i + 1] if i + 1 < len(tokens) else None,
                        arg1=_num(i + 2), value=_num(i + 3),
                        raw_line=stripped,
                    ))
                    i += 4
                    continue

                # Commands with a single value argument
                value = None
                if i + 1 < len(tokens):
                    val_token = tokens[i + 1]
                    value = _eval_expr(val_token, equs)
                    i += 1

                # Track the last control command for running status
                if cmd_name in ('VOL', 'MOD', 'PAN', 'BEND', 'BENDR', 'KEYSH', 'TEMPO'):
                    last_control_cmd = cmd_name
                if cmd_name in _RUNNING_VALUE_CMDS:
                    run_op = cmd_name

                current_track.commands.append(TrackCommand(
                    cmd=cmd_name, tick=current_tick, value=value,
                    raw_line=stripped,
                ))
                i += 1
                continue

            # --- Pitch-only continuation (running status) ---
            # A bare pitch repeats the last note-type command: after an EOT it
            # is another EOT, after a TIE another TIE, otherwise a note with
            # the last Nxx duration. (mid2agb's PrintOp/PrintEndOfTieOp.)
            if token in NOTE_NAMES:
                pitch = NOTE_NAMES[token]
                pending_pitch = pitch

                if run_op == 'EOT':
                    current_track.commands.append(TrackCommand(
                        cmd='EOT', tick=current_tick, pitch=pitch,
                        raw_line=stripped,
                    ))
                    i += 1
                    continue

                velocity = pending_velocity
                gate = None
                if i + 1 < len(tokens):
                    next_t = tokens[i + 1]
                    if next_t.startswith('v') and next_t[1:].isdigit():
                        velocity = int(next_t[1:])
                        pending_velocity = velocity
                        i += 1
                        if i + 1 < len(tokens) and tokens[i + 1] in ('gtp1', 'gtp2', 'gtp3'):
                            gate = int(tokens[i + 1][-1])
                            i += 1

                if run_op == 'TIE':
                    current_track.commands.append(TrackCommand(
                        cmd='TIE', tick=current_tick, pitch=pitch, velocity=velocity,
                        raw_line=stripped,
                    ))
                    i += 1
                    continue

                duration = NOTE_TICKS.get(pending_note_cmd, 0) if pending_note_cmd else 0
                current_track.commands.append(TrackCommand(
                    cmd='NOTE', tick=current_tick, duration=duration,
                    pitch=pitch, velocity=velocity, gate_time=gate,
                    raw_line=stripped,
                ))
                run_op = 'NOTE'
                i += 1
                continue

            # --- Bare velocity (v000-v127) — running-status NOTE ---
            # In GBA M4A bytecode, a bare velocity token after a previous
            # note means: "play a new note with the previous pitch AND
            # the previous duration, but with this new velocity." mid2agb
            # emits this compact form for descending-velocity sequences
            # (e.g. an echoing/decaying same-pitch note tail).
            #
            # Without emitting a NOTE here, the only thing happening is a
            # silent update of `pending_velocity`, and the run of repeated
            # notes vanishes from the parsed song — a "missing notes" bug
            # for any imported MIDI with sustained-decay tails (Sun Song,
            # flute notes, drone instruments).
            if token.startswith('v') and token[1:].isdigit():
                velocity = int(token[1:])
                pending_velocity = velocity
                # Only emit a NOTE if there's a sticky pitch and duration
                # to inherit. Without those, this is a setup token, not
                # a running-status note (rare; happens at file head).
                if pending_pitch is not None and pending_note_cmd is not None:
                    duration = NOTE_TICKS.get(pending_note_cmd, 0)
                    current_track.commands.append(TrackCommand(
                        cmd='NOTE', tick=current_tick,
                        duration=duration,
                        pitch=pending_pitch, velocity=velocity,
                        raw_line=stripped,
                    ))
                i += 1
                continue

            # --- XCMD sub-command continuation lines ---
            # XCMD instructions can span multiple .byte lines:
            #   .byte  XCMD, xIECV, 8    @ first line (handled above)
            #   .byte        xIECL, 8    @ continuation line
            # The sub-command names (xIECV, xIECL, etc.) must be skipped
            # along with their argument, NOT treated as running status.
            _XCMD_SUBCMDS = {'xIECV', 'xIECL', 'xiecv', 'xiecl'}
            if token in _XCMD_SUBCMDS or token.lower() in _XCMD_SUBCMDS:
                # A continuation line is a second XCMD in its own right
                # (xIECL after xIECV). It used to be skipped, which dropped it
                # from the song entirely.
                xval = None
                if i + 1 < len(tokens):
                    try:
                        xval = _eval_expr(tokens[i + 1], equs)
                    except Exception:
                        xval = None
                current_track.commands.append(TrackCommand(
                    cmd='XCMD', tick=current_tick, value=xval, sub=token,
                    raw_line=stripped,
                ))
                i += 2
                continue

            # --- Bare integer (running status for VOL, MOD, etc.) ---
            # In GBA M4A bytecode, a bare value repeats the last control command.
            # e.g. ".byte  95*mus_gym_mvl/mxv" after a VOL command means VOL=95*mvl/mxv
            try:
                val = _eval_expr(token, equs)
                # Repeat the running command (VOICE, LFOS, TUNE, ... too — the
                # old list covered only VOL/MOD/PAN/BEND/BENDR, so a second
                # "VOICE , 3" written as a bare "3" was read as a volume).
                if run_op in _RUNNING_VALUE_CMDS:
                    cmd_type = run_op
                else:
                    cmd_type = last_control_cmd if last_control_cmd else 'VOL'
                current_track.commands.append(TrackCommand(
                    cmd=cmd_type, tick=current_tick, value=val,
                    raw_line=stripped,
                ))
            except Exception:
                pass
            i += 1

    # Don't forget the last track
    if current_track is not None and not in_footer:
        tracks.append(current_track)

    # --- Give every value command a durable identity ------------------------
    # Assigned here rather than while parsing, because it must not depend on
    # the file's layout. A build regenerating the .s from the .mid, or Song
    # Build Protection adding its one header line, moves every line in the
    # file without changing a note of the music; identifying commands by line
    # number meant an open editor could no longer match its own song to the
    # file and refused to save. Counting VALUE commands within each track
    # survives both, and still shifts — so the save still refuses — if the
    # music itself gained or lost a command.
    for t_idx, track in enumerate(tracks):
        n = 0
        for cmd in track.commands:
            if not is_value_command(cmd.cmd):
                cmd.origin = None
                continue
            cmd.origin = (t_idx, n)
            n += 1

    # --- Post-process: convert TIE/EOT pairs into NOTE commands ---
    song.tracks = tracks
    if not song.num_tracks:
        song.num_tracks = len(tracks)

    return song


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def get_song_tempo(song: SongData) -> int:
    """Extract the BPM from the first TEMPO command in the first track."""
    for track in song.tracks:
        for cmd in track.commands:
            if cmd.cmd == 'TEMPO' and cmd.value is not None:
                # TEMPO value in the file is BPM * tbs / 2, so real BPM = value * 2 / tbs
                tbs = song.tempo_base if song.tempo_base else 1
                return (cmd.value * 2) // tbs
    return 120  # default


def get_song_duration_ticks(song: SongData) -> int:
    """Get the total tick length of the song (up to the first GOTO or FINE)."""
    max_ticks = 0
    for track in song.tracks:
        for cmd in track.commands:
            if cmd.cmd in ('GOTO', 'FINE'):
                if cmd.tick > max_ticks:
                    max_ticks = cmd.tick
                break
        else:
            # No GOTO/FINE found — use last command tick
            if track.commands:
                last = track.commands[-1]
                end = last.tick + last.duration
                if end > max_ticks:
                    max_ticks = end
    return max_ticks


def get_loop_info(song: SongData) -> tuple[Optional[int], Optional[int]]:
    """Return (loop_start_tick, loop_end_tick) or (None, None) if no loop."""
    # Use track 1 as reference (all tracks should loop at the same point)
    if song.tracks:
        track = song.tracks[0]
        if track.loop_label and track.loop_tick is not None:
            # Find the GOTO command tick
            for cmd in track.commands:
                if cmd.cmd == 'GOTO':
                    return (track.loop_tick, cmd.tick)
    return (None, None)


def parse_all_songs(songs_dir: str) -> dict[str, SongData]:
    """Parse all .s files in a directory. Returns {label: SongData}."""
    results = {}
    if not os.path.isdir(songs_dir):
        return results
    for filename in sorted(os.listdir(songs_dir)):
        if filename.endswith('.s'):
            filepath = os.path.join(songs_dir, filename)
            try:
                song = parse_song_file(filepath)
                if song.label:
                    results[song.label] = song
            except Exception as e:
                import logging
                logging.getLogger("SoundEditor.SongParser").warning(
                    "failed to parse %s: %s", filename, e)
    return results
