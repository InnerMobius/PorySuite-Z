"""Behavioral tests for the Pokédex search sort array regeneration.

The in-game dex search sorts with three pre-sorted arrays in
``src/data/pokemon/pokedex_orders.h``. They are derived from data the
editor changes (display names, dex heights/weights) but were never
regenerated on save, so they silently drifted stale. These tests prove
the save paths now re-derive them: edit a dex height and a display name
through the app's data layer in a throwaway project, save, and the
arrays come out correctly sorted with every dex entry exactly once.

A separate smoke test runs the regenerator against a copy of the
pristine vanilla reference tree (skipped when that tree is absent).
"""

import os
import re
import shutil
import sys
import tempfile
import types
import importlib.util
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Stub PyQt6 modules so core modules import headlessly.
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

_VANILLA_ROOT = r"C:\GBA\READONLYREFERENCE\pokefirered"

ARRAYS = ("gPokedexOrder_Alphabetical", "gPokedexOrder_Weight",
          "gPokedexOrder_Height")

SPECIES_INFO = """\
[SPECIES_NONE] = {
    .baseHP = 1,
    .types = {TYPE_NORMAL, TYPE_NORMAL},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_BULBASAUR] = {
    .baseHP = 45,
    .types = {TYPE_GRASS, TYPE_POISON},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_IVYSAUR] = {
    .baseHP = 60,
    .types = {TYPE_GRASS, TYPE_POISON},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_VENUSAUR] = {
    .baseHP = 80,
    .types = {TYPE_GRASS, TYPE_POISON},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
[SPECIES_CHARMANDER] = {
    .baseHP = 39,
    .types = {TYPE_FIRE, TYPE_FIRE},
    .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},
},
"""

SPECIES_NAMES_H = """\
const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {
    [SPECIES_NONE] = _("??????????"),
    [SPECIES_BULBASAUR] = _("Bulbasaur"),
    [SPECIES_IVYSAUR] = _("Ivysaur"),
    [SPECIES_VENUSAUR] = _("Venusaur"),
    [SPECIES_CHARMANDER] = _("Charmander"),
};
"""

POKEDEX_H = """\
// National Pokedex order
enum {
    NATIONAL_DEX_NONE,
    NATIONAL_DEX_BULBASAUR,
    NATIONAL_DEX_IVYSAUR,
    NATIONAL_DEX_VENUSAUR,
    NATIONAL_DEX_CHARMANDER,
};
"""


def _dex_entry(const, category, height, weight):
    return (
        f"[{const}] = {{\n"
        f"    .categoryName = _(\"{category}\"),\n"
        f"    .height = {height},\n"
        f"    .weight = {weight},\n"
        f"    .description = gTestPokedexText,\n"
        f"    .unusedDescription = gTestPokedexTextUnused,\n"
        f"    .pokemonScale = 256,\n"
        f"    .pokemonOffset = 0,\n"
        f"    .trainerScale = 256,\n"
        f"    .trainerOffset = 0,\n"
        f"}},\n"
    )


POKEDEX_ENTRIES_H = (
    _dex_entry("NATIONAL_DEX_NONE", "UNKNOWN", 0, 0)
    + _dex_entry("NATIONAL_DEX_BULBASAUR", "SEED", 7, 69)
    + _dex_entry("NATIONAL_DEX_IVYSAUR", "SEED", 10, 130)
    + _dex_entry("NATIONAL_DEX_VENUSAUR", "SEED", 20, 1000)
    + _dex_entry("NATIONAL_DEX_CHARMANDER", "LIZARD", 6, 85)
)

