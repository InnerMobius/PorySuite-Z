import json
import os
import shutil
import sys
import tempfile
import types
import importlib
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Stub PyQt6 modules so mainwindow can be imported without a GUI runtime.
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")
qt_widgets = types.ModuleType("PyQt6.QtWidgets")

qt_core.pyqtSignal = lambda *a, **k: None
qt_core.Qt = type("Qt", (), {})
qt_core.QEvent = type("QEvent", (), {})

class _DummyBlocker:
    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

qt_core.QSignalBlocker = _DummyBlocker
qt_core.QTimer = type("QTimer", (), {})
qt_gui.QFont = type("QFont", (), {})
qt_gui.QKeyEvent = type("QKeyEvent", (), {})
qt_gui.QKeySequence = type("QKeySequence", (), {})
qt_gui.QImage = type("QImage", (), {})
qt_gui.QPixmap = type("QPixmap", (), {})
qt_widgets.QMainWindow = type("QMainWindow", (), {})
qt_widgets.QTreeWidget = type("QTreeWidget", (), {})
qt_widgets.QTreeWidgetItem = type("QTreeWidgetItem", (), {})
qt_widgets.QLabel = type("QLabel", (), {})
qt_widgets.QProgressBar = type("QProgressBar", (), {})
qt_widgets.QListWidgetItem = type("QListWidgetItem", (), {})
qt_widgets.QMessageBox = type("QMessageBox", (), {})
qt_widgets.QPushButton = type("QPushButton", (), {})
qt_widgets.QInputDialog = type("QInputDialog", (), {})
qt_widgets.QApplication = type("QApplication", (), {})
qt_widgets.QDialog = type("QDialog", (), {})
qt_widgets.QTableWidgetItem = type("QTableWidgetItem", (), {})
qt_widgets.QStyledItemDelegate = type("QStyledItemDelegate", (), {})
qt_widgets.QComboBox = type("QComboBox", (), {})
qt_widgets.QTabWidget = type("QTabWidget", (), {})
qt_widgets.QSpinBox = type("QSpinBox", (), {})
qt_widgets.QDoubleSpinBox = type("QDoubleSpinBox", (), {})
qt_widgets.QSlider = type("QSlider", (), {})
qt_widgets.QPlainTextEdit = type("QPlainTextEdit", (), {})
qt_widgets.QLineEdit = type("QLineEdit", (), {})
qt_widgets.QTableWidget = type("QTableWidget", (), {})

qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
qt_module.QtWidgets = qt_widgets
# Shared PyQt6 stub helpers -- see tests/_qt_stub.py for why none of this is a
# hand-written list of Qt class names any more.
from tests._qt_stub import (  # noqa: E402
    StubQtFinder as _StubQtFinder,
    UiStub as _UiStub,
    auto_qt_class as _auto_qt_class,
    autofill_qt_stub as _autofill_qt_stub,
    is_qt_method_name as _is_qt_method_name,
)

if not any(isinstance(f, _StubQtFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _StubQtFinder())
qt_module.__path__ = []  # marks the stub as a package
_autofill_qt_stub(qt_module, qt_core, qt_gui, qt_widgets)

sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)
sys.modules.setdefault("PyQt6.QtWidgets", qt_widgets)

# Stub auxiliary modules that expect optional third-party dependencies.
sys.modules.setdefault("app_util", types.ModuleType("app_util"))
sys.modules.setdefault("newproject", types.ModuleType("newproject")).NewProject = type("NewProject", (), {})
sys.modules.setdefault("exportingwindow", types.ModuleType("exportingwindow")).Exporting = type("Exporting", (), {})
sys.modules.setdefault("plugininfodialog", types.ModuleType("plugininfodialog")).PluginInfoDialog = type("PluginInfoDialog", (), {})

import mainwindow


