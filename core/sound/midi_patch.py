"""Apply an editor's edit to a song's EXISTING .mid, changing only what was edited.

Why
===
A song's .mid often carries more than its .s: track names, time signatures,
controllers mid2agb ignores (bank select, RPN bend range, CC127, …), notes on
tracks the build skips, events written by whichever DAW made it. None of that
changes the game, all of it is the user's editable project. Replacing the .mid
with a fresh export of the .s (what a Piano Roll edit did until 2026-11-15)
lost it: measured on this project, a re-export changes track names in 340 of
383 songs, drops time-signature events in 99 and unknown controllers in 63+.

How (independently designed from the MIDI 1.0 file format and mid2agb's
documented behaviour; see BUGS.md 2026-11-15 for the clean-room record)
===
1. Both versions of the song — as on disk, and as edited — are laid out on the
   playback clock (midi_exporter.expand_song), in M4A ticks.
2. Per track, the two are compared as multisets of musical events: notes as
   (start, pitch, velocity, length) and every other command as (kind, time,
   value). The difference is the edit: what was removed and what was added.
3. Each .s track is the k-th (MIDI track, channel) pair that carries notes, in
   file order (mid2agb's rule; it matches all 383 songs here). The removed
   events are found in that track/channel — by time converted with mid2agb's
   own formula, kind and value — and deleted; the added ones are inserted.
   Tempo and the loop markers are handled in the first MIDI track (the one
   mid2agb reads them from). A track the editor added becomes a new MIDI
   track; one it removed has its channel's events taken out. Tracks the
   editor moved have their MIDI tracks put in the new order (see
   ``_reorder_chunks``).
4. Every other event stays exactly where and as it was. If any removed event
   cannot be found, or an added one cannot be placed at an exact MIDI tick,
   ``MidiPatchUnavailable`` is raised: the caller must not guess.

The caller (safe_song_save.save_editor_song) still runs mid2agb on the result
and checks the build plays exactly what the editor holds before writing.
"""

from __future__ import annotations

import io
from collections import Counter
from fractions import Fraction

import mido


class MidiPatchUnavailable(Exception):
    """The edit cannot be applied to the existing .mid without guessing."""


# MIDI controller per M4A command (mid2agb reads these back; agb.cpp).
_CC = {'VOL': 7, 'PAN': 10, 'MOD': 1, 'BENDR': 20, 'LFOS': 21, 'MODT': 22,
       'TUNE': 24, 'LFODL': 26, 'PRIO': 33}
_XCMD_SELECT = {'xIECV': 8, 'xIECL': 9}
_MEMACC_OPS = {'mem_set': 0, 'mem_add': 1, 'mem_sub': 2,
               'mem_mem_set': 3, 'mem_mem_add': 4, 'mem_mem_sub': 5}
_NOTE = frozenset(('NOTE', 'TIE', 'EOT'))


# ─────────────────────────────────────────────────────────────────────────────
# The two versions as event multisets
# ─────────────────────────────────────────────────────────────────────────────

def _track_events(ex, master, tbs):
    """(notes Counter, commands Counter, tempo Counter) for one expanded track.

    Notes: (start, pitch, velocity, length) — a TIE/EOT pair is one note.
    Commands: (kind, time, value, sub, arg1) with VOL as its raw level and
    TEMPO kept apart as (time, bpm)."""
    from core.sound.midi_exporter import _raw_vol, _tempo_bpm, _clamp_velocity
    from core.sound.safe_song_save import _HeldEvent
    from core.sound.tie_pairing import tie_spans
    notes, cmds, tempo = Counter(), Counter(), Counter()
    # Held notes are paired after the walk, by the one shared rule, so this
    # describes a song exactly as the save check does — BUGS.md 2026-11-18,
    # where four pairings in four places disagreed and a song could not save.
    held: list = []
    for t, _s, c in ex.events:
        k = c.cmd
        if k == 'NOTE':
            if c.pitch is None:
                continue
            held.append(_HeldEvent(k, t, c.pitch, c.velocity))
            notes[(t, c.pitch, _clamp_velocity(c.velocity),
                   int(c.duration or 0) + int(c.gate_time or 0))] += 1
        elif k in ('TIE', 'EOT'):
            held.append(_HeldEvent(k, t, c.pitch, c.velocity))
        elif k == 'TEMPO':
            tempo[(t, round(_tempo_bpm(c, tbs)))] += 1
        elif k == 'KEYSH':
            continue
        else:
            value = _raw_vol(c, master) if k == 'VOL' else c.value
            cmds[(k, t, value, getattr(c, 'sub', None), getattr(c, 'arg1', None))] += 1
    for t0, p, v, t1 in tie_spans(held, end_tick=ex.end_time):
        notes[(t0, p, _clamp_velocity(v), t1 - t0)] += 1
    return notes, cmds, tempo


