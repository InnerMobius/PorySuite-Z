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
qt_core.Qt = type("Qt", (), {})
qt_core.QEvent = type("QEvent", (), {})
class _DummyBlocker:
    def __init__(self, *_):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
qt_core.QSignalBlocker = _DummyBlocker
qt_gui.QFont = type("QFont", (), {})
qt_gui.QKeyEvent = type("QKeyEvent", (), {})
qt_gui.QKeySequence = type("QKeySequence", (), {})
qt_gui.QImage = type("QImage", (), {})
qt_gui.QPixmap = type("QPixmap", (), {})
qt_widgets = types.ModuleType("PyQt6.QtWidgets")


class QTableWidgetItem:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def flags(self):
        return 0

    def setFlags(self, *_):
        pass


qt_widgets.QTableWidgetItem = QTableWidgetItem
qt_widgets.QApplication = type("QApplication", (), {})
qt_widgets.QMainWindow = type("QMainWindow", (), {})
qt_widgets.QLabel = type("QLabel", (), {})
qt_widgets.QProgressBar = type("QProgressBar", (), {})
qt_widgets.QTreeWidgetItem = type("QTreeWidgetItem", (), {})
qt_widgets.QTreeWidget = type("QTreeWidget", (), {})
qt_widgets.QListWidgetItem = type("QListWidgetItem", (), {})
qt_widgets.QMessageBox = type("QMessageBox", (), {})
qt_widgets.QInputDialog = type("QInputDialog", (), {})
qt_widgets.QPushButton = type("QPushButton", (), {})
qt_module.QtWidgets = qt_widgets
sys.modules.setdefault("PyQt6.QtWidgets", qt_widgets)
qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)

POKEDEX_H = """\
enum {
    NATIONAL_DEX_NONE,
    NATIONAL_DEX_TEST,
};
"""

ITEMS_H = """\
[ITEM_TEST] = {
    .name = _(\"TEST\"),
};
"""

BATTLE_MOVES_H = """\
[MOVE_TEST] = {
    .power = 0,
};
"""

MOVE_DESCS_C = '[MOVE_TEST] = _("desc");'

ABILITIES_H = """\
#define ABILITY_NONE 0
#define ABILITY_TEST 1
"""

FIELD_SPECIALS_C = "const u16 sStarterSpecies[] = { SPECIES_TEST };"

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

TRAINERS_H = """\
[TRAINER_TEST] = {
    .trainerClass = TRAINER_CLASS_TEST,
    .trainerName = _(\"NAME\"),
    .items = {},
    .doubleBattle = FALSE,
    .aiFlags = AI_SCRIPT_TEST,
    .party = TRAINER_PARTY_TEST,
};
"""


class WritebackTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)
        with open(os.path.join(root, "include", "constants", "pokedex.h"), "w") as f:
            f.write(POKEDEX_H)
        os.makedirs(os.path.join(root, "src", "data", "graphics"), exist_ok=True)
        with open(
            os.path.join(root, "src", "data", "graphics", "items.h"), "w"
        ) as f:
            f.write("const struct Item gItems[] = {\n" + ITEMS_H + "\n};\n")
        with open(os.path.join(root, "include", "constants", "abilities.h"), "w") as f:
            f.write(ABILITIES_H)
        with open(os.path.join(root, "src", "data", "battle_moves.h"), "w") as f:
            f.write(BATTLE_MOVES_H)
        with open(os.path.join(root, "src", "move_descriptions.c"), "w") as f:
            f.write(MOVE_DESCS_C)
        # Additional files needed for other data classes
        os.makedirs(
            os.path.join(root, "src", "data", "pokemon", "species_info"), exist_ok=True
        )
        with open(
            os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w"
        ) as f:
            f.write("\n")
        with open(
            os.path.join(
                root, "src", "data", "pokemon", "species_info", "pory_species.h"
            ),
            "w",
        ) as f:
            f.write("\n")
        with open(
            os.path.join(root, "src", "data", "pokemon", "evolution.h"), "w"
        ) as f:
            f.write(
                "const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] = {};\n"
            )
        with open(os.path.join(root, "src", "field_specials.c"), "w") as f:
            f.write(FIELD_SPECIALS_C)
        with open(os.path.join(root, "src", "battle_setup.c"), "w") as f:
            f.write(BATTLE_SETUP_C)
        with open(os.path.join(root, "src", "data", "trainers.h"), "w") as f:
            f.write(TRAINERS_H)
        with open(os.path.join(root, "src", "data", "trainers.json"), "w") as f:
            json.dump({
                "TRAINER_TEST": {
                    "trainerClass": "TRAINER_CLASS_TEST",
                    "trainerName": '_("NAME")',
                    "items": "{}",
                    "doubleBattle": "FALSE",
                    "aiFlags": "AI_SCRIPT_TEST",
                    "party": "TRAINER_PARTY_TEST",
                }
            }, f)
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

    def test_item_writeback(self):
        mgr = self.load_manager()
        mgr.data["pokemon_items"].data["ITEM_TEST"]["name"] = '_("CHANGED")'
        mgr.parse_to_c_code()
        with open(
            os.path.join(
                self.project_info["dir"], "src", "data", "graphics", "items.h"
            )
        ) as f:
            content = f.read()
        self.assertIn("CHANGED", content)
        with open(
            os.path.join(self.project_info["dir"], "src", "data", "items.json")
        ) as f:
            raw = json.load(f)
        # items.json may be flat {"ITEM_TEST": {...}} or wrapped {"items": [{itemId, ...}]}
        if isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], list):
            items = {e["itemId"]: {k: v for k, v in e.items() if k != "itemId"}
                     for e in raw["items"] if "itemId" in e}
        else:
            items = raw
        self.assertEqual(items["ITEM_TEST"]["name"], '_("CHANGED")')

    def test_move_writeback(self):
        mgr = self.load_manager()
        mgr.set_move_data("MOVE_TEST", "power", 42)
        mgr.data["pokemon_moves"].data["move_descriptions"]["MOVE_TEST"] = "new"
        mgr.parse_to_c_code()
        with open(
            os.path.join(self.project_info["dir"], "src", "data", "battle_moves.h")
        ) as f:
            content = f.read()
        self.assertIn(".power = 42", content)
        # move_descriptions.c is a canonical file; descriptions are only persisted in JSON
        with open(
            os.path.join(self.project_info["dir"], "src", "data", "moves.json")
        ) as f:
            moves = json.load(f)
        self.assertEqual(moves["moves"]["MOVE_TEST"]["power"], 42)
        self.assertEqual(moves["move_descriptions"]["MOVE_TEST"], "new")

    def test_species_moves_table_edit_persists(self):
        """Verify that set_species_moves persists correctly through source_data."""
        mgr = self.load_manager()
        pmoves = mgr.data["pokemon_moves"].data
        pmoves["species_moves"] = {
            "SPECIES_TEST": [
                {"move": "MOVE_TEST", "method": "level", "value": "1"},
                {"move": "MOVE_CUT", "method": "tm", "value": "TM01"},
            ],
        }
        # Directly call set_species_moves (the save path the new learnset UI uses)
        new_moves = [
            {"move": "MOVE_TEST", "method": "tm", "value": "TM02"},
        ]
        mgr.set_species_moves("SPECIES_TEST", new_moves)
        moves = mgr.get_species_moves("SPECIES_TEST")
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["method"], "tm")
        self.assertEqual(moves[0]["value"], "TM02")

    def test_pokedex_writeback(self):
        mgr = self.load_manager()
        mgr.data["pokedex"].data["national_dex"][0]["dex_constant"] = "NATIONAL_DEX_NEW"
        mgr.parse_to_c_code()
        with open(
            os.path.join(self.project_info["dir"], "include", "constants", "pokedex.h")
        ) as f:
            content = f.read()
        self.assertIn("NATIONAL_DEX_NEW", content)
        with open(
            os.path.join(self.project_info["dir"], "src", "data", "pokedex.json")
        ) as f:
            dex = json.load(f)
        self.assertEqual(dex["national_dex"][0]["dex_constant"], "NATIONAL_DEX_NEW")

    def test_starter_writeback(self):
        mgr = self.load_manager()
        starter = mgr.data["pokemon_starters"].data[0]
        starter["species"] = "SPECIES_NONE"
        starter["level"] = 7
        starter["item"] = "ITEM_TEST"
        starter["ability_num"] = 0
        mgr.parse_to_c_code()
        with open(
            os.path.join(self.project_info["dir"], "src", "field_specials.c")
        ) as f:
            content = f.read()
        self.assertIn("SPECIES_NONE", content)
        with open(os.path.join(self.project_info["dir"], "src", "battle_setup.c")) as f:
            bcontent = f.read()
        self.assertIn("ScriptGiveMon(starterMon, 7, ITEM_TEST", bcontent)
        self.assertIn("abilityNum = 0;", bcontent)

    def test_species_writeback(self):
        mgr = self.load_manager()
        mgr.data["species_data"].data = {
            "SPECIES_TEST": {
                "species_info": {
                    "baseHP": 12,
                    "abilities": ["ABILITY_NONE", "ABILITY_NONE", "ABILITY_NONE"],
                },
                "forms": {},
            }
        }
        mgr.data["pokemon_evolutions"].data = {
            "SPECIES_TEST": [
                {"method": "EVO_LEVEL", "param": 5, "targetSpecies": "SPECIES_OTHER"}
            ]
        }
        mgr.data["species_data"].parse_to_c_code()
        mgr.data["pokemon_evolutions"].parse_to_c_code()
        evo_path = os.path.join(
            self.project_info["dir"], "src", "data", "pokemon", "evolution.h"
        )
        with open(evo_path) as f:
            econtent = f.read()
        self.assertIn("SPECIES_TEST", econtent)
        # pory_species.h was removed; species data is persisted to species.json
        spec_json_path = os.path.join(self.project_info["dir"], "src", "data", "species.json")
        with open(spec_json_path) as f:
            species_data = json.load(f)
        self.assertIn("SPECIES_TEST", species_data)
        self.assertEqual(species_data["SPECIES_TEST"]["species_info"]["baseHP"], 12)

    def test_trainer_writeback(self):
        mgr = self.load_manager()
        mgr.data["pokemon_trainers"].data["TRAINER_TEST"]["aiFlags"] = "AI_NEW"
        mgr.parse_to_c_code()
        with open(
            os.path.join(self.project_info["dir"], "src", "data", "trainers.h")
        ) as f:
            content = f.read()
        self.assertIn("AI_NEW", content)
        with open(
            os.path.join(self.project_info["dir"], "src", "data", "trainers.json")
        ) as f:
            trainers = json.load(f)
        self.assertEqual(trainers["TRAINER_TEST"]["aiFlags"], "AI_NEW")


if __name__ == "__main__":
    unittest.main()
