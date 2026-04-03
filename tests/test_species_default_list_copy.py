import os
import sys
import types

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Minimal PyQt6 stubs
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

import core.pokemon_data_extractor as pde

class DummyDex:
    def __init__(self, project_info, data_file):
        pass
    def extract_data(self):
        return {"national_dex": []}

def test_species_default_lists_are_independent():
    # patch helpers
    orig_parse_species_names = pde.parse_species_names
    orig_parse_species_count = pde._parse_species_count
    orig_read_header = pde._read_header
    orig_parse_pokedex_entries = pde.parse_pokedex_entries
    _orig_load_json = pde._load_json
    orig_dex = pde.PokedexDataExtractor
    orig_get_lines = pde.SpeciesDataExtractor._get_species_header_lines
    try:
        pde.parse_species_names = lambda root: {"SPECIES_A": "A", "SPECIES_B": "B"}
        pde._parse_species_count = lambda root: 2
        pde.parse_pokedex_entries = lambda util: {}
        pde._read_header = lambda util, *parts: []
        pde._load_json = lambda path: None
        pde.PokedexDataExtractor = DummyDex
        header_lines = [
            "[SPECIES_A] = {\n",
            "    .baseHP = 10,\n",
            "},\n",
            "[SPECIES_B] = {\n",
            "    .baseHP = 20,\n",
            "},\n",
        ]
        pde.SpeciesDataExtractor._get_species_header_lines = lambda self: header_lines

        ext = pde.SpeciesDataExtractor({"dir": "", "project_name": "test"}, "species.json")
        data = ext.extract_data()
        types_a = data["SPECIES_A"]["species_info"]["types"]
        types_b = data["SPECIES_B"]["species_info"]["types"]
        assert types_a is not types_b
        types_a[0] = "TYPE_FIRE"
        assert types_b[0] == "TYPE_NORMAL"
    finally:
        pde.parse_species_names = orig_parse_species_names
        pde._parse_species_count = orig_parse_species_count
        pde.parse_pokedex_entries = orig_parse_pokedex_entries
        pde._read_header = orig_read_header
        pde._load_json = _orig_load_json
        pde.PokedexDataExtractor = orig_dex
        pde.SpeciesDataExtractor._get_species_header_lines = orig_get_lines
