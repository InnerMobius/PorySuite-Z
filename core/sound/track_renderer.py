"""
Track and song renderer for the GBA M4A engine.

Walks through parsed track commands (from song_parser) and renders each note
through the audio engine with the correct instrument, timing, volume, and pan.

Handles:
- PATT/PEND: subroutine-style pattern calls (jump to label, play until PEND,
  return to caller) — used extensively in Game Corner, etc.
- GOTO: loop back to a label (plays intro + N loop iterations)
- All control commands: VOICE, VOL, PAN, MOD, BEND, BENDR, KEYSH, TEMPO
- Note rendering with pitch shifting, velocity, ADSR

Mixes all tracks together for full song playback.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from core.sound.audio_engine import (
    OUTPUT_SAMPLE_RATE,
    render_instrument,
    apply_reverb,
    apply_pan,
)
from core.sound.song_parser import SongData, Track, TrackCommand, get_song_tempo
from core.sound.voicegroup_parser import Voicegroup, VoicegroupData
from core.sound.sample_loader import SampleData


# ---------------------------------------------------------------------------
# Timing conversion
# ---------------------------------------------------------------------------

def ticks_to_samples(ticks: int, bpm: int, tbs: int = 1) -> int:
    """Convert M4A ticks to output samples.

    The GBA M4A engine runs its tick clock from the tempo:
      ticks_per_frame = tempo * tbs / 150
      frames_per_second ~= 59.7275 (GBA refresh)
      ticks_per_second = ticks_per_frame * frames_per_second

    So: samples = ticks * OUTPUT_SAMPLE_RATE / ticks_per_second
    """
    if bpm <= 0:
        bpm = 120
    ticks_per_frame = bpm * tbs / 150.0
    ticks_per_second = ticks_per_frame * 59.7275
    if ticks_per_second <= 0:
        return 0
    return int(ticks * OUTPUT_SAMPLE_RATE / ticks_per_second)


# ---------------------------------------------------------------------------
# Command timeline flattener
# ---------------------------------------------------------------------------

def _build_label_index(commands: list[TrackCommand]) -> dict[str, int]:
    """Build a map of label_name -> command list index."""
    index = {}
    for i, cmd in enumerate(commands):
        if cmd.cmd == 'LABEL' and cmd.target_label:
            index[cmd.target_label] = i
    return index


def flatten_track_commands(
    commands: list[TrackCommand],
    loop_count: int = 1,
) -> list[TrackCommand]:
    """Expand PATT/PEND calls and GOTO loops into a flat command sequence.

    PATT works like a function call:
      1. When PATT is hit, save current position on a stack
      2. Jump to the label referenced by PATT's target_label
      3. Execute commands until PEND is hit
      4. Pop back to the instruction after the PATT

    GOTO loops back to a label. We play through (loop_count) times,
    then stop at FINE.
    """
    label_index = _build_label_index(commands)
    result: list[TrackCommand] = []
    current_tick = 0

    # Execution state
    pc = 0                      # program counter (index into commands)
    call_stack: list[int] = []  # return addresses for PATT calls
    goto_count = 0              # how many times we've hit GOTO
    max_commands = len(commands) * (loop_count + 1) * 4  # safety limit

    while pc < len(commands) and len(result) < max_commands:
        cmd = commands[pc]

        if cmd.cmd == 'LABEL':
            # Labels are just markers, skip them
            pc += 1
            continue

        if cmd.cmd == 'PATT':
            # Subroutine call — push return address, jump to target
            target = cmd.target_label
            if target and target in label_index:
                call_stack.append(pc + 1)  # return to next instruction
                pc = label_index[target] + 1  # jump past the label
                continue
            # Unknown target — skip
            pc += 1
            continue

        if cmd.cmd == 'PEND':
            # Return from subroutine
            if call_stack:
                pc = call_stack.pop()
                continue
            # PEND without PATT — just skip
            pc += 1
            continue

        if cmd.cmd == 'GOTO':
            goto_count += 1
            if goto_count <= loop_count:
                # Loop back to the target label
                target = cmd.target_label
                if target and target in label_index:
                    pc = label_index[target] + 1
                    continue
            # Done looping — stop here
            break

        if cmd.cmd == 'FINE':
            break

        # For all other commands, emit with updated tick
        new_cmd = TrackCommand(
            cmd=cmd.cmd,
            tick=current_tick,
            duration=cmd.duration,
            pitch=cmd.pitch,
            velocity=cmd.velocity,
            gate_time=cmd.gate_time,
            value=cmd.value,
            target_label=cmd.target_label,
            raw_line=cmd.raw_line,
        )
        result.append(new_cmd)

        # Only WAITs advance the tick clock — notes are placed at the
        # current tick but don't move it (the WAIT after the note does)
        if cmd.cmd == 'WAIT':
            current_tick += cmd.duration

        pc += 1

    return result


def get_flattened_loop_info(
    commands: list[TrackCommand],
) -> tuple[int | None, int | None, int]:
    """Get loop boundaries and total duration from flattened commands.

    Returns (loop_start_tick, loop_end_tick, total_ticks).
    Runs the flatten logic twice: once with loop_count=0 to find where the
    GOTO is (loop end), and uses the GOTO's target label to find where
    that label's content starts in the flattened timeline (loop start).
    """
    label_index = _build_label_index(commands)

    # Find the GOTO command and its target label
    goto_target = None
    has_goto = False
    for cmd in commands:
        if cmd.cmd == 'GOTO':
            has_goto = True
            goto_target = cmd.target_label
            break

    if not has_goto:
        # No loop — just compute total duration from flatten
        flat = flatten_track_commands(commands, loop_count=0)
        total = 0
        for cmd in flat:
            if cmd.cmd == 'WAIT':
                end = cmd.tick + cmd.duration
                if end > total:
                    total = end
            elif cmd.cmd == 'NOTE' and cmd.duration:
                end = cmd.tick + cmd.duration
                if end > total:
                    total = end
        return (None, None, total)

    # Flatten with loop_count=0: gives intro + loop body (stops at GOTO)
    # The current_tick at the GOTO is the loop_end
    # Re-run the flatten logic manually to capture the label tick
    pc = 0
    call_stack: list[int] = []
    current_tick = 0
    loop_start_tick = None
    max_iters = len(commands) * 8

    iters = 0
    while pc < len(commands) and iters < max_iters:
        iters += 1
        cmd = commands[pc]

        if cmd.cmd == 'LABEL':
            # If this is the GOTO target, record the current tick
            if goto_target and cmd.target_label == goto_target:
                loop_start_tick = current_tick
            pc += 1
            continue

        if cmd.cmd == 'PATT':
            target = cmd.target_label
            if target and target in label_index:
                call_stack.append(pc + 1)
                pc = label_index[target] + 1
                continue
            pc += 1
            continue

        if cmd.cmd == 'PEND':
            if call_stack:
                pc = call_stack.pop()
                continue
            pc += 1
            continue

        if cmd.cmd == 'GOTO':
            # This is where the loop ends
            return (loop_start_tick, current_tick, current_tick)

        if cmd.cmd == 'FINE':
            break

        if cmd.cmd == 'WAIT':
            current_tick += cmd.duration

        pc += 1

    return (loop_start_tick, current_tick, current_tick)


# ---------------------------------------------------------------------------
# Track state machine
# ---------------------------------------------------------------------------

class TrackState:
    """Runtime state for a single track during rendering."""

    def __init__(self):
        self.voice: int = 0           # current instrument slot (0-127)
        self.volume: int = 100        # track volume (0-127)
        self.pan: int = 64            # 0=left, 64=center, 127=right
        self.mod: int = 0             # modulation (vibrato depth)
        self.bend: int = 0            # pitch bend (-128 to 127)
        self.bend_range: int = 2      # bend range in semitones
        self.key_shift: int = 0       # KEYSH transposition
        self.tempo: int = 120         # BPM (shared across tracks but set per-track)


# ---------------------------------------------------------------------------
# Single track renderer
# ---------------------------------------------------------------------------

def render_track(
    track: Track,
    voicegroup: Voicegroup,
    sample_data: SampleData,
    voicegroup_data: VoicegroupData,
    bpm: int,
    tbs: int = 1,
    master_volume: int = 127,
    song_key_shift: int = 0,
    max_duration_ticks: Optional[int] = None,
    loop_count: int = 1,
) -> np.ndarray:
    """Render a single track to a stereo float32 buffer.

    Flattens PATT/PEND patterns and GOTO loops, then walks through the
    expanded command list rendering each note with per-note panning.

    Returns a stereo float32 array of shape (N, 2).
    """
    state = TrackState()
    state.tempo = bpm
    state.key_shift = song_key_shift

    # Flatten the command list (expand PATT/PEND and GOTO)
    flat_cmds = flatten_track_commands(track.commands, loop_count)

    # Find total duration from the flattened commands
    flat_max_tick = 0
    for cmd in flat_cmds:
        end = cmd.tick + cmd.duration
        if end > flat_max_tick:
            flat_max_tick = end

    if max_duration_ticks is not None:
        effective_ticks = min(flat_max_tick, max_duration_ticks)
    else:
        effective_ticks = flat_max_tick

    total_samples = ticks_to_samples(effective_ticks, bpm, tbs)
    if total_samples <= 0:
        return np.zeros((0, 2), dtype=np.float32)

    # Add padding for release tails
    release_pad = int(OUTPUT_SAMPLE_RATE * 0.2)
    buffer = np.zeros((total_samples + release_pad, 2), dtype=np.float32)

    current_tempo = bpm

    for cmd in flat_cmds:
        if cmd.cmd == 'VOICE' and cmd.value is not None:
            state.voice = cmd.value

        elif cmd.cmd == 'VOL' and cmd.value is not None:
            state.volume = max(0, min(127, cmd.value))

        elif cmd.cmd == 'PAN' and cmd.value is not None:
            state.pan = max(0, min(127, cmd.value))

        elif cmd.cmd == 'MOD' and cmd.value is not None:
            state.mod = cmd.value

        elif cmd.cmd == 'BEND' and cmd.value is not None:
            # GBA subtracts C_V (64) before storing: bend is signed, 0 = center
            state.bend = cmd.value - 64

        elif cmd.cmd == 'BENDR' and cmd.value is not None:
            state.bend_range = cmd.value

        elif cmd.cmd == 'KEYSH' and cmd.value is not None:
            state.key_shift = cmd.value

        elif cmd.cmd == 'TEMPO' and cmd.value is not None:
            current_tempo = (cmd.value * 2) // max(tbs, 1)
            state.tempo = current_tempo

        elif cmd.cmd in ('NOTE', 'TIE'):
            if cmd.pitch is None:
                continue

            # For TIE commands, compute duration by finding the matching EOT
            if cmd.cmd == 'TIE':
                tie_duration = 0
                cmd_idx = flat_cmds.index(cmd)
                for fc in flat_cmds[cmd_idx + 1:]:
                    if fc.cmd == 'EOT' and (fc.pitch is None or fc.pitch == cmd.pitch):
                        tie_duration = fc.tick - cmd.tick
                        break
                    if fc.cmd == 'TIE' and fc.pitch == cmd.pitch:
                        tie_duration = fc.tick - cmd.tick
                        break
                    if fc.cmd in ('FINE', 'GOTO'):
                        tie_duration = fc.tick - cmd.tick
                        break
                if tie_duration <= 0:
                    tie_duration = 96
                note_duration = tie_duration
            else:
                note_duration = cmd.duration

            midi_note = cmd.pitch + state.key_shift

            if state.bend != 0:
                # bend is -64 to +63, scale to -1.0 to +1.0 then multiply by range
                bend_semitones = (state.bend / 64.0) * state.bend_range
                midi_note += bend_semitones
            midi_note = int(max(0, min(127, midi_note)))

            velocity = cmd.velocity if cmd.velocity is not None else 100

            # GBA volume pipeline (simplified, matching ChnVolSetAsm):
            #   trackVol = (vol * volX) >> 5   (volX defaults to 64)
            #   chanVol  = (velocity * trackVol) >> 7
            # The song's mvl is already baked into VOL command values
            # (written as "VOL, n*mvl/mxv" in the source), so do NOT
            # multiply by master_volume again.
            #
            # We pass raw velocity to the audio engine (which applies it),
            # then scale the rendered output by track volume.
            track_vol_scale = state.volume / 127.0

            note_samples = ticks_to_samples(note_duration, current_tempo, tbs)
            if note_samples <= 0:
                continue

            instrument = voicegroup.get_instrument(state.voice)
            if instrument is None:
                continue

            release_samples = min(2048, note_samples)
            try:
                note_audio = render_instrument(
                    instrument, midi_note, velocity,
                    note_samples, sample_data, voicegroup_data,
                    release_samples=release_samples,
                )
            except Exception:
                continue

            # Apply track volume (velocity is already applied by audio engine)
            if track_vol_scale < 1.0:
                note_audio *= track_vol_scale

            # Apply per-note panning (GBA linear crossfade)
            note_stereo = apply_pan(note_audio, state.pan)

            start_sample = ticks_to_samples(cmd.tick, current_tempo, tbs)
            if start_sample < len(buffer):
                usable = min(len(note_stereo), len(buffer) - start_sample)
                buffer[start_sample:start_sample + usable] += note_stereo[:usable]

    np.clip(buffer, -1.0, 1.0, out=buffer)
    return buffer


# ---------------------------------------------------------------------------
# Song renderer (mixes all tracks)
# ---------------------------------------------------------------------------

def render_song(
    song: SongData,
    voicegroup_data: VoicegroupData,
    sample_data: SampleData,
    max_duration_ticks: Optional[int] = None,
    loop_count: int = 1,
    progress_callback=None,
) -> np.ndarray:
    """Render a complete song to a stereo float32 buffer.

    Args:
        song: Parsed song data.
        voicegroup_data: All voicegroup definitions.
        sample_data: All loaded sample data.
        max_duration_ticks: Cap the render length (None = full song).
        loop_count: How many times to play through the loop (1 = no repeat).
        progress_callback: Optional callable(track_index, total_tracks) for UI.

    Returns:
        Stereo float32 array of shape (N, 2).
    """
    bpm = get_song_tempo(song)
    tbs = song.tempo_base if song.tempo_base else 1

    # Resolve the voicegroup
    vg = voicegroup_data.get_voicegroup(song.voicegroup)
    if vg is None:
        return np.zeros((OUTPUT_SAMPLE_RATE, 2), dtype=np.float32)

    # Determine song length from flattened commands
    if max_duration_ticks is None:
        max_duration_ticks = 0
        for track in song.tracks:
            flat = flatten_track_commands(track.commands, loop_count)
            for cmd in flat:
                end = cmd.tick + cmd.duration
                if end > max_duration_ticks:
                    max_duration_ticks = end

    if max_duration_ticks <= 0:
        max_duration_ticks = 9600

    total_tracks = len(song.tracks)
    stereo_mix = None

    for i, track in enumerate(song.tracks):
        if progress_callback:
            progress_callback(i, total_tracks)

        stereo = render_track(
            track, vg, sample_data, voicegroup_data,
            bpm, tbs, song.master_volume, song.key_shift,
            max_duration_ticks=max_duration_ticks,
            loop_count=loop_count,
        )

        if len(stereo) == 0:
            continue

        # render_track now returns stereo with per-note panning applied
        if stereo_mix is None:
            stereo_mix = stereo.copy()
        else:
            if len(stereo) > len(stereo_mix):
                pad = np.zeros((len(stereo) - len(stereo_mix), 2), dtype=np.float32)
                stereo_mix = np.concatenate([stereo_mix, pad])
            elif len(stereo) < len(stereo_mix):
                pad = np.zeros((len(stereo_mix) - len(stereo), 2), dtype=np.float32)
                stereo = np.concatenate([stereo, pad])
            stereo_mix += stereo

    if stereo_mix is None:
        return np.zeros((OUTPUT_SAMPLE_RATE, 2), dtype=np.float32)

    # Apply reverb
    if song.reverb > 0:
        left = apply_reverb(stereo_mix[:, 0], song.reverb)
        right = apply_reverb(stereo_mix[:, 1], song.reverb)
        stereo_mix[:, 0] = left
        stereo_mix[:, 1] = right

    # Normalize
    peak = np.max(np.abs(stereo_mix))
    if peak > 1.0:
        stereo_mix /= peak
    elif peak > 0:
        target = 0.8
        if peak < target:
            stereo_mix *= (target / peak)
            np.clip(stereo_mix, -1.0, 1.0, out=stereo_mix)

    if progress_callback:
        progress_callback(total_tracks, total_tracks)

    return stereo_mix


# ---------------------------------------------------------------------------
# Single instrument preview
# ---------------------------------------------------------------------------

def render_instrument_preview(
    instrument,
    midi_note: int,
    sample_data: SampleData,
    voicegroup_data: VoicegroupData,
    duration_ms: int = 500,
    velocity: int = 100,
) -> np.ndarray:
    """Render a single instrument note for preview/audition.

    Returns stereo float32 array.
    """
    duration_samples = int(OUTPUT_SAMPLE_RATE * duration_ms / 1000)
    release_samples = min(2048, duration_samples // 2)

    mono = render_instrument(
        instrument, midi_note, velocity,
        duration_samples, sample_data, voicegroup_data,
        release_samples=release_samples,
    )

    stereo = apply_pan(mono, 64)
    return stereo
