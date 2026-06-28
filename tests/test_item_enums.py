"""Tests for core.item_enums — parsing a project's OWN pocket / item-type
constants from its source headers, so the Items editor never relies on a
hardcoded vanilla list. Project-agnostic: synthetic headers, including renamed
pockets and a custom one, mirror a non-Kanto hack.
"""
import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.item_enums import (
    parse_pockets, parse_item_types, parse_item_name_length, find_include_headers,
)


class ItemEnumParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_parse_pockets_renamed_and_custom(self):
        # A non-Kanto layout: renamed TM/Berry pockets + a project-added one,
        # plus a COUNT sentinel that must be excluded, in non-sorted order.
        self._write("include/constants/global.h", (
            "#define POCKET_KEY_ITEMS  2\n"
            "#define POCKET_ITEMS      1\n"
            "#define POCKET_TM_CASE    4\n"
            "#define POCKET_POKE_BALLS 3\n"
            "#define POCKET_BERRY_POUCH 5\n"
            "#define POCKET_EQUIPMENT  6  // custom hidden pocket\n"
            "#define POCKETS_COUNT     7\n"
        ))
        got = parse_pockets(self.root)
        self.assertEqual([n for n, _ in got], [
            "POCKET_ITEMS", "POCKET_KEY_ITEMS", "POCKET_POKE_BALLS",
            "POCKET_TM_CASE", "POCKET_BERRY_POUCH", "POCKET_EQUIPMENT",
        ])
        self.assertEqual(dict(got)["POCKET_EQUIPMENT"], 6)
        self.assertNotIn("POCKETS_COUNT", dict(got))

    def test_parse_item_types_enum_with_comments(self):
        self._write("include/item.h", (
            "// Item type IDs\n"
            "enum {\n"
            "    ITEM_TYPE_MAIL,\n"
            "    ITEM_TYPE_PARTY_MENU,\n"
            "    ITEM_TYPE_FIELD,\n"
            "    ITEM_TYPE_UNUSED, // Used for Pokeblock case in RSE\n"
            "    ITEM_TYPE_BAG_MENU, // No exit callback\n"
            "};\n"
        ))
        got = parse_item_types(self.root)
        self.assertEqual(got, [
            ("ITEM_TYPE_MAIL", 0), ("ITEM_TYPE_PARTY_MENU", 1),
            ("ITEM_TYPE_FIELD", 2), ("ITEM_TYPE_UNUSED", 3),
            ("ITEM_TYPE_BAG_MENU", 4),
        ])

    def test_parse_item_types_explicit_values(self):
        self._write("include/item.h", (
            "enum ItemType {\n"
            "    ITEM_TYPE_A = 0,\n"
            "    ITEM_TYPE_B = 5,\n"
            "    ITEM_TYPE_C,\n"
            "};\n"
        ))
        self.assertEqual(parse_item_types(self.root),
                         [("ITEM_TYPE_A", 0), ("ITEM_TYPE_B", 5), ("ITEM_TYPE_C", 6)])

    def test_discovers_nonstandard_location(self):
        # Pockets defined in an unexpected header — still found by the walk.
        self._write("include/zeldamon/bag.h", "#define POCKET_ITEMS 1\n#define POCKET_EQUIPMENT 6\n")
        self.assertEqual(dict(parse_pockets(self.root)).get("POCKET_EQUIPMENT"), 6)

    def test_empty_when_no_include(self):
        self.assertEqual(parse_pockets(self.root), [])
        self.assertEqual(parse_item_types(self.root), [])
        self.assertEqual(find_include_headers(self.root), [])

    def test_parse_item_name_length(self):
        self._write("include/constants/global.h", "#define ITEM_NAME_LENGTH 14\n")
        self.assertEqual(parse_item_name_length(self.root), 14)

    def test_parse_item_name_length_custom(self):
        # A project that widened the name buffer.
        self._write("include/constants/global.h", "#define ITEM_NAME_LENGTH 20\n")
        self.assertEqual(parse_item_name_length(self.root), 20)

    def test_parse_item_name_length_default_when_missing(self):
        self.assertEqual(parse_item_name_length(self.root), 14)
        self.assertEqual(parse_item_name_length(self.root, default=18), 18)


if __name__ == "__main__":
    unittest.main()
