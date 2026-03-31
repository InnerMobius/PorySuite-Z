import os
import tempfile
import unittest
import types
import sys
import importlib.util
import io
import contextlib
import unittest.mock
import json

# Ensure the repo root is on sys.path so plugin modules can be imported when
# executed with plain `pytest`
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from header_stubs import write_move_headers, write_species_headers
# Stub PyQt6 modules
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")

def pyqtSignal(*args, **kwargs):
    return None

qt_core.pyqtSignal = pyqtSignal
class QImage:
    pass
class QPixmap:
    pass
qt_gui.QImage = QImage
qt_gui.QPixmap = QPixmap
qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)

# Load the extractor module without running the plugin package __init__
pkg_name = "plugins.pokefirered"
sys.modules.setdefault("plugins", types.ModuleType("plugins"))
pkg_module = types.ModuleType(pkg_name)
pkg_module.__path__ = [os.path.join(os.path.dirname(__file__), "..", "plugins", "pokefirered")]
sys.modules[pkg_name] = pkg_module
spec = importlib.util.spec_from_file_location(
    f"{pkg_name}.pokemon_data_extractor",
    os.path.join(os.path.dirname(__file__), "..", "plugins", "pokefirered", "pokemon_data_extractor.py"),
)
pdextractor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pdextractor
spec.loader.exec_module(pdextractor)

SpeciesDataExtractor = pdextractor.SpeciesDataExtractor
StartersDataExtractor = pdextractor.StartersDataExtractor
PokedexDataExtractor = pdextractor.PokedexDataExtractor
ItemsDataExtractor = pdextractor.ItemsDataExtractor
PokemonConstantsExtractor = pdextractor.PokemonConstantsExtractor

# Load the pokemon_data module to access data classes like PokemonItems
spec_data = importlib.util.spec_from_file_location(
    f"{pkg_name}.pokemon_data",
    os.path.join(
        os.path.dirname(__file__), "..", "plugins", "pokefirered", "pokemon_data.py"
    ),
)
pokemon_data_mod = importlib.util.module_from_spec(spec_data)
sys.modules[spec_data.name] = pokemon_data_mod
spec_data.loader.exec_module(pokemon_data_mod)
PokemonItemsClass = pokemon_data_mod.PokemonItems


class ExtractorReturnTypeTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_info = {
            "dir": self.tempdir.name,
            "project_name": "proj",
        }
        os.makedirs(os.path.join(self.tempdir.name, "src", "data"), exist_ok=True)
        os.makedirs(os.path.join(self.tempdir.name, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.tempdir.name, "include"), exist_ok=True)
        with open(os.path.join(self.tempdir.name, "src", "field_specials.c"), "w") as f:
            f.write("const u16 sStarterSpecies[] = { SPECIES_TEST };")
        with open(os.path.join(self.tempdir.name, "src", "battle_setup.c"), "w") as f:
            f.write(
                "static void CB2_GiveStarter(void)\n{\n    u16 starterMon;\n    switch(gSpecialVar_Result)\n    {\n    case 0:\n        ScriptGiveMon(starterMon, 5, ITEM_TEST, 0, 0, 0);\n        abilityNum = 1;\n        SetMonData(&gPlayerParty[0], MON_DATA_ABILITY_NUM, &abilityNum);\n        break;\n    }\n}\n"
            )
        write_species_headers(self.tempdir.name)
        write_move_headers(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_species_extractor_returns_dict(self):
        ext = SpeciesDataExtractor(self.project_info, "species.json")
        result = ext.extract_data()
        self.assertIsInstance(result, dict)

    def test_starters_extractor_returns_list(self):
        ext = StartersDataExtractor(self.project_info, "starters.json")
        result = ext.extract_data()
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["species"], "SPECIES_TEST")
        self.assertEqual(result[0]["level"], 5)
        self.assertEqual(result[0]["item"], "ITEM_TEST")
        self.assertEqual(result[0]["ability_num"], 1)
        self.assertEqual(result[0]["custom_move"], "MOVE_NONE")

    def test_starters_extractor_missing_ability_and_move(self):
        path = os.path.join(self.tempdir.name, "src", "battle_setup.c")
        with open(path, "w") as f:
            f.write(
                "static void CB2_GiveStarter(void)\n{\n    u16 starterMon;\n    switch(gSpecialVar_Result)\n    {\n    case 0:\n        ScriptGiveMon(starterMon, 5, ITEM_TEST, 0, 0, 0);\n        break;\n    }\n}\n"
            )
        ext = StartersDataExtractor(self.project_info, "starters.json")
        result = ext.extract_data()
        self.assertEqual(result[0]["ability_num"], -1)
        self.assertEqual(result[0]["custom_move"], "MOVE_NONE")

    def test_starters_extractor_parses_custom_move(self):
        path = os.path.join(self.tempdir.name, "src", "battle_setup.c")
        with open(path, "w") as f:
            f.write(
                "static void CB2_GiveStarter(void)\n{\n    u16 starterMon;\n    switch(gSpecialVar_Result)\n    {\n    case 0 : // comment\n        ScriptGiveMon( starterMon , 10 , ITEM_TEST , 0,0,0 ); // trailing\n        GiveMoveToMon(&gPlayerParty[0], MOVE_TEST );\n        abilityNum = 2 ; // comment\n        SetMonData(&gPlayerParty[0], MON_DATA_ABILITY_NUM, &abilityNum );\n        break ; // comment\n    }\n}\n"
            )
        ext = StartersDataExtractor(self.project_info, "starters.json")
        result = ext.extract_data()
        self.assertEqual(result[0]["level"], 10)
        self.assertEqual(result[0]["item"], "ITEM_TEST")
        self.assertEqual(result[0]["ability_num"], 2)
        self.assertEqual(result[0]["custom_move"], "MOVE_TEST")

    def test_pokedex_extractor_returns_dict(self):
        ext = PokedexDataExtractor(self.project_info, "pokedex.json")
        result = ext.extract_data()
        self.assertIsInstance(result, dict)
        self.assertIn("national_dex", result)
        self.assertIsInstance(result["national_dex"], list)


