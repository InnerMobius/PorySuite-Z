"""Read-only inspection of a song's .mid: every event, what the game makes of it.

For each event: the MIDI track (chunk) it is in and that track's name, its
time in MIDI ticks and on the game's clock (mid2agb: 24 * tbs * t / resolution),
its channel, its kind and values, which of the song's tracks it belongs to
(mid2agb numbers tracks by (MIDI track, channel with notes) in file order),
and what the build does with it:

* a note, an instrument change (program change), a pitch bend, a controller
  the build turns into a command (volume, pan, vibrato, tuning, bend range,
  priority, echo / memory commands), tempo, a time signature (bars) or a loop
  marker ("[" / "]") — used by the game;
* anything else (track names, text, key signatures, controllers the build has
  no command for, sysex, aftertouch, ...) — not used by the game. (A channel
  event of that kind in the middle of a song can still make mid2agb split a
  wait there, which changes the .s text but not the music.) Such events stay
  in the .mid, and PorySuite's in-place edits never touch them.

The controller list is the one PorySuite's MIDI patcher writes (midi_patch),
which reproduces all 383 project songs through mid2agb.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import mido

_CC_MEANING = {1: 'vibrato depth (MOD)', 7: 'volume (VOL)', 10: 'pan (PAN)',
               20: 'pitch-bend range (BENDR)', 21: 'vibrato speed (LFOS)',
               22: 'vibrato type (MODT)', 24: 'fine tune (TUNE)', 26: 'vibrato delay (LFODL)',
               33: 'priority (PRIO)', 29: 'echo value (XCMD)', 30: 'echo command select (XCMD)',
               12: 'memory command (MEMACC)', 13: 'memory command (MEMACC)',
               14: 'memory command (MEMACC)'}


@dataclass
class MidiEventRow:
    chunk: int
    chunk_name: str
    midi_tick: int
    game_tick: int
    channel: Optional[int]
    kind: str
    detail: str
    used: bool
    meaning: str
    song_track: Optional[int]      # 0-based .s track, or None


def _meaning(m) -> tuple[bool, str]:
    t = m.type
    if t in ('note_on', 'note_off'):
        if t == 'note_on' and m.velocity > 0:
            return True, 'note start'
        return True, 'note end'
    if t == 'program_change':
        return True, 'instrument change (VOICE)'
    if t == 'pitchwheel':
        return True, 'pitch bend (BEND)'
    if t == 'control_change':
        if m.control in _CC_MEANING:
            return True, _CC_MEANING[m.control]
        return False, (f'controller {m.control}: the build writes no command for it '
                       f'(mid-song, it can split a wait in the .s text; the music is the same)')
    if t == 'set_tempo':
        return True, 'tempo (TEMPO)'
    if t == 'time_signature':
        return True, 'time signature (where bars fall)'
    if t == 'marker' and m.text in ('[', ']'):
        return True, 'loop start' if m.text == '[' else 'loop end (loops back)'
    if t == 'end_of_track':
        return False, 'end of this MIDI track'
    if t == 'track_name':
        return False, "track name (kept in the .mid; the game doesn't use it)"
    if t == 'sysex':
        return False, 'system-exclusive data (ignored by the build)'
    return False, 'ignored by the build'


def _detail(m) -> str:
    if m.type in ('note_on', 'note_off'):
        return f"note {m.note} velocity {m.velocity}"
    if m.type == 'control_change':
        return f"controller {m.control} = {m.value}"
    if m.type == 'program_change':
        return f"program {m.program}"
    if m.type == 'pitchwheel':
        return f"bend {m.pitch}"
    if m.type == 'set_tempo':
        return f"{mido.tempo2bpm(m.tempo):.2f} BPM ({m.tempo} µs per beat)"
    if m.type == 'time_signature':
        return f"{m.numerator}/{m.denominator}"
    if m.type in ('marker', 'text', 'track_name', 'lyrics', 'cue_marker'):
        return repr(getattr(m, 'text', getattr(m, 'name', '')))
    if m.type == 'sysex':
        return f"{len(m.data)} bytes"
    d = m.dict()
    d.pop('type', None)
    d.pop('time', None)
    return ", ".join(f"{k} {v}" for k, v in d.items())


def inspect_midi(data: bytes, tempo_base: int = 1) -> list[MidiEventRow]:
    """Every event of the .mid *data*, in file order per MIDI track."""
    mf = mido.MidiFile(file=io.BytesIO(data))
    tpb = mf.ticks_per_beat
    tbs = max(1, int(tempo_base or 1))
    # mid2agb's numbering: (MIDI track, channel with notes) in file order.
    song_track = {}
    for ci, tr in enumerate(mf.tracks):
        for ch in sorted({m.channel for m in tr if m.type == 'note_on' and m.velocity > 0}):
            song_track[(ci, ch)] = len(song_track)
    rows = []
    for ci, tr in enumerate(mf.tracks):
        name = next((m.name for m in tr if m.type == 'track_name'), '')
        now = 0
        for m in tr:
            now += m.time
            ch = getattr(m, 'channel', None)
            used, meaning = _meaning(m)
            rows.append(MidiEventRow(
                chunk=ci, chunk_name=name, midi_tick=now, game_tick=(24 * tbs * now) // tpb,
                channel=ch, kind=m.type, detail=_detail(m), used=used, meaning=meaning,
                song_track=song_track.get((ci, ch)) if ch is not None else None))
    return rows
