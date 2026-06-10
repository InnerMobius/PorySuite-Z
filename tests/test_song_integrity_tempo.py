"""Regression tests for song_integrity's stale-.mid divergence detection.

Guards the 2026-06-10 fix: a committed .mid that is a STALE render of our own
song (same notes, but carrying phantom tempo events the .s no longer has) must
be detected so the sweep can refresh it from the .s — while a .mid with
DIFFERENT notes (an external/DAW composition) must NEVER be flagged (refreshing
it would wipe the user's work).
"""

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

import mido  # noqa: E402
from core.sound import song_integrity as SI  # noqa: E402


def _mk(notes, tempos):
    """Build a 2-track mido (conductor=tempos, track=notes). `notes` is a list
    of (abs_tick, midi_note); `tempos` a list of (abs_tick, tempo_us)."""
    mid = mido.MidiFile(type=1, ticks_per_beat=48)
    cond = mido.MidiTrack()
    mid.tracks.append(cond)
    prev = 0
    for tick, us in sorted(tempos):
        cond.append(mido.MetaMessage("set_tempo", tempo=us, time=tick - prev))
        prev = tick
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    prev = 0
    for tick, note in sorted(notes):
        tr.append(mido.Message("note_on", note=note, velocity=100, time=tick - prev))
        tr.append(mido.Message("note_off", note=note, velocity=0, time=12))
        prev = tick + 12
    return mid


def test_stale_tempo_same_notes_is_flagged():
    """Same notes, an extra mid-song tempo on disk -> (notes_match, tempo_diverges)."""
    notes = [(0, 60), (48, 62), (96, 64)]
    rendered = _mk(notes, [(0, 500000)])                  # clean: one tempo
    on_disk = _mk(notes, [(0, 500000), (72, 400000)])     # stale: + phantom tempo
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "stale.mid")
        on_disk.save(p)
        notes_match, tempo_diverges = SI._compare_render_to_disk(rendered, p)
    assert notes_match is True
    assert tempo_diverges is True


def test_identical_mid_is_not_flagged():
    """A .mid that already matches the .s render -> no divergence, leave alone."""
    notes = [(0, 60), (48, 62)]
    rendered = _mk(notes, [(0, 500000)])
    on_disk = _mk(notes, [(0, 500000)])
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "clean.mid")
        on_disk.save(p)
        notes_match, tempo_diverges = SI._compare_render_to_disk(rendered, p)
    assert notes_match is True
    assert tempo_diverges is False


def test_external_daw_mid_different_notes_is_protected():
    """A .mid whose NOTES differ (external/DAW) must report notes_match=False so
    the sweep never refreshes (and therefore never wipes) it."""
    rendered = _mk([(0, 60), (48, 62)], [(0, 500000)])
    on_disk = _mk([(0, 67), (48, 69), (96, 71)],          # different composition
                  [(0, 500000), (72, 400000)])
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "daw.mid")
        on_disk.save(p)
        notes_match, _ = SI._compare_render_to_disk(rendered, p)
    assert notes_match is False


def test_unreadable_mid_is_conservative():
    """A .mid that can't be parsed -> (False, False) so it's left untouched."""
    rendered = _mk([(0, 60)], [(0, 500000)])
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "broken.mid")
        with open(p, "wb") as fh:
            fh.write(b"not a midi file")
        notes_match, tempo_diverges = SI._compare_render_to_disk(rendered, p)
    assert notes_match is False
    assert tempo_diverges is False


def test_tempo_microsecond_rounding_not_flagged():
    """A 1-microsecond rounding difference is the SAME bpm and must NOT count as
    a divergence — otherwise an unchanged song gets needlessly refreshed.
    (mid2agb and song_to_midi round a tempo's microseconds one unit apart;
    545454 vs 545455 us are both 110 bpm.)"""
    notes = [(0, 60), (48, 62)]
    rendered = _mk(notes, [(0, 545455)])   # 110 bpm (fresh render rounding)
    on_disk = _mk(notes, [(0, 545454)])    # 110 bpm (original render rounding)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "se.mid")
        on_disk.save(p)
        notes_match, tempo_diverges = SI._compare_render_to_disk(rendered, p)
    assert notes_match is True
    assert tempo_diverges is False   # same bpm -> NOT a divergence
