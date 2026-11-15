"""Regression: one Piano Roll undo / redo history covers every kind of edit.

The canvas used to keep its own 50-step stack of notes and drawn controls
only: instrument, volume, pan, tempo, track add / remove / duplicate and loop
changes could not be undone, there was no redo, and a track removal left the
stack's track numbers wrong. Now every edit is a snapshot step in
``ui/piano_roll_history.EditHistory``.

Driven through the real window — mouse double-click / drag on the canvas,
Delete key, the sidebar's widgets, the track buttons, the Song Structure
panel, the Undo / Redo buttons and Ctrl+Z / Ctrl+Y — on a throwaway project.
"""
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    from PyQt6.QtCore import Qt, QPoint
    from PyQt6.QtTest import QTest


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class PianoRollHistoryTest(GuiProjectCase):

    def _snap(self, win):
        c = win._piano_roll.canvas
        song = win._song
        return (
            sorted(tuple(sorted(n.items())) for n in c.get_notes()),
            sorted(tuple(sorted(e.items())) for e in c._control_events),
            (c._loop_start, c._loop_end),
            [t.label for t in song.tracks],
            [[(x.cmd, x.value) for x in t.commands if x.cmd in ("VOICE", "VOL", "PAN", "TEMPO")]
             for t in song.tracks],
        )

    def _point(self, c, tick, pitch):
        return QPoint(int(c._tick_to_x(tick)) + 2, int(c._note_to_y(pitch)) + 2)

    def test_every_edit_kind_undoes_and_redoes(self):
        label = "mus_kakariko"
        win = self.open_song(label)
        win.show()
        app.processEvents()
        c = win._piano_roll.canvas
        self.assertFalse(win._history.can_undo(), "opening a song made undo steps")
        states = [self._snap(win)]

        def step(what):
            app.processEvents()
            s = self._snap(win)
            self.assertNotEqual(s, states[-1], f"{what} changed nothing")
            states.append(s)

        # Notes, with the mouse: create (double-click), move (drag), resize, delete.
        tick, pitch = 96 * 30 + 48, 108          # high above the song's notes
        QTest.mouseDClick(c, Qt.MouseButton.LeftButton, pos=self._point(c, tick, pitch))
        step("double-click create")
        p = self._point(c, tick, pitch)
        QTest.mousePress(c, Qt.MouseButton.LeftButton, pos=p)
        QTest.mouseMove(c, p + QPoint(int(c._tick_to_x(24) - c._tick_to_x(0)), 0))
        QTest.mouseRelease(c, Qt.MouseButton.LeftButton,
                           pos=p + QPoint(int(c._tick_to_x(24) - c._tick_to_x(0)), 0))
        step("drag move")
        new = next(n for n in c._notes if n["pitch"] == pitch)
        r = c._note_rect(new)
        edge = QPoint(int(r.right()) - 1, int(r.center().y()))
        QTest.mousePress(c, Qt.MouseButton.LeftButton, pos=edge)
        QTest.mouseMove(c, edge + QPoint(int(c._tick_to_x(12) - c._tick_to_x(0)), 0))
        QTest.mouseRelease(c, Qt.MouseButton.LeftButton,
                           pos=edge + QPoint(int(c._tick_to_x(12) - c._tick_to_x(0)), 0))
        step("drag resize")

        # Instrument and volume from the sidebar (a volume drag = one step).
        row = win._track_sidebar._rows[1]
        row._inst_combo.setCurrentIndex((row._inst_combo.currentIndex() + 3)
                                        % row._inst_combo.count())
        step("instrument")
        before_vol = len(win._history._undo)
        for v in (90, 80, 70):
            row._vol_slider.setValue(v)
        step("volume drag")
        self.assertEqual(len(win._history._undo), before_vol + 1,
                         "a slider drag should be one undo step")

        # Tempo, tracks, loop.
        win._bpm_spin.setValue(win._bpm_spin.value() + 10)
        step("tempo")
        win._on_duplicate_track(1)
        step("duplicate track")
        win._on_add_track()
        step("add track")
        win._on_remove_track(0)
        step("remove track 1 (with its notes)")
        s, e = c._loop_start, c._loop_end
        win._structure_panel.loop_requested.emit(s + 96, e)
        step("loop move")
        # Delete key on a selected note.
        c._selected = {0}
        c.setFocus()
        QTest.keyClick(c, Qt.Key.Key_Delete)
        step("delete key")

        # Undo everything — the button and the keyboard — then redo it all.
        for i in range(len(states) - 1, 0, -1):
            if i % 2:
                win._btn_undo.click()
            else:
                QTest.keyClick(win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            app.processEvents()
            self.assertEqual(self._snap(win), states[i - 1], f"undo to step {i - 1} is wrong")
        self.assertFalse(win._btn_undo.isEnabled())
        for i in range(1, len(states)):
            if i % 2:
                win._btn_redo.click()
            else:
                QTest.keyClick(win, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
            app.processEvents()
            self.assertEqual(self._snap(win), states[i], f"redo to step {i} is wrong")
        self.assertFalse(win._btn_redo.isEnabled())

        # A new edit after undo drops the redo branch.
        win._btn_undo.click()
        win._on_add_track()
        self.assertFalse(win._history.can_redo())
        win._btn_undo.click()
        win._btn_redo.click()
        win._btn_undo.click()
        app.processEvents()
        final = self._snap(win)

        # Saved exactly; the history starts again after the save.
        self.save(win)
        self.assertFalse(win._history.can_undo())
        self.assert_rebuild_matches(label)
        win.close()
        app.processEvents()
        win2 = self.open_song(label)
        reopened = self._snap(win2)
        self.assertEqual(reopened[0], final[0], "notes on reopen differ from what was saved")
        self.assertEqual(reopened[2], final[2], "loop on reopen differs")
        # The empty added track is not written (mid2agb keeps no track without notes).
        with_notes = {dict(n)["track"] for n in final[0]}
        self.assertEqual(len(reopened[3]), len(with_notes))

    def test_undo_restores_a_removed_track_completely(self):
        label = "mus_castle_town"
        win = self.open_song(label)
        c = win._piano_roll.canvas
        n = len(win._song.tracks)
        row = win._track_sidebar._rows[2]
        row._inst_combo.setCurrentIndex((row._inst_combo.currentIndex() + 1) % row._inst_combo.count())
        win._track_sidebar.set_mute_solo_state({2}, set())
        voice_cmds = [(x.cmd, x.value) for x in win._song.tracks[2].commands]
        notes = self.track_notes(c, 2)
        ctrls = sorted(tuple(sorted(e.items())) for e in c._control_events if e["track"] == 2)
        track_obj = win._song.tracks[2]
        win._on_remove_track(2)
        self.assertEqual(len(win._song.tracks), n - 1)
        win._btn_undo.click()
        self.assertIs(win._song.tracks[2], track_obj, "the track lost its identity")
        self.assertEqual([(x.cmd, x.value) for x in win._song.tracks[2].commands], voice_cmds)
        self.assertEqual(self.track_notes(c, 2), notes)
        self.assertEqual(sorted(tuple(sorted(e.items())) for e in c._control_events
                                if e["track"] == 2), ctrls)
        self.assertEqual(win._track_sidebar.get_mute_solo_state()["muted"], {2},
                         "mute did not follow the restored track")
        # Saved as an unchanged track: only the instrument edit is written.
        self.save(win)
        self.assert_rebuild_matches(label)


if __name__ == "__main__":
    unittest.main()
