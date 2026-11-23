"""A held note's start and its end are paired ONE way, everywhere.

BUGS.md 2026-11-18, "three different TIE/EOT pairings disagree". `mus_lab`
could not be saved at all: the same untouched note measured 150 ticks to the
save check, 384 to the piano roll that drew it, and 192 after a MIDI round
trip, so the check -- which compares what the editor holds against what the
rebuilt file would play -- refused every time. The song itself was fine; it
was imported through PoryDAW and the game builds it.

What made the pairing ambiguous is two notes of the SAME KEY overlapping on
one track. That is legal, the engine plays it, and other editors write it.

THE RULE, taken from the engine: a tie takes the first end on its own key that
comes after it. `src/m4a_1.s::ply_endtie` stops the first channel whose key
matches and returns, so ties on a key close first-in, first-out, an end for a
key nothing holds is ignored, and a keyless end means the running key.

WHAT MUST NOT BREAK:

* One rule. If a second pairing appears anywhere, this file's cross-checks
  are what catch it before a user's song stops saving.
* An overlap is READ, never repaired. It is the user's music and the engine
  plays it.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GAME = os.path.join(ROOT_DIR, 'pokefirered')
MIDI_DIR = os.path.join(GAME, 'sound', 'songs', 'midi')

from core.sound.tie_pairing import (  # noqa: E402
    tie_ends, tie_notes, tie_spans)


class C:
    """A stand-in for one parsed command."""

    def __init__(self, cmd, tick, pitch=None, velocity=None):
        self.cmd, self.tick, self.pitch, self.velocity = cmd, tick, pitch, velocity


# ── The rule ────────────────────────────────────────────────────────────────

def test_a_tie_takes_the_first_end_on_its_own_key():
    spans = tie_spans([C('TIE', 0, 60, 100), C('EOT', 48, 60)])
    assert spans == [(0, 60, 100, 48)]


def test_two_overlapping_notes_on_one_key_pair_oldest_first():
    """The case mus_lab could not save. Both notes survive, in order."""
    spans = tie_spans([C('TIE', 2688, 55, 100), C('TIE', 2784, 55, 127),
                       C('EOT', 2838, 55), C('EOT', 3072, 55)])
    assert spans == [(2688, 55, 100, 2838), (2784, 55, 127, 3072)]


def test_an_end_does_not_reach_past_its_own_key():
    """A different key's end must not close this note."""
    spans = tie_spans([C('TIE', 0, 60, 100), C('EOT', 24, 67), C('EOT', 48, 60)])
    assert spans == [(0, 60, 100, 48)]


def test_a_keyless_end_means_the_running_key():
    """What the engine reads it as."""
    spans = tie_spans([C('TIE', 0, 60, 100), C('EOT', 48, None)])
    assert spans == [(0, 60, 100, 48)]


def test_a_plain_note_sets_the_running_key_too():
    spans = tie_spans([C('TIE', 0, 60, 100), C('NOTE', 12, 72, 90),
                       C('EOT', 48, None)])
    assert spans == [(0, 60, 100, 48)]


def test_an_end_for_a_key_nothing_holds_is_ignored():
    spans = tie_spans([C('EOT', 10, 60), C('TIE', 20, 60, 100), C('EOT', 44, 60)])
    assert spans == [(20, 60, 100, 44)]


def test_a_note_still_held_at_the_end_stops_there():
    spans = tie_spans([C('TIE', 0, 60, 100)], end_tick=96)
    assert spans == [(0, 60, 100, 96)]


def test_a_note_still_held_when_the_track_stops_ends_there():
    spans = tie_spans([C('TIE', 0, 60, 100), C('GOTO', 384)], end_tick=99999)
    assert spans == [(0, 60, 100, 384)]


def test_nothing_after_the_track_stops_is_read():
    spans = tie_spans([C('TIE', 0, 60, 100), C('GOTO', 384),
                       C('TIE', 400, 67, 100), C('EOT', 440, 67)])
    assert spans == [(0, 60, 100, 384)]


def test_a_span_with_no_length_is_not_a_note():
    """It would draw as a zero-width block on the grid."""
    assert tie_notes([C('TIE', 48, 60, 100), C('EOT', 48, 60)]) == []


