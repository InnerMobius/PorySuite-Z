"""Regression tests for the MIDI import wizard's voice-budget engine:
polyphony analysis, flatten/split voice counting, duplicate detection, merging,
the project voice-limit reader, and the channel-filter/merge MIDI rewrite.
"""

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

import mido  # noqa: E402
from core.sound.midi_polyphony import (  # noqa: E402
    analyze_polyphony, combined_peak, merge_polys,
    find_duplicate_instruments, read_voice_limits,
)
from core.sound.midi_importer import (  # noqa: E402
    read_midi_info, process_midi_for_import,
)


def _make_midi(path):
    """ch1: prog10, 2 sequential mono notes. ch2: prog20, a 2-note chord.
    ch3: prog10 (duplicate of ch1), 1 note. All three start at tick 0."""
    mid = mido.MidiFile(type=1, ticks_per_beat=48)
    cond = mido.MidiTrack()
    cond.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    mid.tracks.append(cond)
    t1 = mido.MidiTrack()
    t1.append(mido.Message("program_change", channel=0, program=10, time=0))
    t1.append(mido.Message("note_on", channel=0, note=60, velocity=100, time=0))
    t1.append(mido.Message("note_off", channel=0, note=60, time=48))
    t1.append(mido.Message("note_on", channel=0, note=62, velocity=100, time=0))
    t1.append(mido.Message("note_off", channel=0, note=62, time=48))
    mid.tracks.append(t1)
    t2 = mido.MidiTrack()
    t2.append(mido.Message("program_change", channel=1, program=20, time=0))
    t2.append(mido.Message("note_on", channel=1, note=64, velocity=100, time=0))
    t2.append(mido.Message("note_on", channel=1, note=67, velocity=100, time=0))
    t2.append(mido.Message("note_off", channel=1, note=64, time=48))
    t2.append(mido.Message("note_off", channel=1, note=67, time=0))
    mid.tracks.append(t2)
    t3 = mido.MidiTrack()
    t3.append(mido.Message("program_change", channel=2, program=10, time=0))
    t3.append(mido.Message("note_on", channel=2, note=72, velocity=100, time=0))
    t3.append(mido.Message("note_off", channel=2, note=72, time=48))
    mid.tracks.append(t3)
    mid.save(path)


def test_analyze_polyphony_peak_and_chordal():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_midi(p)
        rep = analyze_polyphony(p)
    assert rep.overall_peak == 4              # 60 + (64,67) + 72 all at tick 0
    assert rep.per_channel[2].chord_peak == 2  # ch2 is the chord
    assert rep.per_channel[1].chord_peak == 1
    assert rep.per_channel[2].is_chordal and not rep.per_channel[1].is_chordal


def test_combined_peak_flatten_vs_split():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_midi(p)
        rep = analyze_polyphony(p)
    allc = list(rep.per_channel.values())
    assert combined_peak(allc, flatten=False)[0] == 4   # chord counted
    assert combined_peak(allc, flatten=True)[0] == 3     # each channel = 1 voice


def test_merge_polys_recomputes_chord_peak():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_midi(p)
        rep = analyze_polyphony(p)
    merged = merge_polys([rep.per_channel[1], rep.per_channel[3]], 1)
    assert merged.chord_peak == 2   # ch1 + ch3 overlap at tick 0


def test_find_duplicate_instruments():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_midi(p)
        info = read_midi_info(p)
    assert find_duplicate_instruments(info.tracks) == {10: [1, 3]}


def test_process_midi_for_import_keep_and_merge():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.mid")
        out = os.path.join(d, "out.mid")
        _make_midi(src)
        process_midi_for_import(src, keep_channels={1, 2, 3},
                                merge_map={3: 1}, out_path=out)
        notes = {}
        for tr in mido.MidiFile(out).tracks:
            for m in tr:
                if m.type == "note_on" and m.velocity > 0:
                    notes[m.channel + 1] = notes.get(m.channel + 1, 0) + 1
    assert set(notes) == {1, 2}        # ch3 folded into ch1
    assert notes[1] == 3               # ch1 (2) + ch3 (1)
    assert notes[2] == 2


def test_process_midi_drops_unchecked():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.mid")
        out = os.path.join(d, "out.mid")
        _make_midi(src)
        process_midi_for_import(src, keep_channels={2}, merge_map={}, out_path=out)
        chans = {m.channel + 1 for tr in mido.MidiFile(out).tracks
                 for m in tr if m.type == "note_on" and m.velocity > 0}
    assert chans == {2}                # only the kept channel survives


def test_read_voice_limits_defaults_when_no_project():
    lim = read_voice_limits(os.path.join(tempfile.gettempdir(), "no_such_proj_xyz"))
    assert lim.pcm == 8 and lim.psg == 4 and lim.track_cap == 16
