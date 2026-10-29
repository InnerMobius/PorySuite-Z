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
PokemonEvolutionsExtractor = pdextractor.PokemonEvolutionsExtractor


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


class FireRedEvolutionsExtractionTest(unittest.TestCase):
    def test_evolutions_extracted(self):
        project_info = {
            "dir": os.path.join(ROOT_DIR, "pokefirered"),
            "project_name": "fire_red",
        }
        ext = PokemonEvolutionsExtractor(project_info, "evolutions.json")
        data = ext.extract_data()
        # Assert the SHAPE, and that every species named is one the project
        # defines. This used to assert SPECIES_BULBASAUR evolved into
        # SPECIES_IVYSAUR, which only holds for a stock FireRed — a project is
        # free to replace the whole roster, and CLAUDE.md forbids assuming
        # vanilla defaults.
        self.assertIsInstance(data, dict)
        self.assertTrue(data, "no evolutions extracted at all")
        defined = _project_species(project_info["dir"])
        self.assertTrue(defined, "project defines no species constants")
        for species, chain in data.items():
            self.assertIn(species, defined,
                          f"{species} has evolutions but is not defined")
            self.assertGreaterEqual(len(chain), 1,
                                    f"{species} has an empty evolution list")
            for step in chain:
                for key in ("method", "param", "targetSpecies"):
                    self.assertIn(key, step, f"{species}: step missing {key}")
                self.assertIn(step["targetSpecies"], defined,
                              f"{species} evolves into "
                              f"{step['targetSpecies']}, which is not defined")


if __name__ == "__main__":
    unittest.main()
