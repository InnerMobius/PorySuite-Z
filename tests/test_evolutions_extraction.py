import os
import sys
import types
import importlib.util
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Stub minimal PyQt6 modules
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")
qt_core.pyqtSignal = lambda *a, **k: None
class _DummyBlocker:
    def __init__(self, *_):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
qt_core.QSignalBlocker = _DummyBlocker
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
PokemonEvolutionsExtractor = pdextractor.PokemonEvolutionsExtractor


class FireRedEvolutionsExtractionTest(unittest.TestCase):
    def test_evolutions_extracted(self):
        project_info = {
            "dir": os.path.join(ROOT_DIR, "pokefirered"),
            "project_name": "fire_red",
        }
        ext = PokemonEvolutionsExtractor(project_info, "evolutions.json")
        data = ext.extract_data()
        self.assertIn("SPECIES_BULBASAUR", data)
        self.assertGreaterEqual(len(data["SPECIES_BULBASAUR"]), 1)
        self.assertEqual(
            data["SPECIES_BULBASAUR"][0]["targetSpecies"], "SPECIES_IVYSAUR"
        )


if __name__ == "__main__":
    unittest.main()
