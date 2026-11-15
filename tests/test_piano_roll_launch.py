"""Regression: the Piano Roll opens from the Sound Editor the way a user opens it.

BUGS.md, 2026-11-15 — "Piano Roll would not open". The window crashed on every
open with ``AttributeError: 'TrackCommand' object has no attribute 'sub'``
(piano_roll_widget.load_song_data -> track_renderer.flatten_track_commands).
The running PorySuite had been started before the music-editor repair: it
still held the OLD song parser (no ``sub`` field) and loaded the NEW
track_renderer, which named the new fields, the first time the Piano Roll
opened. Every unit test ran in a fresh process and never saw the mix.

Two things are locked here:
  * The normal path end to end, in a throwaway project copy: Sound Editor
    load_project -> select the song -> click "Piano Roll" -> notes on the
    canvas -> Play (audio rendered into a silent stand-in output, never the
    speakers) -> edit a note -> Save button -> close -> reopen and see it.
  * A song read by an OLDER parser (commands without the new fields) still
    opens, and a save refuses with a restart message instead of guessing.

Nothing in the real project is written. (Importing ui.sound_editor_tab appends
to porysuite/sound_editor.log; it no longer overwrites it.)
"""
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
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


class _SilentStream:
    """Stands in for sounddevice.OutputStream: pulls audio from the callback
    on a thread, like a sound card would, and keeps the loudest sample."""
    peak = 0.0
    blocks = 0

    def __init__(self, samplerate, channels, dtype, callback, blocksize):
        import numpy as np
        self._np, self._cb, self._n, self._ch = np, callback, blocksize, channels
        self._run = False

    def start(self):
        self._run = True

        def pump():
            while self._run:
                out = self._np.zeros((self._n, self._ch), dtype="float32")
                self._cb(out, self._n, None, None)
                _SilentStream.peak = max(_SilentStream.peak, float(abs(out).max()))
                _SilentStream.blocks += 1
                time.sleep(0.005)
        threading.Thread(target=pump, daemon=True).start()

    def stop(self):
        self._run = False

    def close(self):
        self._run = False


try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication(sys.argv)
    from core.sound.song_compiler import find_mid2agb
    _QT_OK = True
except Exception:  # pragma: no cover
    _QT_OK = False


def _available():
    return (_QT_OK and bool(find_mid2agb(GAME))
            and os.path.isfile(os.path.join(GAME, "include", "constants", "songs.h")))


@unittest.skipUnless(_available(), "real PyQt6 / game project / mid2agb unavailable")
class PianoRollLaunchTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._real_sd = sys.modules.get("sounddevice")
        sys.modules["sounddevice"] = types.SimpleNamespace(OutputStream=_SilentStream)
        cls.tmp = tempfile.mkdtemp(prefix="porysuite_launch_")
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
        if cls._real_sd is not None:
            sys.modules["sounddevice"] = cls._real_sd
        else:
            sys.modules.pop("sounddevice", None)
        win = getattr(cls.tab, "_piano_roll_window", None)
        if win is not None:
            try:
                win.close()
            except RuntimeError:
                pass
        shutil.rmtree(cls.tmp, ignore_errors=True)
        # load_project keeps labels / the MIDI ledger in PorySuite's cache,
        # in a folder named after the project path: remove just that one.
        slug = hashlib.sha1(os.path.abspath(cls.root).encode()).hexdigest()[:12]
        shutil.rmtree(os.path.join(ROOT_DIR, "cache", slug), ignore_errors=True)

    def _select(self, label):
        tree = self.tab._song_tree
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            from ui.sound_editor_tab import _ROLE_SONG_KEY
            if item.data(0, _ROLE_SONG_KEY) == label:
                tree.setCurrentItem(item)
                return
        self.fail(f"{label} is not in the Sound Editor's song list")

    def _open(self, label):
        self._select(label)
        self.assertTrue(self.tab._btn_piano_roll.isEnabled())
        self.tab._btn_piano_roll.click()            # the user's click
        win = self.tab._piano_roll_window
        self.assertIsNotNone(win, "the Piano Roll window did not open")
        return win

    def test_open_play_edit_save_close_reopen(self):
        label = "mus_kakariko"
        s_path = os.path.join(self.root, "sound", "songs", "midi", label + ".s")
        win = self._open(label)
        notes = win._piano_roll.canvas.get_notes()
        self.assertEqual(len(notes), 455, "the canvas should show every note, repeats included")

        _SilentStream.peak, _SilentStream.blocks = 0.0, 0
        win._btn_play.click()
        deadline = time.time() + 10
        while time.time() < deadline and (_SilentStream.blocks < 40 or _SilentStream.peak == 0.0):
            _app.processEvents()
            time.sleep(0.02)
        win._btn_stop.click()
        self.assertGreater(_SilentStream.peak, 0.0, "playback produced no sound")

        note = next(n for n in win._piano_roll.canvas._notes if n.get("track", 0) == 0)
        tick, old_pitch = note["tick"], note["pitch"]
        note["pitch"] = old_pitch + 1
        win._mark_dirty()
        ask = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            win._btn_save.click() if hasattr(win, "_btn_save") else win._on_save()
        finally:
            QMessageBox.question = ask
        self.assertFalse(win.has_unsaved_changes(), "the save did not complete")
        win.close()
        _app.processEvents()

        from core.sound.song_parser import parse_song_file
        self.assertTrue(any(c.cmd == "NOTE" for c in parse_song_file(s_path).tracks[0].commands))
        win2 = self._open(label)
        pitches = {n["pitch"] for n in win2._piano_roll.canvas.get_notes()
                   if n.get("track", 0) == 0 and n["tick"] == tick}
        self.assertIn(old_pitch + 1, pitches, "the saved edit is not there on reopening")
        win2.close()

    def test_song_from_an_older_parser_opens_and_refuses_to_save(self):
        label = "mus_castle_town"
        win = None
        song = None
        self._select(label)
        song = self.tab._all_songs[label]
        # Make every command look like one from the parser before the update.
        import dataclasses
        from core.sound import song_parser

        @dataclasses.dataclass
        class OldTrackCommand:
            cmd: str
            tick: int = 0
            duration: int = 0
            pitch: object = None
            velocity: object = None
            gate_time: object = None
            value: object = None
            target_label: object = None
            raw_line: str = ''
            parsed_value: object = None

        fresh = song_parser.parse_song_file(song.file_path)
        for t in fresh.tracks:
            t.commands = [OldTrackCommand(**{f.name: getattr(c, f.name)
                                             for f in dataclasses.fields(OldTrackCommand)})
                          for c in t.commands]
        song.tracks = fresh.tracks
        try:
            win = self._open(label)
            self.assertGreater(len(win._piano_roll.canvas.get_notes()), 0)
            s_path = song.file_path
            before = open(s_path, "rb").read()
            with self.assertRaises(ValueError) as cm:
                win.save_to_disk()
            self.assertIn("Restart PorySuite", str(cm.exception))
            self.assertEqual(before, open(s_path, "rb").read())
        finally:
            if win is not None:
                win._is_dirty = False
                win.close()
            self.tab._all_songs[label] = song_parser.parse_song_file(song.file_path)


if __name__ == "__main__":
    unittest.main()
