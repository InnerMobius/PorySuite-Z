"""Which of a song's two files holds its newest music — decided by content.

Forensic report 2026-11-15 (session notes FORENSIC_SONG_PRECEDENCE_REPORT):
the Make button decided by timestamps, git tracking and a per-machine record,
and real builds of disposable vanilla FireRed / Emerald copies lost or hid
music in 16 of 55 checks. The three repaired here:

* **K** — a pull brings a newer .mid and leaves the old .s; on the first Build
  on that machine the old guard protected the tracked .s, so the ROM kept the
  old music.
* **K2** — one commit changes the .mid's music and touches the .s; git writes
  the .s last, so make (and the Build button) ship the old .s.
* **L** — a tracked .s with its own work names a voicegroup that is gone; the
  Build button deleted it and the build remade it from the .mid.

Each test builds a throwaway project from real songs, the game's own song rule
and mid2agb, and a real git repository, then runs real make — the Build
button's steps (core/sound/song_sync.py, the cleanup, make, the after-build
check) and a plain terminal make. Where a risk cannot be prevented without the
game's own Makefile, the test pins the terminal's behaviour so it is
documented, not hidden.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core"), os.path.dirname(__file__)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_midi_cfg_freshness import (_make, _mid2agb, rules_text, GAME, MIDI,  # noqa: E402
                                     PRET_RULES)
from core.sound import song_sync as S  # noqa: E402

SONGS = ("mus_kakariko", "mus_castle_town", "mus_silph", "se_ball")
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false"]
PLACEHOLDER = b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"


def _available():
    return bool(_make() and _mid2agb() and shutil.which("git")
                and os.path.isfile(PRET_RULES)
                and all(os.path.isfile(os.path.join(MIDI, s + ".mid")) for s in SONGS))


class _Project:
    """A throwaway project: real songs, the game's song rule, its mid2agb, git."""

    def __init__(self, rules="whole_cfg", git=True, track_s=True, songs=SONGS):
        self.root = tempfile.mkdtemp(prefix="porysuite_sync_")
        self.md = os.path.join(self.root, "sound", "songs", "midi")
        self.songs = list(songs)
        os.makedirs(self.md)
        tools = os.path.join(self.root, "tools", "mid2agb")
        os.makedirs(tools)
        tool = os.path.join(tools, os.path.basename(_mid2agb()))
        shutil.copy2(_mid2agb(), tool)
        with open(os.path.join(self.root, "audio_rules.mk"), "w", newline="\n") as f:
            f.write(rules_text(rules))
        real_cfg = open(os.path.join(MIDI, "midi.cfg"), "rb").read().decode().replace("\r", "")
        lines = [ln for ln in real_cfg.split("\n")
                 if ln.split(":")[0].strip() in {s + ".mid" for s in self.songs}]
        with open(self.path("midi.cfg"), "wb") as f:
            f.write(("\n".join(lines) + "\n").encode())
        for s in self.songs:
            shutil.copy2(os.path.join(MIDI, s + ".mid"), self.md)
        with open(os.path.join(self.root, "convert.sh"), "w", newline="\n") as f:
            f.write('#!/bin/sh\necho "$2" >> converted.log\nexec "%s" "$@"\n'
                    % tool.replace("\\", "/"))
        with open(os.path.join(self.root, "Makefile"), "w", newline="\n") as f:
            f.write("MID_SUBDIR := sound/songs/midi\nMID := sh convert.sh\ninclude audio_rules.mk\n"
                    "all: $(patsubst %.mid,%.s,$(wildcard sound/songs/midi/*.mid))\n")
        with open(os.path.join(self.root, "sound", "voice_groups.inc"), "w", newline="\n") as f:
            f.write("".join(f"voicegroup{n:03d}::\n" for n in range(200)))
        self.make()                                   # every .s = the build of its .mid
        if git:
            if not track_s:
                with open(os.path.join(self.root, ".gitignore"), "w", newline="\n") as f:
                    f.write("sound/songs/midi/*.s\nconverted.log\nbuild/\n")
            else:
                with open(os.path.join(self.root, ".gitignore"), "w", newline="\n") as f:
                    f.write("converted.log\nbuild/\n")
            self.git("init", "-q")
            self.commit("base")

    # ── plumbing ────────────────────────────────────────────────────────
    def path(self, name):
        return os.path.join(self.md, name)

    def git(self, *args):
        r = subprocess.run(GIT + ["-C", self.root] + list(args), capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(f"git {args}: {r.stderr}")
        return r.stdout

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-qm", msg)

    def cleanup(self):
        try:
            from core.app_info import get_cache_dir
            shutil.rmtree(get_cache_dir(self.root), ignore_errors=True)
        except Exception:
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    def make(self):
        """A real `make all` (what a terminal build does). Returns the songs
        mid2agb converted."""
        log = os.path.join(self.root, "converted.log")
        if os.path.exists(log):
            os.remove(log)
        env = dict(os.environ)
        env["PATH"] = _make() + os.pathsep + env.get("PATH", "")
        r = subprocess.run([os.path.join(_make(), "make.exe"), "all"], cwd=self.root, env=env,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stdout[-600:] + r.stderr[-600:]
        if not os.path.exists(log):
            return []
        return sorted({os.path.basename(l.strip())[:-2] for l in open(log) if l.strip()})

    def build_button(self, keep=None):
        """The Make button's song steps in order, then make, then the
        after-build check. Returns (plan, report, problems, converted);
        report is None when the build was refused (blocking songs, keep=None)."""
        from core.sound.song_integrity import run_sweep
        from ui.mainwindow import MainWindow
        swept = run_sweep(self.root).regenerated
        plan = S.classify_project(self.root, rendered_from_s=swept)
        if plan.blocking and keep is None:
            return plan, None, None, []
        rep = S.apply_plan(plan, keep=[s.label for s in plan.blocking] if keep else [])
        S.expected_rebuilds(plan, rep)
        self.cleanup_log = []
        MainWindow._prune_stale_song_s_files(
            types.SimpleNamespace(log=self.cleanup_log.append), self.root)
        converted = self.make()
        return plan, rep, S.verify_after_build(self.root, rep), converted

    # ── song edits ──────────────────────────────────────────────────────
    def s_text(self, song):
        p = self.path(song + ".s")
        return open(p, "rb").read().decode("latin-1") if os.path.isfile(p) else None

    def built(self, song):
        """What the build makes from the current .mid."""
        from core.sound.safe_song_save import render_s_with_mid2agb, mid2agb_flags
        return render_s_with_mid2agb(self.root, song, open(self.path(song + ".mid"), "rb").read(),
                                     mid2agb_flags(self.root, song))

    def plays(self, song, text):
        a, b = self.s_text(song), text
        if a is None:
            return False
        return S.text_key(a, song) == S.text_key(b, song) or \
            (S.music_key(a, song) is not None and S.music_key(a, song) == S.music_key(b, song))

    def edit_mid(self, song, delta=3):
        """Another program (a DAW) moves one note's pitch in the .mid."""
        import mido
        p = self.path(song + ".mid")
        mf = mido.MidiFile(p)
        for tr in mf.tracks:
            for i, m in enumerate(tr):
                if m.type == "note_on" and m.velocity > 0:
                    old = m.note
                    tr[i] = m.copy(note=old + delta)
                    for j in range(i + 1, len(tr)):
                        o = tr[j]
                        if (o.type == "note_off" or (o.type == "note_on" and o.velocity == 0)) \
                                and o.note == old and o.channel == m.channel:
                            tr[j] = o.copy(note=old + delta)
                            break
                    mf.save(p)
                    return
        raise AssertionError("no note")

    def edit_s(self, song):
        """A direct edit of the .s (by hand, or an old PorySuite): one note's
        pitch moved a semitone. Returns the edited text."""
        names = ["Cn", "Cs", "Dn", "Ds", "En", "Fn", "Fs", "Gn", "Gs", "An", "As", "Bn"]
        p = self.path(song + ".s")
        text = open(p, "rb").read().decode("latin-1")
        m = re.search(r"(\b(?:N\d{2}|TIE)\s*,\s*)([A-G][ns]?)(M?\d)", text)
        base = m.group(2) if len(m.group(2)) == 2 else m.group(2) + "n"
        new = m.group(1) + names[(names.index(base) + 1) % 12] + m.group(3)
        text = text[:m.start()] + new + text[m.end():]
        open(p, "wb").write(text.encode("latin-1"))
        return text

    def age(self, song, s_newer):
        """Put the .s just after (True) or just before (False) its .mid."""
        mt = os.path.getmtime(self.path(song + ".mid"))
        t = mt + 5 if s_newer else mt - 5
        os.utime(self.path(song + ".s"), (t, t))

    def stamp(self, song):
        return (os.path.getmtime(self.path(song + ".s")), os.path.getmtime(self.path(song + ".mid")),
                open(self.path(song + ".s"), "rb").read(), open(self.path(song + ".mid"), "rb").read())


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class ForensicScenariosTest(unittest.TestCase):
    """K, K2, L and the pull / external-MIDI cases, Build button vs terminal."""

    def setUp(self):
        self.p = _Project()

    def tearDown(self):
        self.p.cleanup()

    def test_K_pull_brings_newer_mid_old_s_first_build_builds_the_midi(self):
        p, song = self.p, "mus_kakariko"
        old = p.s_text(song)
        p.edit_mid(song)                      # the pulled commit changed only the .mid
        p.commit("DAW edit")
        p.age(song, s_newer=False)            # git wrote only the .mid: it is newer
        plan, rep, problems, conv = p.build_button()
        self.assertEqual(plan.songs[song].state, S.MIDI_AHEAD, plan.songs[song].reason)
        self.assertIn(song, conv)
        self.assertTrue(p.plays(song, p.built(song)), "the pulled MIDI work must reach the .s")
        self.assertFalse(p.plays(song, old))
        self.assertEqual(problems, [])

    def test_K2_commit_changes_mid_and_touches_s_build_button_builds_the_midi(self):
        p, song = self.p, "mus_castle_town"
        old = p.s_text(song)
        p.edit_mid(song, delta=6)
        with open(p.path(song + ".s"), "ab") as f:
            f.write(b"\n@ touched\n")
        p.commit("DAW edit committed with the old .s")
        p.age(song, s_newer=True)              # git writes x.mid before x.s
        self.assertEqual(p.make(), [], "precondition (the original failure): make alone builds "
                                       "nothing and ships the old .s")
        self.assertTrue(p.plays(song, old))
        plan, rep, problems, conv = p.build_button()
        self.assertEqual(plan.songs[song].state, S.MIDI_AHEAD, plan.songs[song].reason)
        self.assertIn(song, conv)
        self.assertTrue(p.plays(song, p.built(song)))
        self.assertEqual(problems, [])

    def test_K_D_untracked_stock_project_pull_of_mid_is_built(self):
        self.p.cleanup()
        self.p = p = _Project(track_s=False)
        song = "mus_silph"
        p.edit_mid(song)
        p.commit("pull: new .mid")
        plan, rep, problems, conv = p.build_button()
        self.assertEqual(plan.songs[song].state, S.MIDI_AHEAD, plan.songs[song].reason)
        self.assertTrue(p.plays(song, p.built(song)))
        self.assertEqual(problems, [])

    def test_external_midi_edit_is_built_by_both_paths(self):
        p, song = self.p, "mus_kakariko"
        p.edit_mid(song)                        # uncommitted DAW edit
        plan, rep, problems, conv = p.build_button()
        self.assertEqual(plan.songs[song].state, S.MIDI_AHEAD)
        self.assertTrue(p.plays(song, p.built(song)))
        p.edit_mid(song, delta=2)
        self.assertIn(song, p.make(), "terminal make builds a newer .mid too")
        self.assertTrue(p.plays(song, p.built(song)))

    def test_s_edit_survives_build_button_terminal_risk_is_pinned(self):
        p, song = self.p, "mus_silph"
        edited = p.edit_s(song)                 # .s newer work, .mid untouched
        os.utime(p.path(song + ".mid"), None)   # git re-dated the .mid (same content)
        plan, rep, problems, conv = p.build_button()
        self.assertEqual(plan.songs[song].state, S.S_AHEAD, plan.songs[song].reason)
        self.assertTrue(p.plays(song, edited))
        self.assertEqual(problems, [])
        self.assertTrue(os.path.isfile(os.path.join(rep.backup_dir, song + ".s")))
        # Unpreventable without the game's Makefile: a terminal make after git
        # re-dates the .mid rebuilds from it. Pinned so the limit stays documented.
        time.sleep(1.1)
        os.utime(p.path(song + ".mid"), None)
        self.assertIn(song, p.make())
        self.assertFalse(p.plays(song, edited), "terminal make overwrote the .s edit (known limit)")

    def test_both_sides_changed_is_a_conflict_and_nothing_is_touched(self):
        p, song = self.p, "mus_castle_town"
        edited = p.edit_s(song)
        p.edit_mid(song)
        before = p.stamp(song)
        plan, rep, problems, conv = p.build_button()
        self.assertEqual(plan.songs[song].state, S.CONFLICT, plan.songs[song].reason)
        self.assertIsNone(rep, "the build is refused")
        self.assertEqual(p.stamp(song), before, "times and contents untouched")
        plan, rep, problems, conv = p.build_button(keep=True)       # the explicit choice
        self.assertTrue(p.plays(song, edited))
        self.assertTrue(os.path.isfile(os.path.join(rep.backup_dir, song + ".s")))
        self.assertEqual(problems, [])

    def test_L_cleanup_never_deletes_a_s_with_its_own_music(self):
        p, song = self.p, "mus_kakariko"
        p.edit_s(song)
        t = p.s_text(song)
        t = re.sub(r"(\.equ\s+\w+_grp\s*,\s*)voicegroup\w+", r"\1voicegroup_gone", t, count=1)
        open(p.path(song + ".s"), "wb").write(t.encode("latin-1"))
        p.commit("song with its own work, voicegroup later removed")
        from ui.mainwindow import MainWindow
        log = []
        MainWindow._prune_stale_song_s_files(types.SimpleNamespace(log=log.append), p.root)
        self.assertEqual(p.s_text(song), t, "the .s is left exactly as it was")
        self.assertTrue(any("LEFT UNTOUCHED" in l for l in log), log)
        self.assertTrue(any(song in l and "holds music its .mid does not" in l for l in log), log)

    def test_L_cleanup_removes_only_a_provably_old_build_with_a_backup(self):
        p, song = self.p, "mus_silph"
        t = re.sub(r"(\.equ\s+\w+_grp\s*,\s*)voicegroup\w+", r"\1voicegroup_gone", p.s_text(song),
                   count=1)
        open(p.path(song + ".s"), "wb").write(t.encode("latin-1"))
        from ui.mainwindow import MainWindow
        log = []
        MainWindow._prune_stale_song_s_files(types.SimpleNamespace(log=log.append), p.root)
        self.assertIsNone(p.s_text(song), "an old build of its .mid is removed so make remakes it")
        backup = re.search(r"backup: (.+)$", next(l for l in log if song in l)).group(1)
        self.assertEqual(open(backup, "rb").read().decode("latin-1"), t)
        self.assertIn(song, p.make())
        self.assertTrue(p.plays(song, p.built(song)))

    def test_after_build_check_reports_a_kept_song_the_build_changed(self):
        p, song = self.p, "mus_silph"
        p.edit_s(song)
        plan = S.classify_project(p.root)
        rep = S.apply_plan(plan)
        self.assertIn(song, rep.kept)
        shutil.copy2(os.path.join(MIDI, song + ".s"), p.path(song + ".s"))   # something rewrote it
        problems = S.verify_after_build(p.root, rep)
        self.assertTrue(any(song in x and "CHANGED" in x for x in problems), problems)

    def test_branch_switch_builds_each_branch(self):
        p, song = self.p, "mus_kakariko"
        v1 = p.s_text(song)
        p.git("checkout", "-q", "-b", "v2")
        p.edit_mid(song, delta=5)
        open(p.path(song + ".s"), "wb").write(p.built(song).encode("latin-1"))
        p.commit("v2")
        p.git("checkout", "-q", "master")
        self.assertTrue(p.plays(song, v1))
        p.git("checkout", "-q", "v2")
        plan, rep, problems, conv = p.build_button()
        self.assertTrue(p.plays(song, p.built(song)))
        p.git("checkout", "-q", "master")
        plan, rep, problems, conv = p.build_button()
        self.assertEqual(plan.songs[song].state, S.IN_SYNC)
        self.assertTrue(p.plays(song, v1))


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class ProvenanceTest(unittest.TestCase):
    """Unknown provenance is never treated as "the .mid wins" or "the .s wins"."""

    def test_untracked_s_edit_without_history_is_unknown_then_s_ahead_once_recorded(self):
        p = _Project(track_s=False)
        try:
            song = "mus_kakariko"
            p.edit_s(song)
            plan = S.classify_project(p.root)
            self.assertEqual(plan.songs[song].state, S.UNKNOWN, plan.songs[song].reason)
            shutil.copy2(os.path.join(MIDI, song + ".s"), p.path(song + ".s"))
            S.classify_project(p.root)                 # a check while in step: recorded
            edited = p.edit_s(song)
            plan = S.classify_project(p.root)
            self.assertEqual(plan.songs[song].state, S.S_AHEAD, plan.songs[song].reason)
            self.assertTrue(p.plays(song, edited))
        finally:
            p.cleanup()

    def test_no_git_daw_edit_needs_a_record(self):
        p = _Project(git=False)
        try:
            song = "mus_silph"
            p.edit_mid(song)
            self.assertEqual(S.classify_project(p.root).songs[song].state, S.UNKNOWN)
        finally:
            p.cleanup()
        p = _Project(git=False)
        try:
            S.classify_project(p.root)                  # a Build while in step
            p.edit_mid(song)
            plan, rep, problems, conv = p.build_button()
            self.assertEqual(plan.songs[song].state, S.MIDI_AHEAD)
            self.assertTrue(p.plays(song, p.built(song)))
        finally:
            p.cleanup()

    def test_placeholder_song_is_kept_and_survives_the_build_button(self):
        p = _Project()
        try:
            song = "mus_castle_town"
            music = p.s_text(song)
            open(p.path(song + ".mid"), "wb").write(PLACEHOLDER)
            p.commit("song written in PorySuite")
            time.sleep(1.1)
            os.utime(p.path(song + ".mid"), None)       # placeholder looks newer
            plan, rep, problems, conv = p.build_button()
            self.assertIn(plan.songs[song].state, (S.S_AUTHORED, S.IN_SYNC), plan.songs[song].reason)
            self.assertTrue(p.plays(song, music))
            self.assertEqual(problems, [])
        finally:
            p.cleanup()

    def test_identical_music_in_a_different_layout_is_in_sync(self):
        p = _Project()
        try:
            # Different text, same music: an explicit centre pan at the very
            # start of a track sounds exactly like none.
            song = "mus_silph"
            t = p.s_text(song)
            m = re.search(r"^\s*\.byte\s+KEYSH\s*,.*$", t, re.M)
            t2 = t[:m.end()] + "\n\t.byte\t\tPAN   , c_v+0" + t[m.end():]
            self.assertNotEqual(S.text_key(t2, song), S.text_key(t, song))
            open(p.path(song + ".s"), "wb").write(t2.encode("latin-1"))
            self.assertEqual(S.classify_project(p.root).songs[song].state, S.IN_SYNC)
            # And a change a listener would hear is not.
            p.edit_s(song)
            self.assertNotEqual(S.classify_project(p.root).songs[song].state, S.IN_SYNC)
        finally:
            p.cleanup()


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class ImportChoicesTest(unittest.TestCase):
    """The MIDI import's instrument mapping and loop live in the .mid."""

    def setUp(self):
        self.p = _Project()

    def tearDown(self):
        self.p.cleanup()

    def _voices(self, text):
        return [int(v) for v in re.findall(r"VOICE\s*,\s*(\d+)", text)]

    def test_remap_and_custom_loop_are_saved_into_the_mid(self):
        from core.sound.midi_importer import apply_import_choices
        from core.sound.song_parser import parse_song_file
        from core.sound.midi_exporter import song_loop_region, song_time_signatures, bar_starts_for
        p, song = self.p, "mus_silph"
        first = self._voices(p.s_text(song))[0]
        notes = apply_import_choices(p.root, song, {first: (first + 1) % 128},
                                     {'mode': 'custom', 'loop_point': 2, 'play_order': [0, 1],
                                      'sections': [{'name': 'A', 'start': 1, 'end': 2},
                                                   {'name': 'B', 'start': 3, 'end': 8}]})
        self.assertTrue(any("remapped" in n for n in notes), notes)
        text = p.s_text(song)
        self.assertIn((first + 1) % 128, self._voices(text))
        self.assertNotIn(first, self._voices(text)[:1])
        sng = parse_song_file(p.path(song + ".s"))
        start = song_loop_region(sng)[0]
        bars = bar_starts_for(song_time_signatures(sng), start + 1)
        self.assertEqual(start, bars[2], "the loop starts at bar 3")
        self.assertTrue(p.plays(song, p.built(song)), "the .s is exactly the build of the .mid")
        # Any later terminal rebuild from the .mid (a pull re-dating it, make
        # clean, a fresh clone) keeps both choices: they are in the .mid.
        time.sleep(1.1)
        os.utime(p.path(song + ".mid"), None)
        self.assertIn(song, p.make())
        self.assertEqual(song_loop_region(parse_song_file(p.path(song + ".s")))[0], start)
        self.assertIn((first + 1) % 128, self._voices(p.s_text(song)))

    def test_no_loop_layout_is_kept_only_when_it_plays_the_same(self):
        from core.sound.midi_importer import apply_import_choices
        p = self.p
        for song in SONGS:
            notes = apply_import_choices(p.root, song, {}, {'mode': 'no_loop'})
            self.assertEqual(S.classify_project(p.root).songs[song].state, S.IN_SYNC,
                             f"{song}: whatever layout was kept plays what the .mid builds")
            from core.sound.midi_exporter import song_loop_region
            from core.sound.song_parser import parse_song_file
            self.assertIsNone(song_loop_region(parse_song_file(p.path(song + ".s"))), song)
            if any("converter's own layout" in n for n in notes):
                self.assertEqual(S.text_key(p.s_text(song), song), S.text_key(p.built(song), song),
                                 f"{song}: a lossy flatten is not written")
            else:
                self.assertEqual(S.music_key(p.s_text(song), song),
                                 S.music_key(p.built(song), song))

    def test_the_import_dialog_no_longer_moves_times(self):
        src = open(os.path.join(ROOT_DIR, "ui", "dialogs", "midi_import_dialog.py"),
                   encoding="utf-8").read()
        worker = src[src.index("class _ImportWorker"):src.index("def _postprocess_voice_remap")]
        self.assertNotIn("utime", worker)
        self.assertNotIn("_backdate_mid", worker)
        self.assertNotIn("_postprocess_structure", src)


@unittest.skipUnless(_available(), "make / mid2agb / git / the game's songs unavailable")
class MakeButtonDialogTest(unittest.TestCase):
    """The Make button's own code: _prepare_song_sync / _verify_song_sync."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _host(self):
        from PyQt6.QtWidgets import QWidget
        w = QWidget()
        w.logged = []
        w.log_message = w.logged.append
        return w

    def test_clean_project_builds_without_asking(self):
        from ui.unified_mainwindow import UnifiedMainWindow
        p = _Project()
        try:
            host = self._host()
            before = {s: p.stamp(s) for s in SONGS}
            r = UnifiedMainWindow._prepare_song_sync(host, p.root, [])
            self.assertIsNone(r, "nothing to keep or rebuild: nothing to verify")
            self.assertTrue(any("in sync" in l for l in host.logged), host.logged)
            self.assertEqual({s: p.stamp(s) for s in SONGS}, before)
        finally:
            p.cleanup()

    def test_conflict_asks_and_cancel_changes_nothing(self):
        from unittest import mock
        from PyQt6.QtWidgets import QMessageBox
        from ui.unified_mainwindow import UnifiedMainWindow
        p = _Project()
        try:
            song = "mus_kakariko"
            p.edit_s(song)
            p.edit_mid(song)
            before = p.stamp(song)
            host = self._host()
            shown = []
            with mock.patch.object(QMessageBox, "exec", lambda box: shown.append(box.text()) or 0):
                r = UnifiedMainWindow._prepare_song_sync(host, p.root, [])
            self.assertIs(r, False, "the build must not run")
            self.assertTrue(shown and song in shown[0], shown)
            self.assertEqual(p.stamp(song), before)
            self.assertTrue(any("cancelled" in l for l in host.logged), host.logged)
        finally:
            p.cleanup()

    def test_after_build_message(self):
        from ui.unified_mainwindow import UnifiedMainWindow
        p = _Project()
        try:
            song = "mus_silph"
            p.edit_s(song)
            host = self._host()
            rep = UnifiedMainWindow._prepare_song_sync(host, p.root, [])
            self.assertIn(song, rep.kept)
            out = []
            dlg = types.SimpleNamespace(_append_colored=lambda text, colour: out.append(text))
            p.make()
            UnifiedMainWindow._verify_song_sync(host, dlg, p.root, rep)
            self.assertTrue(any("as intended" in t for t in out), out)
        finally:
            p.cleanup()


if __name__ == "__main__":
    unittest.main()
