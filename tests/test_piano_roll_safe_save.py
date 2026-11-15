"""Regression: a Piano Roll save changes only what was edited in the Piano Roll.

BUGS.md, 2026-11-15. The Piano Roll rebuilt every track from its canvas on
save: one instrument per track, controls copied at their TEXT positions, notes
from a flattener that cut long pattern-heavy tracks short. A save with NO edit
changed what the game plays in 212 of the project's 383 songs (mid-song
instrument changes, volume, vibrato, echo; notes in 9 songs).

The save now re-reads the song from disk, applies only the edits, builds the
result with mid2agb in a temp folder and refuses — writing nothing — if the
build would not play what the editor shows or would change anything that was
not edited. These tests drive the real window (offscreen) on COPIES of the
project's songs in a throwaway project. Nothing in the project is written.
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# A sibling test may have stubbed PyQt6; this builds a real widget.
for _m in [m for m in list(sys.modules) if m == "PyQt6" or m.startswith("PyQt6.")]:
    del sys.modules[_m]

try:
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from core.sound.song_parser import parse_song_file
    from core.sound.song_compiler import find_mid2agb
    from core.sound.safe_song_save import audible_signature, signature_differences
    from ui.piano_roll_window import PianoRollWindow
    _QT_OK = True
except Exception:  # pragma: no cover
    _QT_OK = False

GAME = os.path.join(ROOT_DIR, "pokefirered")
REAL_MIDI = os.path.join(GAME, "sound", "songs", "midi")


def _available():
    return _QT_OK and bool(find_mid2agb(GAME)) and os.path.isfile(os.path.join(REAL_MIDI, "midi.cfg"))


def _read(p):
    with open(p, "rb") as f:
        return f.read()


@unittest.skipUnless(_available(), "real PyQt6 / game project / mid2agb unavailable")
class PianoRollSafeSaveTest(unittest.TestCase):

    def _open(self, d, label):
        midi = os.path.join(d, "sound", "songs", "midi")
        tools = os.path.join(d, "tools", "mid2agb")
        os.makedirs(midi)
        os.makedirs(tools)
        shutil.copy2(find_mid2agb(GAME), tools)
        shutil.copy2(os.path.join(REAL_MIDI, "midi.cfg"), midi)
        for ext in (".s", ".mid"):
            shutil.copy2(os.path.join(REAL_MIDI, label + ext), midi)
        path = os.path.join(midi, label + ".s")
        song = parse_song_file(path)
        song.file_path = path
        return midi, path, song, PianoRollWindow(song, project_root=d)

    def test_save_without_edits_writes_nothing(self):
        for label in ("mus_kakariko", "mus_castle_town", "mus_vs_gigalakitu", "mus_cycling"):
            with self.subTest(song=label), tempfile.TemporaryDirectory() as d:
                midi, path, song, win = self._open(d, label)
                files = [path, os.path.join(midi, label + ".mid"), os.path.join(midi, "midi.cfg")]
                before = [(_read(f), os.stat(f).st_mtime_ns) for f in files]
                win.save_to_disk()
                self.assertEqual(before, [(_read(f), os.stat(f).st_mtime_ns) for f in files])

    def test_one_note_edit_changes_only_that_note(self):
        # Castle Town lost mid-song instrument / volume / pan / bend changes and
        # notes on a no-edit save; Gigalakitu lost echo and vibrato as well.
        for label in ("mus_castle_town", "mus_vs_gigalakitu", "mus_cycling", "mus_kakariko"):
            with self.subTest(song=label), tempfile.TemporaryDirectory() as d:
                midi, path, song, win = self._open(d, label)
                original = parse_song_file(path)
                canvas = win._piano_roll.canvas
                note = next(n for n in canvas._notes if n.get("track", 0) == 0)
                note["pitch"] = min(127, note["pitch"] + 1)
                win.save_to_disk()
                after = parse_song_file(path)
                diffs = signature_differences(audible_signature(original), audible_signature(after))
                self.assertEqual(len(diffs), 1, diffs)
                self.assertTrue(diffs[0].startswith("track 1: notes"), diffs)
                self.assertIn("1 lost or moved, 1 new or moved", diffs[0])

    def test_refuses_when_the_file_changed_after_opening(self):
        label = "mus_kakariko"
        with tempfile.TemporaryDirectory() as d:
            midi, path, song, win = self._open(d, label)
            text = _read(path).decode("latin-1").replace("VOICE , 74", "VOICE , 75", 1)
            with open(path, "wb") as f:
                f.write(text.encode("latin-1"))
            win._on_track_instrument(0, 30)   # an edit the save has to replay onto the file
            before = (_read(path), _read(os.path.join(midi, label + ".mid")))
            with self.assertRaises(ValueError) as cm:
                win.save_to_disk()
            self.assertIn("NOT saved", str(cm.exception))
            self.assertEqual(before, (_read(path), _read(os.path.join(midi, label + ".mid"))))
            self.assertEqual([f for f in os.listdir(midi) if f.endswith(".porysuite-tmp")], [])


if __name__ == "__main__":
    unittest.main()
