"""Shared set-up for tests that drive the real Sound Editor -> Piano Roll path.

A throwaway copy of the game's sound folder (plus songs.h and mid2agb) is
made once per test class, and a real ``SoundEditorTab`` loads it, so the
Piano Roll is opened with the user's own click on the "Piano Roll" button.
Nothing in the real project is written. Yes/No questions answer Yes; any
warning box is recorded in ``self.warnings`` instead of blocking the run.
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
    app = QApplication.instance() or QApplication(sys.argv)
    from core.sound.song_compiler import find_mid2agb
    QT_OK = True
except Exception:  # pragma: no cover
    QT_OK = False


def available() -> bool:
    return (QT_OK and bool(find_mid2agb(GAME))
            and os.path.isfile(os.path.join(GAME, "include", "constants", "songs.h")))


def note_key(n):
    return (n["tick"], n["pitch"], n["duration"], n.get("velocity"))


class GuiProjectCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._real_sd = sys.modules.get("sounddevice")
        sys.modules["sounddevice"] = types.SimpleNamespace(OutputStream=None)
        cls._ask = QMessageBox.question
        cls._warn = QMessageBox.warning
        cls._info = QMessageBox.information
        cls.warnings = []
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        QMessageBox.warning = staticmethod(
            lambda parent, title, text, *a, **k: cls.warnings.append(f"{title}: {text}"))
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        cls.tmp = tempfile.mkdtemp(prefix="porysuite_gui_")
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
        QMessageBox.information = cls._info
        if cls._real_sd is not None:
            sys.modules["sounddevice"] = cls._real_sd
        else:
            sys.modules.pop("sounddevice", None)
        win = cls.tab._live_piano_roll()
        if win is not None:
            win._is_dirty = False
            win.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        slug = hashlib.sha1(os.path.abspath(cls.root).encode()).hexdigest()[:12]
        shutil.rmtree(os.path.join(ROOT_DIR, "cache", slug), ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────
    def midi_path(self, label, ext):
        return os.path.join(self.root, "sound", "songs", "midi", label + ext)

    def open_song(self, label):
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
        self.addCleanup(self._close_quietly, win)
        return win

    @staticmethod
    def _close_quietly(win):
        from PyQt6 import sip
        if not sip.isdeleted(win):          # (calling a deleted window crashes)
            win._is_dirty = False
            win.close()
            app.processEvents()

    def save(self, win):
        del self.warnings[:]
        win._btn_save.click()
        self.assertEqual(self.warnings, [], "the save showed a warning")
        self.assertFalse(win.has_unsaved_changes(), "the save did not complete")

    def save_close(self, win):
        self.save(win)
        win.close()
        app.processEvents()

    @staticmethod
    def track_notes(canvas, i):
        return sorted(note_key(n) for n in canvas.get_notes() if n.get("track", 0) == i)

    @staticmethod
    def track_ctrls(canvas, i, kind):
        return sorted((e["tick"], e["value"]) for e in canvas._control_events
                      if e.get("track", 0) == i and e.get("type") == kind)

    def assert_rebuild_matches(self, label):
        """The saved .s is exactly what a game build makes from the saved .mid."""
        from core.sound.safe_song_save import (render_s_with_mid2agb, compare_s,
                                               mid2agb_flags)
        mid = open(self.midi_path(label, ".mid"), "rb").read()
        built = render_s_with_mid2agb(self.root, label, mid, mid2agb_flags(self.root, label))
        disk = open(self.midi_path(label, ".s"), "rb").read().decode("latin-1")
        d = compare_s(disk, built, label)
        self.assertTrue(d.same_length and not d.changed,
                        f"{label}: a rebuild would change the .s: {d.describe(3)}")
