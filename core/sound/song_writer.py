"""Song writer — converts SongData back to .s assembly format.

Takes a SongData object (possibly edited in the piano roll) and writes
a valid GBA M4A song .s file that assembles correctly with mid2agb's
MPlayDef.s definitions.

The writer produces clean, readable output that matches the style of
mid2agb-generated files.
"""

from __future__ import annotations

from typing import Optional

from core.sound.song_parser import SongData, Track, TrackCommand
from core.sound.sound_constants import (
    NOTE_TICKS, WAIT_TICKS, MIDI_TO_NAME, C_V, MXV,
)


# ---------------------------------------------------------------------------
# Reverse lookups: tick count -> Nxx / Wxx name
# ---------------------------------------------------------------------------

_TICKS_TO_NOTE: dict[int, str] = {v: k for k, v in NOTE_TICKS.items()}
_TICKS_TO_WAIT: dict[int, str] = {v: k for k, v in WAIT_TICKS.items()}


def _best_note_name(ticks: int) -> str:
    """Get the Nxx constant for a duration, or the closest one."""
    if ticks in _TICKS_TO_NOTE:
        return _TICKS_TO_NOTE[ticks]
    # Find closest available duration
    available = sorted(NOTE_TICKS.values())
    best = min(available, key=lambda t: abs(t - ticks))
    return _TICKS_TO_NOTE[best]


def _emit_waits(ticks: int) -> list[str]:
    """Break a tick count into one or more Wxx commands.

    Uses the largest available W constants to minimize lines.
    Returns a list of assembly lines.
    """
    lines = []
    available = sorted(WAIT_TICKS.values(), reverse=True)
    remaining = ticks
    while remaining > 0:
        # Find the largest W that fits
        best = 1
        for w in available:
            if w <= remaining and w > 0:
                best = w
                break
        if best == 0:
            best = 1
        wname = _TICKS_TO_WAIT.get(best)
        if wname is None:
            # Shouldn't happen, but fall back to W01s
            lines.extend(['\t.byte\tW01'] * remaining)
            break
        lines.append(f'\t.byte\t{wname}')
        remaining -= best
    return lines


def _pitch_name(midi_note: int) -> str:
    """Convert MIDI note number to GBA pitch name (e.g. 60 -> Cn3)."""
    return MIDI_TO_NAME.get(midi_note, f'Cn3')


def _raw_vol(value: int, master_volume: int) -> int:
    """Reverse the VOL evaluation: value was parsed as raw*mvl/mxv.

    The parser evaluates 127*mvl/mxv → ~90. If we write 90*mvl/mxv,
    the assembler would get ~63 — wrong. We need to recover the original
    raw value (127) so the round-trip is lossless.
    """
    mvl = max(1, master_volume)
    return min(127, round(value * MXV / mvl))


def _raw_tempo(value: int, tempo_base: int) -> int:
    """Reverse the TEMPO evaluation: value was parsed as raw*tbs/2.

    The parser evaluates 50*1/2 → 25. If we write 25*tbs/2 the assembler
    gets 12. We recover the original raw value (50).
    """
    tbs = max(1, tempo_base or 1)
    return round(value * 2 / tbs)


# ---------------------------------------------------------------------------
# Track writer
# ---------------------------------------------------------------------------

def _write_track(
    track: Track,
    song: SongData,
    track_num: int,
) -> list[str]:
    """Convert a Track's commands to assembly lines.

    For piano-roll-edited songs, the commands list contains the raw
    (unflattened) commands. If the user only edited notes but didn't
    change the PATT/GOTO structure, those structural commands are
    preserved via raw_line.

    For newly composed tracks (all notes, no structure), we write
    a simple linear sequence: setup commands, then note+wait pairs.
    """
    label = song.label
    prefix = label
    lines = []

    # Track header comment
    ch_text = f'Midi-Chn.{track.midi_channel}' if track.midi_channel is not None else f'Trk.{track_num}'
    lines.append(f'@**************** Track {track_num} ({ch_text}) ****************@')
    lines.append('')
    lines.append(f'{track.label}:')

    # Check if this track has structural commands (PATT/GOTO/PEND/LABEL)
    has_structure = any(
        c.cmd in ('PATT', 'GOTO', 'PEND', 'LABEL')
        for c in track.commands
    )

    if has_structure and all(c.raw_line for c in track.commands if c.cmd not in ('NOTE', 'WAIT')):
        # Track has structural commands with raw lines — use raw_line round-trip
        # for maximum fidelity to the original file
        lines.extend(_write_track_raw(track, song))
    else:
        # Simple track — write linear note sequence
        lines.extend(_write_track_linear(track, song))

    lines.append('')
    return lines


