"""SongData → .mid exporter.

When the user saves a song via the Piano Roll (or inline header edit),
the .s file becomes the source of truth.  This module generates a fresh
.mid file from that same in-memory SongData so the .mid on disk MATCHES
the .s content note-for-note.

Why this matters: if the .mid is stale (or worse, a 26-byte placeholder)
and the build pipeline ever runs mid2agb on it, the user's hand-edited
.s gets silently overwritten with whatever the stale .mid says.  The
mtime-backdate defense in `save_song_file()` is the primary protection;
this exporter is the secondary one — even if mid2agb DOES run, the .mid
matches the .s, so the regenerated .s is equivalent.  No silent data loss.

The exporter is intentionally lossy on M4A-specific structure (PATT/PEND
subroutines, GOTO loops, KEYSH, BENDR, LFOS, MODT, TUNE — anything that
doesn't round-trip cleanly through a Standard MIDI File).  That's fine:
the .s already holds the canonical full-fidelity version, and the .mid
just needs to encode the audible notes well enough that
  (a) a user can play it in WMP / any DAW, and
  (b) a re-run of mid2agb produces a .s that's audibly equivalent.

Tick scale: M4A natively uses 24 ticks per quarter; the MIDI is written
at 48 ticks per quarter (2× scale) so the resulting file is a clean
even-tick standard SMF that DAWs render at a sensible default zoom.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

import mido

from core.sound.song_parser import SongData, Track, TrackCommand


_log = logging.getLogger("SoundEditor.MidiExporter")

# MIDI tick resolution.  24 ticks/beat is the native M4A value; we double
# it so the SMF is well-formed and DAWs don't auto-quantize oddly.
_MIDI_TPB = 48
_SCALE = _MIDI_TPB // 24  # M4A tick → MIDI tick

# Default microseconds per beat when a song doesn't emit its own TEMPO
# at tick 0 (extremely rare in practice; M4A songs always set tempo).
_DEFAULT_BPM = 120
_DEFAULT_TEMPO_US = int(60_000_000 / _DEFAULT_BPM)


def song_to_midi(song: SongData) -> mido.MidiFile:
    """Convert a parsed/edited SongData into a playable mido.MidiFile.

    Returns a Type-1 MIDI with one conductor track (tempo/meta only) plus
    one track per SongData.Track.
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=_MIDI_TPB)

    # ---- conductor track ----------------------------------------------------
    # Tempo events from EVERY track get pooled onto track 0.  In M4A any
    # track can emit TEMPO but the value is global, so collecting them
    # avoids putting redundant tempo changes on per-channel tracks (which
    # is technically legal SMF but confuses some DAWs).
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage(
        'track_name', name=song.label or 'song', time=0))

    tempo_events: list[tuple[int, int]] = []  # (abs_tick_midi, tempo_us)
    tbs = max(1, int(song.tempo_base or 1))
    saw_tick0_tempo = False

    for track in song.tracks:
        for cmd in track.commands:
            if cmd.cmd != 'TEMPO':
                continue
            raw = int(cmd.value or 0)
            if raw <= 0:
                continue
            bpm = (raw * 2) / tbs
            if bpm <= 0:
                continue
            tempo_us = int(round(60_000_000 / bpm))
            tempo_events.append((int(cmd.tick) * _SCALE, tempo_us))
            if cmd.tick == 0:
                saw_tick0_tempo = True

    if not saw_tick0_tempo:
        tempo_events.insert(0, (0, _DEFAULT_TEMPO_US))

    # Sort by tick, dedupe identical adjacent tempos.
    tempo_events.sort(key=lambda e: e[0])
    last_tempo = None
    pruned: list[tuple[int, int]] = []
    for t, us in tempo_events:
        if us != last_tempo:
            pruned.append((t, us))
            last_tempo = us

    prev_tick = 0
    for abs_tick, us in pruned:
        delta = max(0, abs_tick - prev_tick)
        conductor.append(mido.MetaMessage('set_tempo', tempo=us, time=delta))
        prev_tick = abs_tick

    conductor.append(mido.MetaMessage('end_of_track', time=0))
    mid.tracks.append(conductor)

    # ---- one MidiTrack per SongData track ----------------------------------
    for ti, track in enumerate(song.tracks):
        mid.tracks.append(_build_midi_track(track, ti))

    return mid