# ─────────────────────────────────────────────────────────────────────────────
# The MIDI file as editable, absolute-time event lists
# ─────────────────────────────────────────────────────────────────────────────

class _Track:
    def __init__(self, mtrack):
        self.items = []          # [abs_tick, order_class, seq, msg, alive]
        t = 0
        for i, msg in enumerate(mtrack):
            t += msg.time
            self.items.append([t, 1, i, msg, True])
        self.extra = len(self.items)

    def insert(self, abs_tick, msg, before=False):
        # Inserted note-offs go ahead of what is already at that tick (a note
        # ending exactly where another starts); everything else after it.
        self.items.append([abs_tick, 0 if before else 2, self.extra, msg, True])
        self.extra += 1

    def to_midi(self):
        out = mido.MidiTrack()
        live = sorted((it for it in self.items if it[4]), key=lambda it: (it[0], it[1], it[2]))
        eot = [it for it in live if it[3].type == 'end_of_track']
        body = [it for it in live if it[3].type != 'end_of_track']
        end = max([it[0] for it in live] or [0])
        prev = 0
        for it in body:
            m = it[3].copy(time=it[0] - prev)
            out.append(m)
            prev = it[0]
        last = eot[-1] if eot else None
        out.append(mido.MetaMessage('end_of_track', time=max(0, end - prev))
                   if last is None else last[3].copy(time=max(0, end - prev)))
        return out


def _note_pairs(track: _Track, channel):
    """Notes on *channel* as (on_item, off_item) — off matched first-in first-out."""
    pending: dict = {}
    pairs = []
    for it in track.items:
        m = it[3]
        if getattr(m, 'channel', None) != channel or m.type not in ('note_on', 'note_off'):
            continue
        if m.type == 'note_on' and m.velocity > 0:
            pending.setdefault(m.note, []).append(it)
        else:
            if pending.get(m.note):
                pairs.append((pending[m.note].pop(0), it))
    for _n, ons in pending.items():
        pairs.extend((on, None) for on in ons)
    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# The patch
# ─────────────────────────────────────────────────────────────────────────────

