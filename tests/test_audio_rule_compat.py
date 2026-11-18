"""Music build protection works for ordinary decomp projects, not just one.

BUGS.md 2026-11-15, "Music build protection assumed one project's layout".
PorySuite must recognise each project's song build rule from the rule itself,
per project, and when it does not recognise it, leave midi.cfg alone. It must
never assume a project tracks its song .s files (upstream pret ignores them as
build output), and never let a midi.cfg setting change go unbuilt. Which of a
song's two files the Make button builds from is tested in test_song_sync.py.

The real-build counterpart — vanilla pret FireRed, pokeemerald-expansion,
the per-song rule and a fork, built with make — is recorded in BUGS.md.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core"), os.path.dirname(__file__)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audio_rules_fixture import UPSTREAM_AUDIO_RULES, write_project  # noqa: E402
from core.sound import midi_cfg_freshness as F  # noqa: E402
from core.sound.midi_cfg_freshness import (  # noqa: E402
    audio_rule_mode, settle_midi_cfg, WHOLE_CFG, PER_SONG, UNKNOWN)

GAME = os.path.join(ROOT_DIR, "pokefirered")
PLACEHOLDER = b"MThd\x00\x00\x00\x06\x00\x01\x00\x00\x00\x60"
LF = "\n"
SILENT_S = "\t.byte\t\tVOICE , 0\n\t.byte\tFINE\n"
SONG_CFG = "song.mid: -E -V100\n"
# A song .s that plays a note (what a PorySuite-written song's .s holds).
MUSIC_S = "\t.byte\t\tVOICE , 0\n\t.byte\t\tN06   , Cn3 , v100\n"

EXPANSION_RULE = UPSTREAM_AUDIO_RULES.replace(
    "$(MID_SUBDIR)/$1.mid $(MID_CFG_PATH)",
    "$(MID_SUBDIR)/$1.mid $(MID_CFG_PATH) $(EXPANSION_BATTLE_CONFIG)")
PER_SONG_RULE = UPSTREAM_AUDIO_RULES.replace(
    "# $1: Source path no extension, $2 Options\n",
    "MID_FLAGS_DIR := build/midi_flags\n"
    "$(shell mkdir -p $(MID_FLAGS_DIR) && tr -d '\\r' < $(MID_CFG_PATH) | while read -r name rest; "
    "do f=x; done)\n\n# $1: Source path no extension, $2 Options\n").replace(
    "$(MID_SUBDIR)/$1.mid $(MID_CFG_PATH)", "$(MID_SUBDIR)/$1.mid $(MID_FLAGS_DIR)/$1.flags")
RECIPE_FLAGS_RULE = UPSTREAM_AUDIO_RULES.replace(
    "define MID_RULE\n",
    "MID_FLAGS_DIR := build/midi_flags\ndefine MID_RULE\n"
    "$(MID_FLAGS_DIR)/$1.flags: $(MID_CFG_PATH)\n\t@printf '%s\\n' '$2' > $$@\n").replace(
    "$(MID_SUBDIR)/$1.mid $(MID_CFG_PATH)\n\t$(MID)", "$(MID_SUBDIR)/$1.mid $(MID_FLAGS_DIR)/$1.flags\n\t$(MID)")
LITERAL_CFG_FORK = UPSTREAM_AUDIO_RULES.replace(
    "$(MID_SUBDIR)/$1.mid $(MID_CFG_PATH)", "$(MID_SUBDIR)/$1.mid $(MID_SUBDIR)/midi.cfg")
NO_CFG_FORK = UPSTREAM_AUDIO_RULES.replace(
    "$(MID_SUBDIR)/$1.mid $(MID_CFG_PATH)", "$(MID_SUBDIR)/$1.mid")


def _mid2agb():
    try:
        from core.sound.song_compiler import find_mid2agb
        return find_mid2agb(GAME)
    except Exception:
        return None


class _Project:
    def __init__(self, rules=UPSTREAM_AUDIO_RULES, makefile=None, tool=True):
        self.root = tempfile.mkdtemp(prefix="porysuite_compat_")
        self.md = os.path.join(self.root, "sound", "songs", "midi")
        os.makedirs(self.md)
        write_project(self.root, rules)
        if makefile is not None:
            with open(os.path.join(self.root, "Makefile"), "w", newline="\n") as f:
                f.write(makefile)
        if tool and _mid2agb():
            os.makedirs(os.path.join(self.root, "tools", "mid2agb"))
            shutil.copy2(_mid2agb(), os.path.join(self.root, "tools", "mid2agb"))

    def path(self, name):
        return os.path.join(self.md, name)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class RuleDetectionTest(unittest.TestCase):

    def _mode(self, **kw):
        p = _Project(tool=False, **kw)
        try:
            return audio_rule_mode(p.root)
        finally:
            p.cleanup()

    def test_upstream_and_expansion_rules_are_recognised(self):
        self.assertEqual(self._mode(), WHOLE_CFG)
        self.assertEqual(self._mode(rules=EXPANSION_RULE), WHOLE_CFG)

    def test_per_song_rule_is_recognised(self):
        self.assertEqual(self._mode(rules=PER_SONG_RULE), PER_SONG)

    def test_unrecognised_rules_are_unknown(self):
        self.assertEqual(self._mode(rules=RECIPE_FLAGS_RULE), UNKNOWN)
        self.assertEqual(self._mode(rules=LITERAL_CFG_FORK), UNKNOWN)
        self.assertEqual(self._mode(rules=NO_CFG_FORK), UNKNOWN)
        self.assertEqual(self._mode(makefile="MID_SUBDIR = sound/songs/midi\n# include audio_rules.mk\n"),
                         UNKNOWN, "a commented-out include is not an include")
        self.assertEqual(self._mode(makefile="MID_SUBDIR = music\ninclude audio_rules.mk\n"), UNKNOWN)
        self.assertEqual(audio_rule_mode(os.path.join(tempfile.gettempdir(), "no_such_project_x")), UNKNOWN)

    def test_a_generated_flags_folder_does_not_change_the_answer(self):
        p = _Project(tool=False)
        try:
            os.makedirs(os.path.join(p.root, "build", "midi_flags"))
            self.assertEqual(audio_rule_mode(p.root), WHOLE_CFG)
        finally:
            p.cleanup()

    def test_each_project_gets_its_own_answer_when_switching(self):
        a, b, c = _Project(tool=False), _Project(rules=PER_SONG_RULE, tool=False), \
            _Project(rules=LITERAL_CFG_FORK, tool=False)
        try:
            for _ in range(2):
                self.assertEqual([audio_rule_mode(x.root) for x in (a, b, c)],
                                 [WHOLE_CFG, PER_SONG, UNKNOWN])
                self.assertEqual([settle_midi_cfg(x.root).mode for x in (a, b, c)],
                                 [WHOLE_CFG, PER_SONG, UNKNOWN])
        finally:
            for x in (a, b, c):
                x.cleanup()


@unittest.skipUnless(_mid2agb(), "mid2agb unavailable")
class ContentCheckTest(unittest.TestCase):

    def setUp(self):
        F._HEADER_CACHE.clear()
        self.p = _Project()
        from core.sound.safe_song_save import render_s_with_mid2agb
        from core.sound.midi_cfg_freshness import _probe_midi
        with open(self.p.path("song_a.s"), "w", newline="\n") as f:
            f.write(render_s_with_mid2agb(self.p.root, "song_a", _probe_midi(), ["-E", "-V100"]))
        with open(self.p.path("song_a.mid"), "wb") as f:
            f.write(_probe_midi())

    def tearDown(self):
        self.p.cleanup()

    def test_label_change_is_a_change(self):
        self.assertTrue(F.song_reflects_cfg(self.p.root, "song_a", ("-E", "-V100")))
        self.assertFalse(F.song_reflects_cfg(self.p.root, "song_a", ("-E", "-V100", "-Lother")),
                         "a -L change was treated as nothing")

    def test_an_option_the_s_cannot_show_is_never_assumed_current(self):
        self.assertFalse(F.song_reflects_cfg(self.p.root, "song_a", ("-E", "-V100", "-Z9")))


class UnknownRuleTest(unittest.TestCase):

    def test_midi_cfg_is_left_alone_and_only_placeholder_songs_are_protected(self):
        p = _Project(rules=LITERAL_CFG_FORK, tool=False)
        try:
            t = time.time() - 5000
            for name, data in (("real.mid", b"MThd" + b"\x00" * 60), ("ps.mid", PLACEHOLDER)):
                with open(p.path(name), "wb") as f:
                    f.write(data)
            for name in ("real.s", "ps.s"):
                with open(p.path(name), "w") as f:
                    f.write(MUSIC_S)
            for name in ("real.mid", "ps.mid", "real.s", "ps.s"):
                os.utime(p.path(name), (t, t))
            with open(p.path("midi.cfg"), "w") as f:
                f.write("real.mid: -E\nps.mid: -E\n")
            cfg_mt = os.path.getmtime(p.path("midi.cfg"))
            r = settle_midi_cfg(p.root)
            self.assertEqual(r.mode, UNKNOWN)
            self.assertEqual(os.path.getmtime(p.path("midi.cfg")), cfg_mt, "midi.cfg was moved")
            self.assertEqual(os.path.getmtime(p.path("real.s")), t, "a real song's .s was moved")
            self.assertEqual(r.protected, ["ps"])
            self.assertGreater(os.path.getmtime(p.path("ps.s")), cfg_mt)
        finally:
            p.cleanup()


class EmptySongAndSweepTest(unittest.TestCase):

    def test_an_empty_upstream_song_is_not_treated_as_porysuite_written(self):
        """pret FireRed's mus_dummy.mid is a 26-byte empty MIDI; its .s is
        silent. It must stay an ordinary song the build may rebuild."""
        from core.sound.midi_ownership import is_porysuite_written
        p = _Project(tool=False)
        try:
            with open(p.path("dummy.mid"), "wb") as f:
                f.write(PLACEHOLDER)
            with open(p.path("dummy.s"), "w") as f:
                f.write(SILENT_S)
            with open(p.path("ps.mid"), "wb") as f:
                f.write(PLACEHOLDER)
            with open(p.path("ps.s"), "w") as f:
                f.write(MUSIC_S)
            self.assertFalse(is_porysuite_written(p.path("dummy.s"), p.path("dummy.mid")))
            self.assertTrue(is_porysuite_written(p.path("ps.s"), p.path("ps.mid")))
        finally:
            p.cleanup()

    @unittest.skipUnless(_mid2agb(), "mid2agb unavailable")
    def test_sweep_never_rewrites_a_tempo_change_it_did_not_write(self):
        """A composer's (or a pull's) tempo-only change to a .mid, with the
        .s still built from the old one: the sweep must not 'refresh' the
        .mid from the stale .s — that would revert the change."""
        import io
        import mido
        from core.sound.safe_song_save import render_s_with_mid2agb
        from core.sound.song_integrity import run_sweep
        p = _Project()
        try:
            mf = mido.MidiFile(type=1, ticks_per_beat=96)
            tr = mido.MidiTrack()
            tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
            for i in range(8):
                tr.append(mido.Message("note_on", note=60 + i, velocity=100, time=0))
                tr.append(mido.Message("note_off", note=60 + i, velocity=0, time=48))
            mf.tracks.append(tr)
            buf = io.BytesIO()
            mf.save(file=buf)
            with open(p.path("midi.cfg"), "w") as f:
                f.write(SONG_CFG)
            with open(p.path("song.s"), "w", newline=LF) as f:
                f.write(render_s_with_mid2agb(p.root, "song", buf.getvalue(), ["-E", "-V100"]))
            tr[0] = mido.MetaMessage("set_tempo", tempo=400000, time=0)   # the new tempo
            mf.save(p.path("song.mid"))
            before = open(p.path("song.mid"), "rb").read()
            run_sweep(p.root)
            self.assertEqual(open(p.path("song.mid"), "rb").read(), before,
                             "the sweep reverted somebody's tempo change")
        finally:
            p.cleanup()


if __name__ == "__main__":
    unittest.main()