def _write_track_raw(track: Track, song: SongData) -> list[str]:
    """Write a track using raw_line strings for structural fidelity.

    Falls back to generating commands for any command without a raw_line.
    """
    lines = []
    last_note_dur = None
    last_pitch = None
    last_velocity = None
    measure_tick = 0
    ticks_per_measure = 96  # 4 beats * 24 ticks

    for cmd in track.commands:
        if cmd.raw_line:
            lines.append(cmd.raw_line.rstrip())
            continue

        # Generate the command from parsed data
        if cmd.cmd == 'NOTE':
            lines.extend(_format_note(cmd, last_note_dur, last_pitch, last_velocity))
            last_note_dur = min(cmd.duration or 24, 96)
            last_pitch = cmd.pitch
            last_velocity = cmd.velocity
        elif cmd.cmd == 'TIE':
            lines.extend(_format_tie(cmd, last_pitch, last_velocity))
            last_pitch = cmd.pitch
            if cmd.velocity is not None:
                last_velocity = cmd.velocity
        elif cmd.cmd == 'EOT':
            lines.extend(_format_eot(cmd))
        elif cmd.cmd == 'WAIT':
            lines.extend(_emit_waits(cmd.duration))
        elif cmd.cmd == 'LABEL':
            if cmd.target_label:
                lines.append(f'{cmd.target_label}:')
        elif cmd.cmd == 'VOICE':
            lines.append(f'\t.byte\tVOICE , {cmd.value}')
        elif cmd.cmd == 'VOL':
            raw_v = _raw_vol(cmd.value or 100, song.master_volume)
            lines.append(f'\t.byte\tVOL   , {raw_v}*{song.label}_mvl/mxv')
        elif cmd.cmd == 'PAN':
            pan_offset = (cmd.value or 64) - C_V
            lines.append(f'\t.byte\tPAN   , c_v{pan_offset:+d}')
        elif cmd.cmd == 'TEMPO':
            raw_t = _raw_tempo(cmd.value or 120, song.tempo_base)
            lines.append(f'\t.byte\tTEMPO , {raw_t}*{song.label}_tbs/2')
        elif cmd.cmd == 'MOD':
            lines.append(f'\t.byte\tMOD   , {cmd.value or 0}')
        elif cmd.cmd == 'BEND':
            bend_offset = (cmd.value or 64) - C_V
            lines.append(f'\t.byte\tBEND  , c_v{bend_offset:+d}')
        elif cmd.cmd == 'BENDR':
            lines.append(f'\t.byte\tBENDR , {cmd.value or 2}')
        elif cmd.cmd == 'LFOS':
            lines.append(f'\t.byte\tLFOS  , {cmd.value or 0}')
        elif cmd.cmd == 'KEYSH':
            lines.append(f'\t.byte\tKEYSH , {song.label}_key+0')
        elif cmd.cmd == 'FINE':
            lines.append('\t.byte\tFINE')
        elif cmd.cmd == 'GOTO':
            lines.append('\t.byte\tGOTO')
            lines.append(f'\t .word\t{cmd.target_label}')
        elif cmd.cmd == 'PATT':
            lines.append('\t.byte\tPATT')
            lines.append(f'\t .word\t{cmd.target_label}')
        elif cmd.cmd == 'PEND':
            lines.append('\t.byte\tPEND')

    return lines


