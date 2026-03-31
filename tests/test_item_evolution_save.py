import os
import sys
import types
import json
import tempfile
import importlib.util
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from plugin_abstract.pokemon_data import MissingSourceError

from header_stubs import (
    write_move_headers,
    write_pokedex_headers,
    write_species_headers,
)

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


class ItemEvolutionSaveTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "graphics"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)
        # Provide items.json so ItemsDataExtractor loads without items.h
        with open(os.path.join(root, "src", "data", "items.json"), "w") as f:
            json.dump({"ITEM_TEST": {"name": '_("TEST")'}}, f)
        with open(os.path.join(root, "include", "constants", "pokedex.h"), "w") as f:
            f.write("enum { NATIONAL_DEX_NONE, NATIONAL_DEX_TEST, };\n")
        write_species_headers(root)
        with open(os.path.join(root, "include", "constants", "species.h"), "a") as f:
            f.write("#define SPECIES_A 0\n#define SPECIES_B 1\n#define NUM_SPECIES 2\n")
        with open(os.path.join(root, "include", "constants", "abilities.h"), "w") as f:
            f.write("#define ABILITY_NONE 0\n")
        write_pokedex_headers(root)
        write_move_headers(root)
        # Evolution referencing an item
        with open(os.path.join(root, "src", "data", "evolutions.json"), "w") as f:
            json.dump(
                {
                    "SPECIES_A": [
                        {
                            "method": "EVO_ITEM",
                            "param": "ITEM_TEST",
                            "targetSpecies": "SPECIES_B",
                        }
                    ]
                },
                f,
            )
        # Minimal evolution header for backup
        with open(
            os.path.join(root, "src", "data", "pokemon", "evolution.h"), "w"
        ) as f:
            f.write(
                "const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] = {}\n"
            )
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def load_manager(self):
        pkg_name = "plugins.pokefirered"
        sys.modules.setdefault("plugins", types.ModuleType("plugins"))
        pkg_module = types.ModuleType(pkg_name)
        pkg_module.__path__ = [os.path.join(ROOT_DIR, "plugins", "pokefirered")]
        sys.modules[pkg_name] = pkg_module
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.pokemon_data",
            os.path.join(ROOT_DIR, "plugins", "pokefirered", "pokemon_data.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        class MinimalManager(module.pokemon_data.PokemonDataManager):
            SOURCE_PREFIX = ""
            def __init__(self, project_info):
                super().__init__(project_info)
                self.add_pokemon_items_class(module.PokemonItems)
                self.add_pokemon_evolutions_class(module.PokemonEvolutions)

        return MinimalManager(self.project_info)

    def test_save_item_evolution(self):
        mgr = self.load_manager()
        with self.assertRaises(MissingSourceError):
            mgr.parse_to_c_code()
        evo_path = os.path.join(
            self.project_info["dir"], "src", "data", "pokemon", "evolution.h"
        )
        with open(evo_path) as f:
            content = f.read()
        self.assertNotIn("ITEM_TEST", content)


if __name__ == "__main__":
    unittest.main()
