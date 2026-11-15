"""Regression: Piano Roll track operations keep every unsaved edit.

BUGS.md, 2026-11-15 — "Piano Roll track add / remove / duplicate threw away
unsaved edits". Each of the three re-filled the canvas from the song's stored
commands and re-took the "what was loaded" baseline, so pending note edits,
drawn controls and a moved loop vanished, the next save could not see they
had ever been made, a removal's renumbered notes were never applied, the undo
history kept the old track numbers, and mute / solo were reset.

Driven the way a user drives it, in a throwaway copy of the project: Sound
Editor -> select the song -> "Piano Roll" button -> edits -> several track
operations in a row -> Save button -> close -> reopen. Nothing in the real
project is written. The fake sound output is never used (no Play here).
"""
import hashlib
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for _m in [m for m in list(sys.modules) if m == "PyQt6" or m.startswith("PyQt6.")]:
    del sys.modules[_m]

GAME = os.path.join(ROOT_DIR, "pokefirered")

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication(sys.argv)
    from core.sound.song_compiler import find_mid2agb
    import mido
    _QT_OK = True
except Exception:  # pragma: no cover
    _QT_OK = False


def _available():
    return (_QT_OK and bool(find_mid2agb(GAME))
            and os.path.isfile(os.path.join(GAME, "include", "constants", "songs.h")))


def _yes(*a, **k):
    return QMessageBox.StandardButton.Yes


def _key(n):
    return (n["tick"], n["pitch"], n["duration"], n.get("velocity"))