def _write_track_linear(track: Track, song: SongData) -> list[str]:
    """Write a simple linear track: setup commands, then note+wait pairs.

    Used for newly composed tracks or tracks where the user edited notes
    and the original PATT/GOTO structure was lost.
    """
    lines = []
    prefix = song.label
    ticks_per_measure = 96  # 4/4 time, 24 ticks per beat
    current_tick = 0
    measure = 0
    last_note_dur = None
    last_pitch = None
    last_velocity = None

    # Track last emitted control values to filter redundant commands
    _last_control: dict[str, int] = {}

    # Separate setup commands from note/wait commands
    setup_cmds = []
    note_cmds = []
    loop_label = None
    has_goto = False

    for cmd in track.commands:
        if cmd.cmd in ('KEYSH', 'TEMPO', 'VOICE', 'VOL', 'PAN', 'MOD',
                        'BEND', 'BENDR', 'LFOS', 'MODT', 'TUNE'):
            if not note_cmds:
                setup_cmds.append(cmd)
            else:
                note_cmds.append(cmd)
        elif cmd.cmd in ('NOTE', 'WAIT', 'TIE', 'EOT'):
            note_cmds.append(cmd)
        elif cmd.cmd == 'LABEL':
            if cmd.target_label:
                loop_label = cmd.target_label
            note_cmds.append(cmd)
        elif cmd.cmd in ('GOTO', 'FINE', 'PATT', 'PEND'):
            note_cmds.append(cmd)
            if cmd.cmd == 'GOTO':
                has_goto = True

    # Write setup block
    lines.append(f'\t.byte\tKEYSH , {prefix}_key+0')

    for cmd in setup_cmds:
        if cmd.cmd == 'KEYSH':
            continue  # Already written
        if cmd.raw_line:
            lines.append(cmd.raw_line.rstrip())
        elif cmd.cmd == 'TEMPO':
            raw_t = _raw_tempo(cmd.value or 120, song.tempo_base)
            lines.append(f'\t.byte\tTEMPO , {raw_t}*{prefix}_tbs/2')
        elif cmd.cmd == 'VOICE':
            lines.append(f'\t.byte\tVOICE , {cmd.value}')
        elif cmd.cmd == 'VOL':
            raw_v = _raw_vol(cmd.value or 100, song.master_volume)
            lines.append(f'\t.byte\tVOL   , {raw_v}*{prefix}_mvl/mxv')
        elif cmd.cmd == 'PAN':
            pan_offset = (cmd.value or 64) - C_V
            lines.append(f'\t.byte\tPAN   , c_v{pan_offset:+d}')
        elif cmd.cmd == 'MOD':
            lines.append(f'\t.byte\tMOD   , {cmd.value or 0}')
        elif cmd.cmd == 'BEND':
            bend_offset = (cmd.value or 64) - C_V
            lines.append(f'\t.byte\tBEND  , c_v{bend_offset:+d}')
        elif cmd.cmd == 'BENDR':
            lines.append(f'\t.byte\tBENDR , {cmd.value or 2}')
        elif cmd.cmd == 'LFOS':
            lines.append(f'\t.byte\tLFOS  , {cmd.value or 0}')
        elif cmd.cmd == 'MODT':
            lines.append(f'\t.byte\tMODT  , {cmd.value or 0}')
        elif cmd.cmd == 'TUNE':
            tune_offset = (cmd.value or 64) - C_V
            lines.append(f'\t.byte\tTUNE  , c_v{tune_offset:+d}')
        # Track initial control values for redundancy filtering
        if cmd.value is not None:
            _last_control[cmd.cmd] = cmd.value

    # Write note/wait sequence
    for cmd in note_cmds:
        # Measure comments
        while current_tick >= (measure + 1) * ticks_per_measure:
            measure += 1
        if cmd.cmd in ('NOTE', 'WAIT', 'LABEL') and cmd.tick >= (measure + 1) * ticks_per_measure:
            measure = cmd.tick // ticks_per_measure
            lines.append(f'@ {measure:03d}   ----------------------------------------')

        if cmd.cmd == 'LABEL':
            if cmd.target_label:
                lines.append(f'{cmd.target_label}:')
        elif cmd.cmd == 'NOTE':
            note_lines = _format_note(cmd, last_note_dur, last_pitch, last_velocity)
            lines.extend(note_lines)
            last_note_dur = min(cmd.duration or 24, 96)
            last_pitch = cmd.pitch
            last_velocity = cmd.velocity
        elif cmd.cmd == 'TIE':
            lines.extend(_format_tie(cmd, last_pitch, last_velocity))
            last_pitch = cmd.pitch
            if cmd.velocity is not None:
                last_velocity = cmd.velocity
        elif cmd.cmd == 'EOT':
            lines.extend(_format_eot(cmd))
        elif cmd.cmd == 'WAIT':
            lines.extend(_emit_waits(cmd.duration))
            current_tick += cmd.duration
        elif cmd.cmd == 'GOTO':
            lines.append('\t.byte\tGOTO')
            lines.append(f'\t .word\t{cmd.target_label}')
        elif cmd.cmd == 'PATT':
            lines.append('\t.byte\tPATT')
            lines.append(f'\t .word\t{cmd.target_label}')
        elif cmd.cmd == 'PEND':
            lines.append('\t.byte\tPEND')
        elif cmd.cmd == 'FINE':
            lines.append('\t.byte\tFINE')
        elif cmd.cmd in ('VOICE', 'VOL', 'PAN', 'MOD', 'BEND', 'BENDR',
                          'LFOS', 'MODT', 'TUNE'):
            # Skip redundant control commands (same value as last emitted)
            if cmd.value is not None and _last_control.get(cmd.cmd) == cmd.value:
                continue
            if cmd.raw_line:
                lines.append(cmd.raw_line.rstrip())
            else:
                lines.extend(_format_control(cmd, song))
            if cmd.value is not None:
                _last_control[cmd.cmd] = cmd.value

    # Add FINE if not already there
    if not note_cmds or note_cmds[-1].cmd != 'FINE':
        if not has_goto:
            lines.append('\t.byte\tFINE')

    return lines


