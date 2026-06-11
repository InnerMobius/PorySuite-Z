"""Regression: the MIDI import wizard's mapping page must respect the per-track
import checkboxes from the budget step. Unchecked tracks (and tracks merged onto
another) must NOT appear on the instrument-mapping page — earlier it always
listed every track in the MIDI regardless of the selection.
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

import pytest  # noqa: E402
import mido  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    _QT_OK = True
except Exception:
    _QT_OK = False


def _make_midi(path):
    mid = mido.MidiFile(type=1, ticks_per_beat=48)
    mid.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000, time=0)]))
    for ch, prog in ((0, 10), (1, 20), (2, 30)):
        t = mido.MidiTrack()
        t.append(mido.Message("program_change", channel=ch, program=prog, time=0))
        t.append(mido.Message("note_on", channel=ch, note=60, velocity=100, time=0))
        t.append(mido.Message("note_off", channel=ch, note=60, time=48))
        mid.tracks.append(t)
    mid.save(path)


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_mapping_page_respects_checkboxes():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from ui.dialogs.midi_import_dialog import MidiImportDialog
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_midi(p)
        dlg = MidiImportDialog(d, ["voicegroup000"], None)
        dlg._load_midi(p)

        dlg._populate_mapping_page()
        assert len(dlg._mapping_combos) == 3            # all 3 tracks checked → all shown

        # Uncheck two of the three tracks
        for i, r in enumerate(dlg._budget_rows):
            r["item"].setCheckState(
                0, Qt.CheckState.Checked if i == 0 else Qt.CheckState.Unchecked)
        dlg._populate_mapping_page()
        assert len(dlg._mapping_combos) == 1            # only the kept track is mapped

        dlg.deleteLater()


def _make_dup_midi(path):
    """ch1 + ch2: same instrument (a duplicate pair) whose notes OVERLAP, so
    merging them yields a chordal track."""
    mid = mido.MidiFile(type=1, ticks_per_beat=48)
    mid.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000, time=0)]))
    for ch in (0, 1):
        t = mido.MidiTrack()
        t.append(mido.Message("program_change", channel=ch, program=50, time=0))
        t.append(mido.Message("note_on", channel=ch, note=60 + ch, velocity=100, time=0))
        t.append(mido.Message("note_off", channel=ch, note=60 + ch, time=96))
        mid.tracks.append(t)
    mid.save(path)


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_merge_target_poly_shows_post_merge_chord():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from ui.dialogs.midi_import_dialog import MidiImportDialog
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_dup_midi(p)
        dlg = MidiImportDialog(d, ["voicegroup000"], None)
        dlg._load_midi(p)
        by = {r["channel"]: r for r in dlg._budget_rows}
        assert by[1]["item"].text(5) == "mono"          # before merge
        dlg._on_merge_duplicates()
        assert by[1]["item"].text(5) == "2-note"         # copies overlap → chordal
        dlg.deleteLater()


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_overflow_warning_names_the_tracks():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from ui.dialogs.midi_import_dialog import MidiImportDialog
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_dup_midi(p)
        dlg = MidiImportDialog(d, ["voicegroup000"], None)
        dlg._load_midi(p)
        dlg._on_merge_duplicates()
        by = {r["channel"]: r for r in dlg._budget_rows}
        by[1]["combo"].setCurrentText("PSG square1")     # chordal merged track on PSG
        lines = dlg._voice_overflow_lines(dlg._voice_budget_state())
        assert any("ch 1" in ln for ln in lines)         # names the offending track
        assert any("chord" in ln.lower() for ln in lines)
        dlg.deleteLater()


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_issues_line_and_row_tint_surface_on_page():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from ui.dialogs.midi_import_dialog import MidiImportDialog
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.mid")
        _make_dup_midi(p)
        dlg = MidiImportDialog(d, ["voicegroup000"], None)
        dlg._load_midi(p)
        by = {r["channel"]: r for r in dlg._budget_rows}
        # Two tracks on the same PSG channel → conflict.
        by[1]["combo"].setCurrentText("PSG wave")
        by[2]["combo"].setCurrentText("PSG wave")
        # The problem is spelled out on the page (not just in the Next popup)…
        assert "wave" in dlg._issues_label.text().lower()
        # …and the offending row is tinted.
        assert by[1]["item"].background(0).color().name() == "#5a2020"
        dlg.deleteLater()
