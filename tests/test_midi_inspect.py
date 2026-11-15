"""MIDI event inspection: classification and the read-only window.

The "game uses it" judgement is checked against mid2agb itself: an event the
inspector calls unused is removed from a real song's .mid and the build must
come out the same; and the song-track numbering must match how many tracks
the build makes. Then the Piano Roll's "MIDI Events" window is opened the
user's way and filtered. Throwaway copies only.
"""
import io
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    import mido
    from core.sound.midi_inspect import inspect_midi
    from core.sound.song_parser import parse_song_file
    from core.sound.safe_song_save import render_s_with_mid2agb, mid2agb_flags, compare_s


def _with_extras(data: bytes) -> bytes:
    """Add events the build does not use: a text event, an unknown controller
    and a sysex, to the first note track."""
    mf = mido.MidiFile(file=io.BytesIO(data))
    tr = next(t for t in mf.tracks if any(m.type == "note_on" for m in t))
    ch = next(m.channel for m in tr if m.type == "note_on")
    tr.insert(1, mido.MetaMessage("text", text="inspector test", time=0))
    tr.insert(1, mido.Message("control_change", channel=ch, control=91, value=40, time=0))
    tr.insert(1, mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01], time=0))
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def _drop_unused(data: bytes, tbs: int) -> bytes:
    rows = inspect_midi(data, tbs)
    mf = mido.MidiFile(file=io.BytesIO(data))
    by_chunk = {}
    for r in rows:
        by_chunk.setdefault(r.chunk, []).append(r)
    for ci, tr in enumerate(mf.tracks):
        keep, carry = [], 0
        for m, r in zip(list(tr), by_chunk[ci]):
            if r.used or m.type == "end_of_track":
                keep.append(m.copy(time=m.time + carry))
                carry = 0
            else:
                carry += m.time
        tr[:] = keep
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class MidiInspectTest(GuiProjectCase):

    def test_classification_matches_the_build(self):
        for label in ("mus_kakariko", "mus_silph", "mus_castle_town"):
            with self.subTest(label):
                data = _with_extras(open(self.midi_path(label, ".mid"), "rb").read())
                song = parse_song_file(self.midi_path(label, ".s"))
                rows = inspect_midi(data, song.tempo_base or 1)
                kinds = {(r.kind, r.used) for r in rows}
                self.assertIn(("text", False), kinds)
                self.assertIn(("sysex", False), kinds)
                self.assertTrue(any(r.kind == "control_change" and not r.used for r in rows))
                tracks = {r.song_track for r in rows if r.song_track is not None}
                self.assertEqual(len(tracks), len(song.tracks), "song-track numbering is off")
                flags = mid2agb_flags(self.root, label)
                full = render_s_with_mid2agb(self.root, label, data, flags)
                bare = render_s_with_mid2agb(self.root, label, _drop_unused(data, song.tempo_base or 1), flags)
                d = compare_s(full, bare, label)
                self.assertTrue(d.same_length and not d.changed,
                                f"an event called 'not used' changes the build: {d.describe(3)}")

    def test_window_opens_and_filters(self):
        label = "mus_kakariko"
        win = self.open_song(label)
        win._btn_midi_events.click()
        dlg = win._midi_inspector
        app.processEvents()
        all_rows = dlg.visible_rows()
        self.assertGreater(len(all_rows), 100)
        dlg._unused.setChecked(True)
        self.assertTrue(dlg.visible_rows() and all(not r.used for r in dlg.visible_rows()))
        dlg._unused.setChecked(False)
        dlg._text.setText("tempo")
        self.assertTrue(dlg.visible_rows() and all(r.kind == "set_tempo" for r in dlg.visible_rows()))
        dlg.close()


if __name__ == "__main__":
    unittest.main()