def _format_note(
    cmd: TrackCommand,
    last_dur: Optional[int],
    last_pitch: Optional[int],
    last_vel: Optional[int],
) -> list[str]:
    """Format a NOTE command with running-status optimization.

    Notes with duration > 96 ticks are handled separately via TIE/EOT
    in notes_to_track_commands — this function only handles Nxx notes.
    """
    parts = []

    # Clamp duration to 96 max (caller should use TIE for longer notes)
    duration = min(cmd.duration or 24, 96)

    # Duration (Nxx) — only emit if changed
    dur_name = _best_note_name(duration)
    if duration != last_dur:
        parts.append(dur_name)

    # Pitch — only emit if changed
    pitch_str = _pitch_name(cmd.pitch if cmd.pitch is not None else 60)
    if cmd.pitch != last_pitch or duration != last_dur:
        parts.append(pitch_str)

    # Velocity — only emit if changed
    if cmd.velocity is not None and cmd.velocity != last_vel:
        parts.append(f'v{cmd.velocity:03d}')

    # Gate time
    if cmd.gate_time:
        parts.append(f'gtp{cmd.gate_time}')

    if not parts:
        # Nothing changed — still need to emit something
        parts.append(dur_name)

    # Format: .byte  N12 , Cn3 , v127
    formatted = ' , '.join(parts)
    return [f'\t.byte\t{formatted}']


def _format_tie(
    cmd: TrackCommand,
    last_pitch: Optional[int],
    last_vel: Optional[int],
) -> list[str]:
    """Format a TIE command (sustain note indefinitely until EOT)."""
    parts = ['TIE']

    # TIE always includes pitch (need to know what to sustain)
    pitch_str = _pitch_name(cmd.pitch if cmd.pitch is not None else 60)
    parts.append(pitch_str)

    # Velocity — only include if provided and different
    if cmd.velocity is not None and cmd.velocity != last_vel:
        parts.append(f'v{cmd.velocity:03d}')

    formatted = ' , '.join(parts)
    return [f'\t.byte\t{formatted}']