class ExtractorErrorMessageTest(unittest.TestCase):
    """Ensure extractors warn when no data is written."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_info = {"dir": self.tempdir.name, "project_name": "proj"}
        os.makedirs(os.path.join(self.tempdir.name, "src", "data"), exist_ok=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_species_extractor_aborts_when_empty(self):
        ext = SpeciesDataExtractor(self.project_info, "species.json")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = ext.extract_data()
        self.assertEqual(result, {})
        self.assertIn("Aborting species load.", captured.getvalue())
        self.assertFalse(
            os.path.isfile(os.path.join(self.tempdir.name, "src", "data", "species.json"))
        )

    def test_starters_extractor_aborts_when_empty(self):
        ext = StartersDataExtractor(self.project_info, "starters.json")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = ext.extract_data()
        self.assertEqual(result, [])
        self.assertIn("Aborting starters load.", captured.getvalue())
        self.assertFalse(
            os.path.isfile(os.path.join(self.tempdir.name, "src", "data", "starters.json"))
        )

    def test_species_count_mismatch_writes_json(self):
        root = self.project_info["dir"]
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)

        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(
                """[SPECIES_NONE] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},\n[SPECIES_TEST] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},"""
            )

        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n    [SPECIES_NONE] = _(\"UNKNOWN\"),\n    [SPECIES_TEST] = _(\"TEST\"),\n};"
            )

        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_NONE 0\n#define SPECIES_TEST 1\n#define SPECIES_EGG 2\n#define NUM_SPECIES SPECIES_EGG\n"
            )

        # Patch pokedex extractor to avoid needing dex files
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(PokedexDataExtractor, "extract_data", return_value={"national_dex": []}):
                ext = SpeciesDataExtractor(self.project_info, "species.json")
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    result = ext.extract_data()

        self.assertEqual(len(result), 2)
        self.assertTrue(os.path.isfile(os.path.join(root, "src", "data", "species.json")))
        self.assertIn("expected 3 species", captured.getvalue())

    def test_moves_extractor_writes_when_empty(self):
        root = self.project_info["dir"]
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        with open(os.path.join(root, "src", "data", "battle_moves.h"), "w") as f:
            f.write("\n")
        with open(os.path.join(root, "src", "move_descriptions.c"), "w") as f:
            f.write("\n")

        ext = pdextractor.MovesDataExtractor(self.project_info, "moves.json")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            data = ext.extract_data()

        self.assertIn("Warning: expected at least 1 move", captured.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(root, "src", "data", "moves.json")))
        self.assertEqual(data["moves"], {})


class GenderRatioParsingTest(unittest.TestCase):
    """Verify parsing of gender ratio expressions."""

    def setUp(self):
        self.ext = SpeciesDataExtractor({"dir": ".", "project_name": "proj"}, "species.json")

    def test_percent_female_macro(self):
        key, value = self.ext.parse_value_by_key("genderRatio", "PERCENT_FEMALE(12.5)")
        self.assertEqual(value, int(round(12.5 * 255 / 100)))

    def test_min_expression(self):
        key, value = self.ext.parse_value_by_key("genderRatio", "min (254, ((12.5 * 255) / 100))")
        self.assertEqual(value, int(round(12.5 * 255 / 100)))

    def test_malformed_expression(self):
        key, value = self.ext.parse_value_by_key("genderRatio", "PERCENT_FEMALE()")
        self.assertIsNone(value)


class GenderRatioHeaderCacheTest(unittest.TestCase):
    """_parse_gender_ratio should only read the header once."""

    def setUp(self):
        self.ext = SpeciesDataExtractor({"dir": ".", "project_name": "proj"}, "species.json")

    def test_header_cached(self):
        lines = [
            "[SPECIES_TEST] = {\n",
            "    .genderRatio = PERCENT_FEMALE(50),\n",
            "},\n",
        ]
        with unittest.mock.patch.object(pdextractor, "_read_header", return_value=lines) as mock_read:
            ratio1 = self.ext._parse_gender_ratio("SPECIES_TEST")
            ratio2 = self.ext._parse_gender_ratio("SPECIES_TEST")
            self.assertEqual(ratio1, int(round(50 * 255 / 100)))
            self.assertEqual(ratio2, ratio1)
            self.assertEqual(mock_read.call_count, 1)


class GenderRatioExtractorTest(unittest.TestCase):
    """Ensure gender ratios are extracted as integers."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)

        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(
                """[SPECIES_TEST] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE}, .genderRatio = PERCENT_FEMALE(12.5),\n},"""
            )

        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n    [SPECIES_TEST] = _(\"TEST\"),\n};"
            )

        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write("#define SPECIES_TEST 0\n#define SPECIES_EGG 1\n#define NUM_SPECIES SPECIES_EGG\n")

        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_gender_ratio_value(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(PokedexDataExtractor, "extract_data", return_value={"national_dex": []}):
                ext = SpeciesDataExtractor(self.project_info, "species.json")
                data = ext.extract_data()
        ratio = data["SPECIES_TEST"]["species_info"].get("genderRatio")
        self.assertEqual(ratio, int(round(12.5 * 255 / 100)))


class GenderRatioRewriteTest(unittest.TestCase):
    """Ensure outdated gender ratios are corrected from the header."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)

        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(
                """[SPECIES_TEST] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE}, .genderRatio = PERCENT_FEMALE(50),\n},"""
            )

        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n    [SPECIES_TEST] = _(\"TEST\"),\n};"
            )

        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write("#define SPECIES_TEST 0\n#define SPECIES_EGG 1\n#define NUM_SPECIES SPECIES_EGG\n")

        # cached JSON with incorrect genderRatio
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        bad_data = {
            "SPECIES_TEST": {
                "species_info": {
                    "genderRatio": 0,
                    "baseHP": 1,
                    "types": ["TYPE_NORMAL", "TYPE_NORMAL"],
                    "abilities": ["ABILITY_NONE", "ABILITY_NONE", "ABILITY_NONE"],
                }
            }
        }
        with open(os.path.join(root, "src", "data", "species.json"), "w", encoding="utf-8") as f:
            json.dump(bad_data, f)

        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_ratio_repaired_and_saved(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(PokedexDataExtractor, "extract_data", return_value={"national_dex": []}):
                ext = SpeciesDataExtractor(self.project_info, "species.json")
                data = ext.extract_data()
        expected = int(round(50 * 255 / 100))
        self.assertEqual(data["SPECIES_TEST"]["species_info"]["genderRatio"], expected)
        path = os.path.join(self.project_info["dir"], "src", "data", "species.json")
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["SPECIES_TEST"]["species_info"]["genderRatio"], expected)


class TypeSyncTest(unittest.TestCase):
    """Ensure types in ``species.json`` match ``species_info.h``."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        bad_data = {
            "SPECIES_TEST": {
                "species_info": {
                    "baseHP": 99,
                    "types": ["TYPE_WATER", "TYPE_NONE"],
                    "abilities": ["ABILITY_NONE", "ABILITY_NONE", "ABILITY_NONE"],
                }
            }
        }
        with open(os.path.join(root, "src", "data", "species.json"), "w", encoding="utf-8") as f:
            json.dump(bad_data, f)

        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_types_repaired(self):
        header = {
            "SPECIES_TEST": {
                "species_info": {
                    "baseHP": 1,
                    "types": ["TYPE_FIRE", "TYPE_FLYING"],
                    "abilities": ["ABILITY_NONE", "ABILITY_NONE", "ABILITY_NONE"],
                }
            }
        }
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(PokedexDataExtractor, "extract_data", return_value={"national_dex": []}), \
                unittest.mock.patch.object(SpeciesDataExtractor, "_parse_header_data", return_value=header):
                ext = SpeciesDataExtractor(self.project_info, "species.json")
                data = ext.extract_data()
        types = data["SPECIES_TEST"]["species_info"].get("types")
        self.assertEqual(types, ["TYPE_FIRE", "TYPE_FLYING"])
        # baseHP remains from cached JSON because rebuild was not triggered
        self.assertEqual(data["SPECIES_TEST"]["species_info"].get("baseHP"), 99)
        path = os.path.join(self.project_info["dir"], "src", "data", "species.json")
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["SPECIES_TEST"]["species_info"]["types"], ["TYPE_FIRE", "TYPE_FLYING"])
        self.assertEqual(saved["SPECIES_TEST"]["species_info"].get("baseHP"), 99)

    def test_mismatch_triggers_rebuild(self):
        header = {
            "SPECIES_TEST": {
                "species_info": {
                    "baseHP": 1,
                    "types": ["TYPE_FIRE", "TYPE_FLYING"],
                    "abilities": ["ABILITY_NONE", "ABILITY_NONE", "ABILITY_NONE"],
                }
            }
        }
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(PokedexDataExtractor, "extract_data", return_value={"national_dex": []}), \
                unittest.mock.patch.object(SpeciesDataExtractor, "_parse_header_data", return_value=header):
                ext = SpeciesDataExtractor(self.project_info, "species.json")
                ext.rebuild_on_type_mismatch = True
                data = ext.extract_data()
        self.assertEqual(data["SPECIES_TEST"]["species_info"].get("types"), ["TYPE_FIRE", "TYPE_FLYING"])
        # baseHP restored to header value due to rebuild
        self.assertEqual(data["SPECIES_TEST"]["species_info"].get("baseHP"), 1)
        path = os.path.join(self.project_info["dir"], "src", "data", "species.json")
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["SPECIES_TEST"]["species_info"].get("baseHP"), 1)


