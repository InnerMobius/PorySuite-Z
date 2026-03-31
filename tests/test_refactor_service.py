import os
import sys
import tempfile
import types
import importlib.util
import unittest
import json

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


class RefactorPreviewTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)
        with open(os.path.join(root, "src", "example.c"), "w") as f:
            f.write("int a = SPECIES_TEST;\n")
        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write("#define SPECIES_TEST 1\n")
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "species.json"), "w") as f:
            json.dump({"SPECIES_TEST": {"name": "TEST"}}, f)
        os.makedirs(os.path.join(root, "src", "data", "graphics"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "graphics", "items.h"), "w") as f:
            f.write(
                "const struct Item gItems[] = {\n"
                "[ITEM_TEST] = {.name = _(\"TEST\"),},\n"
                "};\n"
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
            "plugins.pokefirered.pokemon_data",
            os.path.join(ROOT_DIR, "plugins", "pokefirered", "pokemon_data.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.PokemonDataManager(self.project_info)

    def test_preview_lists_files(self):
        mgr = self.load_manager()
        preview = mgr.refactor_service.rename_species(
            "SPECIES_TEST", "SPECIES_NEW", "NEW", preview=True
        )
        files = {p[0] for p in preview}
        self.assertIn("src/example.c", files)
        self.assertIn("include/constants/species.h", files)


if __name__ == "__main__":
    unittest.main()
