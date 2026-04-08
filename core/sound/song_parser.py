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


@dataclass
class Track:
    """One track within a song."""
    index: int                   # 0-based track number
    label: str                   # e.g. 'mus_cycling_1'
    midi_channel: Optional[int] = None  # from comment "Midi-Chn.N"
    commands: list[TrackCommand] = field(default_factory=list)
    loop_label: Optional[str] = None    # GOTO target label (loop start)
    loop_tick: Optional[int] = None     # tick position of the loop start


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

    Returns list of dicts with keys: tick, pitch, duration, velocity.
    (Caller must add the 'track' key.)
    """
    cmds = track.commands
    notes = []
    used_eots: set[int] = set()

    for i, cmd in enumerate(cmds):
        if cmd.cmd != 'TIE' or cmd.pitch is None:
            continue

        tie_tick = cmd.tick
        tie_pitch = cmd.pitch
        tie_velocity = cmd.velocity

        # Search forward for the matching EOT
        eot_tick = None
        for j in range(i + 1, len(cmds)):
            if j in used_eots:
                continue
            other = cmds[j]
            if other.cmd == 'EOT':
                if other.pitch is None or other.pitch == tie_pitch:
                    eot_tick = other.tick
                    used_eots.add(j)
                    break
            if other.cmd == 'TIE' and other.pitch == tie_pitch:
                eot_tick = other.tick
                break
            if other.cmd in ('FINE', 'GOTO'):
                eot_tick = other.tick
                break

        if eot_tick is not None and eot_tick > tie_tick:
            duration = eot_tick - tie_tick
        else:
            duration = 96

        notes.append({
            'tick': tie_tick,
            'pitch': tie_pitch,
            'duration': duration,
            'velocity': tie_velocity if tie_velocity else 100,
        })

    return notes


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

    # Extract metadata from .equ names
    # Convention: <song_label>_grp, _pri, _rev, _mvl, _key, _tbs
    prefix = song_label + '_' if song_label else ''

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

    # --- Pass 2: parse tracks ---
    tracks: list[Track] = []
    current_track: Optional[Track] = None
    current_tick = 0
    pending_note_cmd: Optional[str] = None  # last Nxx seen (sticky duration)
    pending_pitch: Optional[int] = None     # last pitch seen (sticky)
    pending_velocity: Optional[int] = None  # last velocity seen (sticky)
    last_control_cmd: Optional[str] = None  # last VOL/MOD/PAN/etc. for running status
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
                    re.escape(song_label) + r'_(\d+)$', label_name
                ) if song_label else None

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
                    i += 1
                    continue

                # Commands with a single value argument
                value = None
                if i + 1 < len(tokens):
                    val_token = tokens[i + 1]
                    value = _eval_expr(val_token, equs)
                    i += 1

                # XCMD takes two extra bytes (sub-command + value);
                # consume the remaining token on this line if present
                if cmd_name == 'XCMD' and i + 1 < len(tokens):
                    i += 1  # skip the extra XCMD argument

                # Track the last control command for running status
                if cmd_name in ('VOL', 'MOD', 'PAN', 'BEND', 'BENDR', 'KEYSH', 'TEMPO'):
                    last_control_cmd = cmd_name

                current_track.commands.append(TrackCommand(
                    cmd=cmd_name, tick=current_tick, value=value,
                    raw_line=stripped,
                ))
                i += 1
                continue

            # --- Pitch-only continuation (sticky note) ---
            # When a bare pitch name appears, it reuses the last Nxx duration
            if token in NOTE_NAMES:
                pitch = NOTE_NAMES[token]
                pending_pitch = pitch
                velocity = pending_velocity

                if i + 1 < len(tokens):
                    next_t = tokens[i + 1]
                    if next_t.startswith('v') and next_t[1:].isdigit():
                        velocity = int(next_t[1:])
                        pending_velocity = velocity
                        i += 1

                duration = NOTE_TICKS.get(pending_note_cmd, 0) if pending_note_cmd else 0
                current_track.commands.append(TrackCommand(
                    cmd='NOTE', tick=current_tick, duration=duration,
                    pitch=pitch, velocity=velocity,
                    raw_line=stripped,
                ))
                i += 1
                continue

            # --- Bare velocity (v000-v127) ---
            if token.startswith('v') and token[1:].isdigit():
                pending_velocity = int(token[1:])
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
                # Skip the sub-command token and its value argument
                i += 1
                if i < len(tokens):
                    i += 1  # skip the value too
                continue

            # --- Bare integer (running status for VOL, MOD, etc.) ---
            # In GBA M4A bytecode, a bare value repeats the last control command.
            # e.g. ".byte  95*mus_gym_mvl/mxv" after a VOL command means VOL=95*mvl/mxv
            try:
                val = _eval_expr(token, equs)
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
                print(f"Warning: failed to parse {filename}: {e}")
    return results
