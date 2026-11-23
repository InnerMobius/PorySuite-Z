"""One rule for pairing a held note's start with its end.

mid2agb writes a note longer than one length-table step as ``TIE`` (start) and
``EOT`` (end), with other commands, other notes and whole pattern calls in
between. Reading a song back means deciding which EOT ends which TIE, and for
most music any sensible guess gives the same answer -- until two notes of the
SAME KEY overlap on one track, which is legal, which the engine plays, and
which other editors write.

PorySuite used to decide this in three places, three different ways. On
`mus_lab` the same untouched note measured 150 ticks to the save check, 384 to
the piano roll that drew it, and 192 after a MIDI round trip. The save check
compares two of those readings, so the song could not be saved at all --
BUGS.md 2026-11-18, "three different TIE/EOT pairings disagree".

THE RULE, which is the engine's own:

    A tie takes the first end on its OWN KEY that comes after it.

`src/m4a_1.s::ply_endtie` walks the track's channel list, stops the first
channel whose ``midiKey`` matches, and returns immediately -- one end stops one
note, the oldest still sounding on that key. So ties on a key are closed
first-in, first-out, and an end for a key nothing is holding is ignored. An
``EOT`` written with no key of its own means the track's running key, which is
what the engine reads it as.

A tie still open when the track stops (``FINE``, the loop's ``GOTO``, or the
end of the commands) ends there.

This is the same rule PoryDAW works to -- it notes that a pairing of
"every note-on takes the first same-key end after it" cannot represent two
overlapping notes on one key, and keeps overlaps out of its documents by
trimming as they are written. PorySuite reads songs other tools wrote, so it
cannot assume that: it has to read an overlap the way the engine plays it,
NOT repair it. A song that builds is a song PorySuite must handle.
"""

from __future__ import annotations

from typing import Optional

# Commands that stop a track, and so end anything it is still holding.
_STOPPERS = frozenset(('FINE', 'GOTO'))


def tie_spans(commands, end_tick: Optional[int] = None) -> list:
    """Every TIE/EOT pair in *commands*, in the order the ties start.

    Returns ``[(start_tick, pitch, velocity, end_tick)]``. *commands* is read
    in order and never modified. *end_tick* closes whatever is still held when
    the commands run out; without one the last command's tick is used.
    """
    return [(s, p, v, e) for s, p, v, e, _i in _spans(commands, end_tick)]


def tie_ends(commands, end_tick: Optional[int] = None) -> dict:
    """``{position of the TIE in *commands*: the tick it ends on}``.

    For callers walking the commands themselves, which need each tie's end
    where they meet the tie rather than as a separate list.
    """
    return {i: e for _s, _p, _v, e, i in _spans(commands, end_tick)}


def _spans(commands, end_tick: Optional[int] = None) -> list:
    """The pairing itself: ``[(start, pitch, velocity, end, tie position)]``."""
    open_by_pitch: dict = {}      # pitch -> [index into spans], oldest first
    spans: list = []
    last_key = None
    stop_tick = None

    for position, cmd in enumerate(commands):
        kind = getattr(cmd, 'cmd', None)
        tick = getattr(cmd, 'tick', 0)

        if kind == 'NOTE':
            # Not held, so it pairs with nothing -- but it does set the
            # running key, which a keyless EOT later reads.
            if getattr(cmd, 'pitch', None) is not None:
                last_key = cmd.pitch

        elif kind == 'TIE':
            pitch = getattr(cmd, 'pitch', None)
            if pitch is None:
                pitch = last_key
            if pitch is None:
                continue
            last_key = pitch
            vel = getattr(cmd, 'velocity', None) or 100
            spans.append([tick, pitch, vel, None, position])
            open_by_pitch.setdefault(pitch, []).append(len(spans) - 1)

        elif kind == 'EOT':
            pitch = getattr(cmd, 'pitch', None)
            if pitch is None:
                pitch = last_key
            else:
                last_key = pitch
            waiting = open_by_pitch.get(pitch)
            if not waiting:
                continue          # an end for a key nothing is holding
            spans[waiting.pop(0)][3] = tick

        elif kind in _STOPPERS:
            stop_tick = tick
            break

    if stop_tick is None:
        stop_tick = end_tick
    if stop_tick is None:
        ticks = [getattr(c, 'tick', 0) for c in commands]
        stop_tick = max(ticks) if ticks else 0

    for span in spans:
        if span[3] is None:
            span[3] = stop_tick

    return [tuple(span) for span in spans]


def tie_notes(commands, end_tick: Optional[int] = None) -> list:
    """`tie_spans` as the piano roll's note dicts.

    A span that ends where it starts, or earlier, is dropped: it is not a
    note, and drawing it as one would put a zero-width block on the grid.
    """
    out = []
    for start, pitch, velocity, end in tie_spans(commands, end_tick):
        if end > start:
            out.append({'tick': start, 'pitch': pitch,
                        'duration': end - start, 'velocity': velocity})
    return out