def _format_eot(cmd: TrackCommand) -> list[str]:
    """Format an EOT (End of Tie) command."""
    if cmd.pitch is not None:
        pitch_str = _pitch_name(cmd.pitch)
        return [f'\t.byte\tEOT   , {pitch_str}']
    return ['\t.byte\tEOT']


def _format_control(cmd: TrackCommand, song: SongData) -> list[str]:
    """Format a control command (VOL, PAN, MOD, etc.)."""
    if cmd.cmd == 'VOICE':
        return [f'\t.byte\tVOICE , {cmd.value}']
    elif cmd.cmd == 'VOL':
        raw_v = _raw_vol(cmd.value or 100, song.master_volume)
        return [f'\t.byte\tVOL   , {raw_v}*{song.label}_mvl/mxv']
    elif cmd.cmd == 'PAN':
        pan_offset = (cmd.value or 64) - C_V
        return [f'\t.byte\tPAN   , c_v{pan_offset:+d}']
    elif cmd.cmd == 'MOD':
        return [f'\t.byte\tMOD   , {cmd.value or 0}']
    elif cmd.cmd == 'BEND':
        bend_offset = (cmd.value or 64) - C_V
        return [f'\t.byte\tBEND  , c_v{bend_offset:+d}']
    elif cmd.cmd == 'BENDR':
        return [f'\t.byte\tBENDR , {cmd.value or 2}']
    elif cmd.cmd == 'LFOS':
        return [f'\t.byte\tLFOS  , {cmd.value or 0}']
    elif cmd.cmd == 'MODT':
        return [f'\t.byte\tMODT  , {cmd.value or 0}']
    elif cmd.cmd == 'TUNE':
        tune_offset = (cmd.value or 64) - C_V
        return [f'\t.byte\tTUNE  , c_v{tune_offset:+d}']
    return [f'\t.byte\t{cmd.cmd} , {cmd.value or 0}']


# ---------------------------------------------------------------------------
# Notes-to-commands converter
# ---------------------------------------------------------------------------

# Commands that are "control" (not notes/waits/structure) — preserved from
# the original track when regenerating from piano roll notes.
_CONTROL_CMDS = {
    'VOICE', 'VOL', 'PAN', 'MOD', 'BEND', 'BENDR',
    'LFOS', 'LFODL', 'MODT', 'TUNE', 'TEMPO', 'KEYSH',
    'XCMD', 'PRIO',
}

# Structural commands that define song layout
_STRUCTURE_CMDS = {'GOTO', 'PATT', 'PEND', 'LABEL', 'FINE'}


