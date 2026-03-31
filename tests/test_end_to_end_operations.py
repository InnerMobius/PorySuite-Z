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

from header_stubs import write_move_headers, write_pokedex_headers, write_species_headers

# Stub PyQt6 modules
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

# Stub additional modules imported by mainwindow
sys.modules.setdefault("app_util", types.ModuleType("app_util"))
sys.modules.setdefault("newproject", types.ModuleType("newproject")).NewProject = type("NewProject", (), {})
sys.modules.setdefault("exportingwindow", types.ModuleType("exportingwindow")).Exporting = type("Exporting", (), {})
sys.modules.setdefault("plugininfodialog", types.ModuleType("plugininfodialog")).PluginInfoDialog = type("PluginInfoDialog", (), {})

sys.modules.setdefault("ui", types.ModuleType("ui"))
ui_main = types.ModuleType("ui.ui_mainwindow")

class Ui_MainWindow:
    def setupUi(self, _):
        pass

ui_main.Ui_MainWindow = Ui_MainWindow
sys.modules["ui.ui_mainwindow"] = ui_main
sys.modules.setdefault("ui.delegates.pokedexitemdelegate", types.ModuleType("ui.delegates.pokedexitemdelegate")).PokedexItemDelegate = type("PokedexItemDelegate", (), {})

import mainwindow

SPECIES_INFO = """\
[SPECIES_NONE] = {
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_TEST] = {
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_OTHER] = {
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
    NATIONAL_DEX_TEST,
    NATIONAL_DEX_OTHER,
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
[NATIONAL_DEX_TEST] = {
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
[NATIONAL_DEX_OTHER] = {
    .categoryName = _(\"OTHER\"),
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
#define ABILITY_TEST 1
"""

STARTERS_C = "const u16 sStarterSpecies[] = { SPECIES_TEST };"

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

BATTLE_MOVES_H = "[MOVE_TEST] = {.power = 0,};"
MOVE_DESCS_C = '[MOVE_TEST] = _(\"desc\");'

