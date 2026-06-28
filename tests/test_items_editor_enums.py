"""Regression test for the Items editor coercing custom enum constants.

The Pocket / Item Type fields are non-editable data-combos built from a fixed
built-in choice list. A project that adds its own constants (e.g.
POCKET_EQUIPMENT, ITEM_TYPE_BAG_MENU) used to land on combo index 0 at load and
get written back as the default on the next collect() — silently moving the item
to the wrong pocket and zeroing its type. These tests pin that custom values are
added to the combos and preserved verbatim through load -> collect, while
built-in numeric types still round-trip as ints. See BUGS.md.
"""
import os
import sys
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Must select the headless Qt platform before importing PyQt6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Some sibling test modules install a minimal PyQt6 *stub* into sys.modules so
# they can import core headlessly. This test constructs a real widget, so purge
# any stub first and import the genuine PyQt6 (skipping cleanly if it's truly
# unavailable). The real package is harmless to the stub tests — their
# setdefault() no-ops and they simply run on real Qt.
for _m in [m for m in list(sys.modules) if m == "PyQt6" or m.startswith("PyQt6.")]:
    del sys.modules[_m]

try:
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from ui.items_tab_widget import ItemDetailPanel
    _QT_OK = True
except Exception as _e:  # pragma: no cover - environment without real Qt
    _QT_OK = False
    _QT_ERR = _e