# Deliberately STALE: every array is in national-dex order, which is
# wrong for all three sorts, so a regeneration provably fixes them.
POKEDEX_ORDERS_H = """\
const u16 gPokedexOrder_Alphabetical[] =
{
    NATIONAL_DEX_BULBASAUR,
    NATIONAL_DEX_IVYSAUR,
    NATIONAL_DEX_VENUSAUR,
    NATIONAL_DEX_CHARMANDER,
};

const u16 gPokedexOrder_Weight[] =
{
    NATIONAL_DEX_BULBASAUR,
    NATIONAL_DEX_IVYSAUR,
    NATIONAL_DEX_VENUSAUR,
    NATIONAL_DEX_CHARMANDER,
};

const u16 gPokedexOrder_Height[] =
{
    NATIONAL_DEX_BULBASAUR,
    NATIONAL_DEX_IVYSAUR,
    NATIONAL_DEX_VENUSAUR,
    NATIONAL_DEX_CHARMANDER,
};

const u16 gPokedexOrder_Type[] =
{
    SPECIES_BULBASAUR,
    SPECIES_CHARMANDER,
    SPECIES_IVYSAUR,
    SPECIES_VENUSAUR,
};
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

STARTERS_C = "const u16 sStarterSpecies[] = { SPECIES_BULBASAUR };"
BATTLE_SETUP_C = (
    "static void CB2_GiveStarter(void){u16 starterMon;"
    "switch(gSpecialVar_Result){case 0: "
    "ScriptGiveMon(starterMon,5,ITEM_TEST,0,0,0);abilityNum=1;"
    "SetMonData(&gPlayerParty[0],MON_DATA_ABILITY_NUM,&abilityNum);break;}}"
)
BATTLE_MOVES_H = "[MOVE_TEST] = {.power = 0,};"
MOVE_DESCS_C = '[MOVE_TEST] = _(\"desc\");'


def _load_pokedex_orders_module():
    pkg_name = "core"
    if pkg_name not in sys.modules:
        pkg_module = types.ModuleType(pkg_name)
        pkg_module.__path__ = [os.path.join(ROOT_DIR, "core")]
        sys.modules[pkg_name] = pkg_module
    spec = importlib.util.spec_from_file_location(
        "core.pokedex_orders",
        os.path.join(ROOT_DIR, "core", "pokedex_orders.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract_array(text, name):
    decl = re.search(re.escape(name) + r"\s*\[\s*\]\s*=", text)
    if not decl:
        return None
    open_idx = text.find("{", decl.end())
    close_idx = text.find("};", open_idx)
    body = re.sub(r"//[^\n]*", "", text[open_idx + 1:close_idx])
    return re.findall(r"\bNATIONAL_DEX_[A-Za-z0-9_]+\b", body)


class PokedexOrdersProjectTest(unittest.TestCase):
    """Edits through the app's data layer regenerate the sort arrays."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)

        def put(rel_parts, content):
            path = os.path.join(root, *rel_parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)

        put(("src", "data", "pokemon", "species_info.h"), SPECIES_INFO)
        put(("src", "data", "text", "species_names.h"), SPECIES_NAMES_H)
        put(("include", "constants", "species.h"),
            "#define SPECIES_NONE 0\n"
            "#define SPECIES_BULBASAUR 1\n"
            "#define SPECIES_IVYSAUR 2\n"
            "#define SPECIES_VENUSAUR 3\n"
            "#define SPECIES_CHARMANDER 4\n"
            "#define SPECIES_EGG 5\n"
            "#define NUM_SPECIES SPECIES_EGG\n")
        put(("include", "constants", "pokedex.h"), POKEDEX_H)
        put(("src", "data", "pokemon", "pokedex_entries.h"), POKEDEX_ENTRIES_H)
        put(("src", "data", "pokemon", "pokedex_orders.h"), POKEDEX_ORDERS_H)
        put(("src", "data", "pokemon", "pokedex_text_fr.h"), POKEDEX_TEXT)
        put(("src", "data", "graphics", "items.h"),
            "const struct Item gItems[] = {\n" + ITEMS_H + "\n};\n")
        put(("include", "constants", "abilities.h"), ABILITIES_H)
        put(("src", "field_specials.c"), STARTERS_C)
        put(("src", "battle_setup.c"), BATTLE_SETUP_C)
        put(("src", "data", "battle_moves.h"), BATTLE_MOVES_H)
        put(("src", "move_descriptions.c"), MOVE_DESCS_C)
        put(("src", "data", "pokemon", "species_info", "pory_species.h"), "\n")
        put(("src", "data", "constants.json"), "{}")
        self.project_info = {"dir": root, "project_name": "proj"}

    def tearDown(self):
        self.tempdir.cleanup()

    def _orders_path(self):
        return os.path.join(self.project_info["dir"],
                            "src", "data", "pokemon", "pokedex_orders.h")

    def _read_orders(self):
        with open(self._orders_path(), encoding="utf-8") as f:
            return f.read()

    def load_manager(self):
        pkg_name = "core"
        pkg_module = types.ModuleType(pkg_name)
        pkg_module.__path__ = [os.path.join(ROOT_DIR, "core")]
        sys.modules[pkg_name] = pkg_module
        spec = importlib.util.spec_from_file_location(
            "core.pokemon_data",
            os.path.join(ROOT_DIR, "core", "pokemon_data.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.PokemonDataManager(self.project_info)

    def _assert_complete(self, consts, expected_set):
        """Every dex entry exactly once."""
        self.assertEqual(sorted(consts), sorted(expected_set))
        self.assertEqual(len(consts), len(set(consts)))

    EXPECTED = ["NATIONAL_DEX_BULBASAUR", "NATIONAL_DEX_IVYSAUR",
                "NATIONAL_DEX_VENUSAUR", "NATIONAL_DEX_CHARMANDER"]

    def test_height_edit_through_data_layer_save(self):
        mgr = self.load_manager()
        natdex = mgr.data["pokedex"].data["national_dex"]
        entry = next(e for e in natdex
                     if e["dex_constant"] == "NATIONAL_DEX_CHARMANDER")
        # The fixture's extractor merged height/weight from the header.
        self.assertEqual(entry.get("height"), 6)
        entry["height"] = 25  # was the shortest; now the tallest

        # The manager-level save hook regenerates the arrays.
        result = mgr.regenerate_pokedex_orders()
        self.assertEqual(result, 3)

        text = self._read_orders()
        self._assert_complete(
            _extract_array(text, "gPokedexOrder_Alphabetical"), self.EXPECTED)
        self._assert_complete(
            _extract_array(text, "gPokedexOrder_Weight"), self.EXPECTED)
        self._assert_complete(
            _extract_array(text, "gPokedexOrder_Height"), self.EXPECTED)

        self.assertEqual(
            _extract_array(text, "gPokedexOrder_Height"),
            ["NATIONAL_DEX_BULBASAUR", "NATIONAL_DEX_IVYSAUR",
             "NATIONAL_DEX_VENUSAUR", "NATIONAL_DEX_CHARMANDER"])
        self.assertEqual(
            _extract_array(text, "gPokedexOrder_Weight"),
            ["NATIONAL_DEX_BULBASAUR", "NATIONAL_DEX_CHARMANDER",
             "NATIONAL_DEX_IVYSAUR", "NATIONAL_DEX_VENUSAUR"])
        self.assertEqual(
            _extract_array(text, "gPokedexOrder_Alphabetical"),
            ["NATIONAL_DEX_BULBASAUR", "NATIONAL_DEX_CHARMANDER",
             "NATIONAL_DEX_IVYSAUR", "NATIONAL_DEX_VENUSAUR"])
        # The type array is not derived data and stays untouched.
        self.assertIn("SPECIES_BULBASAUR,", text)

    def test_save_pipeline_triggers_regeneration(self):
        """File > Save (manager.parse_to_c_code) runs the regeneration.

        The full plugin pass needs every canonical source file, which
        this fixture deliberately doesn't carry — so the heavy base pass
        is stubbed and the test asserts the save entry point calls the
        regeneration hook after it.
        """
        mgr = self.load_manager()
        calls = []
        mgr.regenerate_pokedex_orders = lambda: calls.append(True) or 3
        base = sys.modules["core.pokemon_data"].pokemon_data
        orig = base.PokemonDataManager.parse_to_c_code
        base.PokemonDataManager.parse_to_c_code = lambda self: None
        try:
            mgr.parse_to_c_code()
        finally:
            base.PokemonDataManager.parse_to_c_code = orig
        self.assertEqual(calls, [True])

    def test_display_name_edit_through_rename_service(self):
        """A rename through the refactor service re-sorts alphabetical."""
        mgr = self.load_manager()
        # Display-only rename: constant unchanged, name Bulbasaur -> Zucchini
        # ("Zucchini" sorts last instead of first).
        mgr.refactor_service.rename_species(
            "SPECIES_BULBASAUR", "SPECIES_BULBASAUR", "Zucchini")

        names_path = os.path.join(
            self.project_info["dir"], "src", "data", "text",
            "species_names.h")
        with open(names_path, encoding="utf-8") as f:
            self.assertIn('_("Zucchini")', f.read())

        text = self._read_orders()
        self.assertEqual(
            _extract_array(text, "gPokedexOrder_Alphabetical"),
            ["NATIONAL_DEX_CHARMANDER", "NATIONAL_DEX_IVYSAUR",
             "NATIONAL_DEX_VENUSAUR", "NATIONAL_DEX_BULBASAUR"])
        self._assert_complete(
            _extract_array(text, "gPokedexOrder_Alphabetical"), self.EXPECTED)

    def test_correct_file_is_not_rewritten(self):
        """An already-correct file keeps its bytes (no phantom diffs)."""
        mgr = self.load_manager()
        self.assertEqual(mgr.regenerate_pokedex_orders(), 3)
        first = self._read_orders()
        mtime = os.path.getmtime(self._orders_path())
        self.assertEqual(mgr.regenerate_pokedex_orders(), 3)
        self.assertEqual(self._read_orders(), first)
        self.assertEqual(os.path.getmtime(self._orders_path()), mtime)

    def test_missing_orders_file_is_a_clean_skip(self):
        os.remove(self._orders_path())
        mgr = self.load_manager()
        self.assertEqual(mgr.regenerate_pokedex_orders(), -2)


class PokedexOrdersVanillaSmokeTest(unittest.TestCase):
    """The regenerator behaves sensibly on the pristine vanilla tree.

    Runs against a throwaway copy of the four relevant files; the
    reference tree itself is never written.
    """

    REL_FILES = (
        os.path.join("src", "data", "pokemon", "pokedex_orders.h"),
        os.path.join("src", "data", "pokemon", "pokedex_entries.h"),
        os.path.join("src", "data", "text", "species_names.h"),
        os.path.join("include", "constants", "pokedex.h"),
    )

    @unittest.skipUnless(os.path.isdir(_VANILLA_ROOT),
                         "vanilla reference tree not present")
    def test_vanilla_round_trip(self):
        po = _load_pokedex_orders_module()
        with tempfile.TemporaryDirectory() as root:
            for rel in self.REL_FILES:
                dst = os.path.join(root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(os.path.join(_VANILLA_ROOT, rel), dst)
            orders_path = os.path.join(
                root, "src", "data", "pokemon", "pokedex_orders.h")
            with open(orders_path, encoding="utf-8") as f:
                before = f.read()

            self.assertEqual(po.regenerate_pokedex_orders(root), 3)
            with open(orders_path, encoding="utf-8") as f:
                after = f.read()

            # Vanilla's alphabetical order already satisfies the sort
            # spec, so that array must be byte-identical — comments
            # inside it included.
            alpha_before = _extract_array(before, "gPokedexOrder_Alphabetical")
            alpha_after = _extract_array(after, "gPokedexOrder_Alphabetical")
            self.assertEqual(alpha_before, alpha_after)
            self.assertIn("// Actual pokemon start here.", after)

            sizes = po.parse_dex_entry_sizes(root)
            enum = po.parse_national_dex_enum(root)
            index = {c: i for i, c in enumerate(enum)}

            for array, key in (("gPokedexOrder_Weight", "weight"),
                               ("gPokedexOrder_Height", "height")):
                order_before = _extract_array(before, array)
                order_after = _extract_array(after, array)
                # Same membership, every entry exactly once.
                self.assertEqual(sorted(order_before), sorted(order_after))
                self.assertEqual(len(order_after), len(set(order_after)))
                # Ascending by value, ties in dex order.
                keys = [(sizes[c][key], index[c]) for c in order_after]
                self.assertEqual(keys, sorted(keys))

            # The type array and everything after it is untouched.
            t_before = before[before.find("gPokedexOrder_Type"):]
            t_after = after[after.find("gPokedexOrder_Type"):]
            self.assertEqual(t_before, t_after)

            # Second run is a byte-for-byte no-op.
            self.assertEqual(po.regenerate_pokedex_orders(root), 3)
            with open(orders_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), after)


if __name__ == "__main__":
    unittest.main()
