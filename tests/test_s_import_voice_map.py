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


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_companion_mid_regenerated_over_stale(tmp_path):
    # A re-import must rebuild the companion .mid from the freshly imported .s,
    # NOT keep a stale .mid on disk — otherwise the build can regenerate the .s
    # from the wrong .mid and silently replace the imported song (this is how
    # an imported SE got reduced to a few wrong notes).
    import mido
    QApplication.instance() or QApplication([])
    from ui.dialogs.s_file_import_dialog import _SImportWorker

    s_text = (
        '\t.include "MPlayDef.s"\n\n'
        '\t.equ\tse_t_grp, voicegroup000\n'
        '\t.equ\tse_t_pri, 0\n\t.equ\tse_t_rev, reverb_set+0\n'
        '\t.equ\tse_t_mvl, 127\n\t.equ\tse_t_key, 0\n\t.equ\tse_t_tbs, 1\n\n'
        '\t.section .rodata\n\t.global\tse_t\n\t.align\t2\n\n'
        'se_t_1:\n\t.byte\tKEYSH , 0\n\t.byte\tTEMPO , 75\n\t.byte\tVOICE , 0\n'
        '\t.byte\tN04 , Cn4 , v100\n\t.byte\tW04\n'
        '\t.byte\tN04 , En4 , v100\n\t.byte\tW04\n'
        '\t.byte\tN04 , Gn4 , v100\n\t.byte\tW04\n\t.byte\tFINE\n\n'
        'se_t:\n\t.byte\t1\n\t.byte\t0\n\t.byte\tse_t_pri\n\t.byte\tse_t_rev\n'
        '\t.word\tse_t_grp\n\t.word\tse_t_1\n\t.end\n'
    )
    d = str(tmp_path)
    s_path = os.path.join(d, "se_t.s")
    with open(s_path, "w", encoding="utf-8") as f:
        f.write(s_text)

    # Plant a stale 1-note .mid — the bug condition.
    stale = os.path.join(d, "se_t.mid")
    mf = mido.MidiFile(); trk = mido.MidiTrack(); mf.tracks.append(trk)
    trk.append(mido.Message('note_on', note=60, velocity=100, time=0))
    trk.append(mido.Message('note_off', note=60, time=10))
    mf.save(stale)

    w = _SImportWorker(d, s_path, "SE_T", "se_t", 1,
                       "voicegroup000", "voicegroup000", 0, 127, 0)
    w._create_companion_mid(d, s_path)

    m = mido.MidiFile(stale)
    notes = sum(1 for t in m.tracks for x in t
                if x.type == 'note_on' and x.velocity > 0)
    assert notes == 3, f"companion .mid not regenerated from .s (got {notes})"


@pytest.mark.skipif(not _QT_OK, reason="PyQt6 unavailable")
def test_malformed_import_rolls_back(tmp_path):
    # A malformed source must NOT clobber an existing good sound: the import is
    # rejected and the project is left exactly as it was (transactional).
    QApplication.instance() or QApplication([])
    from ui.dialogs.s_file_import_dialog import _SImportWorker

    good = (
        '\t.include "MPlayDef.s"\n\t.equ\tse_x_grp, voicegroup013\n'
        '\t.equ\tse_x_pri, 5\n\t.equ\tse_x_rev, reverb_set+0\n'
        '\t.equ\tse_x_mvl, 127\n\t.equ\tse_x_key, 0\n\t.equ\tse_x_tbs, 1\n'
        '\t.section .rodata\n\t.global\tse_x\n\t.align\t2\n'
        'se_x_1:\n\t.byte\tKEYSH , se_x_key+0\n\t.byte\tVOICE , 0\n'
        '\t.byte\tN04 , Cn4 , v127\n\t.byte\tW04\n\t.byte\tFINE\n'
        'se_x:\n\t.byte\t1\n\t.byte\t0\n\t.byte\tse_x_pri\n\t.byte\tse_x_rev\n'
        '\t.word\tse_x_grp\n\t.word\tse_x_1\n\t.end\n'
    )
    bad_src = (
        '\t.include "MPlayDef.s"\n\t.section .rodata\n\t.global\tbad\n'
        'bad_1:\n\t.byte\tN04 , Cn4 , v53\n\t.byte\tFINE\n'   # 2-digit vel
        'bad:\n\t.byte\t1\n\t.byte\t0\n\t.byte\tbad_pri\n\t.byte\tbad_rev\n'
        '\t.hword\tbad_1\n\t.end\n'                            # .hword + no grp
    )
    d = str(tmp_path)
    midi = os.path.join(d, "sound", "songs", "midi")
    os.makedirs(midi)
    dest = os.path.join(midi, "se_x.s")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(good)
    src = os.path.join(d, "bad_source.s")
    with open(src, "w", encoding="utf-8") as f:
        f.write(bad_src)

    w = _SImportWorker(d, src, "SE_X", "se_x", 1, "voicegroup013",
                       "voicegroup000", 0, 127, 5,
                       overwrite=True, skip_registration=True)
    res = []
    w.finished.connect(lambda ok, path, err: res.append((ok, err)))
    w.run()

    assert res and res[0][0] is False, "malformed import should be rejected"
    assert "blocked" in res[0][1].lower()
    with open(dest, encoding="utf-8") as f:
        assert f.read() == good, "rollback failed — good sound was clobbered"