class ProjectTest(unittest.TestCase):
    def create_project(self):
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
                "    [SPECIES_TEST] = _(\"TEST\"),\n"
                "    [SPECIES_OTHER] = _(\"OTHER\"),\n"
                "};"
            )
        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_NONE 0\n"
                "#define SPECIES_TEST 1\n"
                "#define SPECIES_OTHER 2\n"
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
        os.makedirs(os.path.join(root, "src", "data", "pokemon", "species_info"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "pokemon", "evolution.h"), "w") as f:
            f.write("const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] = {};\n")
        write_species_headers(root)
        write_pokedex_headers(root)
        write_move_headers(root)
        with open(os.path.join(root, "src", "data", "constants.json"), "w") as f:
            f.write("{}")
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
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def setUp(self):
        self.create_project()

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

    def test_species_rename_updates_files(self):
        mgr = self.load_manager()
        mgr.refactor_service.rename_species("SPECIES_TEST", "SPECIES_NEW", "NEW")
        # headers updated
        with open(os.path.join(self.project_info["dir"], "include", "constants", "species.h")) as f:
            content = f.read()
        self.assertIn("SPECIES_NEW", content)
        self.assertNotIn("SPECIES_TEST", content)
        with open(os.path.join(self.project_info["dir"], "src", "data", "text", "species_names.h")) as f:
            names = f.read()
        self.assertIn("SPECIES_NEW", names)
        self.assertNotIn("SPECIES_TEST", names)
        # json updated
        with open(os.path.join(self.project_info["dir"], "src", "data", "species.json")) as f:
            species = json.load(f)
        self.assertIn("SPECIES_NEW", species)
        self.assertNotIn("SPECIES_TEST", species)

    def test_edit_starters_updates_sources(self):
        mgr = self.load_manager()
        starter = mgr.data["pokemon_starters"].data[0]
        starter["species"] = "SPECIES_NONE"
        starter["level"] = 7
        starter["item"] = "ITEM_TEST"
        starter["ability_num"] = 0
        mgr.parse_to_c_code()
        with open(os.path.join(self.project_info["dir"], "src", "field_specials.c")) as f:
            content = f.read()
        self.assertIn("SPECIES_NONE", content)
        with open(os.path.join(self.project_info["dir"], "src", "battle_setup.c")) as f:
            bcontent = f.read()
        self.assertIn("ScriptGiveMon(starterMon, 7, ITEM_TEST", bcontent)
        self.assertIn("abilityNum = 0;", bcontent)

    def test_reorder_pokedex_updates_header(self):
        mgr = self.load_manager()
        dex = mgr.data["pokedex"].data["national_dex"]
        dex[0], dex[1] = dex[1], dex[0]
        mgr.data["pokedex"].save()
        mgr.data["pokedex"].parse_to_c_code()
        with open(os.path.join(self.project_info["dir"], "include", "constants", "pokedex.h")) as f:
            lines = f.read().splitlines()
        order = [ln.strip().rstrip(',') for ln in lines if ln.strip().startswith("NATIONAL_DEX_") and "NONE" not in ln]
        self.assertEqual(order[0], "NATIONAL_DEX_OTHER")

    def test_update_data_sets_abilities(self):
        mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        class DummyBox:
            def __init__(self):
                self.index = None
            def setCurrentIndex(self, idx):
                self.index = idx
        class DummyWidget:
            def setText(self, *_):
                pass
            def setPlainText(self, *_):
                pass
            def setValue(self, *_):
                pass
            def setStyleSheet(self, *_):
                pass
            def setCurrentIndex(self, *_):
                pass
            def setEnabled(self, *_):
                pass
        class DummyEvo:
            def clear(self):
                pass
            def addTopLevelItem(self, *_):
                pass
            def columnCount(self):
                return 0
            def resizeColumnToContents(self, *_):
                pass

        mw.ui = types.SimpleNamespace(
            species_name=DummyWidget(),
            dex_num=DummyWidget(),
            species_category=DummyWidget(),
            species_description=DummyWidget(),
            base_hp=DummyWidget(),
            base_atk=DummyWidget(),
            base_def=DummyWidget(),
            base_speed=DummyWidget(),
            base_spatk=DummyWidget(),
            base_spdef=DummyWidget(),
            type1=DummyWidget(),
            type2=DummyWidget(),
            ability1=DummyBox(),
            ability2=DummyBox(),
            ability_hidden=DummyBox(),
            evs_hp=DummyWidget(),
            evs_atk=DummyWidget(),
            evs_def=DummyWidget(),
            evs_speed=DummyWidget(),
            evs_spatk=DummyWidget(),
            evs_spdef=DummyWidget(),
            catch_rate=DummyWidget(),
            exp_yield=DummyWidget(),
            gender_ratio=DummyWidget(),
            held_item_common=DummyWidget(),
            held_item_rare=DummyWidget(),
            egg_cycles=DummyWidget(),
            egg_group_1=DummyWidget(),
            egg_group_2=DummyWidget(),
            exp_growth_rate=DummyWidget(),
            base_friendship=DummyWidget(),
            safari_zone_flee_rate=DummyWidget(),
            frontPic_0=DummyWidget(),
            frontPic_1=DummyWidget(),
            backPic=DummyWidget(),
            iconPic=DummyWidget(),
            footprintPic=DummyWidget(),
            evolutions=DummyEvo(),
            tab_pokemon_data=DummyWidget(),
        )
        mw.type_index_map = {"TYPE_NORMAL": 1}

        mw.update_gender_ratio = lambda *_: None

        abilities = ["ABILITY_TEST", "ABILITY_NONE", "ABILITY_NONE"]
        def get_species_ability(sp, idx, form=None):
            return abilities[idx]

        def get_species_info(sp, key, form=None):
            if key == "types":
                return ["TYPE_NORMAL", "TYPE_NORMAL"]
            if key == "eggGroups":
                return ["EGG_GROUP_FIELD", "EGG_GROUP_FIELD"]
            return 0

        def get_evolutions(sp):
            return []

        mw.source_data = types.SimpleNamespace(
            get_species_info=get_species_info,
            get_evolutions=get_evolutions,
            get_species_data=lambda *a, **k: 0,
            get_constant_data=lambda *a, **k: {"value": 0, "name": ""},
            get_item_data=lambda *a, **k: 0,
            get_species_image_path=lambda *a, **k: None,
            get_species_ability=get_species_ability,
            get_ability_data=lambda const, key: (1 if const == "ABILITY_TEST" else 0),
        )

        mw.update_data("SPECIES_TEST")
        self.assertEqual(mw.ui.ability1.index, 1)
        self.assertEqual(mw.ui.ability2.index, 0)
        self.assertEqual(mw.ui.ability_hidden.index, 0)

if __name__ == "__main__":
    unittest.main()
