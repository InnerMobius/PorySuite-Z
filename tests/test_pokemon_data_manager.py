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

def pyqtSignal(*args, **kwargs):
    return None

qt_core.pyqtSignal = pyqtSignal
class QImage: pass
class QPixmap: pass
qt_gui.QImage = QImage
qt_gui.QPixmap = QPixmap
qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)

from header_stubs import write_move_headers, write_pokedex_headers, write_species_headers
from core.pokemon_data_base import MissingSourceError

SPECIES_INFO = """\
[SPECIES_NONE] =
{
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_TEST] =
{
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_EGG] =
{
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
"""

SPECIES_INFO_MISMATCH = """\
[SPECIES_NONE] =
{
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_TEST] =
{
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
"""

SPECIES_INFO_MACRO = """\
#define BASIC_INFO                            \
    {                                         \
        .baseHP = 1,                          \
        .types = {TYPE_NORMAL, TYPE_NORMAL},   \
        .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE}, \
    }

[SPECIES_NONE] = BASIC_INFO,
[SPECIES_TEST] = BASIC_INFO,
[SPECIES_EXTRA] = BASIC_INFO,
[SPECIES_EGG] = BASIC_INFO,
"""

POKEDEX_H = """\
enum {
    NATIONAL_DEX_NONE,
    NATIONAL_DEX_TEST,
};
"""

POKEDEX_ENTRIES_H = """\
[NATIONAL_DEX_NONE] =
{
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
[NATIONAL_DEX_TEST] =
{
    .categoryName = _(\"TEST\"),
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
[ITEM_TEST] = {
    .name = _(\"TEST\"),
};
"""

STARTERS_C = "const u16 sStarterSpecies[] = { SPECIES_TEST };"

BATTLE_MOVES_H = """\
[MOVE_TEST] = {
    .power = 0,
};
"""

MOVE_DESCS_C = '[MOVE_TEST] = _(\"desc\");'

ABILITIES_H = """\
#define ABILITY_NONE 0
#define ABILITY_TEST 1
"""

BATTLE_SETUP_C = """\
static void CB2_GiveStarter(void)
{
    u16 starterMon;
    switch(gSpecialVar_Result)
    {
    case 0: // SPECIES_TEST
        ScriptGiveMon(starterMon, 5, ITEM_TEST, 0, 0, 0);
        abilityNum = 1;
        SetMonData(&gPlayerParty[0], MON_DATA_ABILITY_NUM, &abilityNum);
        break;
    }
}
"""