@unittest.skipUnless(_available(), "real PyQt6 / game project / mid2agb unavailable")
class PianoRollTrackOpsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._real_sd = sys.modules.get("sounddevice")
        sys.modules["sounddevice"] = types.SimpleNamespace(OutputStream=None)
        cls._ask = QMessageBox.question
        QMessageBox.question = staticmethod(_yes)
        # A warning would block an offscreen run forever: record it instead.
        cls._warn = QMessageBox.warning
        cls.warnings = []
        QMessageBox.warning = staticmethod(
            lambda parent, title, text, *a, **k: cls.warnings.append(f"{title}: {text}"))
        cls.tmp = tempfile.mkdtemp(prefix="porysuite_trackops_")
        root = cls.root = os.path.join(cls.tmp, "game")
        shutil.copytree(os.path.join(GAME, "sound"), os.path.join(root, "sound"))
        os.makedirs(os.path.join(root, "include", "constants"))
        shutil.copy2(os.path.join(GAME, "include", "constants", "songs.h"),
                     os.path.join(root, "include", "constants"))
        os.makedirs(os.path.join(root, "tools", "mid2agb"))
        shutil.copy2(find_mid2agb(GAME), os.path.join(root, "tools", "mid2agb"))
        from ui.sound_editor_tab import SoundEditorTab
        cls.tab = SoundEditorTab()
        cls.tab.load_project(root)

    @classmethod
    def tearDownClass(cls):
        QMessageBox.question = cls._ask
        QMessageBox.warning = cls._warn
        if cls._real_sd is not None:
            sys.modules["sounddevice"] = cls._real_sd
        else:
            sys.modules.pop("sounddevice", None)
        win = getattr(cls.tab, "_piano_roll_window", None)
        if win is not None:
            try:
                win._is_dirty = False
                win.close()
            except RuntimeError:
                pass
        shutil.rmtree(cls.tmp, ignore_errors=True)
        slug = hashlib.sha1(os.path.abspath(cls.root).encode()).hexdigest()[:12]
        shutil.rmtree(os.path.join(ROOT_DIR, "cache", slug), ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────
    def _midi(self, label, ext):
        return os.path.join(self.root, "sound", "songs", "midi", label + ext)

    def _open(self, label):
        from ui.sound_editor_tab import _ROLE_SONG_KEY
        tree = self.tab._song_tree
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.data(0, _ROLE_SONG_KEY) == label:
                tree.setCurrentItem(item)
                break
        else:
            self.fail(f"{label} is not in the Sound Editor's song list")
        self.tab._btn_piano_roll.click()
        win = self.tab._piano_roll_window
        self.assertIsNotNone(win, "the Piano Roll window did not open")
        return win

    def _save_close(self, win):
        del self.warnings[:]
        win._btn_save.click()
        self.assertEqual(self.warnings, [], "the save showed a warning")
        self.assertFalse(win.has_unsaved_changes(), "the save did not complete")
        win.close()
        _app.processEvents()

    @staticmethod
    def _track_notes(canvas, i):
        return sorted(_key(n) for n in canvas.get_notes() if n.get("track", 0) == i)

    @staticmethod
    def _track_ctrls(canvas, i, kind):
        return sorted((e["tick"], e["value"]) for e in canvas._control_events
                      if e.get("track", 0) == i and e.get("type") == kind)

    # ── tests ──────────────────────────────────────────────────────────
    def test_edits_survive_consecutive_track_ops_then_save_and_reopen(self):
        label = "mus_castle_town"
        mid_before = mido.MidiFile(self._midi(label, ".mid"))
        win = self._open(label)
        c = win._piano_roll.canvas
        n_tracks = len(win._song.tracks)
        self.assertEqual(n_tracks, 7)

        # 1. A note edit on track 1, made the way the canvas makes one.
        c.push_undo()
        note = next(n for n in c._notes if n.get("track", 0) == 0)
        note["pitch"] += 1
        edited_note = _key(note)
        win._mark_dirty()
        # 2. An instrument change on track 2, through the sidebar's signal.
        win._track_sidebar.track_instrument.emit(1, 5)
        # 3. A drawn pan control on track 3, at one of its note starts.
        c.push_undo()
        t3_tick = sorted(n["tick"] for n in c._notes if n.get("track", 0) == 2)[10]
        c._control_events.append({"tick": t3_tick, "type": "PAN", "value": 30, "track": 2})
        win._mark_dirty()
        # 4. Mute track 4 and solo nothing.
        win._track_sidebar._rows[3]._btn_mute.setChecked(True)

        t1 = self._track_notes(c, 0)
        t3_pan = self._track_ctrls(c, 2, "PAN")
        t5 = self._track_notes(c, 4)
        t7 = self._track_notes(c, 6)
        undo_depth = len(c.history._undo)
        self.assertIn(edited_note, t1)
        self.assertIn((t3_tick, 30), t3_pan)

        # Several operations in a row, nothing saved in between.
        win._on_add_track()                      # track 8 (empty)
        win._on_duplicate_track(0)               # track 9 = copy of edited track 1
        win._on_remove_track(5)                  # remove track 6: 7..9 become 6..8
        win._on_add_track()                      # another empty one, track 9
        win._on_remove_track(8)                  # remove it again

        self.assertEqual(len(win._song.tracks), 8)
        self.assertEqual(self._track_notes(c, 0), t1, "track 1's edit was lost")
        self.assertEqual(self._track_ctrls(c, 2, "PAN"), t3_pan, "the drawn pan was lost")
        self.assertEqual(self._track_notes(c, 4), t5)
        self.assertEqual(self._track_notes(c, 5), t7, "track 7 did not move up to 6")
        self.assertEqual(self._track_notes(c, 7), t1, "the duplicate is not the edited track")
        self.assertEqual(self._track_notes(c, 6), [], "the added track should be empty")
        self.assertEqual(next(x.value for x in win._song.tracks[1].commands if x.cmd == "VOICE"),
                         5, "the instrument change was lost")
        self.assertEqual(win._track_sidebar.get_mute_solo_state()["muted"], {3},
                         "mute was reset")
        self.assertEqual(len(c.history._undo), undo_depth + 5,
                         "each track operation should be one undo step, earlier steps kept")
        labels = [t.label for t in win._song.tracks]
        self.assertEqual(len(labels), len(set(labels)), f"duplicate track labels: {labels}")
        self.assertEqual(c._track_index, -1)
        self.assertTrue(win.has_unsaved_changes())

        self._save_close(win)

        # Reopen: what was saved is what was on the canvas.
        win2 = self._open(label)
        c2 = win2._piano_roll.canvas
        try:
            self.assertEqual(len(win2._song.tracks), 7,
                             "expected 7 original - 1 removed + 1 duplicate (the empty "
                             "added track has no notes, so the game build has nothing to keep)")
            self.assertEqual(self._track_notes(c2, 0), t1)
            self.assertIn((t3_tick, 30), self._track_ctrls(c2, 2, "PAN"))
            self.assertEqual(self._track_notes(c2, 4), t5)
            self.assertEqual(self._track_notes(c2, 5), t7)
            self.assertEqual(self._track_notes(c2, 6), t1, "the duplicate was not saved")
            voice = next(x.value for x in win2._song.tracks[1].commands if x.cmd == "VOICE")
            self.assertEqual(voice, 5)
        finally:
            win2._is_dirty = False
            win2.close()

        # The .mid was edited in place: the untouched tracks' events are as
        # they were (tracks 4 and 5 — the ones no edit and no removal touched).
        mid_after = mido.MidiFile(self._midi(label, ".mid"))
        self.assertEqual(mid_before.ticks_per_beat, mid_after.ticks_per_beat)

        def chan_events(mf, ch):
            out, now = [], 0
            for tr in mf.tracks:
                now = 0
                for m in tr:
                    now += m.time
                    if not m.is_meta and getattr(m, "channel", None) == ch:
                        out.append((now, str(m.copy(time=0))))
            return sorted(out)
        for ch in self._channels_untouched(mid_before):
            self.assertEqual(chan_events(mid_before, ch), chan_events(mid_after, ch),
                             f"MIDI channel {ch} was changed although nothing on it was edited")

    def _channels_untouched(self, mid):
        """MIDI channels of tracks 4 and 5 of mus_castle_town (0-based 3, 4),
        found the way mid2agb orders tracks: by (chunk, channel with notes)."""
        order = []
        for tr in mid.tracks:
            seen = []
            for m in tr:
                if m.type == "note_on" and m.velocity > 0 and m.channel not in seen:
                    seen.append(m.channel)
            order.extend(sorted(seen))
        return [order[3], order[4]]

    def test_undo_redo_steps_through_track_ops(self):
        """Track operations are undo steps of their own, and undo / redo keep
        each track's notes with the track they belong to."""
        label = "mus_castle_town"
        win = self._open(label)
        c = win._piano_roll.canvas
        try:
            n = len(win._song.tracks)
            labels0 = [t.label for t in win._song.tracks]
            before = [self._track_notes(c, i) for i in range(n)]
            c.push_undo()
            note = next(x for x in c._notes if x.get("track", 0) == n - 1)
            note["pitch"] += 2
            win._mark_dirty()
            edited_last = self._track_notes(c, n - 1)
            win._on_duplicate_track(0)            # new last track
            win._on_remove_track(2)               # later tracks move up one
            after = [self._track_notes(c, i) for i in range(len(win._song.tracks))]
            labels_after = [t.label for t in win._song.tracks]

            c.undo()                              # the removal
            self.assertEqual(len(win._song.tracks), n + 1)
            self.assertEqual(self._track_notes(c, 2), before[2], "removed track came back empty")
            self.assertEqual(self._track_notes(c, n - 1), edited_last)
            c.undo()                              # the duplicate
            self.assertEqual([t.label for t in win._song.tracks], labels0)
            c.undo()                              # the note edit
            self.assertEqual([self._track_notes(c, i) for i in range(n)], before)
            self.assertFalse(c.history.can_undo())

            for _ in range(3):
                c.redo()
            self.assertEqual([t.label for t in win._song.tracks], labels_after)
            self.assertEqual([self._track_notes(c, i) for i in range(len(win._song.tracks))],
                             after, "redo did not reproduce the edits")
            self.assertFalse(any(x.get("track", 0) >= len(win._song.tracks) for x in c._notes),
                             "notes on a track that does not exist")
        finally:
            win._is_dirty = False
            win.close()
            _app.processEvents()

    def test_loop_move_survives_track_ops(self):
        label = "mus_kakariko"
        win = self._open(label)
        c = win._piano_roll.canvas
        start, end = c._loop_start, c._loop_end
        self.assertIsNotNone(start)
        bar = 24 * 4
        c.set_loop_region(start + bar, end)
        win._mark_dirty()
        t2 = self._track_notes(c, 1)
        win._on_duplicate_track(1)
        win._on_add_track()
        win._on_remove_track(3)
        self.assertEqual((c._loop_start, c._loop_end), (start + bar, end), "the loop move was lost")
        self.assertEqual(self._track_notes(c, 2), t2)
        self._save_close(win)
        win2 = self._open(label)
        try:
            c2 = win2._piano_roll.canvas
            self.assertEqual((c2._loop_start, c2._loop_end), (start + bar, end))
            self.assertEqual(len(win2._song.tracks), 3)
            self.assertEqual(self._track_notes(c2, 2), t2)
        finally:
            win2._is_dirty = False
            win2.close()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main()
