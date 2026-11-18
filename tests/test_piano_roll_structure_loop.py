"""Regression: the Song Structure panel and the canvas agree on the loop, on
the playback clock, and loop edits made there save where they were made.

BUGS.md 2026-11-15 — "Song Structure panel showed and set the loop at text
positions". The panel listed commands at their place in the song's TEXT, where
a pattern call takes no time, so in songs with repeats the loop showed (and
was set) too early: 40 of the project's 81 looping songs. Its Section /
Pattern Call / End Song buttons changed nothing a save wrote.

Real Sound Editor -> Piano Roll path on a throwaway project copy.
"""
import os
import unittest

from piano_roll_gui_support import GuiProjectCase, available

if available():
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import song_loop_region
    import mido
    from PyQt6.QtCore import Qt, QEvent
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication, QDialog
    import ui.piano_roll_structure as structure_mod

COMPLEX = ("mus_kakariko", "mus_lostwoods", "mus_faron_woods")


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class StructureLoopTest(GuiProjectCase):

    def _panel_loop(self, win):
        kinds = {k: t for t, k, _n in win._structure_panel._entries
                 if k in ("loop_start", "loop_end")}
        return kinds.get("loop_start"), kinds.get("loop_end")

    def _mid_markers(self, label):
        mf = mido.MidiFile(self.midi_path(label, ".mid"))
        now, out = 0, []
        for m in mf.tracks[0]:
            now += m.time
            if m.type == "marker" and m.text in ("[", "]"):
                out.append((m.text, now * 24 // mf.ticks_per_beat))
        return out

    def test_panel_and_canvas_show_the_loop_where_it_plays(self):
        for label in COMPLEX:
            with self.subTest(label):
                played = song_loop_region(parse_song_file(self.midi_path(label, ".s")))
                win = self.open_song(label)
                c = win._piano_roll.canvas
                self.assertEqual((c._loop_start, c._loop_end), played)
                self.assertEqual(self._panel_loop(win), played)
                # Pattern repeats are listed each time they play, in order.
                pats = [t for t, k, _n in win._structure_panel._entries if k == "pattern"]
                self.assertGreater(len(pats), 3)
                self.assertEqual(pats, sorted(pats))
                win.close()

    def test_move_loop_end_with_the_panel_save_rebuild_reopen(self):
        label = "mus_kakariko"
        win = self.open_song(label)
        c = win._piano_roll.canvas
        start, end = c._loop_start, c._loop_end
        notes = sorted(map(tuple, (sorted(n.items()) for n in c.get_notes())))
        new_end = end - 96 * 4                       # four bars earlier
        win._structure_panel.set_cursor_tick(new_end)
        win._structure_panel._btn_end_here.click()
        self.assertEqual((c._loop_start, c._loop_end), (start, new_end))
        self.assertEqual(self._panel_loop(win), (start, new_end))
        self.save_close(win)

        self.assert_rebuild_matches(label)
        self.assertEqual(song_loop_region(parse_song_file(self.midi_path(label, ".s"))),
                         (start, new_end))
        self.assertIn(("]", new_end), self._mid_markers(label))
        win2 = self.open_song(label)
        c2 = win2._piano_roll.canvas
        self.assertEqual((c2._loop_start, c2._loop_end), (start, new_end))
        self.assertEqual(self._panel_loop(win2), (start, new_end))
        # Notes before the new loop end are untouched.
        before = [n for n in notes if dict(n)["tick"] < new_end]
        after = sorted(map(tuple, (sorted(n.items()) for n in c2.get_notes())))
        self.assertEqual([n for n in after if dict(n)["tick"] < new_end], before)

    def test_create_then_remove_a_loop_and_undo(self):
        label = "mus_castle_town"                   # plays once, no loop
        win = self.open_song(label)
        c = win._piano_roll.canvas
        self.assertEqual((c._loop_start, c._loop_end), (None, None))
        win._structure_panel.loop_requested.emit(192, 96 * 40)   # what "Set Loop…" emits
        self.assertEqual((c._loop_start, c._loop_end), (192, 3840))
        c.undo()
        self.assertEqual((c._loop_start, c._loop_end), (None, None), "undo kept the loop")
        c.redo()
        self.assertEqual((c._loop_start, c._loop_end), (192, 3840), "redo lost the loop")
        self.save_close(win)
        self.assert_rebuild_matches(label)
        self.assertEqual(song_loop_region(parse_song_file(self.midi_path(label, ".s"))),
                         (192, 3840))

        win2 = self.open_song(label)
        self.assertEqual(self._panel_loop(win2), (192, 3840))
        win2._structure_panel._btn_remove.click()
        self.assertEqual(self._panel_loop(win2), (None, None))
        self.save_close(win2)
        self.assert_rebuild_matches(label)
        self.assertIsNone(song_loop_region(parse_song_file(self.midi_path(label, ".s"))))
        self.assertEqual(self._mid_markers(label), [])

    def test_loop_start_here_on_a_song_with_repeats(self):
        label = "mus_faron_woods"
        win = self.open_song(label)
        c = win._piano_roll.canvas
        start, end = c._loop_start, c._loop_end
        win._structure_panel.set_cursor_tick(start + 96 * 8)
        win._structure_panel._btn_start_here.click()
        self.save_close(win)
        self.assert_rebuild_matches(label)
        self.assertEqual(song_loop_region(parse_song_file(self.midi_path(label, ".s"))),
                         (start + 96 * 8, end))


    # ── the list itself: double-click edits the loop, Delete removes it ──
    # BUGS.md 2026-11-16 "Song Structure list: nothing could be edited or
    # removed from it", and the follow-up "the pattern lines still could not be
    # removed". The loop's lines are saved as loop markers; the pattern lines
    # are switched off with midi.cfg -N (the build writes repeats out in full).

    def _row(self, panel, kind):
        for i, (_t, k, _n) in enumerate(panel._entries):
            if k == kind:
                return i
        self.fail(f"no {kind} line in the list")

    @staticmethod
    def _press_delete(panel, row):
        panel._list.setCurrentRow(row)
        QApplication.sendEvent(panel._list, QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))

    def test_loop_lines_are_edited_and_removed_from_the_list(self):
        label = "mus_kakariko"
        win = self.open_song(label)
        panel, c = win._structure_panel, win._piano_roll.canvas
        start, end = c._loop_start, c._loop_end
        self.assertIsNotNone(start)
        new_start = start + 96 * 4

        # Double-click "Loop starts": the dialog opens on the start box; type a tick.
        seen = {}

        def fake_exec(dlg):
            seen["focus"] = dlg.focusWidget()
            seen["start_box"] = dlg._start
            dlg._start.setValue(new_start)
            return QDialog.DialogCode.Accepted

        real_exec = structure_mod._LoopDialog.exec
        structure_mod._LoopDialog.exec = fake_exec
        try:
            row = self._row(panel, "loop_start")
            panel._list.itemDoubleClicked.emit(panel._list.item(row))
        finally:
            structure_mod._LoopDialog.exec = real_exec
        self.assertIs(seen.get("focus"), seen.get("start_box"),
                      "double-clicking the loop start did not put the cursor in the start box")
        self.assertEqual((c._loop_start, c._loop_end), (new_start, end),
                         "double-clicking the loop start did not change it")
        self.assertEqual(self._panel_loop(win), (new_start, end))
        c.undo()
        self.assertEqual((c._loop_start, c._loop_end), (start, end), "undo kept the edit")
        c.redo()

        # Delete on a pattern line removes the patterns (all of them: the
        # build makes every one) and leaves the loop alone; undo restores.
        self._press_delete(panel, self._row(panel, "pattern"))
        self.assertEqual((c._loop_start, c._loop_end), (new_start, end),
                         "Delete on a pattern line touched the loop")
        self.assertFalse(self._kinds(panel) & {"pattern"},
                         "Delete on a pattern line did not remove the patterns")
        c.undo()
        self.assertIn("pattern", self._kinds(panel), "undo did not bring the patterns back")

        # Delete on the loop's end line removes the loop; undo brings it back.
        self._press_delete(panel, self._row(panel, "loop_end"))
        self.assertEqual((c._loop_start, c._loop_end), (None, None),
                         "Delete on a loop line did not remove the loop")
        c.undo()
        self.assertEqual((c._loop_start, c._loop_end), (new_start, end), "undo lost the loop")

        # Right-click a loop line: Change Loop… and Remove Loop are offered; a
        # pattern line offers neither.
        offered = {}

        class FakeMenu(structure_mod.QMenu):
            pick = None

            def exec(self, *_a):
                acts = [a for a in self.actions()]
                offered[FakeMenu.pick_row] = [(a.text(), a.isEnabled()) for a in acts]
                return next((a for a in acts if a.text() == FakeMenu.pick), None)

        real_menu = structure_mod.QMenu
        structure_mod.QMenu = FakeMenu
        try:
            for kind, pick in (("pattern", None), ("loop_start", "Remove Loop")):
                row = self._row(panel, kind)
                FakeMenu.pick_row, FakeMenu.pick = kind, pick
                pos = panel._list.visualItemRect(panel._list.item(row)).center()
                panel._on_list_menu(pos)
        finally:
            structure_mod.QMenu = real_menu
        enabled = lambda k: [t for t, on in offered[k] if on]
        self.assertIn("Remove Loop", enabled("loop_start"))
        self.assertIn("Change Loop…", enabled("loop_start"))
        self.assertNotIn("Remove Loop", enabled("pattern"))
        self.assertNotIn("Change Loop…", enabled("pattern"))
        self.assertTrue([t for t in enabled("pattern") if t.startswith("Remove All Patterns")],
                        "a pattern line's menu does not offer to remove the patterns")
        self.assertIn("Clear All", enabled("pattern"))
        self.assertEqual((c._loop_start, c._loop_end), (None, None),
                         "right-click Remove Loop did not remove it")

        # Bring the edited loop back and save: the edit is real saved data.
        c.undo()
        self.assertEqual((c._loop_start, c._loop_end), (new_start, end))
        self.save_close(win)
        self.assert_rebuild_matches(label)
        self.assertEqual(song_loop_region(parse_song_file(self.midi_path(label, ".s"))),
                         (new_start, end))

    @staticmethod
    def _kinds(panel):
        return {k for _t, k, _n in panel._entries}

    def _cfg_flags(self, label):
        cfg = open(os.path.join(self.root, "sound", "songs", "midi", "midi.cfg"),
                   encoding="utf-8").read()
        for line in cfg.splitlines():
            head, sep, rest = line.partition(":")
            if sep and head.strip() == label + ".mid":
                return rest.split()
        self.fail(f"{label}.mid has no midi.cfg line")

    def _s_pattern_calls(self, label):
        text = open(self.midi_path(label, ".s"), encoding="latin-1").read()
        # mid2agb writes a pattern call as ".byte PATT".
        return sum(1 for ln in text.splitlines() if ln.split()[:2] == [".byte", "PATT"])

    def test_patterns_are_removed_saved_and_brought_back(self):
        """BUGS.md 2026-11-16 "Song Structure list: the pattern lines could not be
        removed": removing a pattern line removes the build's patterns for the
        song (midi.cfg -N), sounds the same, saves, survives a rebuild and a
        reopen, and can be switched back on."""
        label = "mus_kakariko"
        self.assertNotIn("-N", [f.upper() for f in self._cfg_flags(label)])
        self.assertGreater(self._s_pattern_calls(label), 0)
        others = {ln for ln in open(os.path.join(self.root, "sound", "songs", "midi",
                                                "midi.cfg"), encoding="utf-8")
                  if not ln.startswith(label + ".mid")}
        win = self.open_song(label)
        panel, c = win._structure_panel, win._piano_roll.canvas
        loop = (c._loop_start, c._loop_end)
        notes = self.track_notes(c, 0)

        # Right-click a pattern line -> Remove All Patterns.
        class FakeMenu(structure_mod.QMenu):
            def exec(self, *_a):
                return next(a for a in self.actions()
                            if a.text().startswith("Remove All Patterns"))
        real_menu = structure_mod.QMenu
        structure_mod.QMenu = FakeMenu
        try:
            row = self._row(panel, "pattern")
            panel._on_list_menu(panel._list.visualItemRect(panel._list.item(row)).center())
        finally:
            structure_mod.QMenu = real_menu
        self.assertNotIn("pattern", self._kinds(panel))
        self.assertTrue(win.has_unsaved_changes())
        self.assertEqual((c._loop_start, c._loop_end), loop, "removing patterns moved the loop")
        c.undo()
        self.assertIn("pattern", self._kinds(panel))
        c.redo()
        self.assertNotIn("pattern", self._kinds(panel))
        self.save_close(win)

        self.assertIn("-N", [f.upper() for f in self._cfg_flags(label)])
        self.assertEqual(self._s_pattern_calls(label), 0, "the saved .s still has patterns")
        self.assert_rebuild_matches(label)
        after_others = {ln for ln in open(os.path.join(self.root, "sound", "songs", "midi",
                                                      "midi.cfg"), encoding="utf-8")
                        if not ln.startswith(label + ".mid")}
        self.assertEqual(after_others, others, "another song's midi.cfg line changed")
        # The song list's own copy of the line knows too, so saving the song
        # list later cannot put the patterns back.
        entry = next(e for e in self.tab._song_table.entries if e.label == label)
        self.assertIn("-N", entry.extra_flags.upper().split())

        win2 = self.open_song(label)
        p2, c2 = win2._structure_panel, win2._piano_roll.canvas
        self.assertNotIn("pattern", self._kinds(p2), "reopened song lists patterns again")
        self.assertEqual((c2._loop_start, c2._loop_end), loop)
        self.assertEqual(self.track_notes(c2, 0), notes, "the notes changed")

        # Switch them back on (right-click anywhere in the list).
        class BackMenu(structure_mod.QMenu):
            def exec(self, *_a):
                return next(a for a in self.actions()
                            if a.text() == "Let the Build Make Patterns Again")
        structure_mod.QMenu = BackMenu
        try:
            p2._on_list_menu(p2._list.visualItemRect(p2._list.item(0)).center())
        finally:
            structure_mod.QMenu = real_menu
        self.save_close(win2)
        self.assertNotIn("-N", [f.upper() for f in self._cfg_flags(label)])
        self.assertGreater(self._s_pattern_calls(label), 0, "patterns did not come back")
        self.assert_rebuild_matches(label)
        win3 = self.open_song(label)
        self.assertIn("pattern", self._kinds(win3._structure_panel))
        win3.close()

    def test_clear_all_removes_loop_and_patterns_in_one_undo_step(self):
        label = "mus_vs_trainer"          # (a song no other test here relies on)
        win = self.open_song(label)
        panel, c = win._structure_panel, win._piano_roll.canvas
        loop = (c._loop_start, c._loop_end)
        self.assertIsNotNone(loop[0])
        self.assertIn("pattern", self._kinds(panel))
        self.assertTrue(panel._btn_clear.isEnabled())
        panel._btn_clear.click()
        self.assertEqual((c._loop_start, c._loop_end), (None, None))
        self.assertFalse(self._kinds(panel) & {"pattern", "loop_start", "loop_end"})
        self.assertIn("end", self._kinds(panel), "Clear All removed the song's end line")
        self.assertFalse(panel._btn_clear.isEnabled(), "nothing left to clear")
        c.undo()                                     # ONE step brings both back
        self.assertEqual((c._loop_start, c._loop_end), loop)
        self.assertIn("pattern", self._kinds(panel))
        c.redo()
        self.save_close(win)
        self.assertIn("-N", [f.upper() for f in self._cfg_flags(label)])
        self.assertEqual(self._s_pattern_calls(label), 0)
        self.assertIsNone(song_loop_region(parse_song_file(self.midi_path(label, ".s"))))
        self.assert_rebuild_matches(label)


if __name__ == "__main__":
    unittest.main()