class EvolutionsExtractorTest(unittest.TestCase):
    """Ensure evolution.h is parsed into species.json."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)

        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(
                """[SPECIES_TEST] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},\n[SPECIES_OTHER] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},"""
            )

        with open(os.path.join(root, "src", "data", "pokemon", "evolution.h"), "w") as f:
            f.write(
                """const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] = {\n    [SPECIES_TEST] = {{EVO_LEVEL, 5, SPECIES_OTHER}},\n};"""
            )

        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n    [SPECIES_TEST] = _(\"TEST\"),\n    [SPECIES_OTHER] = _(\"OTHER\"),\n};"
            )

        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_TEST 0\n#define SPECIES_OTHER 1\n#define SPECIES_EGG 2\n#define NUM_SPECIES SPECIES_EGG\n"
            )

        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_evolutions_parsed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(PokedexDataExtractor, "extract_data", return_value={"national_dex": []}):
                ext = SpeciesDataExtractor(self.project_info, "species.json")
                data = ext.extract_data()
        evos = data["SPECIES_TEST"]["species_info"].get("evolutions")
        self.assertEqual(evos, [{"method": "EVO_LEVEL", "param": 5, "targetSpecies": "SPECIES_OTHER"}])
        self.assertEqual(data["SPECIES_OTHER"]["species_info"].get("evolutions"), [])


class EvolutionsJsonWriteTest(unittest.TestCase):
    """Ensure evolution data is written to JSON when parsing."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)

        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(
                """[SPECIES_TEST] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},\n[SPECIES_OTHER] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},"""
            )

        with open(os.path.join(root, "src", "data", "pokemon", "evolution.h"), "w") as f:
            f.write(
                """const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] = {\n    [SPECIES_TEST] = {{EVO_LEVEL, 5, SPECIES_OTHER}},\n};"""
            )

        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n    [SPECIES_TEST] = _(\"TEST\"),\n    [SPECIES_OTHER] = _(\"OTHER\"),\n};"
            )

        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_TEST 0\n#define SPECIES_OTHER 1\n#define SPECIES_EGG 2\n#define NUM_SPECIES SPECIES_EGG\n"
            )

        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_json_contains_evolutions(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(PokedexDataExtractor, "extract_data", return_value={"national_dex": []}):
                ext = SpeciesDataExtractor(self.project_info, "species.json")
                data = ext.extract_data()
        root = self.project_info["dir"]
        evo_path = os.path.join(root, "src", "data", "evolutions.json")
        species_path = os.path.join(root, "src", "data", "species.json")
        if os.path.isfile(evo_path):
            with open(evo_path, encoding="utf-8") as f:
                saved = json.load(f)
            evos = saved.get("SPECIES_TEST")
        else:
            with open(species_path, encoding="utf-8") as f:
                saved = json.load(f)
            evos = saved["SPECIES_TEST"]["species_info"].get("evolutions")
        self.assertEqual(evos, [{"method": "EVO_LEVEL", "param": 5, "targetSpecies": "SPECIES_OTHER"}])


class ItemsExtractorRegenerationTest(unittest.TestCase):
    """Ensure ``items.json`` is rebuilt from the header."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        header_path = os.path.join(root, "src", "data", "items.h")
        with open(header_path, "w") as f:
            f.write(
                "const struct Item gItems[] = {\n"
                "[ITEM_TEST] = {\n"
                "    .name = _(\"TEST\"),\n"
                "    .price = 100,\n"
                "    .description = COMPOUND_STRING(\"desc\"),\n"
                "},\n"
                "};\n"
            )
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_header_rebuilds_json(self):
        ext = ItemsDataExtractor(self.project_info, "items.json")
        with contextlib.redirect_stdout(io.StringIO()):
            data = ext.extract_data()
        self.assertEqual(
            data.get("ITEM_TEST"),
            {
                "name": "_(\"TEST\")",
                "price": "100",
                "description": "COMPOUND_STRING(\"desc\")",
            },
        )
        self.assertGreater(len(data), 0)
        path = os.path.join(self.project_info["dir"], "src", "data", "items.json")
        self.assertTrue(os.path.isfile(path))


class ItemsExtractorJsonPreferredTest(unittest.TestCase):
    """Valid ``items.json`` should load without reading the header."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        json_path = os.path.join(root, "src", "data", "items.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"ITEM_TEST": {"name": "TEST"}}, f)
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_valid_json_skips_header(self):
        ext = ItemsDataExtractor(self.project_info, "items.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            data = ext.extract_data()
        self.assertEqual(data, {"ITEM_TEST": {"name": "TEST"}})
        output = buf.getvalue().lower()
        self.assertIn("loaded 1 items", output)
        self.assertNotIn("items.h", output)


class ItemsExtractorInvalidJsonFallbackTest(unittest.TestCase):
    """Invalid JSON should fall back to parsing ``items.h``."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        header_path = os.path.join(root, "src", "data", "items.h")
        with open(header_path, "w") as f:
            f.write(
                "const struct Item gItems[] = {\n"
                "[ITEM_TEST] = {\n"
                "    .name = _(\"TEST\"),\n"
                "    .price = 100,\n"
                "},\n"
                "};\n"
            )
        json_path = os.path.join(root, "src", "data", "items.json")
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w") as f:
            f.write("not json")
        self.json_path = json_path
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_invalid_json_uses_header(self):
        ext = ItemsDataExtractor(self.project_info, "items.json")
        with contextlib.redirect_stdout(io.StringIO()):
            data = ext.extract_data()
        self.assertIn("ITEM_TEST", data)
        with open(self.json_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved, data)


class ItemsExtractorMalformedHeaderTest(unittest.TestCase):
    """Ensure a malformed items header logs an error and returns ``None``."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        with open(
            os.path.join(root, "src", "data", "items.h"), "w"
        ) as f:
            f.write("not a valid header\n")
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_malformed_header_logs_error(self):
        ext = ItemsDataExtractor(self.project_info, "items.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            data = ext.extract_data()
        self.assertIsNone(data)
        self.assertIn("no item entries found", buf.getvalue().lower())
        path = os.path.join(self.project_info["dir"], "src", "data", "items.json")
        self.assertFalse(os.path.isfile(path))


class ItemsExtractorMissingHeaderTest(unittest.TestCase):
    """Missing ``items.h`` should yield ``None`` with a warning."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_missing_header_returns_none(self):
        ext = ItemsDataExtractor(self.project_info, "items.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            data = ext.extract_data()
        self.assertIsNone(data)
        self.assertIn("no item data loaded", buf.getvalue().lower())
        path = os.path.join(self.project_info["dir"], "src", "data", "items.json")
        self.assertFalse(os.path.isfile(path))

    def test_items_txt_with_c_code_missing_header(self):
        txt_path = os.path.join(
            self.project_info["dir"], "src", "data", "items.json.txt"
        )
        with open(txt_path, "w") as f:
            f.write("const struct Item gItems[] = {};")
        ext = ItemsDataExtractor(self.project_info, "items.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            data = ext.extract_data()
        self.assertIsNone(data)
        self.assertIn("no item data loaded", buf.getvalue().lower())
        path = os.path.join(self.project_info["dir"], "src", "data", "items.json")
        self.assertFalse(os.path.isfile(path))

class PokemonItemsEmptyResultHandledTest(unittest.TestCase):
    """Regression test: ``PokemonItems`` handles ``None`` extractor result."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        # Malformed header to force ``None`` from extractor
        with open(
            os.path.join(root, "src", "data", "items.h"), "w"
        ) as f:
            f.write("not a valid header\n")
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_pokemon_items_init_does_not_crash(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            items = PokemonItemsClass(self.project_info)
        self.assertEqual(items.data, {})
        # Either a missing or malformed header should emit a clear message
        self.assertIn("no item", buf.getvalue().lower())


class AbilitiesHeaderPriorityTest(unittest.TestCase):
    """_find_abilities_header should prefer include/constants/abilities.h."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        # A header that would be discovered first during os.walk
        with open(os.path.join(root, "other.h"), "w") as f:
            f.write("#define ABILITY_FAKE 0\n")
        with open(
            os.path.join(root, "include", "constants", "abilities.h"), "w"
        ) as f:
            f.write("#define ABILITY_NONE 0\n#define ABILITY_TEST 1\n")
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_preferred_header_used(self):
        header = pdextractor._find_abilities_header(self.project_info["dir"])
        expected = os.path.join(
            self.project_info["dir"], "include", "constants", "abilities.h"
        )
        self.assertEqual(os.path.abspath(header), os.path.abspath(expected))

    def test_extractor_loads_all_definitions(self):
        ext = pdextractor.AbilitiesDataExtractor(self.project_info, "abilities.json")
        with contextlib.redirect_stdout(io.StringIO()):
            data = ext.extract_data()
        self.assertEqual(set(data.keys()), {"ABILITY_NONE", "ABILITY_TEST"})
        path = os.path.join(self.project_info["dir"], "src", "data", "abilities.json")
        self.assertTrue(os.path.isfile(path))


class PokemonConstantsExtractorTest(unittest.TestCase):
    """Ensure constants.json is rebuilt from the pokemon.h header."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        with open(os.path.join(root, "include", "constants", "pokemon.h"), "w") as f:
            f.write("#define TYPE_TEST 1\n#define EVO_TEST 2\n")
        open(os.path.join(root, "src", "data", "constants.json"), "w").close()
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_evolution_constants_parsed(self):
        ext = PokemonConstantsExtractor(self.project_info, "constants.json")
        with contextlib.redirect_stdout(io.StringIO()):
            data = ext.extract_data()
        self.assertIn("evolution_types", data)
        self.assertIn("EVO_TEST", data["evolution_types"])


class SpeciesGraphicsExtractorTest(unittest.TestCase):
    """Ensure graphics header parsing generates species_graphics.json."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data"), exist_ok=True)
        os.makedirs(os.path.join(root, "graphics", "pokemon", "bulbasaur"), exist_ok=True)
        graphics_header = os.path.join(root, "src", "data", "graphics")
        os.makedirs(graphics_header, exist_ok=True)
        with open(os.path.join(graphics_header, "pokemon.h"), "w") as f:
            f.write(
                'const u32 gMonFrontPic_Bulbasaur[] = INCBIN_U32("graphics/pokemon/bulbasaur/front.4bpp.lz");'
            )
        write_species_headers(root)
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_graphics_json_created(self):
        ext = pdextractor.SpeciesGraphicsDataExtractor(
            self.project_info, "species_graphics.json"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ext.extract_data()
        self.assertIn("gMonFrontPic_Bulbasaur", data)
        path = os.path.join(self.project_info["dir"], "src", "data", "species_graphics.json")
        self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
