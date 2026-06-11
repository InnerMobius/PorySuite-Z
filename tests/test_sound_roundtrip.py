"""Regression tests for the sound round-trip + integrity fixes (2026-06-10):

- An UNCHANGED editable control (VOL/PAN/…) re-emits its exact source line on
  save instead of a round-tripped-and-drifted regeneration (no phantom edit);
  an EDITED control still regenerates from its new value.
- The integrity sweep's note signature is channel-aware, so it can't mistake a
  differently-routed .mid for our own render and overwrite it.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

import mido  # noqa: E402
from core.sound.song_parser import TrackCommand  # noqa: E402
from core.sound.song_writer import _preserve_raw  # noqa: E402
from core.sound.song_integrity import _note_signature  # noqa: E402

_VOL_LINE = "\t.byte\tVOL , 95*mus_test_mvl/mxv"


def test_post_init_snapshots_parsed_value():
    # Parser evaluated "95*mvl/mxv" to int(95*100/127)=74; parsed_value mirrors it.
    c = TrackCommand(cmd="VOL", value=74, raw_line=_VOL_LINE)
    assert c.parsed_value == 74


def test_unchanged_control_preserves_raw():
    c = TrackCommand(cmd="VOL", value=74, raw_line=_VOL_LINE)
    assert _preserve_raw(c) is True   # value untouched → keep the exact "95*…" line


def test_edited_control_regenerates():
    c = TrackCommand(cmd="VOL", value=74, raw_line=_VOL_LINE)
    c.value = 80                      # user changed it in the UI
    assert _preserve_raw(c) is False  # changed → regenerate from the new value


def test_structural_command_always_preserved():
    c = TrackCommand(cmd="GOTO", raw_line="\t.byte\tGOTO", target_label="mus_x_1")
    assert _preserve_raw(c) is True   # not an editable control → keep raw_line


def test_control_without_raw_line_regenerates():
    c = TrackCommand(cmd="VOL", value=64)   # UI-created, no source line
    assert _preserve_raw(c) is False


def _one_note(channel):
    m = mido.MidiFile(type=1, ticks_per_beat=48)
    t = mido.MidiTrack()
    t.append(mido.Message("note_on", channel=channel, note=60, velocity=100, time=0))
    t.append(mido.Message("note_off", channel=channel, note=60, time=48))
    m.tracks.append(t)
    return m


def test_note_signature_is_channel_aware():
    a, b = _one_note(0), _one_note(5)
    # Same (tick, note) content, different channel routing → must NOT match,
    # so the sweep won't overwrite a differently-routed .mid.
    assert _note_signature(a) != _note_signature(b)
    assert _note_signature(a) == _note_signature(_one_note(0))