class DataManagerGenerationTest(unittest.TestCase):
    def create_project(self, info_content=SPECIES_INFO):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)
        # header files
        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(info_content)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n"
                "    [SPECIES_NONE] = _(\"UNKNOWN\"),\n"
                "    [SPECIES_TEST] = _(\"TEST\"),\n"
                "};"
            )
        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_NONE 0\n"
                "#define SPECIES_TEST 1\n"
                "#define SPECIES_EGG 2\n"
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
        with open(os.path.join(root, "src", "data", "trainers.h"), "w") as f:
            f.write("[TRAINER_TEST] = {};")
        write_species_headers(root)
        write_pokedex_headers(root)
        write_move_headers(root)
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def setUp(self):
        self.create_project()

    def test_json_generated(self):
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
        module.PokemonDataManager(self.project_info)
        expected = [
            "species.json",
            "items.json",
            "abilities.json",
            "starters.json",
            "moves.json",
            "pokedex.json",
        ]
        for name in expected:
            path = os.path.join(self.project_info["dir"], "src", "data", name)
            self.assertTrue(os.path.isfile(path), f"{name} not generated")

        with open(os.path.join(self.project_info["dir"], "src", "data", "starters.json")) as f:
            starters = json.load(f)
        self.assertEqual(starters[0]["species"], "SPECIES_TEST")
        self.assertEqual(starters[0]["level"], 5)
        self.assertEqual(starters[0]["item"], "ITEM_TEST")
        self.assertEqual(starters[0]["ability_num"], 1)

        # Ensure species names were stored
        with open(
            os.path.join(self.project_info["dir"], "src", "data", "species.json"),
            "r",
            encoding="utf-8",
        ) as f:
            species = json.load(f)
        self.assertTrue(any(
            info.get("species_info", {}).get("speciesName") for info in species.values()
        ))
        # Assert at least one entry preserved the display name
        self.assertTrue(any(
            info.get("name") for info in species.values()
        ))

    def test_pokedex_info_merged(self):
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
        module.PokemonDataManager(self.project_info)

        with open(
            os.path.join(self.project_info["dir"], "src", "data", "species.json"),
            "r",
            encoding="utf-8",
        ) as f:
            species = json.load(f)

        entry = species["SPECIES_TEST"].get("pokedex", {})
        self.assertEqual(entry.get("categoryName"), "TEST")
        self.assertEqual(entry.get("description"), "gTestPokedexText")

    def test_species_count_mismatch(self):
        self.tempdir.cleanup()
        self.create_project(SPECIES_INFO_MISMATCH)

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
        module.PokemonDataManager(self.project_info)

        species_json = os.path.join(self.project_info["dir"], "src", "data", "species.json")
        self.assertTrue(os.path.isfile(species_json), "species.json should be written")
        with open(species_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 2)

    def test_macro_species_definitions(self):
        self.tempdir.cleanup()
        self.create_project(SPECIES_INFO_MACRO)

        root = self.project_info["dir"]
        names_path = os.path.join(root, "src", "data", "text", "species_names.h")
        with open(names_path, "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n"
                "    [SPECIES_NONE] = _(\"UNKNOWN\"),\n"
                "    [SPECIES_TEST] = _(\"TEST\"),\n"
                "    [SPECIES_EXTRA] = _(\"EXTRA\"),\n"
                "};"
            )

        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_NONE 0\n"
                "#define SPECIES_TEST 1\n"
                "#define SPECIES_EGG 2\n"
                "#define SPECIES_EXTRA 3\n"
                "#define NUM_SPECIES SPECIES_EGG\n"
            )

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
        module.PokemonDataManager(self.project_info)

        species_json = os.path.join(root, "src", "data", "species.json")
        with open(species_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("SPECIES_EXTRA", data)
        self.assertEqual(len(data), 4)


class MissingItemsJsonRegressionTest(unittest.TestCase):
    """Ensure items still load when items.json is absent."""

    def setUp(self):
        DataManagerGenerationTest.create_project(self)

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
        return module.PokemonDataManager(self.project_info)

    def test_items_loaded_without_json(self):
        mgr = self.load_manager()
        self.assertIn("ITEM_TEST", mgr.get_pokemon_items())

    def test_missing_items_header_blocks_save(self):
        mgr = self.load_manager()
        root = self.project_info["dir"]
        graphics_header = os.path.join(root, "src", "data", "graphics", "items.h")
        os.remove(graphics_header)
        with self.assertRaises(MissingSourceError) as ctx:
            mgr.save()
        missing_paths = ctx.exception.missing
        self.assertTrue(
            any("items.h" in path.replace("\\", "/") for path in missing_paths),
            f"items.h not reported missing: {missing_paths}",
        )

    def test_items_header_prefers_primary_path(self):
        mgr = self.load_manager()
        root = self.project_info["dir"]
        graphics_header = os.path.join(root, "src", "data", "graphics", "items.h")
        if os.path.exists(graphics_header):
            os.remove(graphics_header)
        primary_header = os.path.join(root, "src", "data", "items.h")
        with open(primary_header, "w", encoding="utf-8") as f:
            f.write("const struct Item gItems[] = {\n" + ITEMS_H + "\n};\n")

        missing = mgr.missing_required_sources()
        self.assertEqual([], missing)

        items_obj = mgr.data.get("pokemon_items")
        self.assertIsNotNone(items_obj)
        self.assertEqual(
            os.path.normpath("src/data/items.h"),
            getattr(items_obj, "_items_header_path"),
        )

        # Should not raise now that a canonical header exists
        mgr.save()


class ItemsListFormatTest(unittest.TestCase):
    pass
if __name__ == "__main__":
    unittest.main()
