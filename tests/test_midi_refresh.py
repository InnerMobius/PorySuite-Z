"""Refresh from MIDI: a song whose .mid was changed by another program.

The "other program" here is this test editing the .mid with mido in a
throwaway project copy — a simulation of what an outside editor such as
PoryDAW leaves behind (it writes only the .mid). PoryDAW itself is not run.

Covers the Sound Editor's status line and button, the refusal while the Piano
Roll holds unsaved edits, the .mid / midi.cfg left byte-identical, the backup
of the replaced .s, the conflict case (the .s changed instead), a song with
no .s yet, editing after a refresh (outside events survive the in-place
patch) and the open Piano Roll noticing the outside change by itself.
"""
import glob
import os
import re
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    import mido
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from core.sound.midi_refresh import midi_sync_status, refresh_from_midi
    from core.sound.midi_ownership import record_own_write
    from core.sound.safe_song_save import SongSaveRefused


def _outside_edit(path, shift=2):
    """Change the first note's pitch and add a text event, as another editor
    might. Returns the new pitch."""
    mf = mido.MidiFile(path)
    for tr in mf.tracks:
        for i, m in enumerate(tr):
            if m.type == "note_on" and m.velocity > 0:
                old = m.note
                tr[i] = m.copy(note=old + shift)
                for j in range(i + 1, len(tr)):
                    o = tr[j]
                    if (o.type == "note_off" or (o.type == "note_on" and o.velocity == 0)) \
                            and o.note == old and o.channel == m.channel:
                        tr[j] = o.copy(note=old + shift)
                        break
                tr.insert(1, mido.MetaMessage("text", text="edited elsewhere", time=0))
                mf.save(path)
                return old + shift
    raise AssertionError("no note found")


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class MidiRefreshTest(GuiProjectCase):

    def _select(self, label):
        from ui.sound_editor_tab import _ROLE_SONG_KEY
        tree = self.tab._song_tree
        for i in range(tree.topLevelItemCount()):
            if tree.topLevelItem(i).data(0, _ROLE_SONG_KEY) == label:
                tree.setCurrentItem(tree.topLevelItem(i))
                return
        self.fail(label)

    def _read(self, p):
        return open(p, "rb").read()

    def test_refresh_after_an_outside_midi_edit(self):
        label = "mus_kakariko"
        mid, s, cfg = (self.midi_path(label, ".mid"), self.midi_path(label, ".s"),
                       self.midi_path("midi", ".cfg"))
        self.assertEqual(midi_sync_status(self.root, label).state, "in_sync")
        win = self.open_song(label)
        c = win._piano_roll.canvas
        c.push_undo()
        c._notes[0]["pitch"] += 1
        win._mark_dirty()
        # This test is about the Sound Editor's path; the open window noticing
        # the change by itself is test_open_piano_roll_notices_an_outside_change.
        win._watcher.blockSignals(True)

        new_pitch = _outside_edit(mid)
        app.processEvents()
        st = midi_sync_status(self.root, label)
        self.assertEqual(st.state, "midi_changed")
        # The user switches back to PorySuite with the song still selected.
        self._select(label)
        self.tab._on_app_state_changed(Qt.ApplicationState.ApplicationActive)
        self.assertTrue(self.tab._btn_refresh_midi.isVisibleTo(self.tab))
        self.assertIn("changed outside PorySuite", self.tab._midi_sync_label.text())

        # Unsaved Piano Roll edits block it; nothing is written.
        old_s, mid_bytes, cfg_bytes = self._read(s), self._read(mid), self._read(cfg)
        del self.warnings[:]
        self.tab._btn_refresh_midi.click()
        self.assertTrue(any("unsaved edits" in w for w in self.warnings), self.warnings)
        self.assertEqual(self._read(s), old_s)
        # ... and saving those edits is refused (the .s no longer matches the .mid).
        with self.assertRaises(ValueError) as cm:
            win.save_to_disk()
        self.assertIn("Refresh from MIDI", str(cm.exception))
        self.assertEqual(self._read(mid), mid_bytes)
        win._is_dirty = False
        win.close()
        app.processEvents()

        self.tab._btn_refresh_midi.click()
        self.assertEqual(midi_sync_status(self.root, label).state, "in_sync")
        self.assertEqual(self._read(mid), mid_bytes, "the .mid was written")
        self.assertEqual(self._read(cfg), cfg_bytes, "midi.cfg was written")
        self.assertGreater(os.path.getmtime(s), os.path.getmtime(mid))
        backups = glob.glob(os.path.join(self._cache(), "replaced_s", label + "-*.s"))
        self.assertTrue(backups and any(self._read(b) == old_s for b in backups),
                        "the replaced .s was not backed up")
        self.assertFalse(self.tab._btn_refresh_midi.isVisibleTo(self.tab))
        self.assert_rebuild_matches(label)

        # The refreshed song opens with the outside edit, and can be edited:
        # the in-place patch keeps the outside editor's text event.
        win2 = self.open_song(label)
        c2 = win2._piano_roll.canvas
        self.assertIn(new_pitch, {n["pitch"] for n in c2.get_notes()})
        c2.push_undo()
        c2._notes[5]["pitch"] -= 1
        win2._mark_dirty()
        self.save(win2)
        texts = [m.text for t in mido.MidiFile(mid).tracks for m in t if m.type == "text"]
        self.assertIn("edited elsewhere", texts)

    def _cache(self):
        from core.app_info import get_cache_dir
        return get_cache_dir(self.root)

    def test_conflict_needs_an_explicit_choice(self):
        label = "mus_lostwoods"
        s = self.midi_path(label, ".s")
        record_own_write(self.root, label)        # PorySuite wrote this .mid last
        text = self._read(s).decode("latin-1")
        # A hand edit to the .s: the first volume command a little quieter.
        m = re.search(r"(VOL\s*,\s*)(\d+)", text)
        hand = text[:m.start(2)] + str(max(0, int(m.group(2)) - 4)) + text[m.end(2):]
        open(s, "wb").write(hand.encode("latin-1"))
        st = midi_sync_status(self.root, label)
        self.assertEqual(st.state, "conflict")
        with self.assertRaises(SongSaveRefused):
            refresh_from_midi(self.root, label)
        self.assertEqual(self._read(s).decode("latin-1"), hand, "refused, but the .s changed")
        refresh_from_midi(self.root, label, allow_conflict=True)
        self.assertEqual(midi_sync_status(self.root, label).state, "in_sync")
        backups = glob.glob(os.path.join(self._cache(), "replaced_s", label + "-*.s"))
        self.assertTrue(any(self._read(b).decode("latin-1") == hand for b in backups))

    def test_song_with_no_s_yet(self):
        label = "mus_faron_woods"
        s = self.midi_path(label, ".s")
        want = self._read(s)
        os.remove(s)
        self.assertEqual(midi_sync_status(self.root, label).state, "no_s")
        refresh_from_midi(self.root, label)
        self.assertEqual(self._read(s), want, "the built .s is not what the build makes")

    def test_open_piano_roll_notices_an_outside_change(self):
        label = "mus_castle_town"
        win = self.open_song(label)
        before = sorted((n["tick"], n["pitch"]) for n in win._piano_roll.canvas.get_notes())
        new_pitch = _outside_edit(self.midi_path(label, ".mid"), shift=-5)
        QTest.qWait(1200)                         # watcher + its 300 ms settle
        app.processEvents()
        self.assertEqual(midi_sync_status(self.root, label).state, "in_sync",
                         "the window did not refresh the song from its MIDI")
        after = sorted((n["tick"], n["pitch"]) for n in win._piano_roll.canvas.get_notes())
        self.assertIn(new_pitch, {p for _t, p in after})
        self.assertNotEqual(before, after, "the window still shows the old notes")
        self.assertFalse(win.has_unsaved_changes())


if __name__ == "__main__":
    unittest.main()
