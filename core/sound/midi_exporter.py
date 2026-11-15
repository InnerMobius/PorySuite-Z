"""SongData → .mid exporter.

The build makes a song's .s from its .mid with mid2agb, so a .mid written here
is the song from then on. The contract: mid2agb run on the .mid this module
writes gives back the .s it was made from. That holds for every song in the
project (all 383, checked by running mid2agb on the export of each — see
BUGS.md, "Sound Editor tempo save ..." 2026-11-15), and it is what makes an
editor save safe.

What it takes to keep that contract, each one a defect that used to lose music:

* Every track is walked on the PLAYBACK clock (``_expand_track``): a pattern
  call plays its pattern and takes its time, so repeats are heard and nothing
  after them slides earlier.
* Every command mid2agb can rebuild is written as the MIDI event it reads back:
  notes (with exact gate time), TIE/EOT, VOICE, VOL (its raw level), PAN, BEND,
  MOD, BENDR, LFOS, MODT, TUNE, LFODL, PRIO, XCMD echo, MEMACC, TEMPO (every
  one), the loop, and each track's full length including closing silence.
* What only shaped mid2agb's encoding is put back too: time signatures read from
  the bar comments, and markers where the source MIDI split a wait.

``song_to_midi(song, edit)`` also applies an editor's edits (``SongEdit``) on
that clock, so an edit to the notes leaves everything else as it was.

Tick scale: M4A natively uses 24 ticks per quarter; the MIDI is written
at 48 ticks per quarter (2× scale) so the resulting file is a clean
even-tick standard SMF that DAWs render at a sensible default zoom.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import mido

from core.sound.song_parser import SongData, Track, TrackCommand


_log = logging.getLogger("SoundEditor.MidiExporter")

# MIDI tick resolution.  24 ticks/beat is the native M4A value; we double
# it so the SMF is well-formed and DAWs don't auto-quantize oddly.
_MIDI_TPB = 48
_SCALE = _MIDI_TPB // 24  # M4A tick → MIDI tick


# ─────────────────────────────────────────────────────────────────────────────
# Editor edits, applied on the playback clock
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TrackEdit:
    """What an editor changed on one track, in playback ticks.

    ``notes``: the editor's full note list for the track (dicts with tick,
    pitch, duration, velocity) — replaces every note, or None to keep them.
    ``controls``: the editor's control events of the EDITABLE_CONTROLS kinds
    (volume, pan, bends, vibrato and tuning) — replaces the
    track's mid-song ones (tick > 0), or None to keep them.
    """
    notes: Optional[list] = None
    controls: Optional[list] = None


@dataclass
class SongEdit:
    """Edits an editor made, keyed by track index. ``loop`` is None to keep
    each track's own loop, or (start, end) — (None, None) removes the loop.
    ``time_signatures`` is None to keep the song's, or the complete new list
    of (tick, numerator, denominator) on the playback clock."""
    tracks: dict = field(default_factory=dict)
    loop: Optional[tuple] = None
    time_signatures: Optional[list] = None


_NOTE_KINDS = frozenset(('NOTE', 'TIE', 'EOT'))
EDITABLE_CONTROLS = frozenset(('BEND', 'BENDR', 'VOL', 'PAN', 'VOICE',
                               'MOD', 'LFOS', 'MODT', 'LFODL', 'TUNE', 'TEMPO'))


def expand_song(song: SongData, edit: Optional[SongEdit] = None) -> list:
    """Every track on the playback clock, with an editor's edits applied.

    Everything the edit does not name is left exactly as the song has it, so
    instrument changes, effects, tempo changes, timing and bar layout survive
    an edit to the notes.
    """
    expanded = [_expand_track(t) for t in song.tracks]
    if edit is None:
        return expanded
    for ti, te in (edit.tracks or {}).items():
        if 0 <= ti < len(expanded) and te is not None:
            _apply_track_edit(expanded[ti], te)
    if edit.loop is not None:
        for ex in expanded:
            _apply_loop(ex, *edit.loop)
    return expanded


def song_loop_region(song: SongData):
    """(start, end) of the song's loop on the playback clock — the loop the
    build makes (track 1's, as the exporter writes it) — or None.

    A loop label's position in the TEXT is not where it plays once pattern
    calls take their time (Kakariko: text 2879, playback 3935), so an editor
    drawing notes on the playback clock must draw the loop from this."""
    if not song.tracks:
        return None
    ex = _expand_track(song.tracks[0])
    if ex.goto_target is None or ex.goto_time is None:
        return None
    start = ex.label_times.get(ex.goto_target)
    if start is None or start >= ex.goto_time:
        return None
    return (start, ex.goto_time)


def song_time_signatures(song: SongData) -> list:
    """The song's time signatures, (tick, numerator, denominator) on the
    playback clock, read back from the bar marks mid2agb wrote into the .s
    (the same reading a full export writes back). [] = 4/4 throughout."""
    if not song.tracks:
        return []
    expanded = [_expand_track(t) for t in song.tracks]
    ref = max(expanded[1:] or expanded, key=lambda e: len(e.bar_times))
    return _time_signatures(ref.bar_times)


def valid_time_signature(num: int, den: int) -> bool:
    """mid2agb can lay bars out for it: a power-of-two denominator and a bar
    length 96 * num / den that is a whole number of ticks."""
    return (1 <= num <= 255 and den in (1, 2, 4, 8, 16, 32, 64)
            and (96 * num) % den == 0)


def bar_starts_for(signatures: list, end: int) -> list:
    """Bar lines up to *end* for a list of time signatures, by mid2agb's rule:
    a bar every 96 * num / den ticks from the last signature (4/4 = 96 before
    the first one), and every signature itself starts a bar."""
    sigs = sorted(signatures)
    segments = [] if sigs and sigs[0][0] == 0 else [(0, 96)]
    segments += [(t, max(1, 96 * num // den)) for t, num, den in sigs]
    out = []
    for k, (start, length) in enumerate(segments):
        stop = segments[k + 1][0] if k + 1 < len(segments) else end + 1
        t = start
        while t < stop:
            out.append(t)
            t += length
    return sorted(set(out))


def song_bar_starts(song: SongData) -> list:
    """Playback tick of every bar line in track 1 — the bars the build marks in
    the .s from the song's time signatures — or [] when the song has none."""
    if not song.tracks:
        return []
    return sorted(set(_expand_track(song.tracks[0]).bar_times.values()))


def song_structure(song: SongData) -> dict:
    """Track 1's structure on the playback clock, for display: ``loop``
    (start, end) or None, ``sections`` [(tick, label)] for labels that are
    neither a pattern nor the loop start, ``patterns`` [(tick, label)] — every
    time a pattern is played (its first, written-out playing included) — and
    ``end``, the song's length. Labels are the file's; ticks are when it plays."""
    out = {'loop': song_loop_region(song), 'sections': [], 'patterns': [], 'end': 0}
    if not song.tracks:
        return out
    ex = _expand_track(song.tracks[0])
    called = {lbl for _t, lbl in ex.pattern_calls}
    for lbl, t in ex.label_times.items():
        if lbl in called:
            out['patterns'].append((t, lbl))
        elif lbl != ex.goto_target and (ex.goto_time is None or t < ex.goto_time):
            # (A label at or after the loop back is never reached by playing.)
            out['sections'].append((t, lbl))
    # A pattern first reached through a call has its label time = that call.
    out['patterns'] = sorted(set(out['patterns']) | set(ex.pattern_calls))
    out['sections'].sort()
    out['end'] = ex.end_time
    return out


def _apply_track_edit(ex: '_Expanded', te: TrackEdit) -> None:
    played = ex.events[:ex.played_count]
    post = ex.events[ex.played_count:]
    seq = max((s for _t, s, _c in ex.events), default=0) + 1
    end_note = 0

    def place(t, cmd, item=None):
        """Put an event on the timeline. A reused original keeps its own place
        (its tick and order among same-tick events); a new one goes after."""
        nonlocal seq
        if item is None:
            item = (t, seq, cmd)
            seq += 1
        (post if ex.goto_time is not None and t >= ex.goto_time else played).append(item)

    if te.notes is not None:
        # Reuse the song's own command for a note the editor left as it was, so
        # what the editor cannot show (gate time, the exact source line) stays.
        originals: dict = {}
        for item in played:
            t, _s, c = item
            if c.cmd == 'NOTE' and c.pitch is not None:
                key = (t, c.pitch, int(c.duration or 0), c.velocity or 100)
                originals.setdefault(key, []).append(item)
        # The editor's notes replace the notes that PLAY. Notes after the loop's
        # GOTO never play and an editor does not show them, so they are kept.
        played = [e for e in played if e[2].cmd not in _NOTE_KINDS]
        for n in te.notes:
            t = int(n['tick'])
            pitch = int(n['pitch'])
            dur = max(1, int(n.get('duration') or 1))
            vel = int(n.get('velocity') or 100)
            same = originals.get((t, pitch, dur, vel))
            if same:
                item = same.pop(0)
                place(t, item[2], item)
            else:
                place(t, TrackCommand(cmd='NOTE', tick=t, duration=dur,
                                      pitch=pitch, velocity=vel))
            end_note = max(end_note, t + dur)

    if te.controls is not None:
        originals = {}
        for item in played:
            t, _s, c = item
            if c.cmd in EDITABLE_CONTROLS and t > 0:
                originals.setdefault((t, c.cmd, c.value), []).append(item)
        played = [e for e in played if not (e[2].cmd in EDITABLE_CONTROLS and e[0] > 0)]
        for e in te.controls:
            kind, t = e.get('type'), int(e.get('tick', 0))
            if kind not in EDITABLE_CONTROLS or t <= 0:
                continue
            same = originals.pop((t, kind, e.get('value')), None)
            if same:
                # The editor keeps one copy of identical events at one tick;
                # put back every copy the song had.
                for item in same:
                    place(t, item[2], item)
            else:
                place(t, TrackCommand(cmd=kind, tick=t, value=e.get('value')))

    played.sort(key=lambda e: (e[0], e[1]))
    post.sort(key=lambda e: (e[0], e[1]))
    ex.events = played + post
    ex.played_count = len(played)
    if ex.goto_time is None:
        ex.end_time = max(ex.end_time, end_note)


def _apply_loop(ex: '_Expanded', start, end) -> None:
    events = sorted(ex.events, key=lambda e: (e[0], e[1]))
    if start is None or end is None or end <= start:
        ex.goto_time = None
        ex.goto_target = None
        ex.events = events
        ex.played_count = len(events)
        ex.end_time = max([ex.end_time] + [t for t, _s, _c in events])
        return
    played = [e for e in events if e[0] < end]
    ex.events = played + [e for e in events if e[0] >= end]
    ex.played_count = len(played)
    ex.goto_time = int(end)
    ex.goto_target = '__editor_loop__'
    ex.label_times['__editor_loop__'] = int(start)
    ex.end_time = max(int(end), max([t for t, _s, _c in ex.events], default=0))


def song_to_midi(song: SongData, edit: Optional[SongEdit] = None) -> mido.MidiFile:
    """Convert a parsed/edited SongData into a playable mido.MidiFile.

    Returns a Type-1 MIDI with one conductor track (tempo, loop markers) plus
    one track per SongData.Track. Every track is walked on a real playback
    clock (``_expand_track``): pattern calls play their pattern and take its
    time, so nothing after a repeat slides earlier and no repeat goes silent.
    *edit* applies an editor's changes on that clock (see ``expand_song``).
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=_MIDI_TPB)
    expanded = expand_song(song, edit)

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

    # Every TEMPO command is kept, a repeat of the same value included: each
    # one is a command in the song, and dropping "redundant" ones removed
    # them from the .s on the next rebuild. A song with no tempo gets none
    # (an SMF without one already means 120 BPM; inventing one put a TEMPO
    # command into songs that never had it).
    for ex in expanded:
        for t, _seq, cmd in ex.events:
            if cmd.cmd != 'TEMPO':
                continue
            bpm = _tempo_bpm(cmd, tbs)
            if bpm <= 0:
                continue
            tempo_events.append((t * _SCALE, int(round(60_000_000 / bpm))))
    tempo_events.sort(key=lambda e: e[0])

    # Loop markers go on the CONDUCTOR track, ONCE: mid2agb reads '[' (LoopBegin)
    # and ']' (LoopEnd) here and applies the loop to EVERY instrument track.
    # All tracks loop at the same point, so use track 0's loop: the LABEL a
    # GOTO jumps back to is the loop start; that GOTO is the loop end. Both are
    # positions on the PLAYBACK clock (repeats included).
    loop_start = loop_end = None
    if expanded and expanded[0].goto_target:
        ls = expanded[0].label_times.get(expanded[0].goto_target)
        le = expanded[0].goto_time
        loop_start = None if ls is None else ls * _SCALE
        loop_end = None if le is None else le * _SCALE

    cond_events: list[tuple[int, mido.MetaMessage]] = [
        (t, mido.MetaMessage('set_tempo', tempo=us, time=0)) for t, us in tempo_events]
    # Only emit loop markers for a valid forward span — a mis-paired GOTO/LABEL
    # (start >= end) would put ']' before '[' and make mid2agb rebuild the wrong
    # loop on a fresh clone. Bail rather than emit a broken loop.
    if loop_start is not None and loop_end is not None and 0 <= loop_start < loop_end:
        cond_events.append(
            (loop_start, mido.MetaMessage('marker', text='[', time=0)))
        cond_events.append(
            (loop_end, mido.MetaMessage('marker', text=']', time=0)))
    # Time signatures, read back from the bar comments. mid2agb merges the
    # tempo events into track 1 only, so track 1's bar marks also depend on
    # whether a tempo came before a time signature at the same tick (it then
    # adds its own bar line first). Read the signatures from another track,
    # and where track 1 shows an extra mark, put the tempo first there.
    tempo_first: set[int] = set()
    new_sigs = getattr(edit, 'time_signatures', None) if edit is not None else None
    if new_sigs is not None:
        # The editor's own list, as given.
        for t, num, den in sorted(new_sigs):
            cond_events.append((t * _SCALE, mido.MetaMessage(
                'time_signature', numerator=num, denominator=den, time=0)))
    elif expanded:
        ref = max(expanded[1:] or expanded, key=lambda e: len(e.bar_times))
        for t, num, den in _time_signatures(ref.bar_times):
            cond_events.append((t * _SCALE, mido.MetaMessage(
                'time_signature', numerator=num, denominator=den, time=0)))
        if ref is not expanded[0]:
            first = list(expanded[0].bar_times.values())
            other = list(ref.bar_times.values())
            tempo_first = {t * _SCALE for t in set(first) if first.count(t) > other.count(t)}

    def _cond_order(e):
        t, msg = e
        if msg.type == 'time_signature':
            return (t, 1 if t in tempo_first else 0)
        if msg.type == 'set_tempo' and t in tempo_first:
            return (t, 0)
        return (t, 2)
    cond_events.sort(key=_cond_order)

    prev_tick = 0
    for abs_tick, msg in cond_events:
        msg.time = max(0, abs_tick - prev_tick)
        conductor.append(msg)
        prev_tick = abs_tick

    conductor.append(mido.MetaMessage('end_of_track', time=0))
    mid.tracks.append(conductor)

    # ---- one MidiTrack per SongData track ----------------------------------
    # The song's master volume is needed to un-scale an EDITED VOL back to a
    # raw cc7 (see _raw_vol) so mid2agb's re-scaling on rebuild round-trips.
    master = getattr(song, 'master_volume', None)
    for ti, (track, ex) in enumerate(zip(song.tracks, expanded)):
        mid.tracks.append(_build_midi_track(track, ti, master, ex))

    return mid


def _tempo_bpm(cmd: TrackCommand, tbs: int) -> float:
    """BPM of a TEMPO command. The source line ``TEMPO , 129*x_tbs/2`` names the
    BPM directly; the parsed value (BPM*tbs/2, integer) cannot hold an odd one,
    so the line wins when the command is unchanged since it was read."""
    if cmd.raw_line and cmd.value == cmd.parsed_value:
        m = _RAW_TEMPO.search(cmd.raw_line)
        if m:
            return float(m.group(1))
    raw = int(cmd.value or 0)
    return (raw * 2) / tbs if raw > 0 else 0.0


class _Expanded:
    """One track laid out on the playback clock (M4A ticks)."""
    __slots__ = ('events', 'label_times', 'end_time', 'goto_time', 'goto_target',
                 'bar_times', 'wait_splits', 'played_count', 'pattern_calls')

    def __init__(self):
        self.events: list[tuple[int, int, TrackCommand]] = []
        self.label_times: dict[str, int] = {}
        self.end_time = 0
        self.goto_time = None
        self.goto_target = None
        # How many of `events` the engine actually plays: everything before
        # the loop GOTO (what follows it is data the engine never reaches).
        self.played_count = 0
        self.bar_times: dict[int, int] = {}   # bar number -> playback tick
        self.wait_splits: list[tuple[int, int]] = []  # (tick, command index) where two waits meet
        self.pattern_calls: list[tuple[int, str]] = []  # (playback tick, pattern label) per PATT


_MAX_EXPAND_STEPS = 2_000_000
_MAX_PATTERN_DEPTH = 4


def _expand_track(track: Track) -> _Expanded:
    """Walk a track the way the M4A engine plays it.

    The parser's ``cmd.tick`` is a position in the TEXT: only WAIT advances it,
    so a PATT (play a pattern, then come back) records zero time. Here a PATT
    jumps into the pattern and PEND returns, so the pattern's notes are heard
    each time it is called, at the time they are heard. A PEND reached without
    a call (the pattern's own first, inline playing) simply continues.

    The loop GOTO marks the loop end, but the walk carries on to FINE: mid2agb
    puts events that sit exactly at the loop end after the GOTO, and they are
    part of the song data. FINE's time is the track's length, silence included.

    Bar comments (``Track.bar_marks``) are timed on the way, on the first,
    linear pass over them.
    """
    ex = _Expanded()
    cmds = track.commands
    label_idx: dict[str, int] = {}
    for i, c in enumerate(cmds):
        if c.cmd == 'LABEL' and c.target_label and c.target_label not in label_idx:
            label_idx[c.target_label] = i
    bars_at: dict[int, list[int]] = {}
    for bar_no, idx in getattr(track, 'bar_marks', None) or ():
        bars_at.setdefault(idx, []).append(bar_no)
    bar_idx = set(bars_at)
    t = 0
    i = 0
    seq = 0
    stack: list[int] = []
    steps = 0
    prev_wait = None   # the WAIT executed just before, if the last command was one
    while i < len(cmds):
        steps += 1
        if steps > _MAX_EXPAND_STEPS:
            _log.warning("Track %s: playback walk did not finish; truncated", track.label)
            break
        if not stack and i in bars_at:
            for bar_no in bars_at.pop(i):
                ex.bar_times.setdefault(bar_no, t)
        c = cmds[i]
        kind = c.cmd
        if kind == 'WAIT':
            # Two waits in a row with no bar line between them and the first
            # not a full W96: mid2agb split the wait there because the source
            # MIDI had an event at that tick that prints nothing (a DAW's
            # RPN, sustain, ...). Remember it, so the rebuild splits it too.
            if prev_wait is not None and i not in bar_idx and int(prev_wait.duration or 0) < 96:
                ex.wait_splits.append((t, i))
            t += int(c.duration or 0)
            prev_wait = c
            i += 1
            continue
        prev_wait = None
        if kind == 'LABEL':
            if c.target_label:
                ex.label_times.setdefault(c.target_label, t)
            i += 1
        elif kind == 'PATT':
            j = label_idx.get(c.target_label)
            if j is None or len(stack) >= _MAX_PATTERN_DEPTH:
                _log.warning("Track %s: pattern %r cannot be played", track.label, c.target_label)
                i += 1
            else:
                ex.pattern_calls.append((t, c.target_label))
                stack.append(i + 1)
                i = j + 1
        elif kind == 'PEND':
            i = stack.pop() if stack else i + 1
        elif kind == 'GOTO':
            if ex.goto_time is None:
                ex.goto_time = t
                ex.goto_target = c.target_label
                ex.played_count = len(ex.events)
            i += 1
        elif kind == 'FINE':
            break
        else:
            ex.events.append((t, seq, c))
            seq += 1
            i += 1
    for idx, bar_nos in bars_at.items():
        if idx >= len(cmds):
            for bar_no in bar_nos:
                ex.bar_times.setdefault(bar_no, t)
    ex.end_time = t
    if ex.goto_time is None:
        ex.played_count = len(ex.events)
    return ex


def _time_signatures(bar_times: dict[int, int]) -> list[tuple[int, int, int]]:
    """(tick, numerator, denominator) of each time-signature event the source
    MIDI had, read back from mid2agb's bar comments.

    mid2agb splits waits at every bar, names pattern labels after bar numbers
    and starts its pattern compression on bar lines, so a song only rebuilds
    the same way if its time signatures are written back.

    This replays mid2agb's own bar-line logic (midi.cpp InsertTimingEvents):
    a bar line falls every L ticks from the last time signature (L = 96 at
    the start, 4/4), and each time-signature event is itself one bar mark.
    Walking the marks the .s records, any mark the replay did not predict, or
    a bar whose length differs from the current L, is where the source had a
    time-signature event; several marks at one tick mean several events there.
    The length of the bar that follows gives the signature: L = 96 * num / den.
    """
    marks = sorted(bar_times.values())
    if not marks:
        return []
    distinct = sorted(set(marks))
    counts = {t: marks.count(t) for t in distinct}
    out: list[tuple[int, int, int]] = []
    cur_len = 96
    next_default = 0
    for k, t in enumerate(distinct):
        nxt = distinct[k + 1] if k + 1 < len(distinct) else None
        length = (nxt - t) if nxt is not None else None
        predicted = (t == next_default)
        if counts[t] == 1 and predicted and (length is None or length == cur_len):
            next_default = t + cur_len
            continue
        new_len = length if length else cur_len
        sig = _signature_for(new_len)
        if sig is None:
            next_default = t + cur_len
            continue
        for _ in range(counts[t]):
            out.append((t, sig[0], sig[1]))
        cur_len = new_len
        next_default = t + cur_len
    return out


def _signature_for(length: int):
    """(numerator, denominator) with 96 * num / den == length, or None."""
    for den in (4, 8, 16, 32, 64):
        num = length * den / 96
        if num == int(num) and 0 < num < 256:
            return int(num), den
    return None


# MIDI controllers mid2agb turns back into M4A commands (tools/mid2agb/agb.cpp,
# PrintControllerOp). Each must be written, or the command silently vanishes
# from the song the next time mid2agb rebuilds it.
_CC_FOR = {
    'MOD': 1, 'BENDR': 20, 'LFOS': 21, 'MODT': 22, 'TUNE': 24, 'LFODL': 26, 'PRIO': 33,
}
# XCMD sub-commands: CC30 selects the extended command, CC29 carries its value.
_XCMD_SELECT = {'xIECV': 8, 'xIECL': 9}
# MEMACC operations mid2agb can print (agb.cpp PrintMemAcc; MPlayDef.s values).
_MEMACC_OPS = {'mem_set': 0, 'mem_add': 1, 'mem_sub': 2,
               'mem_mem_set': 3, 'mem_mem_add': 4, 'mem_mem_sub': 5}
# Placeholder for a wait split: CC3 and CC9 are undefined in General MIDI and
# mid2agb ignores both, so they mark a tick without changing any sound.
_SPLIT_CC = (3, 9)

_RAW_VOL = re.compile(r'(\d+)\s*\*\s*\w+_mvl\s*/\s*mxv')
_RAW_TEMPO = re.compile(r'TEMPO\s*,\s*(\d+)\s*\*')


def _build_midi_track(track: Track, track_index: int,
                      master_volume: int | None = None,
                      ex: _Expanded | None = None) -> mido.MidiTrack:
    """Convert one Track to a mido.MidiTrack of note + control events."""
    if ex is None:
        ex = _expand_track(track)
    mt = mido.MidiTrack()

    # Channel: the .s comment "Midi-Chn.N" is 1-based (mid2agb prints the MIDI
    # channel + 1), so channel N-1 rebuilds the same comment. Without a
    # comment, fall back to a per-track default that skips channel 10 (GM drums).
    if track.midi_channel is not None:
        channel = max(0, min(15, int(track.midi_channel) - 1))
    else:
        channel = track_index if track_index < 9 else track_index + 1
        channel = max(0, min(15, channel))

    # Sort key (abs_tick, order, seq): at one tick a note_off goes first so a
    # re-strike of the same pitch isn't cancelled by the old note's end; then
    # everything else in the order it appears in the song.
    events: list[tuple[int, int, int, mido.BaseMessage]] = []

    def add(tick, seq, msg, order=1):
        events.append((tick, order, seq, msg))

    add(0, -1, mido.MetaMessage(
        'track_name', name=track.label or f'track_{track_index}', time=0), order=-1)

    # TIE/EOT pairs are stitched into a single note_on/note_off span. An EOT
    # with no key ends the track's last key (that is what the engine does).
    open_ties: list[int] = []
    last_key = None

    # mid2agb writes "VOL , 127*<song>_mvl/mxv" ahead of KEYSH when a track
    # has no volume of its own before its first note (agb.cpp PrintAgbTrack).
    # That line is mid2agb's, not the music's: writing it into the MIDI would
    # make the rebuilt track carry a real volume event in a new place.
    # Recognised by all three marks mid2agb leaves: it is the first VOL, it
    # comes before the track's KEYSH, and its level is exactly 127.
    synthetic_vol = None
    if any(c.cmd == 'KEYSH' for c in track.commands):
        for c in track.commands:
            if c.cmd == 'KEYSH':
                break
            if c.cmd == 'VOL':
                if _raw_vol(c, None) == 127 and c.raw_line and c.value == c.parsed_value:
                    synthetic_vol = c
                break

    for tick_m4a, seq, cmd in ex.events:
        tick = int(tick_m4a) * _SCALE
        kind = cmd.cmd
        if cmd is synthetic_vol:
            continue

        if kind == 'NOTE':
            pitch = _clamp_pitch(cmd.pitch)
            if pitch is None:
                continue
            last_key = pitch
            velocity = _clamp_velocity(cmd.velocity)
            # gtp1..gtp3 lengthen the note by 1-3 ticks (exact gate time).
            dur = max(1, int(cmd.duration or 24)) + int(cmd.gate_time or 0)
            add(tick, seq, mido.Message('note_on', channel=channel, note=pitch,
                                        velocity=velocity, time=0))
            add(tick + dur * _SCALE, seq, mido.Message(
                'note_off', channel=channel, note=pitch, velocity=64, time=0), order=0)

        elif kind == 'TIE':
            pitch = _clamp_pitch(cmd.pitch)
            if pitch is None:
                continue
            last_key = pitch
            open_ties.append(pitch)
            add(tick, seq, mido.Message('note_on', channel=channel, note=pitch,
                                        velocity=_clamp_velocity(cmd.velocity), time=0))

        elif kind == 'EOT':
            pitch = _clamp_pitch(cmd.pitch)
            if pitch is None:
                pitch = last_key
            else:
                last_key = pitch
            if pitch is None or pitch not in open_ties:
                continue  # loose EOT — nothing to terminate
            open_ties.remove(pitch)
            add(tick, seq, mido.Message('note_off', channel=channel, note=pitch,
                                        velocity=64, time=0), order=0)

        elif kind == 'VOICE':
            # cmd.value of 0 is a valid voice (voice slot 0).  Use explicit
            # None check, not `or` — `0 or 100` would silently mangle a
            # legitimate value-0 to the default.
            v = 0 if cmd.value is None else int(cmd.value)
            add(tick, seq, mido.Message('program_change', channel=channel,
                                        program=max(0, min(127, v)), time=0))

        elif kind == 'VOL':
            add(tick, seq, mido.Message('control_change', channel=channel, control=7,
                                        value=_raw_vol(cmd, master_volume), time=0))

        elif kind == 'PAN':
            # PAN=0 is hard-left (valid).  Use None check, not `or`.
            v = 64 if cmd.value is None else int(cmd.value)
            add(tick, seq, mido.Message('control_change', channel=channel, control=10,
                                        value=max(0, min(127, v)), time=0))

        elif kind == 'BEND':
            # M4A BEND value is c_v-based: 64 = center, 0 = -64, 127 = +63.
            # mid2agb reads the high 7 bits back (agb.cpp: param2 - 64).
            # BEND=0 (hard-down) is valid — use None check.
            v = 64 if cmd.value is None else int(cmd.value)
            pb = max(-8192, min(8191, (v - 64) * 128))
            add(tick, seq, mido.Message('pitchwheel', channel=channel, pitch=pb, time=0))

        elif kind in _CC_FOR:
            if cmd.value is None:
                continue
            add(tick, seq, mido.Message('control_change', channel=channel,
                                        control=_CC_FOR[kind],
                                        value=max(0, min(127, int(cmd.value))), time=0))

        elif kind == 'XCMD':
            sel = _XCMD_SELECT.get(getattr(cmd, 'sub', None) or '')
            if sel is None or cmd.value is None:
                _log.warning("Track %s: XCMD %r has no MIDI form; dropped",
                             track.label, cmd.raw_line)
                continue
            add(tick, seq, mido.Message('control_change', channel=channel, control=30,
                                        value=sel, time=0))
            add(tick, seq, mido.Message('control_change', channel=channel, control=29,
                                        value=max(0, min(127, int(cmd.value))), time=0))

        elif kind == 'MEMACC':
            # mid2agb: CC13 = operation, CC14 = first operand, then CC12 with
            # the value prints the command (agb.cpp PrintMemAcc). Only the six
            # operations mid2agb can print have a MIDI form.
            op = _MEMACC_OPS.get(getattr(cmd, 'sub', None) or '')
            if op is None or cmd.value is None or getattr(cmd, 'arg1', None) is None:
                _log.warning("Track %s: MEMACC %r has no MIDI form; dropped",
                             track.label, cmd.raw_line)
                continue
            for control, value in ((13, op), (14, int(cmd.arg1)), (12, int(cmd.value))):
                add(tick, seq, mido.Message('control_change', channel=channel,
                                            control=control,
                                            value=max(0, min(127, value)), time=0))

        # TEMPO is hoisted to the conductor. KEYSH is not a MIDI event: mid2agb
        # writes "KEYSH , <song>_key+0" at every track's start by itself.

    # Wait splits (see _expand_track): controllers mid2agb prints nothing for
    # (PrintControllerOp's default case) and General MIDI leaves undefined, so
    # they change no sound anywhere — they only put the split back. Their
    # values come from the split's place in the .s: a pattern and each replay
    # of it carry the same values (mid2agb still sees them as one pattern),
    # while two look-alike bars mid2agb had kept apart stay apart.
    for t, idx in ex.wait_splits:
        for control, value in zip(_SPLIT_CC, (idx % 128, (idx // 128) % 128)):
            add(t * _SCALE, 1 << 29, mido.Message('control_change', channel=channel,
                                                  control=control, value=value, time=0))

    # A TIE never closed (corrupt input) ends with the track.
    for pitch in open_ties:
        add(ex.end_time * _SCALE, 1 << 30, mido.Message(
            'note_off', channel=channel, note=pitch, velocity=64, time=0), order=0)

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    # Absolute -> delta time.
    prev_tick = 0
    for abs_tick, _order, _seq, msg in events:
        msg.time = max(0, abs_tick - prev_tick)
        mt.append(msg)
        prev_tick = abs_tick

    # The track lasts until its GOTO / FINE, silence included — mid2agb turns
    # the gap back into the final waits.
    mt.append(mido.MetaMessage('end_of_track',
                               time=max(0, ex.end_time * _SCALE - prev_tick)))
    return mt


def _raw_vol(cmd: TrackCommand, master_volume) -> int:
    """The cc7 value for a VOL command: the raw multiplier mid2agb scales by mvl.

    The source line ``VOL , 97*x_mvl/mxv`` names it exactly. The parsed value is
    the master-scaled level, and un-scaling it (eff*127/mvl) lands one below in
    many songs, so every save drifted the volume down. The literal is used when
    the command is unchanged since it was read; an edited level is un-scaled.
    """
    if cmd.raw_line and cmd.value == cmd.parsed_value:
        m = _RAW_VOL.search(cmd.raw_line)
        if m:
            return max(0, min(127, int(m.group(1))))
    # VOL=0 is silence (valid).  Use None check, not `or`.
    eff = 100 if cmd.value is None else int(cmd.value)
    # The parser stores the EFFECTIVE level it read from the .s expression
    # "<mult>*mvl/mxv" (already master-scaled). mid2agb re-applies the master
    # (-V) when it regenerates the .s on build, so an edited level must be
    # stored as the RAW multiplier (eff*mxv/mvl, mxv=127) — otherwise the
    # master is applied twice and every save quietens the track.
    # The level the .s gives is raw*mvl/mxv rounded DOWN (the assembler's and
    # the reader's integer division), so the raw multiplier must be rounded UP:
    # the smallest raw that plays at least eff plays exactly eff (for any eff
    # up to the master). Rounding to nearest landed one below for 3,906 of the
    # possible (master, level) pairs (41 -> raw 52 -> plays 40 at mvl 100).
    if master_volume and 0 < int(master_volume) < 127:
        raw = -(-eff * 127 // int(master_volume))
    else:
        raw = eff
    return max(0, min(127, raw))


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