def notes_to_track_commands(
    notes: list[dict],
    track_index: int,
    voice: int = 0,
    volume: int = 100,
    pan: int = 64,
    loop_start_tick: Optional[int] = None,
    loop_end_tick: Optional[int] = None,
    loop_label: Optional[str] = None,
    original_commands: Optional[list[TrackCommand]] = None,
) -> list[TrackCommand]:
    """Convert piano roll note dicts to a flat list of TrackCommands.

    This is used when saving piano roll edits back to a song. It takes
    the visual note representation and creates the command sequence that
    the writer expects.

    If loop_start_tick/loop_end_tick are provided, a LABEL is placed at
    the loop start and a GOTO at the loop end, creating a repeating loop.

    If original_commands is provided, mid-song control changes (VOL, PAN,
    MOD, BEND, TEMPO, etc.) are preserved at their original tick positions.
    Structural commands (PATT/GOTO/PEND/LABEL) from the original are also
    preserved.

    notes: list of dicts with keys: tick, pitch, duration, velocity, track
    """
    # Filter to just this track's notes and sort by tick
    track_notes = sorted(
        [n for n in notes if n.get('track', 0) == track_index],
        key=lambda n: n['tick'],
    )

    # Extract control commands from original track (preserves mid-song
    # VOL/PAN/MOD/BEND/TEMPO changes that the piano roll doesn't edit)
    control_events: list[TrackCommand] = []
    structure_events: list[TrackCommand] = []
    has_patt_structure = False  # True if original uses PATT/PEND subroutines
    if original_commands:
        # Deduplicate control events by (tick, cmd, value).
        # PATT flattening can produce N copies of the same event at the
        # same tick (one per subroutine call).  Without this dedup, a song
        # with 18 PAN commands in subroutines called 7x becomes 1800+ PANs
        # that flood the timeline and bloat the saved file.
        _seen_ctrl_exact: set[tuple[int, str, int]] = set()
        for cmd in original_commands:
            if cmd.cmd in _CONTROL_CMDS:
                if cmd.value is not None:
                    key = (cmd.tick, cmd.cmd, cmd.value)
                    if key in _seen_ctrl_exact:
                        continue  # duplicate from PATT expansion — skip
                    _seen_ctrl_exact.add(key)
                control_events.append(cmd)
            elif cmd.cmd in _STRUCTURE_CMDS:
                structure_events.append(cmd)
                if cmd.cmd in ('PATT', 'PEND'):
                    has_patt_structure = True

    # PATT/PEND subroutines CANNOT survive the flatten→edit→save round-trip.
    # The piano roll flattens all PATT calls into expanded linear notes.
    # Trying to mix those flattened notes back into the PATT structure
    # produces garbage (notes after FINE, broken subroutine boundaries).
    # When PATT/PEND exist, we MUST strip them and write a fully linear track.
    # Simple GOTO loops (no subroutines) are safe to preserve.
    if has_patt_structure:
        # Strip ALL structure — will be written as linear with FINE at end
        structure_events = [
            c for c in structure_events
            if c.cmd == 'GOTO'  # keep GOTO if present (rare with PATT)
        ]

    has_orig_structure = bool(structure_events) and not has_patt_structure

    commands: list[TrackCommand] = []
    current_tick = 0

    # Setup commands — VOICE, VOL, PAN always come from the caller's
    # parameters (which reflect the user's sidebar edits), NOT from the
    # original file's tick-0 commands. Other tick-0 controls (KEYSH,
    # TEMPO, MOD, etc.) are preserved from the original.
    tick0_controls = {c.cmd: c for c in control_events if c.tick == 0}
    commands.append(tick0_controls.pop('KEYSH', TrackCommand(
        cmd='KEYSH', tick=0, value=0)))
    if 'TEMPO' in tick0_controls:
        commands.append(tick0_controls.pop('TEMPO'))
    # Discard any original VOICE/VOL/PAN at tick 0 — use the caller's
    # values instead (these reflect the user's current sidebar settings)
    tick0_controls.pop('VOICE', None)
    tick0_controls.pop('VOL', None)
    tick0_controls.pop('PAN', None)
    commands.append(TrackCommand(cmd='VOICE', tick=0, value=voice))
    commands.append(TrackCommand(cmd='VOL', tick=0, value=volume))
    if pan != 64:
        commands.append(TrackCommand(cmd='PAN', tick=0, value=pan))
    if 'MOD' in tick0_controls:
        commands.append(tick0_controls.pop('MOD'))

    # Remaining tick-0 controls
    for cmd in tick0_controls.values():
        commands.append(cmd)

    # Build a timeline: merge notes with mid-song control changes
    # Control events after tick 0
    mid_controls = sorted(
        [c for c in control_events if c.tick > 0],
        key=lambda c: c.tick,
    )

    # If the original had PATT/GOTO structure, preserve those too
    if has_orig_structure:
        all_structure = sorted(structure_events, key=lambda c: c.tick)
    else:
        all_structure = []

    # Build the loop label name if needed
    if loop_start_tick is not None and loop_label is None:
        # Auto-generate a loop label if none provided
        loop_label = f'_loop_{track_index}'

    # Merge everything into a single timeline
    # Each item: (tick, priority, command_or_note)
    # Priority: 0=label, 1=control, 2=note, 3=structure(goto/fine)
    timeline = []

    # Add loop start label — but only if the original didn't already have
    # structural commands (PATT/GOTO/LABEL).  When the original structure
    # is preserved, its labels are already in all_structure and adding a
    # new one would create a duplicate label that breaks assembly.
    if loop_start_tick is not None and loop_label and not has_orig_structure:
        timeline.append((loop_start_tick, 0, TrackCommand(
            cmd='LABEL', tick=loop_start_tick,
            target_label=loop_label)))

    # Add mid-song controls
    for cmd in mid_controls:
        timeline.append((cmd.tick, 1, cmd))

    # Add notes — long notes (>96 ticks) are split into TIE + EOT so that
    # control events (PAN sweeps, BEND, etc.) interleave naturally between
    # them via the gap-based WAIT generation below.
    for note in track_notes:
        tick = note['tick']
        pitch = note.get('pitch', 60)
        duration = note.get('duration', 24)
        velocity = note.get('velocity', 100)

        if duration > 96:
            # Split into separate TIE and EOT timeline entries.
            # Control events between these ticks will be interleaved
            # with proper WAIT timing by the gap-based emit loop.
            timeline.append((tick, 2, TrackCommand(
                cmd='TIE', tick=tick, pitch=pitch, velocity=velocity)))
            timeline.append((tick + duration, 2, TrackCommand(
                cmd='EOT', tick=tick + duration, pitch=pitch)))
        else:
            timeline.append((tick, 2, TrackCommand(
                cmd='NOTE', tick=tick,
                duration=duration, pitch=pitch, velocity=velocity)))

    # Add structural commands from original
    for cmd in all_structure:
        if cmd.cmd == 'LABEL':
            timeline.append((cmd.tick, 0, cmd))
        elif cmd.cmd == 'FINE':
            timeline.append((cmd.tick, 4, cmd))
        elif cmd.cmd in ('GOTO', 'PATT', 'PEND'):
            timeline.append((cmd.tick, 3, cmd))

    # Add user-edited loop GOTO into the timeline at the correct tick.
    # This must be in the timeline (not appended after) so it sorts
    # before any notes/events past the loop end.  Without this, the
    # GOTO would be placed after all notes — at the wrong tick.
    if (loop_start_tick is not None and loop_end_tick is not None
            and loop_label and not has_orig_structure):
        timeline.append((loop_end_tick, 3, TrackCommand(
            cmd='GOTO', tick=loop_end_tick, target_label=loop_label)))

    # Sort by tick, then priority
    timeline.sort(key=lambda x: (x[0], x[1]))

    # Emit the timeline.
    #
    # Key design: WAITs are generated ONLY from tick gaps between timeline
    # events — notes do NOT auto-emit a WAIT for their duration. This lets
    # control events (PAN, BEND, etc.) that fall mid-note be placed at
    # their correct tick positions with proper WAITs around them.
    #
    # For a note at tick 0 (dur=48) followed by a note at tick 48:
    #   gap=48 → WAIT(48) before the second note.  Same result as before.
    #
    # For TIE at tick 0 with PAN@2, PAN@4, ..., EOT@100:
    #   TIE, W02, PAN, W02, PAN, ..., W02, EOT.  Properly interleaved.
    has_goto = False
    has_fine = False
    for tick, priority, cmd in timeline:
        # Emit WAIT to advance to this tick — including for LABELs.
        # The WAIT must come BEFORE the label so the parser sees the
        # correct tick position when it encounters the label line.
        # Without this, a label at tick 192 after an EOT at tick 190
        # would be placed at tick 190 on reload (W02 appeared after label).
        gap = tick - current_tick
        if gap > 0:
            commands.append(TrackCommand(
                cmd='WAIT', tick=current_tick, duration=gap))
            current_tick += gap

        if cmd.cmd == 'LABEL':
            commands.append(cmd)
            continue

        if cmd.cmd == 'NOTE':
            commands.append(cmd)
            # Don't auto-emit WAIT — the gap to the next event handles it
        elif cmd.cmd == 'TIE':
            commands.append(cmd)
            # Gaps to interleaved control events generate the WAITs
        elif cmd.cmd == 'EOT':
            commands.append(cmd)
        elif cmd.cmd in ('GOTO', 'PATT', 'PEND', 'FINE'):
            commands.append(cmd)
            if cmd.cmd == 'GOTO':
                has_goto = True
                break  # GOTO terminates the track — skip remaining events
            if cmd.cmd == 'FINE':
                has_fine = True
                break  # FINE terminates the track
        else:
            # Control command
            commands.append(cmd)

    # End marker if no GOTO or FINE yet
    if not has_goto and not has_fine:
        commands.append(TrackCommand(cmd='FINE', tick=current_tick))

    return commands