class CacheToolsBehaviourTest(unittest.TestCase):
    """Verify Tools > Rebuild/Clear cache actions perform the expected work."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = os.path.join(self.tempdir.name, "pokefirered")
        shutil.copytree(os.path.join(ROOT_DIR, "pokefirered"), self.project_root)
        self.project_info = {
            "dir": self.project_root,
            "project_name": "pokefirered",
            "name": "CacheTools",
            "plugin_identifier": "pokefirered",
            "plugin_version": "0.0.0",
            "source_prefix": "",
        }
        module = importlib.import_module("core.pokemon_data")
        self.manager = module.PokemonDataManager(self.project_info)

        self.window = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        # MainWindow.__init__ is skipped on purpose (it would need a real Qt
        # app), so any plain attribute the methods under test read has to be
        # seeded here. _loading_depth is the re-entrancy counter that
        # update_data and the loading guard increment.
        self.window._loading_depth = 0
        self.window.project_info = dict(self.project_info)
        self.window.source_data = self.manager
        self.log_messages: list[str] = []
        self.load_calls: list[dict] = []
        self.window.log = lambda message: self.log_messages.append(message)
        self.window.load_data = lambda info: self.load_calls.append(info)
        status_stub = types.SimpleNamespace(showMessage=lambda *a, **k: None)
        self.window.statusBar = lambda: status_stub

    def tearDown(self):
        self.tempdir.cleanup()

    def test_rebuild_caches_restores_tm_hm_entries(self):
        moves_path = os.path.join(self.project_root, "src", "data", "moves.json")
        with open(moves_path, "r", encoding="utf-8") as handle:
            moves_data = json.load(handle)

        # Pick a species the PROJECT itself gives both TM and HM moves, rather
        # than naming one. This used to hardcode SPECIES_CHARMANDER, which only
        # exists in a stock FireRed -- a project is free to replace the roster,
        # and CLAUDE.md forbids assuming vanilla defaults.
        species_moves = moves_data.get("species_moves", {})
        victim = None
        for species, entries in species_moves.items():
            methods = {e.get("method") for e in entries if isinstance(e, dict)}
            if {"TM", "HM"} <= methods:
                victim = species
                break
        self.assertIsNotNone(
            victim, "no species in this project has both a TM and an HM move")

        species_moves[victim] = []
        with open(moves_path, "w", encoding="utf-8") as handle:
            json.dump(moves_data, handle)

        self.window.rebuild_caches()

        self.assertTrue(self.load_calls, "load_data should execute after rebuilding caches")
        with open(moves_path, "r", encoding="utf-8") as handle:
            rebuilt = json.load(handle)
        restored = rebuilt["species_moves"][victim]
        methods = {entry.get("method") for entry in restored}
        self.assertIn("TM", methods)
        self.assertIn("HM", methods)

    def test_clearing_caches_removes_data_jsons_but_keeps_items(self):
        """items.json must survive a cache clear; the rest is re-extractable.

        This used to call ``window.clear_caches_next_load()`` and also assert
        that the move overlays were emptied. No such method has ever existed on
        the window, and nothing clears those overlays, so the test was pinning a
        feature that was never built. The real API is
        ``_clear_plugin_cache_files``, which rebuild_caches installs as the
        source data's clear step -- and its items.json protection is worth
        pinning, because items.json is the editable source of truth that the
        build regenerates items.h from.
        """
        species_path = os.path.join(self.project_root, "src", "data", "species.json")
        moves_path = os.path.join(self.project_root, "src", "data", "moves.json")
        items_path = os.path.join(self.project_root, "src", "data", "items.json")
        with open(items_path, "w", encoding="utf-8") as handle:
            json.dump({"items": {}}, handle)
        self.assertTrue(os.path.isfile(species_path))
        self.assertTrue(os.path.isfile(moves_path))

        self.window._clear_plugin_cache_files()

        self.assertFalse(os.path.exists(species_path))
        self.assertFalse(os.path.exists(moves_path))
        self.assertTrue(os.path.exists(items_path), "items.json should be preserved")
        # species_info is stashed before deletion so user edits survive the
        # re-extraction that follows.
        self.assertTrue(getattr(self.window, "_stashed_species_edits", None))


if __name__ == "__main__":
    unittest.main()
