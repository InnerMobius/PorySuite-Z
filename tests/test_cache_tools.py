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
        module = importlib.import_module("plugins.pokefirered.pokemon_data")
        self.manager = module.PokemonDataManager(self.project_info)

        self.window = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
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
        moves_data["species_moves"]["SPECIES_CHARMANDER"] = []
        with open(moves_path, "w", encoding="utf-8") as handle:
            json.dump(moves_data, handle)

        self.window.rebuild_caches()

        self.assertTrue(self.load_calls, "load_data should execute after rebuilding caches")
        with open(moves_path, "r", encoding="utf-8") as handle:
            rebuilt = json.load(handle)
        charmander_moves = rebuilt["species_moves"]["SPECIES_CHARMANDER"]
        methods = {entry.get("method") for entry in charmander_moves}
        self.assertIn("TM", methods)
        self.assertIn("HM", methods)

    def test_clear_caches_next_load_removes_jsons_and_overlays(self):
        species_path = os.path.join(self.project_root, "src", "data", "species.json")
        moves_path = os.path.join(self.project_root, "src", "data", "moves.json")
        items_path = os.path.join(self.project_root, "src", "data", "items.json")
        with open(items_path, "w", encoding="utf-8") as handle:
            json.dump({"items": {}}, handle)
        self.assertTrue(os.path.isfile(species_path))
        self.assertTrue(os.path.isfile(moves_path))

        self.manager._fr_species_moves_overlay["SPECIES_CHARMANDER"] = [
            {"move": "MOVE_TACKLE", "method": "LEVEL", "value": 1}
        ]
        self.manager._fr_move_desc_overlay["MOVE_TACKLE"] = "A simple tackle"
        self.manager._fr_move_desc_ready = True

        self.window.clear_caches_next_load()

        self.assertFalse(os.path.exists(species_path))
        self.assertFalse(os.path.exists(moves_path))
        self.assertTrue(os.path.exists(items_path), "items.json should be preserved")
        self.assertEqual(self.manager._fr_species_moves_overlay, {})
        self.assertEqual(self.manager._fr_move_desc_overlay, {})
        self.assertFalse(self.manager._fr_move_desc_ready)


if __name__ == "__main__":
    unittest.main()
