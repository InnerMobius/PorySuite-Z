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
    _backdate_midi_cfg, voicegroup_index_from_name,
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

    def test_voicegroup_index_from_name(self):
        self.assertEqual(voicegroup_index_from_name("voicegroup013"), 13)
        self.assertEqual(voicegroup_index_from_name("voicegroup008"), 8)
        self.assertEqual(voicegroup_index_from_name("voicegroup8"), 8)
        self.assertIsNone(voicegroup_index_from_name(None))
        self.assertIsNone(voicegroup_index_from_name("nope"))

    def test_update_flags_syncs_G_and_preserves_other_flags(self):
        # The voicegroup-desync fix: a save must be able to change -G to the
        # song's current bank so the mid2agb recompile can't revert it. Passing
        # the other flags too must PRESERVE them (a None arg drops a flag).
        ok = update_midi_cfg_flags(self.root, "se_small_item",
                                   voicegroup=voicegroup_index_from_name("voicegroup008"),
                                   volume=90, reverb=50, priority=0)
        self.assertTrue(ok)
        line = [l for l in open(self.cfg, encoding="utf-8")
                if l.startswith("se_small_item.mid")][0]
        self.assertIn("-G008", line, "-G must be synced to the new voicegroup")
        self.assertIn("-V090", line, "-V must be preserved, not dropped")
        self.assertIn("-R50", line, "-R must be preserved when passed")
        # every OTHER song's line is untouched (surgical, one-line update)
        others = [l for l in open(self.cfg, encoding="utf-8")
                  if l.startswith("se_heart.mid") or l.startswith("se_rupee.mid")]
        for l in others:
            self.assertIn("-G013", l)

    def test_save_paths_still_wire_the_voicegroup_G_sync(self):
        # GUARD against the exact regression the user hit ("the -G thing didn't
        # stick"): a save path calls recompile_song, which regenerates the .s
        # from midi.cfg's -G — so it MUST first update_midi_cfg_flags(...,
        # voicegroup=...). If a refactor drops that arg, the bank silently
        # reverts again. Assert the wiring is present in each save path body.
        import re as _re
        checks = [
            ("ui/piano_roll_window.py", r"def save_to_disk"),
            ("ui/sound_editor_tab.py", r"def _save_song_via_mid2agb"),
        ]
        for rel, fn_re in checks:
            src = open(os.path.join(ROOT_DIR, rel), encoding="utf-8").read()
            m = _re.search(fn_re, src)
            self.assertIsNotNone(m, f"{fn_re} not found in {rel}")
            # body = from the def to the next top-level def
            body = src[m.start():]
            nxt = _re.search(r"\n    def ", body[10:])
            body = body[:nxt.start() + 10] if nxt else body
            self.assertIn("voicegroup", body,
                          f"{rel}:{fn_re} must pass voicegroup to update_midi_cfg_flags "
                          f"before recompile_song, or a bank change reverts")
            self.assertIn("recompile_song", body)

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
