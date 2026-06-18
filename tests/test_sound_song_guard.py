"""Regression tests for the Sound Editor song-table data-loss guard (#1) and
the midi.cfg property sync (#4).

#1: a wholesale rebuild of song_table.inc / songs.h from a STALE in-memory
model silently dropped custom sounds (e.g. importing one SE wiped two existing
ones). write_song_table/write_songs_h now refuse to write fewer songs than are
on disk unless allow_shrink=True (a real delete/cleanup).

#4: editing a sound's priority/reverb/volume must update midi.cfg's -P/-R/-V
(surgically, preserving other lines + flags) so a mid2agb regen can't revert it.
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

# Stub PyQt6 so core.* imports headlessly.
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
_qt.QtCore = _qtc
_qt.QtGui = _qtg
sys.modules.setdefault("PyQt6", _qt)
sys.modules.setdefault("PyQt6.QtCore", _qtc)
sys.modules.setdefault("PyQt6.QtGui", _qtg)

from core.sound.song_table_manager import (
    load_song_table, write_song_table, write_songs_h, update_midi_cfg_flags)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class SongGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        # 4 songs on disk: indices 0..3
        labels = ["mus_a", "se_b", "se_rupee", "se_rupee_big"]
        table = "gSongTable::\n" + "".join(
            f"\tsong {l}, {0 if i == 0 else 4}, 0\n" for i, l in enumerate(labels)
        ) + "\ndummy_song_header:\n\t.byte 0, 0, 0, 0\n"
        _write(os.path.join(self.root, "sound", "song_table.inc"), table)
        consts = ["MUS_A", "SE_B", "SE_RUPEE", "SE_RUPEE_BIG"]
        songs_h = ("#ifndef GUARD_CONSTANTS_SONGS_H\n#define GUARD_CONSTANTS_SONGS_H\n\n"
                   + "".join(f"#define {c} {i}\n" for i, c in enumerate(consts))
                   + "\n#define MUS_NONE 0xFFFF\n\n#endif\n")
        _write(os.path.join(self.root, "include", "constants", "songs.h"), songs_h)
        _write(os.path.join(self.root, "sound", "songs", "midi", "midi.cfg"),
               "se_rupee.mid:                  -R05 -G013 -V100\n"
               "se_rupee_big.mid:              -G013 -V100 -P50\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _table_labels(self):
        with open(os.path.join(self.root, "sound", "song_table.inc"),
                  encoding="utf-8") as f:
            return [ln.split()[1].rstrip(",") for ln in f
                    if ln.strip().startswith("song ")]

    def test_guard_blocks_stale_shrink(self):
        data = load_song_table(self.root)
        self.assertEqual(len(data.entries), 4)
        # Simulate a stale model missing the two rupees.
        data.entries = [e for e in data.entries
                        if "rupee" not in e.label]
        self.assertEqual(len(data.entries), 2)
        write_song_table(self.root, data)          # default: no allow_shrink
        write_songs_h(self.root, data)
        # On-disk files must be untouched — all 4 songs still present.
        self.assertEqual(self._table_labels(),
                         ["mus_a", "se_b", "se_rupee", "se_rupee_big"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, "sound", "song_table.inc.prewrite_backup")))

    def test_guard_allows_explicit_shrink(self):
        data = load_song_table(self.root)
        data.entries = data.entries[:3]            # a real delete of the last one
        for i, e in enumerate(data.entries):
            e.index = i
        write_song_table(self.root, data, allow_shrink=True)
        self.assertEqual(self._table_labels(), ["mus_a", "se_b", "se_rupee"])

    def test_guard_allows_growth(self):
        # Adding a song (more than on disk) is always fine.
        data = load_song_table(self.root)
        from core.sound.song_table_manager import SongEntry
        data.entries.append(SongEntry(index=4, label="se_lift_up",
                                      constant="SE_LIFT_UP", music_player=4,
                                      unknown=0))
        write_song_table(self.root, data)
        self.assertIn("se_lift_up", self._table_labels())

    def test_midi_cfg_surgical_update(self):
        ok = update_midi_cfg_flags(self.root, "se_rupee",
                                   priority=77, reverb=20, volume=120)
        self.assertTrue(ok)
        with open(os.path.join(self.root, "sound", "songs", "midi", "midi.cfg"),
                  encoding="utf-8") as f:
            lines = f.read().splitlines()
        rupee = [l for l in lines if l.startswith("se_rupee.mid:")][0]
        self.assertIn("-P77", rupee)
        self.assertIn("-R20", rupee)
        self.assertIn("-V120", rupee)
        self.assertIn("-G013", rupee, "must preserve the voicegroup flag")
        # The other song's line is untouched.
        big = [l for l in lines if l.startswith("se_rupee_big.mid:")][0]
        self.assertIn("-P50", big)
        self.assertIn("-V100", big)


if __name__ == "__main__":
    unittest.main()
