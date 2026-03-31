import os
import sys
import types
import importlib.util
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Stub PyQt6 modules
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")
qt_core.pyqtSignal = lambda *a, **k: None
qt_gui.QImage = type("QImage", (), {})
qt_gui.QPixmap = type("QPixmap", (), {})
qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)

# Load the extractor module without running the plugin package __init__
pkg_name = "plugins.pokefirered"
sys.modules.setdefault("plugins", types.ModuleType("plugins"))
pkg_module = types.ModuleType(pkg_name)
pkg_module.__path__ = [os.path.join(ROOT_DIR, "plugins", "pokefirered")]
sys.modules[pkg_name] = pkg_module
spec = importlib.util.spec_from_file_location(
    f"{pkg_name}.pokemon_data_extractor",
    os.path.join(ROOT_DIR, "plugins", "pokefirered", "pokemon_data_extractor.py"),
)
pdextractor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pdextractor
spec.loader.exec_module(pdextractor)
MovesDataExtractor = pdextractor.MovesDataExtractor


class FireRedMovesExtractionTest(unittest.TestCase):
    def test_moves_extracted(self):
        project_info = {
            "dir": os.path.join(ROOT_DIR, "pokefirered"),
            "project_name": "fire_red",
        }
        ext = MovesDataExtractor(project_info, "moves.json")
        data = ext.extract_data()
        self.assertIn("moves", data)
        self.assertGreater(len(data["moves"]), 0)
        self.assertEqual(data["moves"]["MOVE_POUND"]["power"], 40)
        self.assertIn("MOVE_ABSORB", data["moves"]) 


if __name__ == "__main__":
    unittest.main()