def patch_midi(original: bytes, pristine, edited, edit, mapped: dict) -> bytes:
    """Return *original* (.mid bytes) with exactly the edit applied.

    *pristine* is the song as on disk, *edited* the editor's version, *edit*
    the ``midi_exporter.SongEdit`` and *mapped* {edited track index: pristine
    track index} for the tracks the two share (an edited track missing from it
    is new). Raises MidiPatchUnavailable rather than guess."""
    from core.sound.midi_exporter import expand_song, _expand_track
    mid = mido.MidiFile(file=io.BytesIO(original))
    if mid.type not in (0, 1):
        raise MidiPatchUnavailable("only MIDI format 0 and 1 files can be edited in place")
    tpb = mid.ticks_per_beat
    tbs = max(1, int(getattr(pristine, 'tempo_base', 1) or 1))
    tracks = [_Track(t) for t in mid.tracks]

    def agb(abs_tick):                       # mid2agb: (24 * tbs * t) / ticks-per-beat
        return (24 * tbs * abs_tick) // tpb

    def to_midi(t):
        v = Fraction(t * tpb, 24 * tbs)
        if v.denominator != 1:
            raise MidiPatchUnavailable(
                f"tick {t} has no exact position in this MIDI file's resolution ({tpb} per beat)")
        return int(v)

    # .s track k  <->  k-th (MIDI track, channel) that carries notes.
    pairs = []
    for ti, tr in enumerate(mid.tracks):
        chans = sorted({m.channel for m in tr if m.type == 'note_on' and m.velocity > 0})
        pairs += [(ti, ch) for ch in chans]
    if len(pairs) != len(pristine.tracks):
        raise MidiPatchUnavailable(
            f"the MIDI file has {len(pairs)} note tracks but the song has {len(pristine.tracks)}")

    p_ex = expand_song(pristine)
    e_ex = expand_song(edited, edit)
    master_p = getattr(pristine, 'master_volume', None)
    master_e = getattr(edited, 'master_volume', None)

    tempo_before, tempo_after = Counter(), Counter()
    for bi, ex in enumerate(p_ex):
        tempo_before += _track_events(ex, master_p, tbs)[2]
    for li, ex in enumerate(e_ex):
        tempo_after += _track_events(ex, master_e, tbs)[2]

    new_at: dict = {}
    for li, ex_after in enumerate(e_ex):
        if li not in mapped:
            new_at[li] = len(tracks)
            _append_new_track(mid, tracks, edited, li, ex_after, tpb, tbs)
            continue
        bi = mapped[li]
        mt, ch = pairs[bi]
        n_b, c_b, _ = _track_events(p_ex[bi], master_p, tbs)
        n_a, c_a, _ = _track_events(ex_after, master_e, tbs)
        synthetic = _synthetic_vol(pristine.tracks[bi])
        _patch_notes(tracks[mt], ch, n_b - n_a, n_a - n_b, agb, to_midi)
        _patch_commands(tracks[mt], ch, c_b - c_a, c_a - c_b, agb, to_midi, synthetic)

    removed_tracks = set(range(len(pristine.tracks))) - set(mapped.values())
    for bi in removed_tracks:
        mt, ch = pairs[bi]
        for it in tracks[mt].items:
            if it[4] and getattr(it[3], 'channel', None) == ch:
                it[4] = False

    _patch_tempo(tracks[0], tempo_before - tempo_after, tempo_after - tempo_before, agb, to_midi)
    if getattr(edit, 'loop', None) is not None:
        _patch_loop(tracks[0], edit.loop, to_midi)
    if getattr(edit, 'time_signatures', None) is not None:
        _patch_time_signatures(tracks, edit.time_signatures, to_midi)

    want = [pairs[mapped[li]] if li in mapped else (new_at[li], None)
            for li in range(len(e_ex))]
    order = _reorder_chunks(len(tracks), want)
    mid.tracks = [tracks[i].to_midi() for i in order]
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _reorder_chunks(n_chunks: int, want: list) -> list:
    """The order to write the MIDI tracks in so that mid2agb numbers the song's
    tracks as the editor has them.

    *want* is, per editor track in order, its (MIDI track index, channel).
    mid2agb numbers tracks by MIDI track in file order and, within one MIDI
    track, by channel. So the MIDI tracks holding notes are laid into the
    slots that held notes before, in the order of first appearance in *want*;
    every other MIDI track (the first one with tempo and loop markers, empty
    ones, ones whose track was removed) stays where it is.

    Refused when the order cannot be made by moving whole MIDI tracks: two
    editor tracks that share a MIDI track (different channels of one track)
    separated or swapped, or the first MIDI track — which holds the tempo
    map and the loop markers — moved."""
    chunk_order: list = []
    for mt, _ch in want:
        if chunk_order and chunk_order[-1] == mt:
            continue
        if mt in chunk_order:
            raise MidiPatchUnavailable(
                "the moved tracks share one MIDI track (different channels), so they cannot "
                "be put in this order by moving MIDI tracks")
        chunk_order.append(mt)
    for mt in chunk_order:
        chans = [ch for m, ch in want if m == mt]
        if chans != sorted(chans, key=lambda c: -1 if c is None else c):
            raise MidiPatchUnavailable(
                "two tracks that are channels of the same MIDI track cannot swap places")
    slots = sorted(chunk_order)
    if chunk_order == slots:
        return list(range(n_chunks))
    if 0 in slots and chunk_order[0] != 0:
        raise MidiPatchUnavailable(
            "the first MIDI track also holds the tempo and loop markers, so it cannot be "
            "moved behind another track")
    placed = dict(zip(slots, chunk_order))
    return [placed.get(i, i) for i in range(n_chunks)]