def test_the_notes_carry_a_default_velocity():
    assert tie_notes([C('TIE', 0, 60, 0), C('EOT', 24, 60)])[0]['velocity'] == 100


# ── Everything reads a real song the same way ───────────────────────────────

REAL = pytest.mark.skipif(not os.path.isdir(MIDI_DIR),
                          reason='needs the pokefirered sound folder')

SONGS = ['mus_lab', 'mus_faron_woods', 'mus_castle_town', 'mus_cremia']


def _present(label):
    p = os.path.join(MIDI_DIR, label + '.s')
    return p if os.path.isfile(p) else None


def _drawn_notes(song, ti):
    """Exactly what the piano roll draws for one track.

    It pairs over the FLATTENED commands, so a note inside a pattern is at
    the tick it is heard rather than the tick it is written -- the same
    stream the save check measures. Comparing against the raw commands
    compares two different clocks.
    """
    from core.sound.song_parser import extract_tie_notes, Track
    from core.sound.track_renderer import flatten_track_commands
    flat_cmds = flatten_track_commands(song.tracks[ti].commands, loop_count=0)
    flat = Track(index=ti, label=song.tracks[ti].label)
    flat.commands = flat_cmds
    # Plain NOTEs are added by the widget itself; held notes come from the
    # pairing. Both are drawn, so both are compared.
    out = [{'tick': c.tick, 'pitch': c.pitch,
            'duration': int(c.duration or 0) + int(c.gate_time or 0),
            'velocity': c.velocity or 100}
           for c in flat_cmds if c.cmd == 'NOTE' and c.pitch is not None]
    return out + extract_tie_notes(flat)


@REAL
@pytest.mark.parametrize('label', SONGS)
def test_the_editor_and_the_save_check_agree_on_every_note(label):
    """The actual defect: these two disagreed, so the song could not save.

    The piano roll draws what the parser returns; the save check measures
    what `audible_signature` returns. They must describe the same notes.
    """
    path = _present(label)
    if path is None:
        pytest.skip(f'{label}.s is not in this project')
    from core.sound.song_parser import parse_song_file
    from core.sound.safe_song_save import audible_signature

    song = parse_song_file(path)
    sig = audible_signature(song)
    checked = 0
    for ti in range(len(song.tracks)):
        drawn = {(n['tick'], n['pitch'], n['duration']) for n in _drawn_notes(song, ti)}
        if not drawn:
            continue
        checked += 1
        measured = {(k[0], k[1], k[3]) for k in (sig[ti].get('notes') or {})}
        missing = drawn - measured
        assert not missing, (
            f'{label} track {ti + 1}: the editor draws notes the save check '
            f'does not measure the same way: {sorted(missing)[:4]}')
    assert checked, f'{label} has no held notes to check'


@REAL
def test_mus_lab_keeps_its_overlapping_pair():
    """It is the user's music and the engine plays it -- read it, never trim it."""
    path = _present('mus_lab')
    if path is None:
        pytest.skip('mus_lab.s is not in this project')
    from core.sound.song_parser import parse_song_file

    song = parse_song_file(path)
    drawn = _drawn_notes(song, 0)
    pair = sorted((n for n in drawn if n['pitch'] == 55 and n['tick'] in (2688, 2784)),
                  key=lambda n: n['tick'])
    assert len(pair) == 2, f'both notes of the overlapping pair must survive: {pair}'
    a, b = pair
    assert a['tick'] + a['duration'] > b['tick'], (
        'the overlap was silently removed; the engine can play it')


