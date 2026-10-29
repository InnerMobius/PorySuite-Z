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

pkg_name = "core"
pkg_module = types.ModuleType(pkg_name)
pkg_module.__path__ = [os.path.join(ROOT_DIR, "core")]
sys.modules[pkg_name] = pkg_module
spec = importlib.util.spec_from_file_location(
    f"{pkg_name}.pokemon_data_extractor",
    os.path.join(ROOT_DIR, "core", "pokemon_data_extractor.py"),
)
pdextractor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pdextractor
spec.loader.exec_module(pdextractor)
StartersDataExtractor = pdextractor.StartersDataExtractor


def _project_species(project_dir):
    """Every SPECIES_* constant the project defines, read from its own source.

    Tests must not name a species: this project's roster is its own, so the
    only correct check is "is this one of the species this project defines".
    """
    import re
    path = os.path.join(project_dir, "include", "constants", "species.h")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return set()
    return set(re.findall(r"^\s*#define\s+(SPECIES_\w+)", text, re.MULTILINE))


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
        # Assert the SHAPE, and that every starter names a species the project
        # actually defines. This used to assert data[0] was SPECIES_BULBASAUR,
        # which only holds for a stock FireRed — a project is free to replace
        # the whole roster, and CLAUDE.md forbids assuming vanilla defaults.
        defined = _project_species(project_info["dir"])
        self.assertTrue(defined, "project defines no species constants")
        for i, entry in enumerate(data):
            self.assertIsInstance(entry, dict, f"starter {i} is not a dict")
            self.assertIn("species", entry, f"starter {i} has no species")
            self.assertIn(entry["species"], defined,
                          f"starter {i} is {entry['species']}, which the "
                          f"project does not define")


if __name__ == "__main__":
    unittest.main()
