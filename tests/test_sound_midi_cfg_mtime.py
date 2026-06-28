"""Regression: PorySuite must not touch un-edited sound .s files, and must keep
midi.cfg OLDER than every .s.

The build rule is ``%.s: %.mid midi.cfg`` — if midi.cfg is newer than a .s, the
next ``make`` re-runs mid2agb and regenerates the .s from its .mid, reverting any
edit whose .mid has drifted (the "bomb sound keeps dying" bug). The old code
rewrote midi.cfg to NOW and then os.utime-touched EVERY .s — perturbing the mtime
of sounds the user never opened. The fix backdates midi.cfg below the oldest .s
and touches nothing else.
"""
import os
import sys
import time
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.sound.song_table_manager import (
    SongTableData, SongEntry, write_midi_cfg, update_midi_cfg_flags,
    _backdate_midi_cfg,
)

_NAMES = ("se_heart", "se_rupee", "se_small_item")


class MidiCfgMtimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.midi_dir = os.path.join(self.root, "sound", "songs", "midi")
        os.makedirs(self.midi_dir)
        # .s files with KNOWN, well-past mtimes (one far older than 1 hour ago,
        # to prove the old "now - 1h" backdate was insufficient).
        base = time.time() - 100000
        for i, name in enumerate(_NAMES):
            s = os.path.join(self.midi_dir, name + ".s")
            with open(s, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"\t.equ {name}_grp, voicegroup013\n\t.byte 0\n")
            mt = base + i * 1000
            os.utime(s, (mt, mt))
            with open(os.path.join(self.midi_dir, name + ".mid"), "wb") as f:
                f.write(b"MThd")
        self.cfg = os.path.join(self.midi_dir, "midi.cfg")
        with open(self.cfg, "w", encoding="utf-8", newline="\n") as f:
            for name in _NAMES:
                f.write(f"{name}.mid: -V100 -G013\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _data(self):
        d = SongTableData()
        for i, name in enumerate(_NAMES):
            d.entries.append(SongEntry(
                index=i, label=name, constant="SE_" + name.upper(),
                music_player=1, unknown=1, voicegroup_index=13,
                volume=100, midi_filename=name + ".mid"))
        d.rebuild_indices()
        return d

    def _s_mtime(self, name):
        return os.stat(os.path.join(self.midi_dir, name + ".s")).st_mtime

    def test_write_midi_cfg_backdates_below_oldest_s(self):
        before = {n: self._s_mtime(n) for n in _NAMES}
        write_midi_cfg(self.root, self._data())
        cfg_mt = os.stat(self.cfg).st_mtime
        oldest_s = min(self._s_mtime(n) for n in _NAMES)
        self.assertLess(cfg_mt, oldest_s,
                        "midi.cfg must be backdated below the OLDEST .s so the "
                        "build never regenerates a .s from its .mid")

    def test_write_midi_cfg_does_not_touch_s_files(self):
        before = {n: self._s_mtime(n) for n in _NAMES}
        write_midi_cfg(self.root, self._data())
        for n in _NAMES:
            self.assertEqual(self._s_mtime(n), before[n],
                             f"{n}.s mtime was touched — PorySuite must not "
                             f"disturb a sound the user never edited")

    def test_update_flags_backdates_and_leaves_s_untouched(self):
        before = {n: self._s_mtime(n) for n in _NAMES}
        changed = update_midi_cfg_flags(self.root, "se_small_item", volume=90)
        self.assertTrue(changed)
        cfg_mt = os.stat(self.cfg).st_mtime
        self.assertLess(cfg_mt, min(self._s_mtime(n) for n in _NAMES))
        for n in _NAMES:
            self.assertEqual(self._s_mtime(n), before[n])

    def test_backdate_helper_handles_no_s_files(self):
        # An empty dir (no .s) must not raise and must leave cfg alone.
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        cfg = os.path.join(empty, "midi.cfg")
        with open(cfg, "w") as f:
            f.write("x")
        before = os.stat(cfg).st_mtime
        _backdate_midi_cfg(cfg)   # must not raise
        self.assertEqual(os.stat(cfg).st_mtime, before)


if __name__ == "__main__":
    unittest.main()