@REAL
@pytest.mark.parametrize('label', SONGS)
def test_the_midi_exporter_pairs_the_same_way(label):
    """The exporter keeps its own pairing inline, for its own event stream.

    It must stay the SAME rule. A change to one and not the other would
    show up as a user's song refusing to save, so it is caught here: every
    note the exporter writes into the MIDI must be a note the save check
    measures, at the same tick, key and length.

    ONLY the part of a track that PLAYS is compared. A track can carry
    material after its loop jump -- `mus_lab` does, because the second half
    was looped out rather than cut -- and the two readers treat it
    differently ON PURPOSE: the exporter keeps those notes so a MIDI round
    trip does not silently delete them, while the save check ignores them
    because the engine never reaches them. Comparing past the loop jump
    measures that deliberate difference instead of the pairing rule, which
    is what this test is for.
    """
    path = _present(label)
    if path is None:
        pytest.skip(f'{label}.s is not in this project')
    pytest.importorskip('mido')
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import expand_song, song_to_midi, _SCALE
    from core.sound.safe_song_save import audible_signature

    song = parse_song_file(path)
    sig = audible_signature(song)
    mid = song_to_midi(song)
    # Where each track loops back, on the same playback clock the exporter
    # and the save check both use. None = the track plays to its end.
    stops = [ex.goto_time for ex in expand_song(song)]

    # The first MIDI track is the tempo map; the rest follow the song's.
    tracks = [t for t in mid.tracks[1:]]
    for ti, mtrack in enumerate(tracks):
        if ti not in sig:
            continue
        measured = {(k[0], k[1], k[3]) for k in (sig[ti].get('notes') or {})}
        if not measured:
            continue
        stop = stops[ti] if ti < len(stops) else None
        t = 0
        open_notes: dict = {}
        exported = set()
        for msg in mtrack:
            t += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                open_notes.setdefault(msg.note, []).append(t)
            elif msg.type in ('note_off',) or (msg.type == 'note_on' and not msg.velocity):
                waiting = open_notes.get(msg.note)
                if waiting:
                    t0 = waiting.pop(0)
                    start = t0 // _SCALE
                    if stop is not None and start >= stop:
                        continue        # after the loop jump: never played
                    exported.add((start, msg.note, (t - t0) // _SCALE))
        extra = exported - measured
        assert not extra, (
            f'{label} track {ti + 1}: the exporter writes notes the save check '
            f'does not measure the same way: {sorted(extra)[:4]}')


# ── Every reader, one answer ────────────────────────────────────────────────

def test_tie_ends_points_at_the_tie_that_was_written():
    """Playback walks the commands itself, so it needs each tie's end by
    position -- not by value. The old renderer looked its tie up with
    `flat_cmds.index(cmd)`, which finds the FIRST equal command rather than
    this one, so two identical ties both took the first one's length."""
    cmds = [C('TIE', 0, 60, 100), C('TIE', 96, 60, 100),
            C('EOT', 48, 60), C('EOT', 384, 60)]
    ends = tie_ends(cmds)
    assert ends == {0: 48, 1: 384}


def test_tie_ends_and_tie_spans_never_disagree():
    cmds = [C('TIE', 0, 60, 100), C('NOTE', 10, 67, 90), C('TIE', 24, 60, 90),
            C('EOT', 48, None), C('EOT', 96, 60)]
    ends = tie_ends(cmds)
    assert sorted(ends.values()) == sorted(s[3] for s in tie_spans(cmds))


@REAL
@pytest.mark.parametrize('label', SONGS)
def test_playback_and_the_save_check_agree_on_every_note(label):
    """What is previewed must be what is drawn and what is saved.

    The renderer used to pair ties by its own rule, so a song could be heard
    with a note length nothing else believed in.
    """
    path = _present(label)
    if path is None:
        pytest.skip(f'{label}.s is not in this project')
    from core.sound.song_parser import parse_song_file
    from core.sound.track_renderer import flatten_track_commands
    from core.sound.safe_song_save import audible_signature

    song = parse_song_file(path)
    sig = audible_signature(song)
    checked = 0
    for ti, track in enumerate(song.tracks):
        flat = flatten_track_commands(track.commands, loop_count=0)
        ends = tie_ends(flat)
        if not ends:
            continue
        checked += 1
        measured = {(k[0], k[1], k[3]) for k in (sig[ti].get('notes') or {})}
        for pos, end in ends.items():
            cmd = flat[pos]
            if end <= cmd.tick:
                continue
            assert (cmd.tick, cmd.pitch, end - cmd.tick) in measured, (
                f'{label} track {ti + 1}: playback would hold tick {cmd.tick} '
                f'key {cmd.pitch} for {end - cmd.tick}, which the save check '
                f'does not measure')
    if not checked:
        pytest.skip(f'{label} has no held notes — every note is a plain one')
