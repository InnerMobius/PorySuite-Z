"""Automation and velocity lanes: real mouse edits on the lane, saved exactly.

Real Sound Editor -> Piano Roll path on a throwaway project copy. Checks that
lane points sit where the controls play (repeats included), that each kind of
lane edit is saved and reopened, that only the active track's MIDI channel
changes, and that every lane edit is one undo step.
"""
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    import mido
    from PyQt6.QtCore import Qt, QPoint
    from PyQt6.QtTest import QTest
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import expand_song


def _channel_events(path):
    """{channel: sorted [(abs tick, message)]} for every channel message."""
    out = {}
    for tr in mido.MidiFile(path).tracks:
        now = 0
        for m in tr:
            now += m.time
            if not m.is_meta and hasattr(m, "channel"):
                out.setdefault(m.channel, []).append((now, str(m.copy(time=0))))
    return {k: sorted(v) for k, v in out.items()}


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class LanesTest(GuiProjectCase):

    def _lane(self, win, kind):
        combo = win._piano_roll._lane_combo
        combo.setCurrentIndex(combo.findData(kind))
        lane = win._piano_roll.lane
        self.assertEqual(lane.kind, kind)
        return lane

    def _pt(self, lane, tick, value):
        return QPoint(int(lane.tick_to_x(tick)), int(lane.value_to_y(value)))

    def _scroll_to(self, win, tick):
        win._piano_roll.scroll_to_tick(tick)
        app.processEvents()

    def test_lane_points_sit_where_the_controls_play(self):
        for label in ("mus_faron_woods", "mus_kakariko", "mus_lostwoods"):
            with self.subTest(label):
                song = parse_song_file(self.midi_path(label, ".s"))
                exp = expand_song(song)
                win = self.open_song(label)
                c = win._piano_roll.canvas
                for ti, ex in enumerate(exp):
                    end = ex.goto_time if ex.goto_time is not None else 10 ** 9
                    for kind in ("VOL", "PAN", "MOD", "BEND"):
                        played = {(t, cmd.value) for t, _s, cmd in ex.events
                                  if cmd.cmd == kind and t < end}
                        shown = {(e["tick"], e["value"]) for e in c._control_events
                                 if e["track"] == ti and e["type"] == kind and e["tick"] < end}
                        self.assertEqual(shown, played, f"{label} track {ti + 1} {kind}")
                win.close()

    def test_lane_edits_save_and_reopen(self):
        label = "mus_castle_town"
        mid = self.midi_path(label, ".mid")
        before = _channel_events(mid)
        win = self.open_song(label)
        win.show()
        app.processEvents()
        c = win._piano_roll.canvas
        win._track_sidebar.select_row(1)              # track 2 is the active track
        self.assertEqual(c._active_track, 1)
        others = sorted((e["track"], e["tick"], e["type"], e["value"])
                        for e in c._control_events if e["track"] != 1)

        # Volume: add a mid-song point with a click, then drag it.
        lane = self._lane(win, "VOL")
        t_add = 96 * 6
        self._scroll_to(win, t_add)
        QTest.mouseClick(lane, Qt.MouseButton.LeftButton, pos=self._pt(lane, t_add, 50))
        app.processEvents()
        vols = self.track_ctrls(c, 1, "VOL")
        self.assertIn(t_add, [t for t, _v in vols], "the click did not add a volume point")
        added = dict(vols)[t_add]
        p = self._pt(lane, t_add, added)
        QTest.mousePress(lane, Qt.MouseButton.LeftButton, pos=p)
        QTest.mouseMove(lane, self._pt(lane, t_add + 24, 80))
        QTest.mouseRelease(lane, Qt.MouseButton.LeftButton, pos=self._pt(lane, t_add + 24, 80))
        vols = dict(self.track_ctrls(c, 1, "VOL"))
        self.assertNotIn(t_add, vols)
        self.assertIn(t_add + 24, vols)
        moved_value = vols[t_add + 24]

        # Starting volume (tick 0): drag its point; the track's opening VOL follows.
        self._scroll_to(win, 0)
        start = dict(self.track_ctrls(c, 1, "VOL"))[0]
        p0 = self._pt(lane, 0, start)
        QTest.mousePress(lane, Qt.MouseButton.LeftButton, pos=p0 + QPoint(1, 0))
        QTest.mouseMove(lane, self._pt(lane, 0, 40) + QPoint(1, 0))
        QTest.mouseRelease(lane, Qt.MouseButton.LeftButton, pos=self._pt(lane, 0, 40) + QPoint(1, 0))
        new_start = dict(self.track_ctrls(c, 1, "VOL"))[0]
        self.assertNotEqual(new_start, start)
        opening = next(x for x in win._song.tracks[1].commands if x.cmd == "VOL")
        self.assertEqual(opening.value, new_start, "the starting value did not reach the track")

        # Vibrato depth: add a point.
        lane = self._lane(win, "MOD")
        t_mod = 96 * 8
        self._scroll_to(win, t_mod)
        QTest.mouseClick(lane, Qt.MouseButton.LeftButton, pos=self._pt(lane, t_mod, 30))
        mods = dict(self.track_ctrls(c, 1, "MOD"))
        self.assertIn(t_mod, mods)

        # Velocity: paint the first notes of the track.
        lane = self._lane(win, "velocity")
        first = sorted(n["tick"] for n in c._notes if n["track"] == 1)[:3]
        self._scroll_to(win, first[0])
        for t in first:
            QTest.mousePress(lane, Qt.MouseButton.LeftButton, pos=self._pt(lane, t, 61) + QPoint(1, 0))
            QTest.mouseRelease(lane, Qt.MouseButton.LeftButton, pos=self._pt(lane, t, 61) + QPoint(1, 0))
        vels = {n["velocity"] for n in c._notes if n["track"] == 1 and n["tick"] in first}
        self.assertTrue(vels and all(v % 4 == 0 or v == 127 for v in vels),
                        f"velocities not on the game's steps of 4: {vels}")

        # Undo the velocity paint (3 presses = 3 steps), then redo them.
        for _ in range(3):
            win._btn_undo.click()
        self.assertNotIn(61, {n["velocity"] for n in c._notes if n["track"] == 1})
        for _ in range(3):
            win._btn_redo.click()
        self.assertEqual(sorted(n["velocity"] for n in c._notes
                                if n["track"] == 1 and n["tick"] in first)[0] % 4, 0)

        # Nothing on other tracks moved.
        self.assertEqual(sorted((e["track"], e["tick"], e["type"], e["value"])
                                for e in c._control_events if e["track"] != 1), others)
        notes_t2 = self.track_notes(c, 1)

        self.save_close(win)
        self.assert_rebuild_matches(label)
        win2 = self.open_song(label)
        c2 = win2._piano_roll.canvas
        vols2 = dict(self.track_ctrls(c2, 1, "VOL"))
        self.assertEqual(vols2.get(t_add + 24), moved_value)
        self.assertEqual(vols2.get(0), new_start)
        self.assertEqual(dict(self.track_ctrls(c2, 1, "MOD")).get(t_mod), mods[t_mod])
        self.assertEqual(self.track_notes(c2, 1), notes_t2)
        self.assertEqual(sorted((e["track"], e["tick"], e["type"], e["value"])
                                for e in c2._control_events if e["track"] != 1), others)

        # In the .mid only track 2's channel changed.
        after = _channel_events(mid)
        changed = [ch for ch in set(before) | set(after) if before.get(ch) != after.get(ch)]
        self.assertEqual(len(changed), 1, f"channels changed: {changed}")

    def test_right_click_deletes_a_point(self):
        label = "mus_kakariko"
        win = self.open_song(label)
        win.show()
        c = win._piano_roll.canvas
        lane = self._lane(win, "VOL")
        # A track with a mid-song volume point, if any; else make one first.
        win._track_sidebar.select_row(0)
        mids = [t for t, _v in self.track_ctrls(c, 0, "VOL") if t > 0]
        if not mids:
            c.push_undo()
            c._control_events.append({"tick": 96, "type": "VOL", "value": 70, "track": 0})
            mids = [96]
        t = mids[0]
        v = dict(self.track_ctrls(c, 0, "VOL"))[t]
        self._scroll_to(win, t)
        QTest.mouseClick(lane, Qt.MouseButton.RightButton, pos=self._pt(lane, t, v))
        self.assertNotIn(t, dict(self.track_ctrls(c, 0, "VOL")))
        win._btn_undo.click()
        self.assertIn(t, dict(self.track_ctrls(c, 0, "VOL")))


if __name__ == "__main__":
    unittest.main()
