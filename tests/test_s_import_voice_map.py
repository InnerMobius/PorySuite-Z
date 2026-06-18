"""Regression: the .s import wizard must let the user remap each VOICE slot to a
voicegroup instrument on import (the MIDI importer already does this; the .s
importer used to copy the file's VOICE numbers verbatim, so an imported sound
silently used whatever instrument sat in that slot of the target voicegroup).

Covers the new pure helpers plus the shared VOICE rewriter and the dialog's
remap-collection, exercised against a real (headless) Qt like the sibling
MIDI-import test.
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication  # noqa: F401
    _QT_OK = True
except Exception:
    _QT_OK = False


# ── Fakes for the pure helpers (no parser / no Qt needed) ───────────────────

class _Cmd:
    def __init__(self, cmd, value=None):
        self.cmd = cmd
        self.value = value


class _Track:
    def __init__(self, index, cmds):
        self.index = index
        self.commands = cmds


class _Song:
    def __init__(self, tracks):
        self.tracks = tracks


class _Inst:
    """Minimal stand-in for a voicegroup Instrument."""
    def __init__(self, **flags):
        for k in ('is_square', 'is_programmable_wave', 'is_noise',
                  'is_directsound', 'is_keysplit'):
            setattr(self, k, flags.get(k, False))
        self.friendly_name = flags.get('friendly_name', 'x')


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_extract_voice_usage_buckets_by_track():
    from ui.dialogs.s_file_import_dialog import _extract_voice_usage
    song = _Song([
        _Track(0, [_Cmd('VOICE', 0), _Cmd('NOTE'), _Cmd('VOICE', 5)]),
        _Track(1, [_Cmd('VOICE', 0), _Cmd('NOTE')]),
        _Track(2, [_Cmd('NOTE')]),  # no VOICE → not listed
    ])
    usage = _extract_voice_usage(song)
    assert usage == {0: [0, 1], 5: [0]}


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_extract_voice_usage_empty_when_no_voice():
    from ui.dialogs.s_file_import_dialog import _extract_voice_usage
    assert _extract_voice_usage(_Song([_Track(0, [_Cmd('NOTE')])])) == {}


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_type_tag():
    from ui.dialogs.s_file_import_dialog import _type_tag
    assert _type_tag(_Inst(is_square=True)) == 'square'
    assert _type_tag(_Inst(is_directsound=True)) == 'sample'
    assert _type_tag(_Inst(is_programmable_wave=True)) == 'wave'
    assert _type_tag(_Inst(is_noise=True)) == 'noise'
    assert _type_tag(_Inst(is_keysplit=True)) == 'keysplit'
    assert _type_tag(_Inst()) == ''


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_postprocess_voice_remap_rewrites_only_listed_voices():
    # The shared rewriter that the .s importer reuses from the MIDI importer.
    from ui.dialogs.midi_import_dialog import _postprocess_voice_remap
    src = (
        '\t.byte\tVOICE , 0\n'
        '\t.byte\tN03 , Bn4 , v127\n'
        '\t.byte\tVOICE , 1\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False,
                                     encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        _postprocess_voice_remap(path, {0: 5})  # remap voice 0 → slot 5 only
        with open(path, encoding="utf-8") as f:
            out = f.read()
        assert '.byte\tVOICE , 5' in out      # 0 was remapped
        assert '.byte\tVOICE , 1' in out      # 1 left untouched
        assert 'VOICE , 0' not in out
    finally:
        os.unlink(path)


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_dialog_collects_remap_only_for_changed_rows():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from ui.dialogs.s_file_import_dialog import SFileImportDialog
    dlg = SFileImportDialog("/tmp/nope", ["voicegroup013"], None)
    # Two voices in use; vg_data=None so slot combos carry raw indices 0..127.
    dlg._parsed_song = _Song([
        _Track(0, [_Cmd('VOICE', 0)]),
        _Track(1, [_Cmd('VOICE', 3)]),
    ])
    dlg._populate_mapping_page()
    assert len(dlg._voice_map_combos) == 2     # one row per distinct voice

    # Leave voice 3 alone, point voice 0 at slot 12.
    combos = {old: c for old, c in dlg._voice_map_combos}
    combos[0].setCurrentIndex(12)
    assert dlg._build_voice_remap() == {0: 12}  # only the changed row
    dlg.deleteLater()
