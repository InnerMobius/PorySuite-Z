"""midi.cfg changes rebuild only the songs they really affect — judged by make.

BUGS.md 2026-11-15, "midi.cfg rebuilds every song". Every song's .s depends on
the one shared midi.cfg (the game's audio_rules.mk), so a git checkout / pull
that rewrote it (same content, Windows line endings) made `make` rebuild all
371 songs. PorySuite's answer was to move midi.cfg's clock back by an hour
without looking at it: not enough for songs built days earlier, and blind to
whether the change was real.

These tests ask `make` itself. A throwaway project holds four real songs, the
game's own audio_rules.mk and its mid2agb; `make -n` lists what the build
would rebuild. Covered: an ordinary build, a git-style rewrite (line endings
and spacing only), a real flag change, a modified .mid, a song with no .s yet,
all of them at once, and PorySuite's own flag edit. After a real flag change
the build is actually run, and afterwards nothing is stale.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GAME = os.path.join(ROOT_DIR, "pokefirered")
MIDI = os.path.join(GAME, "sound", "songs", "midi")
SONGS = ("mus_kakariko", "mus_castle_town", "se_ball", "mus_silph")
# devkitPro's first: it is the make PorySuite's Make button runs (and the one
# that treated a flag file whose recipe ran as changed).
_MSYS = [r"C:\devkitPro\msys2\usr\bin", r"C:\msys64\usr\bin"]


def _make():
    for d in _MSYS:
        if os.path.isfile(os.path.join(d, "make.exe")):
            return d
    return None


def _mid2agb():
    try:
        from core.sound.song_compiler import find_mid2agb
        return find_mid2agb(GAME)
    except Exception:
        return None


def _available():
    return bool(_make() and _mid2agb() and os.path.isfile(PRET_RULES))


# The two versions of the song rule in audio_rules.mk. WHOLE_CFG is upstream
# pret's (every song depends on the whole midi.cfg); PER_SONG is the per-song
# rule PorySuite projects may use since 2026-11-15 (each song depends on its
# own flags). Tests build both from a fixed copy of pret's file
# (fixtures/pret_audio_rules.mk), never from the live game's: that one is the
# owner's and changes (installing Song Build Protection rewrote its rule, and
# every test built on it stopped at setup - BUGS.md 2026-11-17).
PRET_RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                          "pret_audio_rules.mk")
WHOLE_CFG = ('# $1: Source path no extension, $2 Options\ndefine MID_RULE\n$(MID_ASM_DIR)/$1.s: $(MID_SUBDIR)/$1.mid $(MID_CFG_PATH)\n\t$(MID) $$< $$@ $2\nendef\n',
             '$(foreach line,$(shell cat $(MID_CFG_PATH) | sed "s/ /__SPACE__/g"),$(call MID_EXPANSION,$(subst __SPACE__, ,$(line))))')
PER_SONG = ('# Each song depends on its OWN flags, not the whole midi.cfg, so a change to\n# one line (or git rewriting the file with other line endings) no longer\n# rebuilds every song. While make reads this file, each song\'s copy\n# build/midi_flags/<song>.flags is rewritten only when that song\'s flags\n# really change; a new copy is dated 1970 (the song was built with the flags in\n# the file when this rule was adopted). Done here, not as a recipe, because\n# some make builds treat a prerequisite whose recipe ran as changed even when\n# the recipe left it alone.\nMID_FLAGS_DIR := build/midi_flags\n$(shell mkdir -p $(MID_FLAGS_DIR) && tr -d \'\\r\' < $(MID_CFG_PATH) | while read -r name rest; do case "$$name" in (*.mid:) ;; (*) continue ;; esac; f="$(MID_FLAGS_DIR)/$${name%.mid:}.flags"; set -- $$rest; new="$$*"; if [ -e "$$f" ]; then old=; read -r old < "$$f"; [ "$$old" = "$$new" ] || printf \'%s\\n\' "$$new" > "$$f"; else printf \'%s\\n\' "$$new" > "$$f"; touch -d @0 "$$f"; fi; done)\n\n# $1: Source path no extension, $2 Options\ndefine MID_RULE\n$(MID_ASM_DIR)/$1.s: $(MID_SUBDIR)/$1.mid $(MID_FLAGS_DIR)/$1.flags\n\t$(MID) $$< $$@ $2\nendef\n',
            '$(foreach line,$(shell cat $(MID_CFG_PATH) | tr -d \'\\r\' | sed "s/ /__SPACE__/g"),$(call MID_EXPANSION,$(subst __SPACE__, ,$(line))))')


def rules_text(kind):
    text = open(PRET_RULES, encoding="utf-8").read().replace("\r", "")
    have = PER_SONG if PER_SONG[0] in text else WHOLE_CFG
    want = PER_SONG if kind == "per_song" else WHOLE_CFG
    for a, b in zip(have, want):
        assert a in text, "audio_rules.mk no longer has the song rule this test knows"
        text = text.replace(a, b)
    return text


@unittest.skipUnless(_available(), "make / mid2agb / the game's audio_rules.mk unavailable")
class MidiCfgFreshnessTest(unittest.TestCase):
    """With upstream's rule (every song depends on the whole midi.cfg):
    PorySuite's content check decides what make rebuilds."""
    RULES = "whole_cfg"
    MID_WRAP = False

    def setUp(self):
        from core.sound import midi_cfg_freshness
        midi_cfg_freshness._HEADER_CACHE.clear()
        self.tmp = tempfile.mkdtemp(prefix="porysuite_cfgfresh_")
        self.root = self.tmp
        self.md = os.path.join(self.root, "sound", "songs", "midi")
        os.makedirs(self.md)
        os.makedirs(os.path.join(self.root, "tools", "mid2agb"))
        tool = os.path.join(self.root, "tools", "mid2agb", os.path.basename(_mid2agb()))
        shutil.copy2(_mid2agb(), tool)
        with open(os.path.join(self.root, "audio_rules.mk"), "w", newline="\n") as f:
            f.write(rules_text(self.RULES))
        real_cfg = open(os.path.join(MIDI, "midi.cfg"), "rb").read().decode().replace("\r", "")
        lines = [ln for ln in real_cfg.split("\n") if ln.split(":")[0].strip() in
                 {s + ".mid" for s in SONGS}]
        self.assertEqual(len(lines), len(SONGS))
        self._write_cfg("\n".join(lines) + "\n")
        for s in SONGS:
            for ext in (".mid", ".s"):
                shutil.copy2(os.path.join(MIDI, s + ext), self.md)
        mk = tool.replace("\\", "/")
        if self.MID_WRAP:
            # Record each song the real build converts, then convert it.
            with open(os.path.join(self.root, "convert.sh"), "w", newline="\n") as f:
                f.write(f'#!/bin/sh\necho "$2" >> converted.log\nexec "{mk}" "$@"\n')
            mk = "sh convert.sh"
        # Named Makefile, as in a real project: PorySuite reads it to recognise
        # the song build rule (midi_cfg_freshness.audio_rule_mode).
        with open(os.path.join(self.root, "Makefile"), "w", newline="\n") as f:
            f.write("MID_SUBDIR := sound/songs/midi\n"
                    f"MID := {mk}\n"
                    "include audio_rules.mk\n"
                    "all: $(patsubst %.mid,%.s,$(wildcard sound/songs/midi/*.mid))\n")
        # A settled starting point: .mid oldest, then midi.cfg, then every .s.
        t = time.time() - 50000
        for s in SONGS:
            os.utime(os.path.join(self.md, s + ".mid"), (t, t))
        os.utime(self.cfg, (t + 100, t + 100))
        for i, s in enumerate(SONGS):
            os.utime(os.path.join(self.md, s + ".s"), (t + 200 + i, t + 200 + i))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────
    @property
    def cfg(self):
        return os.path.join(self.md, "midi.cfg")

    def _write_cfg(self, text, crlf=False):
        data = text.replace("\n", "\r\n") if crlf else text
        with open(os.path.join(self.md, "midi.cfg"), "wb") as f:
            f.write(data.encode())

    def _run_make(self, dry=True):
        env = dict(os.environ)
        env["PATH"] = _make() + os.pathsep + env.get("PATH", "")
        r = subprocess.run([os.path.join(_make(), "make.exe")] + (["-n"] if dry else [])
                           + ["all"],
                           cwd=self.root, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return r.stdout

    def would_rebuild(self):
        out = self._run_make(dry=True)
        return sorted(set(re.findall(r"sound/songs/midi/(\w+)\.s\b", out)))

    def settle(self):
        from core.sound.midi_cfg_freshness import settle_midi_cfg
        return settle_midi_cfg(self.root)

    def _cfg_text(self):
        return open(self.cfg, "rb").read().decode()

    # ── tests ──────────────────────────────────────────────────────────
    def test_ordinary_build_rebuilds_nothing(self):
        self.assertEqual(self.would_rebuild(), [])
        r = self.settle()
        self.assertEqual(r.checked, 0)
        self.assertEqual(self.would_rebuild(), [])

    def test_git_rewrite_with_line_endings_and_spacing_only(self):
        text = self._cfg_text()
        self._write_cfg(re.sub(r":\s+", ":    ", text), crlf=True)   # what a pull leaves
        self.assertEqual(self.would_rebuild(), sorted(SONGS),
                         "without the fix make rebuilds every song (the bug)")
        before = {s: open(os.path.join(self.md, s + ".s"), "rb").read() for s in SONGS}
        r = self.settle()
        self.assertEqual(sorted(r.current), sorted(SONGS))
        self.assertEqual(r.rebuild, [])
        self.assertEqual(self.would_rebuild(), [])
        for s in SONGS:
            self.assertEqual(open(os.path.join(self.md, s + ".s"), "rb").read(), before[s],
                             "settling must never write a song")

    def test_real_flag_change_rebuilds_exactly_that_song(self):
        text = self._cfg_text()
        self._write_cfg(re.sub(r"(se_ball\.mid:.*?)-V\d+", r"\1-V050", text))
        r = self.settle()
        self.assertEqual(r.rebuild, ["se_ball"])
        self.assertEqual(self.would_rebuild(), ["se_ball"])
        self._run_make(dry=False)                       # the real build
        s = open(os.path.join(self.md, "se_ball.s"), "rb").read().decode("latin-1")
        self.assertIn("se_ball_mvl, 50", s)
        self.assertEqual(self.settle().checked, 0)
        self.assertEqual(self.would_rebuild(), [], "after the build nothing is stale")

    def test_modified_midi_still_rebuilds(self):
        mid = os.path.join(self.md, "mus_castle_town.mid")
        t = time.time()
        os.utime(mid, (t, t))                           # edited by another program
        self.settle()
        self.assertEqual(self.would_rebuild(), ["mus_castle_town"])

    def test_new_song_without_s_is_built(self):
        shutil.copy2(os.path.join(MIDI, "mus_lostwoods.mid"), self.md)
        real = open(os.path.join(MIDI, "midi.cfg"), "rb").read().decode().replace("\r", "")
        line = next(ln for ln in real.split("\n") if ln.startswith("mus_lostwoods.mid"))
        self._write_cfg(self._cfg_text() + line + "\n")
        self.settle()
        self.assertEqual(self.would_rebuild(), ["mus_lostwoods"])

    def test_everything_at_once(self):
        text = self._cfg_text()
        text = re.sub(r"(mus_silph\.mid:.*?)-R\d+", r"\1-R40", text)   # real change
        self._write_cfg(text, crlf=True)                                # + git rewrite
        t = time.time()
        os.utime(os.path.join(self.md, "mus_kakariko.mid"), (t, t))    # + a .mid edit
        r = self.settle()
        self.assertEqual(r.rebuild, ["mus_silph"])
        self.assertEqual(self.would_rebuild(), ["mus_kakariko", "mus_silph"])

    def test_porysuite_flag_edit_marks_only_that_song(self):
        from core.sound.song_table_manager import update_midi_cfg_flags
        self.assertTrue(update_midi_cfg_flags(self.root, "se_ball", volume=90))
        self.assertEqual(self.would_rebuild(), ["se_ball"])

    def test_a_song_written_in_porysuite_is_never_sent_to_the_build(self):
        # Its .mid is a placeholder: rebuilding from it would wipe the song.
        with open(os.path.join(self.md, "se_ball.mid"), "wb") as f:
            f.write(b"MThd\x00\x00\x00\x06\x00\x01\x00\x00\x00\x60")
        t = time.time() - 60000
        os.utime(os.path.join(self.md, "se_ball.mid"), (t, t))
        text = self._cfg_text()
        self._write_cfg(re.sub(r"(se_ball\.mid:.*?)-V\d+", r"\1-V050", text))
        r = self.settle()
        self.assertIn("se_ball", r.current)
        self.assertEqual(self.would_rebuild(), [])

    def converted(self):
        """Run the real build; the songs it converted."""
        log = os.path.join(self.root, "converted.log")
        if os.path.exists(log):
            os.remove(log)
        self._run_make(dry=False)
        if not os.path.exists(log):
            return []
        return sorted(set(re.findall(r"midi/(\w+)\.s", open(log).read())))

    def test_settle_without_songs_does_nothing(self):
        from core.sound.midi_cfg_freshness import settle_for_cfg_path
        empty = os.path.join(self.tmp, "x", "sound", "songs", "midi")
        os.makedirs(empty)
        cfg = os.path.join(empty, "midi.cfg")
        open(cfg, "w").write("a.mid: -E\n")
        before = os.path.getmtime(cfg)
        settle_for_cfg_path(cfg)
        self.assertEqual(os.path.getmtime(cfg), before)



class PerSongRuleTest(MidiCfgFreshnessTest):
    """With this project's rule (each song depends on its own flags): make
    alone decides, for every make, and PorySuite leaves midi.cfg's time alone —
    moving it below a song's flag copy would stop make comparing that song's
    flags, and a real change would be missed."""
    RULES = "per_song"
    MID_WRAP = True

    # The whole-cfg tests above are about PorySuite's own decision; here the
    # build decides, so they are replaced by these.
    test_git_rewrite_with_line_endings_and_spacing_only = None
    test_real_flag_change_rebuilds_exactly_that_song = None
    test_modified_midi_still_rebuilds = None
    test_new_song_without_s_is_built = None
    test_everything_at_once = None
    test_porysuite_flag_edit_marks_only_that_song = None
    test_a_song_written_in_porysuite_is_never_sent_to_the_build = None
    test_ordinary_build_rebuilds_nothing = None

    def test_build_alone_rebuilds_only_real_changes(self):
        self.assertEqual(self.converted(), [], "adopting the rule rebuilt songs")
        self.assertEqual(self.converted(), [], "an incremental build rebuilt songs")
        text = self._cfg_text()
        self._write_cfg(re.sub(r":\s+", ":    ", text), crlf=True)
        self.assertEqual(self.converted(), [], "a line-ending rewrite rebuilt songs")
        self._write_cfg(re.sub(r"(se_ball\.mid:.*?)-V\d+", r"\1-V050", self._cfg_text()))
        self.assertEqual(self.converted(), ["se_ball"])
        self.assertIn("se_ball_mvl, 50",
                      open(os.path.join(self.md, "se_ball.s"), "rb").read().decode("latin-1"))
        self.assertEqual(self.converted(), [])
        time.sleep(1.1)
        os.utime(os.path.join(self.md, "mus_silph.mid"), None)
        self.assertEqual(self.converted(), ["mus_silph"])

    def test_porysuite_leaves_midi_cfg_alone_and_changes_are_still_built(self):
        self.assertEqual(self.converted(), [])          # flag copies now exist
        self._write_cfg(re.sub(r"(mus_silph\.mid:.*?)-R\d+", r"\1-R40", self._cfg_text()))
        before = os.path.getmtime(self.cfg)
        r = self.settle()                              # what PorySuite's watcher does
        self.assertEqual(r.mode, "per_song")
        self.assertEqual(os.path.getmtime(self.cfg), before, "PorySuite moved midi.cfg")
        self.assertEqual(self.converted(), ["mus_silph"], "the real change was missed")

    def test_porysuite_flag_edit_is_built(self):
        self.assertEqual(self.converted(), [])
        from core.sound.song_table_manager import update_midi_cfg_flags
        self.assertTrue(update_midi_cfg_flags(self.root, "se_ball", volume=90))
        self.assertEqual(self.converted(), ["se_ball"])


if __name__ == "__main__":
    unittest.main()