@unittest.skipUnless(_QT_OK, "real PyQt6 unavailable")
class ItemsEditorEnumTest(unittest.TestCase):
    def _panel(self):
        return ItemDetailPanel()

    def test_custom_pocket_and_type_preserved(self):
        # Real flow: dropdowns built from the project's parsed constants.
        panel = self._panel()
        panel.set_enum_choices(
            [("POCKET_ITEMS", "POCKET_ITEMS"), ("POCKET_EQUIPMENT", "POCKET_EQUIPMENT")],
            [("ITEM_TYPE_BAG_MENU", "ITEM_TYPE_BAG_MENU")],
        )
        data = {"itemId": "ITEM_SOOT_SACK", "english": "BOMB", "price": 50,
                "pocket": "POCKET_EQUIPMENT", "type": "ITEM_TYPE_BAG_MENU"}
        panel.load_item("ITEM_SOOT_SACK", data)
        panel.f_price.setValue(75)              # edit one field
        out = panel.collect(data)
        self.assertEqual(out["pocket"], "POCKET_EQUIPMENT")
        self.assertEqual(out["type"], "ITEM_TYPE_BAG_MENU")
        self.assertEqual(out["price"], 75)

    def test_custom_value_preserved_without_prescan(self):
        # Even with no enum setup at all, load_item must preserve the values:
        # pocket via _select_or_add_combo_data, type via the editable combo.
        panel = self._panel()
        data = {"itemId": "ITEM_X", "english": "X", "price": 1,
                "pocket": "POCKET_TM_CASE", "type": "ITEM_TYPE_FIELD"}
        panel.load_item("ITEM_X", data)
        out = panel.collect(data)
        self.assertEqual(out["pocket"], "POCKET_TM_CASE")
        self.assertEqual(out["type"], "ITEM_TYPE_FIELD")

    def test_builtin_numeric_type_round_trips_as_int(self):
        panel = self._panel()
        data = {"itemId": "ITEM_POTION", "english": "POTION", "price": 300,
                "pocket": "POCKET_ITEMS", "type": 0}
        panel.load_item("ITEM_POTION", data)
        out = panel.collect(data)
        self.assertEqual(out["type"], 0)
        self.assertIsInstance(out["type"], int)
        self.assertEqual(out["pocket"], "POCKET_ITEMS")

    def test_set_enum_choices_uses_project_constants_not_vanilla(self):
        # Simulate what load_items does: populate the dropdowns from the
        # project's OWN parsed constants (renamed + custom pockets), then load
        # an item and confirm the custom pocket round-trips and the hardcoded
        # vanilla names are NOT present.
        panel = self._panel()
        pocket_choices = [("POCKET_ITEMS", "POCKET_ITEMS"),
                          ("POCKET_TM_CASE", "POCKET_TM_CASE"),
                          ("POCKET_EQUIPMENT", "POCKET_EQUIPMENT")]
        type_choices = [("ITEM_TYPE_FIELD", "ITEM_TYPE_FIELD"),
                        ("ITEM_TYPE_BAG_MENU", "ITEM_TYPE_BAG_MENU")]
        panel.set_enum_choices(pocket_choices, type_choices)
        present = [panel.f_pocket.itemData(i) for i in range(panel.f_pocket.count())]
        self.assertIn("POCKET_EQUIPMENT", present)
        self.assertIn("POCKET_TM_CASE", present)
        self.assertNotIn("POCKET_TM_HM", present)   # vanilla name gone
        self.assertNotIn("POCKET_BERRIES", present)
        data = {"itemId": "ITEM_SOOT_SACK", "english": "BOMB", "price": 50,
                "pocket": "POCKET_EQUIPMENT", "type": "ITEM_TYPE_BAG_MENU"}
        panel.load_item("ITEM_SOOT_SACK", data)
        out = panel.collect(data)
        self.assertEqual(out["pocket"], "POCKET_EQUIPMENT")
        self.assertEqual(out["type"], "ITEM_TYPE_BAG_MENU")

    def test_set_enum_choices_falls_back_when_empty(self):
        # A project where parsing found nothing must still get usable dropdowns.
        panel = self._panel()
        panel.set_enum_choices([], [])
        self.assertGreater(panel.f_pocket.count(), 0)
        self.assertGreater(panel.f_type.count(), 0)

    def test_string_enum_secondaryid_preserved_on_rename(self):
        # A fishing rod's string-enum secondaryId must survive a name-only edit
        # instead of coercing to 0 (the round-2 bug).
        panel = self._panel()
        data = {"itemId": "ITEM_OLD_ROD", "english": "OLD ROD", "price": 0,
                "pocket": "POCKET_KEY_ITEMS", "type": "ITEM_TYPE_FIELD",
                "secondaryId": "OLD_ROD"}
        panel.load_item("ITEM_OLD_ROD", data)
        panel.f_name.setText("Old Rod")          # rename ONLY
        out = panel.collect(data)
        self.assertEqual(out["secondaryId"], "OLD_ROD")   # NOT 0
        self.assertEqual(out["english"], "Old Rod")

    def test_numeric_secondaryid_still_round_trips_as_int(self):
        panel = self._panel()
        data = {"itemId": "ITEM_X", "english": "X", "secondaryId": 3,
                "pocket": "POCKET_ITEMS", "type": 0}
        panel.load_item("ITEM_X", data)
        out = panel.collect(data)
        self.assertEqual(out["secondaryId"], 3)
        self.assertIsInstance(out["secondaryId"], int)

    def test_guard_covers_whole_record_not_just_secondaryid(self):
        # ANY numeric/bool field holding a string enum must be preserved.
        panel = self._panel()
        data = {"itemId": "ITEM_Y", "english": "Y",
                "pocket": "POCKET_ITEMS", "type": 0,
                "holdEffectParam": "SOME_PARAM_ENUM", "battleUsage": "SOME_USAGE"}
        panel.load_item("ITEM_Y", data)
        out = panel.collect(data)
        self.assertEqual(out["holdEffectParam"], "SOME_PARAM_ENUM")
        self.assertEqual(out["battleUsage"], "SOME_USAGE")

    def test_name_field_default_cap_is_thirteen(self):
        # ITEM_NAME_LENGTH(14) - 1 = 13 max displayable chars.
        panel = self._panel()
        self.assertEqual(panel.f_name.maxLength(), 13)

    def test_set_name_limit_blocks_overlong_input(self):
        panel = self._panel()
        panel.set_name_limit(13)
        self.assertEqual(panel.f_name.maxLength(), 13)
        panel.f_name.setText("ABCDEFGHIJKLMNOPQRST")   # 20 chars
        self.assertEqual(len(panel.f_name.text()), 13)   # blocked at the cap

    def test_set_name_limit_follows_project(self):
        # A project that widened ITEM_NAME_LENGTH to 20 -> cap 19.
        panel = self._panel()
        panel.set_name_limit(19)
        self.assertEqual(panel.f_name.maxLength(), 19)
        self.assertIn("/19", panel._name_counter.text())

    def test_unknown_extra_fields_survive(self):
        # collect() starts from the base dict, so project-added fields the
        # editor doesn't know about must pass through untouched.
        panel = self._panel()
        data = {"itemId": "ITEM_Y", "english": "Y", "price": 5,
                "pocket": "POCKET_ITEMS", "type": 0,
                "zeldamon_custom_flag": 7}
        panel.load_item("ITEM_Y", data)
        out = panel.collect(data)
        self.assertEqual(out.get("zeldamon_custom_flag"), 7)


if __name__ == "__main__":
    unittest.main()