# ---------------------------------------------------------------------------
# Full song writer
# ---------------------------------------------------------------------------

def write_song(song: SongData) -> str:
    """Convert a SongData object to a complete .s file string."""
    prefix = song.label
    lines = []

    # Header
    lines.append('\t.include "MPlayDef.s"')
    lines.append('')

    # .equ constants — use raw_equs for round-trip fidelity where available
    raw = song._raw_equs

    lines.append(f'\t.equ\t{prefix}_grp, {song.voicegroup}')
    lines.append(f'\t.equ\t{prefix}_pri, {song.priority}')
    lines.append(f'\t.equ\t{prefix}_rev, reverb_set+{song.reverb}')
    lines.append(f'\t.equ\t{prefix}_mvl, {song.master_volume}')
    lines.append(f'\t.equ\t{prefix}_key, {song.key_shift}')
    lines.append(f'\t.equ\t{prefix}_tbs, {song.tempo_base or 1}')

    # Include exg/cmp if they were in the original
    if f'{prefix}_exg' in raw:
        lines.append(f'\t.equ\t{prefix}_exg, {raw[prefix + "_exg"]}')
    else:
        lines.append(f'\t.equ\t{prefix}_exg, 1')
    if f'{prefix}_cmp' in raw:
        lines.append(f'\t.equ\t{prefix}_cmp, {raw[prefix + "_cmp"]}')
    else:
        lines.append(f'\t.equ\t{prefix}_cmp, 1')

    lines.append('')
    lines.append('\t.section .rodata')
    lines.append(f'\t.global\t{prefix}')
    lines.append('\t.align\t2')
    lines.append('')

    # Track bodies
    for i, track in enumerate(song.tracks):
        track_lines = _write_track(track, song, i + 1)
        lines.extend(track_lines)

    # Footer: song metadata block
    lines.append('@******************************************************@')
    lines.append('\t.align\t2')
    lines.append('')
    lines.append(f'{prefix}:')
    lines.append(f'\t.byte\t{len(song.tracks)}\t@ NumTrks')
    lines.append(f'\t.byte\t0\t@ NumBlks')
    lines.append(f'\t.byte\t{prefix}_pri\t@ Priority')
    lines.append(f'\t.byte\t{prefix}_rev\t@ Reverb.')
    lines.append('')
    lines.append(f'\t.word\t{prefix}_grp')
    lines.append('')

    for track in song.tracks:
        lines.append(f'\t.word\t{track.label}')

    lines.append('')
    lines.append('\t.end')
    lines.append('')

    return '\n'.join(lines)


