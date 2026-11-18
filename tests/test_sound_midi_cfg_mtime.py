"""Regression: PorySuite's own midi.cfg writes must not make the build rebuild
songs they did not change, and must not hide a song they did change.

The build rule is ``%.s: %.mid midi.cfg`` — if midi.cfg is newer than a .s, the
next ``make`` re-runs mid2agb and regenerates the .s from its .mid. The oldest
code touched EVERY .s after a write; the next moved midi.cfg below the oldest
.s without looking at it, which also hid real flag changes from the build.
Now (midi_cfg_freshness) the decision is by content: a song whose .s header
already carries its current flags is left alone, and a song whose flags really
changed is left for the build to rebuild. The songs here are real mid2agb
output, so their headers are what the build writes. See also
tests/test_midi_cfg_freshness.py, which asks make itself.
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

import shutil

from core.sound.song_table_manager import (
    SongTableData, SongEntry, write_midi_cfg, update_midi_cfg_flags,
    voicegroup_index_from_name,
)
from core.sound.song_compiler import find_mid2agb

GAME = os.path.join(ROOT_DIR, "pokefirered")

_NAMES = ("se_heart", "se_coin", "se_small_item")


@unittest.skipUnless(find_mid2agb(GAME), "the game's mid2agb is unavailable")
class MidiCfgMtimeTest(unittest.TestCase):
    def setUp(self):
        from core.sound import midi_cfg_freshness
        from core.sound.midi_cfg_freshness import _probe_midi
        midi_cfg_freshness._HEADER_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.midi_dir = os.path.join(self.root, "sound", "songs", "midi")
        os.makedirs(self.midi_dir)
        os.makedirs(os.path.join(self.root, "tools", "mid2agb"))
        shutil.copy2(find_mid2agb(GAME), os.path.join(self.root, "tools", "mid2agb"))
        # An upstream-shaped project: pret's Makefile shape and song rule.
        from audio_rules_fixture import write_project
        write_project(self.root)
        self.cfg = os.path.join(self.midi_dir, "midi.cfg")
        with open(self.cfg, "w", encoding="utf-8", newline="\n") as f:
            for name in _NAMES:
                f.write(f"{name}.mid: -V100 -G013\n")
        # Real mid2agb output for those flags, with KNOWN, well-past mtimes
        # (one far older than 1 hour ago, which the old "now - 1h" missed).
        from core.sound.safe_song_save import render_s_with_mid2agb
        base = time.time() - 100000
        for i, name in enumerate(_NAMES):
            mid = os.path.join(self.midi_dir, name + ".mid")
            with open(mid, "wb") as f:
                f.write(_probe_midi())
            s = os.path.join(self.midi_dir, name + ".s")
            with open(s, "w", encoding="latin-1", newline="\n") as f:
                f.write(render_s_with_mid2agb(self.root, name, _probe_midi(),
                                              ["-V100", "-G013"]))
            os.utime(mid, (base - 10, base - 10))
            mt = base + i * 1000
            os.utime(s, (mt, mt))
        os.utime(self.cfg, (base - 5, base - 5))

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

    def test_update_flags_leaves_only_that_song_to_rebuild(self):
        # A real flag change is NOT hidden from the build (the old backdate hid
        # it): that song's .s is left older than midi.cfg, the others untouched
        # and newer than midi.cfg.
        before = {n: self._s_mtime(n) for n in _NAMES}
        changed = update_midi_cfg_flags(self.root, "se_small_item", volume=90)
        self.assertTrue(changed)
        cfg_mt = os.stat(self.cfg).st_mtime
        self.assertLess(self._s_mtime("se_small_item"), cfg_mt,
                        "the song whose flags changed must be left for the build")
        for n in ("se_heart", "se_coin"):
            self.assertEqual(self._s_mtime(n), before[n])
            self.assertLess(cfg_mt, self._s_mtime(n))

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
                  if l.startswith("se_heart.mid") or l.startswith("se_coin.mid")]
        for l in others:
            self.assertIn("-G013", l)

    def test_save_paths_still_wire_the_voicegroup_G_sync(self):
        # GUARD against the exact regression the user hit ("the -G thing didn't
        # stick"): a save path rebuilds the .s with mid2agb using midi.cfg's -G,
        # so it MUST pass the song's current voicegroup. If a refactor drops
        # that arg, the bank silently reverts again. Assert the wiring is
        # present in each save path body.
        import re as _re
        # Neither editor calls recompile_song any more (it is gone): the Sound
        # Editor's header save goes through safe_song_save.save_song_header and
        # the Piano Roll through safe_song_save.save_editor_song, both of which
        # build the .s with the NEW flags (including -G) and check it before
        # writing. The guarantee is the same — the current voicegroup must be
        # passed every time.
        checks = [
            ("ui/piano_roll_window.py", r"def save_to_disk", "save_editor_song"),
            ("ui/sound_editor_tab.py", r"def _save_song_inline", "save_song_header"),
        ]
        for rel, fn_re, rebuild_call in checks:
            src = open(os.path.join(ROOT_DIR, rel), encoding="utf-8").read()
            m = _re.search(fn_re, src)
            self.assertIsNotNone(m, f"{fn_re} not found in {rel}")
            # body = from the def to the next top-level def
            body = src[m.start():]
            nxt = _re.search(r"\n    def ", body[10:])
            body = body[:nxt.start() + 10] if nxt else body
            self.assertIn("voicegroup", body,
                          f"{rel}:{fn_re} must pass the current voicegroup to the "
                          f"rebuild, or a bank change reverts")
            self.assertIn(rebuild_call, body)


if __name__ == "__main__":
    unittest.main()
