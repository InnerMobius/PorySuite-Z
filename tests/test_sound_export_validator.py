"""Regression tests for the sound-export safety net:
  * the export validator (rejects malformed .s / out-of-range voices)
  * midi.cfg never drops the -G voicegroup flag (recovers it from the .s)

Covers the failure modes that required hand-repair on the pokefirered side:
v53 (non-3-digit velocity), .hword track pointers, missing voicegroup pointer,
undefined `.byte REV`, and a sound stranded on the wrong voicegroup.
"""

import os
import sys
import types
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.sound.song_validator import (  # noqa: E402
    validate_s_text, validate_song_voices)
from core.sound.song_table_manager import (  # noqa: E402
    _resolve_voicegroup_index_from_s, _format_cfg_line)


_GOOD_S = (
    '\t.include "MPlayDef.s"\n'
    '\t.equ\tse_x_grp, voicegroup013\n'
    '\t.section .rodata\n\t.global\tse_x\n'
    'se_x_1:\n\t.byte\tKEYSH , se_x_key+0\n'
    '\t.byte\tN04 , Cn4 , v127\n\t.byte\tFINE\n'
    'se_x:\n\t.byte\t1\n\t.byte\t0\n\t.byte\tse_x_pri\n\t.byte\tse_x_rev\n'
    '\t.word\tse_x_grp\n\t.word\tse_x_1\n\t.end\n'
)

_BAD_S = (
    '\t.include "MPlayDef.s"\n'
    '\t.section .rodata\n\t.global\tse_x\n'
    'se_x_1:\n\t.byte\tN04 , Cn4 , v53\n'      # 2-digit velocity
    '\t.byte\tREV , 0\n'                        # undefined macro
    '\t.byte\tFINE\n'
    'se_x:\n\t.byte\t1\n\t.byte\t0\n\t.byte\tse_x_pri\n\t.byte\tse_x_rev\n'
    '\t.hword\tse_x_1\n\t.end\n'                # .hword + NO voicegroup pointer
)


class _Cmd:
    def __init__(self, cmd, value=None):
        self.cmd, self.value = cmd, value


class _Track:
    def __init__(self, cmds):
        self.commands = cmds


class _Song:
    def __init__(self, voicegroup, tracks):
        self.voicegroup, self.tracks = voicegroup, tracks


class _Inst:
    pass


class _VG:
    def __init__(self, n):
        self.instruments = [_Inst() for _ in range(n)]


class _VGData:
    def __init__(self, vgs):
        self._vgs = vgs

    def get_voicegroup(self, name):
        return self._vgs.get(name)

    def get_instrument_overflow(self, name, slot):
        # Sum this voicegroup + all higher-numbered ones (ROM overflow).
        num = int(name.replace("voicegroup", ""))
        total = 0
        for n, vg in sorted(self._vgs.items()):
            if int(n.replace("voicegroup", "")) >= num:
                total += len(vg.instruments)
        return _Inst() if 0 <= slot < total else None


class ExportValidatorTest(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertEqual(validate_s_text(_GOOD_S, "se_x"), [])

    def test_malformed_text_rejected(self):
        errs = validate_s_text(_BAD_S, "se_x")
        joined = " ".join(errs)
        self.assertIn("v53", joined)            # non-3-digit velocity
        self.assertIn(".hword", joined)         # bad pointer width
        self.assertIn("_grp", joined)           # missing voicegroup pointer
        self.assertIn("REV", joined)            # undefined macro

    def test_voice_in_range_ok(self):
        vgd = _VGData({"voicegroup013": _VG(64)})
        song = _Song("voicegroup013", [_Track([_Cmd("VOICE", 5)])])
        self.assertEqual(validate_song_voices(song, None, vg_data=vgd), [])

    def test_voice_overflow_ok(self):
        # VOICE beyond this vg's own count but mapped via overflow into the
        # next vg — legitimate, must NOT be flagged.
        vgd = _VGData({"voicegroup013": _VG(50), "voicegroup014": _VG(50)})
        song = _Song("voicegroup013", [_Track([_Cmd("VOICE", 80)])])
        self.assertEqual(validate_song_voices(song, None, vg_data=vgd), [])

    def test_voice_truly_out_of_range_flagged(self):
        vgd = _VGData({"voicegroup000": _VG(62)})
        song = _Song("voicegroup000", [_Track([_Cmd("VOICE", 200)])])
        errs = validate_song_voices(song, None, vg_data=vgd)
        self.assertTrue(any("VOICE 200" in e for e in errs))

    def test_missing_voicegroup_flagged(self):
        vgd = _VGData({})
        song = _Song("voicegroup999", [_Track([_Cmd("VOICE", 0)])])
        errs = validate_song_voices(song, None, vg_data=vgd)
        self.assertTrue(any("voicegroup999" in e for e in errs))


class VoicegroupFlagRecoveryTest(unittest.TestCase):
    def test_g_flag_recovered_from_s(self):
        with tempfile.TemporaryDirectory() as d:
            midi = os.path.join(d, "sound", "songs", "midi")
            os.makedirs(midi)
            with open(os.path.join(midi, "se_y.s"), "w", encoding="utf-8") as f:
                f.write("\t.equ\tse_y_grp, voicegroup042\n\t.global se_y\n")
            entry = types.SimpleNamespace(
                label="se_y", midi_filename="se_y.mid", voicegroup_index=None,
                reverb=None, volume=80, priority=5, extra_flags="")
            _resolve_voicegroup_index_from_s(d, entry)
            self.assertEqual(entry.voicegroup_index, 42)
            self.assertIn("-G042", _format_cfg_line(entry))


if __name__ == "__main__":
    unittest.main()