def save_song_file(song: SongData, path: Optional[str] = None) -> str:
    """Write a SongData to its .s file. Returns the path written to.

    If path is None, uses song.file_path (the original location).

    After writing, ensures the .s file's mtime is newer than the
    corresponding .mid file (if one exists).  pokefirered's Makefile has
    a `%.s: %.mid` rule — if .mid is newer than .s, `make` runs mid2agb
    which OVERWRITES the .s with whatever the .mid produces.  For songs
    edited through the piano roll (where .s is the source of truth and
    .mid is just a placeholder), this would destroy the user's work.
    """
    output_path = path or song.file_path
    if not output_path:
        raise ValueError(f"No file path for song '{song.label}'")

    content = write_song(song)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)

    # Protect against mid2agb overwrite: ensure .s is newer than .mid.
    # Writing the file already sets mtime to now, but we also backdate
    # the .mid to guarantee the ordering even if both happen in the same
    # second (filesystem resolution can be 1-2 seconds on FAT/NTFS).
    import os
    mid_path = output_path.rsplit('.', 1)[0] + '.mid'
    if os.path.isfile(mid_path):
        s_stat = os.stat(output_path)
        # Set .mid mtime to 2 seconds before the .s mtime
        past = s_stat.st_mtime - 2
        os.utime(mid_path, (past, past))

    return output_path
