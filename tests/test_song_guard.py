"""Song build protection (core/sound/song_guard.py + song_guard.sh): terminal
builds, git and `make clean` can no longer lose or hide song music.

Forensic report 2026-11-15: 11 of the 16 losses happened with no PorySuite step
at all — a terminal make after midi.cfg was rewritten or a .mid re-dated (B,
J), one commit changing the .mid and touching the .s (K2), a placeholder song
rebuilt into silence (G), and `make clean` deleting every .s (I). PorySuite's
Make button cannot prevent those; only the project's own build can. The guard
is opt-in and installed only by the setup the owner confirms.

Every test builds a throwaway project from real songs, the game's own song
rule, its mid2agb and real git, installs the guard through the real setup, then
runs real `make` / `make modern` / `make clean` / clones / pulls / branch
switches and checks the MUSIC each .s plays — not whether the build passed.
A refusal that leaves both files untouched is a pass where the two genuinely
conflict.
"""
import os
import re
import shutil
import subprocess
import sys
import time
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core"), os.path.dirname(__file__)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_midi_cfg_freshness import _make  # noqa: E402
from test_song_sync import _Project, _available, GIT, PLACEHOLDER  # noqa: E402
from core.sound import song_guard as G  # noqa: E402
from core.sound import song_sync as S  # noqa: E402

MAKEFILE = """\
MID_SUBDIR := sound/songs/midi
MID := sh convert.sh
RULES_NO_SCAN += clean clean-assets tidy
include audio_rules.mk
all: $(patsubst %.mid,%.s,$(wildcard sound/songs/midi/*.mid))
modern: all
clean: tidy clean-assets
tidy:
\trm -rf build
clean-assets:
\trm -f $(MID_SUBDIR)/*.s
\trm -f converted.log
"""


