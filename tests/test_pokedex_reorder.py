import os
import sys
import tempfile
import types
import importlib.util
import json
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

SPECIES_INFO = """\
[SPECIES_NONE] = {
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_ALPHA] = {
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_BETA] = {
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_EGG] = {
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
"""

POKEDEX_H = """\
enum {
    NATIONAL_DEX_NONE,
    NATIONAL_DEX_ALPHA,
    NATIONAL_DEX_BETA,
};
"""

POKEDEX_ENTRIES_H = """\
[NATIONAL_DEX_NONE] = {
    .categoryName = _(\"UNKNOWN\"),
    .height = 0,
    .weight = 0,
    .description = gDummyPokedexText,
    .unusedDescription = gDummyPokedexTextUnused,
    .pokemonScale = 256,
    .pokemonOffset = 0,
    .trainerScale = 256,
    .trainerOffset = 0,
},
[NATIONAL_DEX_ALPHA] = {
    .categoryName = _(\"ALPHA\"),
    .height = 1,
    .weight = 1,
    .description = gTestPokedexText,
    .unusedDescription = gTestPokedexTextUnused,
    .pokemonScale = 256,
    .pokemonOffset = 0,
    .trainerScale = 256,
    .trainerOffset = 0,
},
[NATIONAL_DEX_BETA] = {
    .categoryName = _(\"BETA\"),
    .height = 1,
    .weight = 1,
    .description = gTestPokedexText,
    .unusedDescription = gTestPokedexTextUnused,
    .pokemonScale = 256,
    .pokemonOffset = 0,
    .trainerScale = 256,
    .trainerOffset = 0,
},
"""

POKEDEX_TEXT = """\
const u8 gDummyPokedexText[] = _(\"dummy\");
const u8 gDummyPokedexTextUnused[] = _(\"");
const u8 gTestPokedexText[] = _(\"Test entry.\");
const u8 gTestPokedexTextUnused[] = _(\"");
"""

ITEMS_H = """\
[ITEM_TEST] = {.name = _(\"TEST\"),};
"""

ABILITIES_H = """\
#define ABILITY_NONE 0
"""

STARTERS_C = "const u16 sStarterSpecies[] = { SPECIES_ALPHA };"
BATTLE_SETUP_C = "static void CB2_GiveStarter(void){u16 starterMon;switch(gSpecialVar_Result){case 0: ScriptGiveMon(starterMon,5,ITEM_TEST,0,0,0);abilityNum=1;SetMonData(&gPlayerParty[0],MON_DATA_ABILITY_NUM,&abilityNum);break;}}"
BATTLE_MOVES_H = "[MOVE_TEST] = {.power = 0,};"
MOVE_DESCS_C = '[MOVE_TEST] = _(\"desc\");'

class ReorderTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(SPECIES_INFO)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n"
                "    [SPECIES_NONE] = _(\"UNKNOWN\"),\n"
                "    [SPECIES_ALPHA] = _(\"ALPHA\"),\n"
                "    [SPECIES_BETA] = _(\"BETA\"),\n"
                "};"
            )
        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_NONE 0\n"
                "#define SPECIES_ALPHA 1\n"
                "#define SPECIES_BETA 2\n"
                "#define SPECIES_EGG 3\n"
                "#define NUM_SPECIES SPECIES_EGG\n"
            )
        with open(os.path.join(root, "include", "constants", "pokedex.h"), "w") as f:
            f.write(POKEDEX_H)
        with open(os.path.join(root, "src", "data", "pokemon", "pokedex_entries.h"), "w") as f:
            f.write(POKEDEX_ENTRIES_H)
        with open(os.path.join(root, "src", "data", "pokemon", "pokedex_text_fr.h"), "w") as f:
            f.write(POKEDEX_TEXT)
        os.makedirs(os.path.join(root, "src", "data", "graphics"), exist_ok=True)
        with open(
            os.path.join(root, "src", "data", "graphics", "items.h"), "w"
        ) as f:
            f.write("const struct Item gItems[] = {\n" + ITEMS_H + "\n};\n")
        with open(os.path.join(root, "include", "constants", "abilities.h"), "w") as f:
            f.write(ABILITIES_H)
        with open(os.path.join(root, "src", "field_specials.c"), "w") as f:
            f.write(STARTERS_C)
        with open(os.path.join(root, "src", "battle_setup.c"), "w") as f:
            f.write(BATTLE_SETUP_C)
        with open(os.path.join(root, "src", "data", "battle_moves.h"), "w") as f:
            f.write(BATTLE_MOVES_H)
        with open(os.path.join(root, "src", "move_descriptions.c"), "w") as f:
            f.write(MOVE_DESCS_C)
        os.makedirs(os.path.join(root, "src", "data", "pokemon", "species_info"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write("\n")
        with open(os.path.join(root, "src", "data", "pokemon", "species_info", "pory_species.h"), "w") as f:
            f.write("\n")
        with open(os.path.join(root, "src", "data", "constants.json"), "w") as f:
            f.write("{}")
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

    def test_reorder_updates_header_and_species(self):
        mgr = self.load_manager()
        dex = mgr.data["pokedex"].data["national_dex"]
        dex[0], dex[1] = dex[1], dex[0]
        mgr.data["pokedex"].save()
        mgr.data["pokedex"].parse_to_c_code()
        with open(os.path.join(self.project_info["dir"], "include", "constants", "pokedex.h")) as f:
            content = f.read().splitlines()
        order = [ln.strip().rstrip(',') for ln in content if ln.strip().startswith("NATIONAL_DEX_") and "NONE" not in ln]
        self.assertEqual(order[0], "NATIONAL_DEX_BETA")
        # Species data should also update to reflect the new order
        if os.path.isfile(os.path.join(self.project_info["dir"], "src", "data", "species.json")):
            with open(os.path.join(self.project_info["dir"], "src", "data", "species.json")) as f:
                species = json.load(f)
            self.assertEqual(species.get("SPECIES_BETA", {}).get("dex_num"), 1)
            self.assertEqual(species.get("SPECIES_ALPHA", {}).get("dex_num"), 2)

    def test_entry_text_populated(self):
        mgr = self.load_manager()
        dex = mgr.data["pokedex"].data["national_dex"]
        first = dex[0]
        self.assertEqual(first.get("descriptionText"), "Test entry.")

if __name__ == "__main__":
    unittest.main()
