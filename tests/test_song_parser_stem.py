"""Regression test: a song .s whose public .global label differs from its
internal equate/track stem (the result of an import/rename that didn't unify
the naming) must still parse its voicegroup, properties, AND tracks — not show
voicegroup blank + Tracks: 0.
"""
import os
import sys
import types
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_qt = types.ModuleType("PyQt6")
_qtc = types.ModuleType("PyQt6.QtCore")
_qtg = types.ModuleType("PyQt6.QtGui")
_qtc.pyqtSignal = lambda *a, **k: None
class _Blk:
    def __init__(self, *_): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
_qtc.QSignalBlocker = _Blk
_qtg.QImage = type("QImage", (), {})
_qtg.QPixmap = type("QPixmap", (), {})
sys.modules.setdefault("PyQt6", _qt)
sys.modules.setdefault("PyQt6.QtCore", _qtc)
sys.modules.setdefault("PyQt6.QtGui", _qtg)
_qt.QtCore = _qtc
_qt.QtGui = _qtg

from core.sound.song_parser import parse_song_file


def _song_s(public_label: str, stem: str) -> str:
    return f"""\t.include "MPlayDef.s"

\t.equ\t{stem}_grp, voicegroup042
\t.equ\t{stem}_pri, 7
\t.equ\t{stem}_rev, reverb_set+0
\t.equ\t{stem}_mvl, 90
\t.equ\t{stem}_key, 0
\t.equ\t{stem}_tbs, 1

\t.section .rodata
\t.global\t{public_label}
\t.align\t2

{stem}_1:
\t.byte\tKEYSH , {stem}_key+0
\t.byte\tTEMPO , 150*{stem}_tbs/2
\t.byte\tVOICE , 0
\t.byte\tW02 , Cn4 , v090
\t.byte\tFINE

{public_label}:
\t.byte\t1
\t.byte\t{stem}_pri
\t.byte\tREV , {stem}_rev
\t.byte\t{stem}_mvl
\t.word\t{stem}_grp
\t.hword\t{stem}_1
"""


class SongParserStemTest(unittest.TestCase):
    def _parse(self, text):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".s", delete=False, encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            return parse_song_file(path)
        finally:
            os.unlink(path)

    def test_mismatched_stem_resolves(self):
        # Public label se_confirm, internal stem se_sfx_minish_106 — the
        # reported bug. Everything must still resolve.
        song = self._parse(_song_s("se_confirm", "se_sfx_minish_106"))
        self.assertEqual(song.label, "se_confirm")
        self.assertEqual(song.voicegroup, "voicegroup042")
        self.assertEqual(song.priority, 7)
        self.assertEqual(song.master_volume, 90)
        self.assertEqual(len(song.tracks), 1, "track must parse despite the stem mismatch")
        self.assertEqual(song.tracks[0].label, "se_sfx_minish_106_1")

    def test_matching_stem_still_works(self):
        # The normal case (stem == label) must keep working.
        song = self._parse(_song_s("se_normal", "se_normal"))
        self.assertEqual(song.voicegroup, "voicegroup042")
        self.assertEqual(song.priority, 7)
        self.assertEqual(len(song.tracks), 1)


if __name__ == "__main__":
    unittest.main()
