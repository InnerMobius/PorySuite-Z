import os
import sys
import types
import importlib.util
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

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
StartersDataExtractor = pdextractor.StartersDataExtractor


class FireRedStartersExtractionTest(unittest.TestCase):
    def test_starters_extracted(self):
        project_info = {
            "dir": os.path.join(ROOT_DIR, "pokefirered"),
            "project_name": "fire_red",
        }
        ext = StartersDataExtractor(project_info, "starters.json")
        data = ext.extract_data()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 3)
        self.assertEqual(data[0]["species"], "SPECIES_BULBASAUR")


if __name__ == "__main__":
    unittest.main()