class _GProject(_Project):
    """A throwaway project shaped like pret's (a clean-assets rule that deletes
    every song .s), optionally protected through the real setup."""

    def __init__(self, rules="whole_cfg", track_s=True, protect=True, git=True, root=None):
        if root is not None:                     # a clone of another project
            self.root = root
            self.md = os.path.join(root, "sound", "songs", "midi")
            self.songs = []
            return
        super().__init__(rules=rules, git=False)
        with open(os.path.join(self.root, "Makefile"), "w", newline="\n") as f:
            f.write(MAKEFILE)
        self.setup_report = None
        if protect:
            self.protect()
        if git:
            with open(os.path.join(self.root, ".gitignore"), "w", newline="\n") as f:
                f.write(("" if track_s else "sound/songs/midi/*.s\n")
                        + "converted.log\nbuild/\nsong_guard_backups/\n")
            self.git("init", "-q", "-b", "master")
            self.commit("base")

    def protect(self):
        plan = G.plan_install(self.root)
        self.setup_report = G.install_with_records(self.root, plan)
        assert self.setup_report.result.ok, self.setup_report.result.messages
        return self.setup_report

    def run(self, *goals):
        """make <goals> -> (exit code, output, songs mid2agb converted)."""
        log = os.path.join(self.root, "converted.log")
        if os.path.exists(log):
            os.remove(log)
        env = dict(os.environ)
        env["PATH"] = _make() + os.pathsep + env.get("PATH", "")
        r = subprocess.run([os.path.join(_make(), "make.exe")] + list(goals or ["all"]),
                           cwd=self.root, env=env, capture_output=True, text=True, timeout=600)
        conv = []
        if os.path.exists(log):
            conv = sorted({os.path.basename(l.strip())[:-2] for l in open(log) if l.strip()})
        return r.returncode, r.stdout + r.stderr, conv

    def make(self, *goals):
        rc, out, conv = self.run(*goals)
        assert rc == 0, out[-1500:]
        return conv

    def clone(self, where=None):
        dst = where or self.root + "_clone"
        subprocess.run(GIT + ["clone", "-q", self.root, dst], check=True, capture_output=True)
        c = _GProject(root=dst)
        c.songs = list(self.songs)
        return c

    def pull(self, other):
        self.git("fetch", "-q", other.root, "master")
        self.git("merge", "-q", "--no-edit", "FETCH_HEAD")

    def guard(self, *args):
        env = dict(os.environ)
        env["PATH"] = _make() + os.pathsep + env.get("PATH", "")
        return subprocess.run([os.path.join(_make(), "sh.exe"), G.SCRIPT] + list(args), cwd=self.root,
                              env=env, capture_output=True, text=True, timeout=120)

    def cleanup(self):
        super().cleanup()
        def onexc(func, path, _exc):
            os.chmod(path, 0o700)
            func(path)
        for extra in (self.root, self.root + "_clone", self.root + "_clone2"):
            if os.path.exists(extra):
                shutil.rmtree(extra, onexc=onexc)

    def files(self, song):
        return open(self.path(song + ".s"), "rb").read(), open(self.path(song + ".mid"), "rb").read()


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class RecordLineTest(unittest.TestCase):
    """The record line PorySuite writes and the one the script writes agree."""

    def setUp(self):
        self.p = _GProject(git=False)

    def tearDown(self):
        self.p.cleanup()

    def test_fingerprint_and_flag_order_match_the_script(self):
        p = self.p
        names = [p.path(s + ".s") for s in p.songs]
        # add awkward text: CRLF, trailing spaces, a comment-only line, a tab
        odd = p.path("odd.s")
        open(odd, "wb").write(b"\t.byte\tN12 , Cn3 \r\n@ only a comment\r\n  \r\n\tVOL , 80@x\t \nlast")
        out = p.guard("fingerprint", *[os.path.relpath(n, p.root) for n in names + [odd]]).stdout
        for line in out.strip().splitlines():
            digest, rel = line.split(" ", 1)
            self.assertEqual(digest, G.fingerprint(open(os.path.join(p.root, rel), "rb").read()), rel)
        for flags in (["-E", "-R50", "-G127", "-V070", "-P4"], ["-V100", "-G013"], ["-G013", "-V100"],
                      ["-V80", "-V100"], [], ["-N"]):
            self.assertEqual(p.guard("canon", *flags).stdout.strip(), G.canon_flags(flags), flags)

    def test_python_and_script_write_the_same_bytes(self):
        p, song = self.p, "mus_kakariko"
        for crlf in (False, True):
            data = G.strip_stamp(p.files(song)[0]).replace(b"\r\n", b"\n")
            if crlf:
                data = data.replace(b"\n", b"\r\n")
            open(p.path(song + ".s"), "wb").write(data)
            r = p.guard("keep", "sound/songs/midi", song)
            self.assertEqual(r.returncode, 0, r.stderr)
            mid = p.files(song)[1]
            flags = G._cfg(p.root)[song]
            want = G.with_stamp(data, G._sha1(mid), G.SOURCE, G.canon_flags(flags))
            self.assertEqual(p.files(song)[0], want, f"crlf={crlf}")

    def test_setup_records_every_song_and_the_script_agrees(self):
        p = self.p
        self.assertEqual(p.setup_report.stamped, len(p.songs))
        self.assertEqual(p.setup_report.unsettled, {})
        for s in p.songs:
            st = G.parse_stamp(open(p.path(s + ".s"), "rb").read())
            self.assertIsNotNone(st, s)
        self.assertEqual(p.make(), [], "a recorded, in-step project rebuilds nothing")
        status = p.guard("status", "sound/songs/midi").stdout
        self.assertEqual(status.count(": in sync"), len(p.songs), status)

    def test_repeated_builds_after_sync_convert_nothing(self):
        p = self.p
        for _ in range(3):
            self.assertEqual(p.make(), [])
        os.utime(p.path("mus_silph.mid"), None)          # re-dated, same content
        rc, out, conv = p.run()
        self.assertEqual((rc, conv), (0, []), "already what this .mid builds: nothing converted")
        self.assertEqual(p.run()[1].count("song_guard"), 0, "and the next build is silent")


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class SetupTest(unittest.TestCase):
    """Install / remove: exactly three build lines and two new files, backed
    up, refused on unknown shapes, reversible to the byte."""

    def setUp(self):
        self.p = _GProject(protect=False, git=False)
        self.orig = {n: open(os.path.join(self.p.root, n), "rb").read()
                     for n in ("Makefile", "audio_rules.mk")}

    def tearDown(self):
        self.p.cleanup()

    def test_plan_changes_three_lines_and_adds_two_files(self):
        plan = G.plan_install(self.p.root)
        self.assertEqual(plan.names(), [G.SCRIPT, G.MAKEFRAG, "Makefile", "audio_rules.mk"])
        diff = plan.diff(full_new_files=False)
        changed = [l for l in diff.splitlines() if l[:1] in ("+", "-") and not l.startswith(("+++", "---"))]
        self.assertEqual(sorted(changed), sorted([
            "-\trm -f $(MID_SUBDIR)/*.s", "+\t$(SONG_GUARD_CLEAN)",
            "+# Song build protection (PorySuite-Z, opt-in; see song_guard.mk)",
            "+include song_guard.mk",
            "-\t$(MID) $$< $$@ $2", "+\t$(SONG_GUARD) $$< $$@ $2"]), diff)
        self.assertEqual(G.detect(self.p.root).state, G.NOT_INSTALLED, "planning writes nothing")

    def test_install_then_remove_restores_the_build_files_exactly(self):
        p = self.p
        rep = G.install_with_records(p.root, G.plan_install(p.root))
        self.assertTrue(rep.result.ok, rep.result.messages)
        self.assertEqual(G.detect(p.root).state, G.INSTALLED)
        for name, data in self.orig.items():
            self.assertEqual(open(os.path.join(rep.result.backup_dir, name), "rb").read(), data)
        songs_before = {s: p.files(s)[0] for s in p.songs}
        # an unrelated edit the owner makes later survives removal
        with open(os.path.join(p.root, "Makefile"), "a", newline="\n") as f:
            f.write("my_rule:\n\techo mine\n")
        res = G.apply(p.root, G.plan_uninstall(p.root))
        self.assertTrue(res.ok, res.messages)
        self.assertEqual(G.detect(p.root).state, G.NOT_INSTALLED)
        self.assertFalse(os.path.exists(os.path.join(p.root, G.SCRIPT)))
        self.assertEqual(open(os.path.join(p.root, "audio_rules.mk"), "rb").read(),
                         self.orig["audio_rules.mk"])
        self.assertEqual(open(os.path.join(p.root, "Makefile"), "rb").read(),
                         self.orig["Makefile"] + b"my_rule:\n\techo mine\n")
        self.assertEqual({s: p.files(s)[0] for s in p.songs}, songs_before,
                         "removing protection does not touch any song")
        self.assertEqual(p.make(), [], "and the stock rule still builds (record lines are comments)")

    def test_unknown_build_shapes_are_refused_and_nothing_is_written(self):
        p = self.p
        mk = os.path.join(p.root, "Makefile")
        data = self.orig["Makefile"].replace(b"\trm -f $(MID_SUBDIR)/*.s\n", b"\tfind sound -name '*.s' -delete\n")
        open(mk, "wb").write(data)
        with self.assertRaises(G.GuardRefused) as cm:
            G.plan_install(p.root)
        self.assertTrue(any("clean rule" in r for r in cm.exception.reasons), cm.exception.reasons)
        open(mk, "wb").write(self.orig["Makefile"])
        ar = os.path.join(p.root, "audio_rules.mk")
        open(ar, "wb").write(self.orig["audio_rules.mk"].replace(b"\t$(MID) $$< $$@ $2", b"\t$(MID) $< $@ $2"))
        with self.assertRaises(G.GuardRefused):
            G.plan_install(p.root)
        self.assertFalse(os.path.exists(os.path.join(p.root, G.SCRIPT)))

    def test_a_file_changed_after_the_plan_was_shown_writes_nothing(self):
        p = self.p
        plan = G.plan_install(p.root)
        with open(os.path.join(p.root, "Makefile"), "a", newline="\n") as f:
            f.write("# edited meanwhile\n")
        res = G.apply(p.root, plan)
        self.assertFalse(res.ok)
        self.assertEqual(G.detect(p.root).state, G.NOT_INSTALLED)
        self.assertFalse(os.path.exists(os.path.join(p.root, G.SCRIPT)))

    def test_interrupted_setup_rolls_back_and_every_partial_state_is_safe(self):
        p = self.p
        import importlib
        from unittest import mock
        real = importlib.import_module("core.sound.safe_song_save")._replace

        def failing(src, dst, *a, **k):
            if dst.endswith("audio_rules.mk"):
                raise PermissionError("simulated: file in use")
            return real(src, dst, *a, **k)

        with mock.patch("core.sound.safe_song_save._replace", failing):
            res = G.apply(p.root, G.plan_install(p.root))
        self.assertFalse(res.ok)
        self.assertEqual(G.detect(p.root).state, G.NOT_INSTALLED, "rolled back")
        for name, data in self.orig.items():
            self.assertEqual(open(os.path.join(p.root, name), "rb").read(), data)
        # The half-way point the order is designed for: make clean already guarded,
        # the song rule not yet. make clean must delete no song then.
        plan = G.plan_install(p.root)
        for c in plan.changes:
            if c.name == "Makefile":
                open(os.path.join(p.root, "Makefile"), "wb").write(c.new)
        self.assertEqual(G.detect(p.root).state, G.PARTIAL)
        before = {s: p.files(s)[0] for s in p.songs}
        p.make("clean")
        self.assertEqual({s: p.files(s)[0] for s in p.songs}, before,
                         "a half-installed make clean deletes no song")


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class ProtectedTerminalTest(unittest.TestCase):
    """Configuration B: protected project, terminal only (PorySuite not running)."""

    def setUp(self):
        self.p = _GProject()

    def tearDown(self):
        self.p.cleanup()

    def _cfg_rewrite(self, crlf=False):
        """midi.cfg rewritten as a pull does: same text (newer file time), or
        with Windows line endings."""
        cfg = self.p.path("midi.cfg")
        data = open(cfg, "rb").read()
        if crlf:
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        open(cfg, "wb").write(data)

    def test_B_s_edit_survives_midi_cfg_rewrite_and_redated_mid(self):
        p, song = self.p, "mus_silph"
        edited = p.edit_s(song)
        time.sleep(1.1)
        self._cfg_rewrite()
        rc, out, conv = p.run()
        self.assertEqual(rc, 0, out)
        self.assertNotIn(song, conv)
        self.assertTrue(p.plays(song, edited), "the .s edit survives a terminal make")
        self.assertIn("kept", out)
        time.sleep(1.1)
        self._cfg_rewrite(crlf=True)
        rc, out, conv = p.run()
        self.assertEqual(rc, 0, out)
        self.assertTrue(p.plays(song, edited), "and a rewrite with Windows line endings")
        os.utime(p.path(song + ".mid"), None)                 # git re-dated the .mid
        for goal in ("all", "modern"):
            rc, out, conv = p.run(goal)
            self.assertEqual(rc, 0, out)
            self.assertTrue(p.plays(song, edited), goal)

    def test_B_then_real_mid_change_stops_with_both_files_untouched(self):
        p, song = self.p, "mus_castle_town"
        p.edit_s(song)
        p.edit_mid(song)
        before = p.files(song)
        rc, out, conv = p.run()
        self.assertNotEqual(rc, 0, "a genuine conflict stops the build")
        self.assertIn(f"STOPPED - {song}", out)
        self.assertIn("keep", out)
        self.assertEqual(p.files(song), before, "both files untouched")
        # settle it in favour of the .s: kept for good, by every later build
        r = p.guard("keep", "sound/songs/midi", song)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(G.strip_stamp(p.files(song)[0]), G.strip_stamp(before[0]))
        rc, out, conv = p.run()
        self.assertEqual(rc, 0, out)
        self.assertEqual(G.strip_stamp(p.files(song)[0]), G.strip_stamp(before[0]))

    def test_conflict_settled_for_the_mid_keeps_a_backup(self):
        p, song = self.p, "mus_castle_town"
        p.edit_s(song)
        p.edit_mid(song)
        edited = p.files(song)[0]
        self.assertNotEqual(p.run()[0], 0)
        r = p.guard("rebuild", "sound/songs/midi", song)
        self.assertEqual(r.returncode, 0, r.stderr)
        backup = re.search(r"copied to (\S+) and removed", r.stderr).group(1)
        self.assertEqual(open(os.path.join(p.root, backup), "rb").read(), edited)
        self.assertIn(song, p.make())
        self.assertTrue(p.plays(song, p.built(song)))

    def test_K2_one_commit_changing_mid_and_touching_s_rebuilds(self):
        p = self.p
        other = p.clone()
        song = "mus_castle_town"
        other.edit_mid(song, delta=6)
        with open(other.path(song + ".s"), "ab") as f:
            f.write(b"\n@ touched\n")
        other.commit("DAW edit committed with the old .s")
        old = p.s_text(song)
        p.pull(other)                                         # git writes the .s after the .mid
        conv = p.make()
        self.assertIn(song, conv, "the guard checks it whatever the file times say")
        self.assertTrue(p.plays(song, p.built(song)))
        self.assertFalse(p.plays(song, old))

    def test_K_D_pull_of_mid_on_a_fresh_clone_with_no_cache(self):
        p = self.p
        other = p.clone()
        song = "mus_kakariko"
        other.edit_mid(song)
        other.commit("pull: new .mid")
        fresh = other.clone(p.root + "_clone2")              # a new computer: no PorySuite at all
        self.assertIn(song, fresh.make())
        self.assertTrue(fresh.plays(song, fresh.built(song)))
        p.pull(other)
        self.assertIn(song, p.make())
        self.assertTrue(p.plays(song, p.built(song)))

    def test_multiple_songs_in_one_pull_rebuild_only_those(self):
        p = self.p
        other = p.clone()
        other.edit_mid("mus_kakariko")
        other.edit_mid("se_ball", delta=2)
        other.commit("two songs")
        p.pull(other)
        self.assertEqual(p.make(), ["mus_kakariko", "se_ball"])
        for s in ("mus_kakariko", "se_ball"):
            self.assertTrue(p.plays(s, p.built(s)))

    def test_C_pull_of_new_mid_with_s_edited_after_its_build_keeps_the_edit(self):
        p = self.p
        other = p.clone()
        song = "mus_silph"
        other.edit_mid(song)
        other.make()                                          # protected machine: .s rebuilt + recorded
        extra = other.edit_s(song)                            # then hand work on top
        other.commit("new .mid, and the .s with extra work")
        p.pull(other)
        rc, out, conv = p.run()
        self.assertEqual(rc, 0, out)
        self.assertTrue(p.plays(song, extra), "the .s's extra work is the newer work")

    def test_two_machines_conflicting_changes_stop_after_merge(self):
        p = self.p
        other = p.clone()
        song = "mus_kakariko"
        other.edit_mid(song)
        other.commit("machine B: .mid")
        p.edit_s(song)
        p.commit("machine A: .s")
        p.pull(other)
        before = p.files(song)
        rc, out, _ = p.run()
        self.assertNotEqual(rc, 0)
        self.assertIn(f"STOPPED - {song}", out)
        self.assertEqual(p.files(song), before)

    def test_H_branch_switch_builds_each_branch(self):
        p, song = self.p, "mus_silph"
        v1 = p.s_text(song)
        p.git("checkout", "-q", "-b", "v2")
        p.edit_mid(song)
        p.make()
        v2 = p.s_text(song)
        p.commit("v2")
        p.git("checkout", "-q", "master")
        p.make()
        self.assertTrue(p.plays(song, v1))
        p.git("checkout", "-q", "v2")
        p.make()
        self.assertTrue(p.plays(song, v2))

    def test_I_make_clean_keeps_unreconstructable_songs(self):
        p = self.p
        edited = p.edit_s("mus_silph")
        # a song written in PorySuite: its .mid is an empty stand-in
        src = p.s_text("mus_castle_town")
        open(p.path("mus_castle_town.mid"), "wb").write(PLACEHOLDER)
        open(p.path("mus_castle_town.s"), "wb").write(G.strip_stamp(src.encode("latin-1")))
        rc, out, _ = p.run()
        self.assertEqual(rc, 0, out)                          # adopted as the song's source
        self.assertEqual(G.parse_stamp(p.files("mus_castle_town")[0]).s, G.SOURCE)
        pristine = {s: p.s_text(s) for s in ("mus_kakariko", "se_ball")}
        rc, out, _ = p.run("clean")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(p.s_text("mus_kakariko"), "rebuildable songs are cleaned")
        self.assertTrue(p.plays("mus_silph", edited), "the edited song survives make clean")
        self.assertTrue(p.plays("mus_castle_town", src), "the PorySuite-written song survives")
        self.assertIn("KEPT", out)
        p.make()
        for s, t in pristine.items():
            self.assertTrue(p.plays(s, t), f"{s} rebuilt with the same music")
        self.assertTrue(p.plays("mus_silph", edited))

    def test_G_placeholder_song_survives_midi_cfg_rewrite(self):
        p, song = self.p, "mus_castle_town"
        src = p.s_text(song)
        open(p.path(song + ".mid"), "wb").write(PLACEHOLDER)
        open(p.path(song + ".s"), "wb").write(G.strip_stamp(src.encode("latin-1")))
        time.sleep(1.1)
        self._cfg_rewrite()
        rc, out, conv = p.run()
        self.assertEqual(rc, 0, out)
        self.assertTrue(p.plays(song, src), "not rebuilt into silence")

    def test_G_placeholder_only_committed_is_built_silent_with_a_loud_warning(self):
        self.p.cleanup()
        self.p = p = _GProject(track_s=False)
        song = "mus_castle_town"
        open(p.path(song + ".mid"), "wb").write(PLACEHOLDER)
        p.commit("song written in PorySuite; only the stand-in .mid is committed")
        fresh = p.clone()
        rc, out, _ = fresh.run()
        self.assertEqual(rc, 0, out)
        self.assertIn(f"WARNING - {song}", out)
        self.assertIn("never committed", out)

    def test_copied_song_with_a_foreign_record_and_stand_in_mid_is_kept(self):
        p = self.p
        # a song copied from another one (its record names the donor's .mid),
        # with an empty stand-in .mid: the .s is its only music
        donor = p.files("mus_silph")[0].decode("latin-1").replace("mus_silph", "mus_castle_town")
        open(p.path("mus_castle_town.s"), "wb").write(donor.encode("latin-1"))
        open(p.path("mus_castle_town.mid"), "wb").write(PLACEHOLDER)
        rc, out, _ = p.run()
        self.assertEqual(rc, 0, out)
        self.assertTrue(p.plays("mus_castle_town", donor))
        self.assertEqual(G.parse_stamp(p.files("mus_castle_town")[0]).s, G.SOURCE)

    def test_unrecorded_s_that_differs_stops_the_build(self):
        p, song = self.p, "mus_silph"
        edited = p.edit_s(song)
        stripped = G.strip_stamp(edited.encode("latin-1"))
        open(p.path(song + ".s"), "wb").write(stripped)    # written by something else
        rc, out, _ = p.run()
        self.assertNotEqual(rc, 0)
        self.assertIn("no record", out)
        self.assertEqual(p.files(song)[0], stripped)

    def test_unrecorded_s_that_matches_is_recorded_once(self):
        p, song = self.p, "mus_silph"
        data = G.strip_stamp(p.files(song)[0])
        open(p.path(song + ".s"), "wb").write(data)
        self.assertEqual(p.make(), [song])
        self.assertIsNotNone(G.parse_stamp(p.files(song)[0]))
        self.assertEqual(p.make(), [])

    def test_empty_s_is_rebuilt_not_shipped(self):
        p, song = self.p, "se_ball"
        open(p.path(song + ".s"), "wb").close()               # emptied by a crash or a tool
        self.assertIn(song, p.make())
        self.assertTrue(p.plays(song, p.built(song)))

    def test_missing_s_is_built(self):
        p, song = self.p, "se_ball"
        os.remove(p.path(song + ".s"))
        self.assertIn(song, p.make())
        self.assertTrue(p.plays(song, p.built(song)))

    def test_missing_converter_corrupt_midi_and_unknown_flag_change_nothing(self):
        p, song = self.p, "mus_silph"
        p.edit_mid(song)                                     # needs a rebuild
        before = p.files(song)
        conv = os.path.join(p.root, "convert.sh")
        real = open(conv, "rb").read()
        open(conv, "wb").write(b"#!/bin/sh\nexec ./tools/nothing_here \"$@\"\n")
        rc, out, _ = p.run()
        self.assertNotEqual(rc, 0)
        self.assertEqual(p.files(song), before, "missing converter: .s untouched")
        open(conv, "wb").write(real)
        open(p.path(song + ".mid"), "wb").write(b"not a midi file")
        rc, out, _ = p.run()
        self.assertNotEqual(rc, 0)
        self.assertEqual(p.files(song)[0], before[0], "corrupt .mid: .s untouched")
        open(p.path(song + ".mid"), "wb").write(before[1])
        cfg = p.path("midi.cfg")
        data = open(cfg, "rb").read()
        open(cfg, "wb").write(re.sub(rb"(mus_silph\.mid:[^\n]*)", rb"\1 -Q9", data))
        rc, out, _ = p.run()
        self.assertNotEqual(rc, 0)
        self.assertEqual(p.files(song)[0], before[0], "unknown flag: .s untouched")
        self.assertEqual([n for n in os.listdir(p.md) if "song-guard-new" in n], [])

    def test_interrupted_conversion_leaves_the_s_whole(self):
        p, song = self.p, "mus_silph"
        p.edit_mid(song)
        before = p.files(song)[0]
        conv = os.path.join(p.root, "convert.sh")
        real = open(conv, "rb").read()
        # the converter starts writing, then dies (power cut / Ctrl+C / crash)
        open(conv, "wb").write(b'#!/bin/sh\necho "\t.include \\"MPlayDef.s\\"" > "$2"\nkill -9 $$\n')
        rc, out, _ = p.run()
        self.assertNotEqual(rc, 0)
        self.assertEqual(p.files(song)[0], before, "the old .s is whole")
        open(conv, "wb").write(real)
        self.assertIn(song, p.make())
        self.assertTrue(p.plays(song, p.built(song)))

    def test_malformed_cfg_line_and_song_without_line(self):
        p = self.p
        cfg = p.path("midi.cfg")
        open(cfg, "ab").write(b"this line is not a song\nmus_orphan.mid\n")
        rc, out, conv = p.run()
        self.assertEqual(rc, 0, out)
        self.assertEqual(conv, [])


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class PerSongRuleProtectedTest(unittest.TestCase):
    def setUp(self):
        self.p = _GProject(rules="per_song")

    def tearDown(self):
        self.p.cleanup()

    def test_reordered_flags_keep_an_edited_s(self):
        p, song = self.p, "mus_kakariko"
        p.make()
        edited = p.edit_s(song)
        cfg = p.path("midi.cfg")
        data = open(cfg, "rb").read()
        line = re.search(rb"mus_kakariko\.mid:[^\n]*", data).group(0)
        head, flags = line.split(b":", 1)
        new = head + b":  " + b" ".join(reversed(flags.split()))
        open(cfg, "wb").write(data.replace(line, new))
        rc, out, _ = p.run()
        self.assertEqual(rc, 0, out)
        self.assertTrue(p.plays(song, edited), "same flags in another order are not a change")


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class ProtectedBuildButtonTest(unittest.TestCase):
    """The Make button in a protected project writes record lines, not times."""

    def setUp(self):
        self.p = _GProject()

    def tearDown(self):
        self.p.cleanup()

    def _button(self, keep=None):
        plan = S.classify_project(self.p.root)
        self.assertTrue(plan.protected)
        if plan.blocking and keep is None:
            return plan, None
        rep = S.apply_plan(plan, keep=[s.label for s in plan.blocking] if keep else [])
        S.expected_rebuilds(plan, rep)
        return plan, rep

    def test_no_times_are_moved(self):
        p = self.p
        p.edit_s("mus_silph")
        p.edit_mid("mus_kakariko")
        times = {n: os.path.getmtime(os.path.join(p.md, n)) for n in os.listdir(p.md)}
        plan, rep = self._button()
        self.assertEqual(plan.songs["mus_silph"].state, S.S_AHEAD, plan.songs["mus_silph"].reason)
        self.assertEqual(plan.songs["mus_kakariko"].state, S.MIDI_AHEAD)
        for n, t in times.items():
            if n.endswith(".mid") or n == "midi.cfg":
                self.assertEqual(os.path.getmtime(os.path.join(p.md, n)), t, n)
        conv = p.make()
        self.assertIn("mus_kakariko", conv)
        self.assertEqual(S.verify_after_build(p.root, rep), [])

    def test_keep_choice_settles_the_song_for_later_terminal_builds(self):
        p, song = self.p, "mus_castle_town"
        edited = p.edit_s(song)
        p.edit_mid(song)
        plan, rep = self._button()
        self.assertEqual(plan.songs[song].state, S.CONFLICT, plan.songs[song].reason)
        self.assertIsNone(rep)
        plan, rep = self._button(keep=True)
        self.assertEqual(G.parse_stamp(p.files(song)[0]).s, G.SOURCE)
        for _ in range(2):
            rc, out, _ = p.run()
            self.assertEqual(rc, 0, out)
            self.assertTrue(p.plays(song, edited))

    def test_records_answer_on_a_clone_without_history_or_cache(self):
        p = self.p
        edited = p.edit_s("mus_silph")
        p.make()
        p.commit("edit")
        fresh = p.clone()
        plan = S.classify_project(fresh.root)
        self.assertEqual(plan.songs["mus_silph"].state, S.S_AHEAD, plan.songs["mus_silph"].reason)
        self.assertTrue(fresh.plays("mus_silph", edited))

    def test_integrity_sweep_records_the_s_as_source_so_the_terminal_does_not_stop(self):
        p, song = self.p, "mus_castle_town"
        src = p.s_text(song)
        open(p.path(song + ".mid"), "wb").write(PLACEHOLDER)
        from core.sound.song_integrity import run_sweep
        rep = run_sweep(p.root)
        self.assertIn(song, rep.regenerated)
        self.assertEqual(G.parse_stamp(p.files(song)[0]).s, G.SOURCE)
        rc, out, _ = p.run()
        self.assertEqual(rc, 0, out)
        self.assertTrue(p.plays(song, src))

    def test_build_button_no_longer_edits_gitignore(self):
        self.p.cleanup()
        self.p = p = _GProject(track_s=False, protect=False)
        gi = os.path.join(p.root, ".gitignore")
        before = open(gi, "rb").read()
        import ui.unified_mainwindow as U
        self.assertFalse(hasattr(U, "_ensure_song_s_tracked"),
                         "the automatic .gitignore edit is gone (it is an opt-in setup choice now)")
        self.assertEqual(open(gi, "rb").read(), before)


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class UnprotectedLimitsTest(unittest.TestCase):
    """Configuration A: an unmodified pret build. PorySuite's own operations are
    safe (test_song_sync); these pin what a terminal still does there, so the
    limits stay documented — each is prevented in the protected tests above."""

    def setUp(self):
        self.p = _GProject(protect=False)

    def tearDown(self):
        self.p.cleanup()

    def test_terminal_overwrites_s_edit_after_cfg_rewrite(self):
        p, song = self.p, "mus_silph"
        edited = p.edit_s(song)
        time.sleep(1.1)
        cfg = p.path("midi.cfg")
        data = open(cfg, "rb").read()
        open(cfg, "wb").write(data)                          # same text, newer file time
        self.assertIn(song, p.make())
        self.assertFalse(p.plays(song, edited), "known limit without protection")

    def test_make_clean_deletes_every_s(self):
        p = self.p
        p.edit_s("mus_silph")
        p.make("clean")
        self.assertEqual([n for n in os.listdir(p.md) if n.endswith(".s")], [])

    def test_corrupt_midi_deletes_the_s_without_protection(self):
        p, song = self.p, "mus_silph"
        open(p.path(song + ".mid"), "wb").write(b"not a midi file")
        rc, out, _ = p.run()
        self.assertNotEqual(rc, 0)
        s = p.path(song + ".s")
        self.assertTrue(not os.path.exists(s) or os.path.getsize(s) == 0,
                        "mid2agb empties the .s before failing (pinned; the guard prevents it)")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class SetupDialogTest(unittest.TestCase):
    """Tools → Song Build Protection, driven offscreen: nothing is written
    until Install is confirmed; Remove puts the build back."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.p = _GProject(protect=False, track_s=False)

    def tearDown(self):
        self.p.cleanup()

    def test_install_cancel_install_remove(self):
        from unittest import mock
        from PyQt6.QtWidgets import QMessageBox
        from ui.dialogs.song_guard_dialog import SongGuardDialog
        p = self.p
        before = {n: open(os.path.join(p.root, n), "rb").read() for n in ("Makefile", "audio_rules.mk",
                                                                          ".gitignore")}
        dlg = SongGuardDialog(p.root)
        self.assertTrue(dlg._install.isEnabled())
        self.assertIn("SONG_GUARD", dlg._diff.toPlainText())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            dlg._on_install()
        self.assertEqual(G.detect(p.root).state, G.NOT_INSTALLED, "cancel writes nothing")
        dlg._track.setChecked(True)
        self.assertIn(".gitignore", dlg._diff.toPlainText())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(QMessageBox, "information") as info:
            dlg._on_install()
        self.assertEqual(G.detect(p.root).state, G.INSTALLED)
        self.assertIn("song(s) got their protection record", info.call_args[0][2])
        self.assertNotEqual(open(os.path.join(p.root, ".gitignore"), "rb").read(), before[".gitignore"])
        self.assertTrue(dlg._remove.isEnabled())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(QMessageBox, "information"):
            dlg._on_remove()
        self.assertEqual(G.detect(p.root).state, G.NOT_INSTALLED)
        for n in ("Makefile", "audio_rules.mk"):
            self.assertEqual(open(os.path.join(p.root, n), "rb").read(), before[n], n)
        dlg.close()


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class ProtectionStatusTest(unittest.TestCase):
    """The Songs page's status line: protected / unprotected-but-safe /
    unprotected-at-risk, read-only, no warning for rebuildable songs."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.p = _GProject(protect=False, git=False)

    def tearDown(self):
        self.p.cleanup()

    def _tree(self):
        out = {}
        for d, _, fs in os.walk(self.p.root):
            for f in fs:
                q = os.path.join(d, f)
                out[q] = (os.path.getmtime(q), open(q, "rb").read())
        return out

    def test_three_states_and_no_game_file_written(self):
        from ui.song_protection_status import SongProtectionStatus
        p = self.p
        w = SongProtectionStatus()
        before = self._tree()
        rep = G.assess_risk(p.root)
        self.assertEqual(rep.state, G.RISK_SAFE, rep)
        self.assertEqual(self._tree(), before, "the check writes nothing in the project")
        w.show_report(rep)
        self.assertTrue(w._button.isHidden(), "no warning when every song is rebuildable")
        p.edit_s("mus_silph")
        p.age("mus_silph", s_newer=True)
        os.utime(p.path("mus_silph.mid"), None)          # the .mid re-dated too: still at risk
        rep = G.assess_risk(p.root)
        self.assertEqual(rep.state, G.RISK_AT_RISK)
        self.assertEqual(list(rep.at_risk), ["mus_silph"])
        w.show_report(rep)
        text = w._label.text()
        self.assertIn("mus_silph", text)
        self.assertIn("cannot step in", text)
        self.assertNotIn("PorySuite protects", text)
        self.assertFalse(w._button.isHidden(), "offers the way to Tools → Song Build Protection")
        G.install_with_records(p.root, G.plan_install(p.root))
        rep = G.assess_risk(p.root)
        self.assertEqual(rep.state, G.RISK_PROTECTED)
        w.show_report(rep)
        self.assertIn("protection is on", w._label.text())
        self.assertFalse(w._button.isHidden(), "a quiet Details… link to the setup window")
        self.assertEqual(w._button.text(), "Details…")

    def test_placeholder_song_is_at_risk(self):
        p = self.p
        open(p.path("mus_castle_town.mid"), "wb").write(PLACEHOLDER)
        rep = G.assess_risk(p.root)
        self.assertEqual(rep.state, G.RISK_AT_RISK)
        self.assertIn("mus_castle_town", rep.at_risk)


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class LineEndingTest(unittest.TestCase):
    """mid2agb on Windows writes CRLF; pret's .gitattributes stores *.s with LF.
    In a protected project rebuilt songs are written with LF, so after a commit
    ordinary rebuilds leave `git status` clean (365 false changes, 11-15)."""

    def setUp(self):
        self.p = p = _GProject(protect=False, git=False)
        with open(os.path.join(p.root, ".gitattributes"), "w", newline="\n") as f:
            f.write("*.s text eol=lf\n*.mk text eol=lf\nMakefile text eol=lf\n")
        with open(os.path.join(p.root, ".gitignore"), "w", newline="\n") as f:
            f.write("converted.log\nbuild/\nsong_guard_backups/\n")
        p.git("init", "-q", "-b", "master")
        p.git("config", "core.autocrlf", "true")
        self.assertTrue(any(b"\r\n" in p.files(s)[0] for s in p.songs), "precondition: mid2agb wrote CRLF")
        p.protect()
        p.commit("protected")
        self.status = lambda: [l for l in p.git("status", "--porcelain").splitlines() if l.strip()]

    def tearDown(self):
        self.p.cleanup()

    def test_records_are_written_with_lf(self):
        for s in self.p.songs:
            self.assertNotIn(b"\r", self.p.files(s)[0], s)
        self.assertEqual(self.status(), [])

    def test_repeated_builds_midi_rebuild_and_make_clean_leave_git_clean(self):
        p = self.p
        music = {s: G.fingerprint(p.files(s)[0]) for s in p.songs}
        for goal in ("all", "modern", "all"):
            p.make(goal)
            self.assertEqual(self.status(), [], goal)
        os.utime(p.path("mus_silph.mid"), None)           # re-dated: rebuilt by make, same content
        p.make()
        self.assertEqual(self.status(), [], "a re-dated .mid leaves no line-ending change")
        p.edit_mid("mus_kakariko")                        # a song-specific MIDI change
        self.assertIn("mus_kakariko", p.make())
        changed = sorted(l[3:] for l in self.status())
        self.assertEqual(changed, ["sound/songs/midi/mus_kakariko.mid", "sound/songs/midi/mus_kakariko.s"])
        self.assertNotIn(b"\r", p.files("mus_kakariko")[0])
        p.commit("new kakariko")
        self.assertEqual(self.status(), [])
        music["mus_kakariko"] = G.fingerprint(p.files("mus_kakariko")[0])
        p.make("clean")
        p.make()
        self.assertEqual(self.status(), [], "protected make clean + make: no line-ending changes")
        self.assertEqual({s: G.fingerprint(p.files(s)[0]) for s in p.songs}, music, "same music")

    def test_edited_s_kept_with_its_edit(self):
        p = self.p
        edited = p.edit_s("mus_silph")
        p.commit("hand edit")
        os.utime(p.path("mus_silph.mid"), None)
        p.make()
        self.assertTrue(p.plays("mus_silph", edited))
        self.assertEqual(self.status(), [])