def _synthetic_vol(track):
    """mid2agb's own "VOL , 127" before KEYSH: in the .s, not in the MIDI."""
    if not any(c.cmd == 'KEYSH' for c in track.commands):
        return False
    for c in track.commands:
        if c.cmd == 'KEYSH':
            return False
        if c.cmd == 'VOL':
            return True
    return False


def agb_velocity(v: int) -> int:
    """The velocity mid2agb writes for MIDI velocity *v*: rounded UP to a
    multiple of 4, at most 127 (its note-velocity table; e.g. 93 -> 96)."""
    return 0 if v <= 0 else min(127, (v + 3) // 4 * 4)


def _patch_notes(track, ch, removed, added, agb, to_midi):
    if not removed and not added:
        return
    pairs = _note_pairs(track, ch)
    for (t, pitch, vel, length), n in removed.items():
        for _ in range(n):
            best = None
            for on, off in pairs:
                if (not on[4] or agb(on[0]) != t or on[3].note != pitch
                        or agb_velocity(on[3].velocity) != vel):
                    continue
                got = agb(off[0]) - agb(on[0]) if off is not None else None
                score = abs(got - length) if got is not None else 10 ** 6
                if best is None or score < best[0]:
                    best = (score, on, off)
            if best is None:
                raise MidiPatchUnavailable(
                    f"a note the edit removes (pitch {pitch} at tick {t}) is not in the MIDI file")
            best[1][4] = False
            if best[2] is not None:
                best[2][4] = False
    for (t, pitch, vel, length), n in added.items():
        for _ in range(n):
            start, end = to_midi(t), to_midi(t + max(1, length))
            track.insert(start, mido.Message('note_on', channel=ch, note=pitch, velocity=vel))
            track.insert(end, mido.Message('note_off', channel=ch, note=pitch, velocity=64),
                         before=True)


def _command_messages(kind, value, sub, arg1, ch):
    """The MIDI message(s) mid2agb turns into this command, in order."""
    if kind == 'VOICE':
        return [mido.Message('program_change', channel=ch, program=max(0, min(127, int(value))))]
    if kind == 'BEND':
        return [mido.Message('pitchwheel', channel=ch,
                             pitch=max(-8192, min(8191, (int(value) - 64) * 128)))]
    if kind in _CC:
        return [mido.Message('control_change', channel=ch, control=_CC[kind],
                             value=max(0, min(127, int(value))))]
    if kind == 'XCMD' and sub in _XCMD_SELECT:
        return [mido.Message('control_change', channel=ch, control=30, value=_XCMD_SELECT[sub]),
                mido.Message('control_change', channel=ch, control=29, value=int(value))]
    if kind == 'MEMACC' and sub in _MEMACC_OPS and arg1 is not None:
        return [mido.Message('control_change', channel=ch, control=13, value=_MEMACC_OPS[sub]),
                mido.Message('control_change', channel=ch, control=14, value=int(arg1)),
                mido.Message('control_change', channel=ch, control=12, value=int(value))]
    raise MidiPatchUnavailable(f"a {kind} command has no MIDI form")


def _same(a, b):
    """Two channel messages say the same thing to mid2agb."""
    if a.type != b.type or getattr(a, 'channel', None) != getattr(b, 'channel', None):
        return False
    if a.type == 'pitchwheel':
        return (a.pitch + 8192) >> 7 == (b.pitch + 8192) >> 7
    if a.type == 'program_change':
        return a.program == b.program
    if a.type == 'control_change':
        return a.control == b.control and a.value == b.value
    return False


def _patch_commands(track, ch, removed, added, agb, to_midi, synthetic):
    for (kind, t, value, sub, arg1), n in removed.items():
        for _ in range(n):
            want = _command_messages(kind, value, sub, arg1, ch)
            # The run of messages that makes the command, at that tick, on
            # this track's own channel.
            items = [it for it in track.items if it[4] and agb(it[0]) == t
                     and getattr(it[3], 'channel', None) == ch]
            found = None
            for i in range(len(items) - len(want) + 1):
                if all(_same(items[i + j][3], want[j]) for j in range(len(want))):
                    found = items[i:i + len(want)]
                    break
            if found is None:
                if kind == 'VOL' and t == 0 and synthetic and value == 127:
                    continue     # mid2agb wrote it itself; nothing in the MIDI to remove
                raise MidiPatchUnavailable(
                    f"a {kind} command the edit removes (tick {t}) is not in the MIDI file")
            for it in found:
                it[4] = False
    for (kind, t, value, sub, arg1), n in added.items():
        for _ in range(n):
            at = to_midi(t)
            for msg in _command_messages(kind, value, sub, arg1, ch):
                track.insert(at, msg)


def _patch_tempo(track, removed, added, agb, to_midi):
    for (t, bpm), n in removed.items():
        for _ in range(n):
            hit = next((it for it in track.items if it[4] and it[3].type == 'set_tempo'
                        and agb(it[0]) == t and round(60_000_000 / it[3].tempo) == bpm), None)
            if hit is None:
                raise MidiPatchUnavailable(f"a tempo the edit changes (tick {t}) is not in the MIDI file")
            hit[4] = False
    for (t, bpm), n in added.items():
        for _ in range(n):
            track.insert(to_midi(t), mido.MetaMessage('set_tempo', tempo=int(round(60_000_000 / bpm))))


def _patch_loop(track, loop, to_midi):
    for it in track.items:
        if it[4] and it[3].type == 'marker' and it[3].text in ('[', ']'):
            it[4] = False
    start, end = loop
    if start is not None and end is not None and end > start:
        track.insert(to_midi(start), mido.MetaMessage('marker', text='['))
        track.insert(to_midi(end), mido.MetaMessage('marker', text=']'))


def _patch_time_signatures(tracks, signatures, to_midi):
    """Replace the file's time-signature events with *signatures*, in the first
    MIDI track. The metronome fields (clocks per click, 32nds per quarter) of
    the file's first signature are kept; nothing else is touched."""
    keep = None
    for tr in tracks:
        for it in tr.items:
            if it[4] and it[3].type == 'time_signature':
                if keep is None:
                    keep = it[3]
                it[4] = False
    extra = {}
    if keep is not None:
        extra = {'clocks_per_click': keep.clocks_per_click,
                 'notated_32nd_notes_per_beat': keep.notated_32nd_notes_per_beat}
    for t, num, den in sorted(signatures):
        tracks[0].insert(to_midi(t), mido.MetaMessage(
            'time_signature', numerator=num, denominator=den, **extra))


def _append_new_track(mid, tracks, edited, li, ex, tpb, tbs):
    """A track the editor added: export it on its own, at this file's resolution."""
    from core.sound.midi_exporter import _build_midi_track, _MIDI_TPB
    used = {m.channel for t in mid.tracks for m in t if hasattr(m, 'channel')}
    free = next((c for c in range(16) if c not in used and c != 9), None)
    if free is None:
        raise MidiPatchUnavailable("no free MIDI channel left for a new track")
    track = edited.tracks[li]
    saved = track.midi_channel
    track.midi_channel = free + 1
    try:
        built = _build_midi_track(track, li, getattr(edited, 'master_volume', None), ex)
    finally:
        track.midi_channel = saved
    scale = Fraction(tpb, _MIDI_TPB * tbs)
    new = mido.MidiTrack()
    abs_src = 0
    prev = 0
    for m in built:
        abs_src += m.time
        v = abs_src * scale
        if v.denominator != 1:
            raise MidiPatchUnavailable("the new track has no exact position in this file's resolution")
        new.append(m.copy(time=int(v) - prev))
        prev = int(v)
    tracks.append(_Track(new))