def _build_midi_track(track: Track, track_index: int) -> mido.MidiTrack:
    """Convert one Track to a mido.MidiTrack of note + control events."""
    mt = mido.MidiTrack()

    # Channel resolution: prefer the track's parsed midi_channel, else fall
    # back to a sane per-track default.  Skip channel 9 by default so we
    # don't accidentally land on the GM drum channel.
    if track.midi_channel is not None:
        channel = max(0, min(15, int(track.midi_channel)))
    else:
        # 0..15 cycling, skip 9 (drums) unless caller forced it
        channel = track_index if track_index < 9 else track_index + 1
        channel = max(0, min(15, channel))

    # priority used only for stable sort: tempos -2, controls/labels -1,
    # note_off 0, note_on 1.  Note_off before note_on at the same tick so
    # a re-strike of the same pitch doesn't end up with the off cancelling
    # the new on.
    events: list[tuple[int, int, mido.BaseMessage]] = []

    # Track name first.
    events.append((0, -3, mido.MetaMessage(
        'track_name', name=track.label or f'track_{track_index}', time=0)))

    # Walk the parsed commands.  TIE/EOT pairs are stitched into a
    # single note_on/note_off span; loose EOTs (no matching TIE) are
    # dropped.
    active_tie_pitch: int | None = None

    for cmd in track.commands:
        tick = int(cmd.tick) * _SCALE
        kind = cmd.cmd

        if kind == 'NOTE':
            pitch = _clamp_pitch(cmd.pitch)
            if pitch is None:
                continue
            velocity = _clamp_velocity(cmd.velocity)
            duration = max(1, int(cmd.duration or 24)) * _SCALE
            events.append((tick, 1, mido.Message(
                'note_on', channel=channel, note=pitch,
                velocity=velocity, time=0)))
            events.append((tick + duration, 0, mido.Message(
                'note_off', channel=channel, note=pitch,
                velocity=64, time=0)))

        elif kind == 'TIE':
            pitch = _clamp_pitch(cmd.pitch)
            if pitch is None:
                continue
            velocity = _clamp_velocity(cmd.velocity)
            active_tie_pitch = pitch
            events.append((tick, 1, mido.Message(
                'note_on', channel=channel, note=pitch,
                velocity=velocity, time=0)))

        elif kind == 'EOT':
            pitch = _clamp_pitch(cmd.pitch)
            if pitch is None:
                pitch = active_tie_pitch
            if pitch is None:
                continue  # loose EOT — nothing to terminate
            events.append((tick, 0, mido.Message(
                'note_off', channel=channel, note=pitch,
                velocity=64, time=0)))
            active_tie_pitch = None

        elif kind == 'VOICE':
            # cmd.value of 0 is a valid voice (voice slot 0).  Use explicit
            # None check, not `or` — `0 or 0` is fine but `0 or 100` would
            # silently mangle a legitimate value-0 to the default.
            v = 0 if cmd.value is None else int(cmd.value)
            prog = max(0, min(127, v))
            events.append((tick, -1, mido.Message(
                'program_change', channel=channel, program=prog, time=0)))

        elif kind == 'VOL':
            # VOL=0 is silence (valid).  Use None check, not `or`.
            v = 100 if cmd.value is None else int(cmd.value)
            val = max(0, min(127, v))
            events.append((tick, -1, mido.Message(
                'control_change', channel=channel,
                control=7, value=val, time=0)))

        elif kind == 'PAN':
            # PAN=0 is hard-left (valid).  Use None check, not `or`.
            v = 64 if cmd.value is None else int(cmd.value)
            val = max(0, min(127, v))
            events.append((tick, -1, mido.Message(
                'control_change', channel=channel,
                control=10, value=val, time=0)))

        elif kind == 'MOD':
            v = 0 if cmd.value is None else int(cmd.value)
            val = max(0, min(127, v))
            events.append((tick, -1, mido.Message(
                'control_change', channel=channel,
                control=1, value=val, time=0)))

        elif kind == 'BEND':
            # M4A BEND value is c_v-based: 64 = center, 0 = -64, 127 = +63.
            # MIDI pitchwheel is -8192..+8191.  Scale to roughly full range.
            # BEND=0 (hard-down) is valid — use None check.
            v = 64 if cmd.value is None else int(cmd.value)
            offset = v - 64
            pb = max(-8192, min(8191, offset * 128))
            events.append((tick, -1, mido.Message(
                'pitchwheel', channel=channel, pitch=pb, time=0)))

        # Everything else (KEYSH, BENDR, LFOS, LFODL, MODT, TUNE, XCMD,
        # PRIO, TEMPO already hoisted to conductor, GOTO/PATT/PEND/LABEL/
        # FINE/WAIT) is intentionally not emitted to MIDI.  WAITs are
        # implicit via tick gaps between events.

    # If a TIE was opened and never closed (corrupt input), terminate it
    # at the last event tick so the MIDI parser doesn't complain about an
    # un-released note_on.
    if active_tie_pitch is not None and events:
        last_tick = events[-1][0]
        events.append((last_tick, 0, mido.Message(
            'note_off', channel=channel, note=active_tie_pitch,
            velocity=64, time=0)))

    # Stable sort by (abs_tick, priority).
    events.sort(key=lambda e: (e[0], e[1]))

    # Absolute → delta time.
    prev_tick = 0
    for abs_tick, _pri, msg in events:
        delta = max(0, abs_tick - prev_tick)
        msg.time = delta
        mt.append(msg)
        prev_tick = abs_tick

    mt.append(mido.MetaMessage('end_of_track', time=0))
    return mt


def _clamp_pitch(p):
    if p is None:
        return None
    return max(0, min(127, int(p)))


def _clamp_velocity(v):
    if v is None:
        return 100
    # note_on velocity=0 is interpreted as note_off; clamp to 1.
    return max(1, min(127, int(v)))


def write_midi_file(song: SongData, path: str) -> bool:
    """Render `song` to a .mid file on disk.

    Returns True if the file was written (or already byte-identical to
    what we would have written).  Returns False on hard I/O error after
    logging the cause; never raises — the .s save must succeed even if
    the .mid render fails for some reason.

    A byte-equality short-circuit is included so that re-saving an
    unchanged song doesn't bump the .mid's mtime / dirty git status.
    """
    try:
        mid = song_to_midi(song)
    except Exception as exc:
        _log.warning(
            "song_to_midi failed for %s: %s — .mid will be left untouched",
            song.label, exc, exc_info=True)
        return False

    # Render to bytes in memory so we can compare against the existing
    # file before deciding to write.
    import io
    buf = io.BytesIO()
    try:
        mid.save(file=buf)
    except Exception as exc:
        _log.warning(
            "mido.save failed for %s: %s — .mid will be left untouched",
            song.label, exc, exc_info=True)
        return False
    new_bytes = buf.getvalue()

    try:
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                if f.read() == new_bytes:
                    return True  # already up-to-date
        with open(path, 'wb') as f:
            f.write(new_bytes)
        return True
    except OSError as exc:
        _log.warning(
            "Failed to write .mid for %s at %s: %s",
            song.label, path, exc)
        return False
