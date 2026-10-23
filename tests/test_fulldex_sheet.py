"""Regression guard: the Full Dex Sheet must draw type badges the way the GAME draws them.

THE BUG (first cut of core/fulldex_sheet.py): the type badges were coloured with the
palette whose FILE NAME matched the badge sheet -- graphics/interface/pokemon_types.pal,
reached by looking for an INCBIN whose symbol shared the sheet's stem. That palette is
STALE in this project. The badge sheet was re-indexed against the battle HUD palette so
the overworld HUD could draw type icons, and `ListMenuLoadStdPalAt` now loads
`gBattleInterface_Healthbar_Pal` for EVERY menu-info blit regardless of the palette id it
is handed. Colouring with the name-matched palette rendered every badge in one wrong
colour ramp.

THE RULE (core/fulldex_sheet.py::_badge_palette_symbol):
  * Resolve the badge palette by reading what the project's palette-loading function
    ACTUALLY loads, not by matching file or symbol names.
  * A project is free to re-index its sheet against any palette; the stale
    conventionally-named one may still sit on disk, so a name match is not evidence.
  * Fall back to a stem-matched symbol only when no loader can be read.

Also guarded: a species whose two type slots hold the SAME type is single-typed and gets
ONE badge, as the game shows it -- not the same badge drawn twice.

If you touch the badge palette resolution or `collect_entries`, keep this green.
"""

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.fulldex_sheet import (_badge_palette_symbol, _incbin_path,
                                collect_entries)
from core.gba_font import (SLOT_BG, SLOT_FG, SLOT_SHADOW, parse_charmap,
                           parse_glyph_value_slots)


# A project that re-indexed its badge sheet against the HUD palette. The
# conventionally-named palette still exists and is stale -- exactly the trap.
LIST_MENU_REINDEXED = """
void ListMenuLoadStdPalAt(u8 palOffset, u8 palId)
{
    // The sheet was re-indexed against the HUD palette; the old
    // pokemon_types.gbapal is stale and renders the wrong colours.
    (void)palId;
    LoadPalette(gBattleInterface_Healthbar_Pal, palOffset, PLTT_SIZE_4BPP);
}

void BlitMenuInfoIcon(u8 windowId, u8 iconId, u16 x, u16 y)
{
    BlitBitmapRectToWindow(windowId, &gMenuInfoElements_Gfx[sMenuInfoIcons[iconId].offset * TILE_SIZE_4BPP], 0, 0, 128, 128, x, y, 32, 12);
}
"""

# A project that never re-indexed: the loader reads the name-matched palette.
LIST_MENU_VANILLA = """
void ListMenuLoadStdPalAt(u8 palOffset, u8 palId)
{
    LoadPalette(gMenuInfoElements2_Pal, palOffset, PLTT_SIZE_4BPP);
}
"""

GRAPHICS_C = """
const u16 gMenuInfoElements1_Pal[] = INCBIN_U16("graphics/interface/dex_caught_pokeball.gbapal");
const u16 gMenuInfoElements2_Pal[] = INCBIN_U16("graphics/interface/pokemon_types.gbapal");
const u8 gMenuInfoElements_Gfx[] = INCBIN_U8("graphics/interface/menu_info.4bpp");
const u16 gBattleInterface_Healthbar_Pal[] = INCBIN_U16("graphics/battle_interface/healthbar.gbapal");
"""


class _StubSource:
    """Minimal stand-in for the project data manager."""

    def __init__(self, species):
        self._species = species

    def get_pokemon_data(self):
        return {k: {} for k in self._species}

    def get_national_dex(self):
        return [{"species": k} for k in self._species]

    def get_species_data(self, sp, key):
        return {"dex_num": self._species[sp]["dex"],
                "name": self._species[sp]["name"]}.get(key)

    def get_species_info(self, sp, key, form=None):
        return self._species[sp].get(key)


class BadgePaletteTest(unittest.TestCase):

    def test_follows_the_palette_the_game_actually_loads(self):
        """The re-indexed project must resolve to the HUD palette, not the
        stale name-matched one."""
        sym = _badge_palette_symbol(LIST_MENU_REINDEXED, "gMenuInfoElements")
        self.assertEqual(sym, "gBattleInterface_Healthbar_Pal")
        self.assertNotEqual(
            sym, "gMenuInfoElements2_Pal",
            "resolved the stale palette by name instead of reading the loader")

    def test_resolved_symbol_maps_to_its_real_file(self):
        sym = _badge_palette_symbol(LIST_MENU_REINDEXED, "gMenuInfoElements")
        self.assertEqual(_incbin_path(GRAPHICS_C, sym),
                         "graphics/battle_interface/healthbar.gbapal")

    def test_a_project_that_never_reindexed_still_resolves(self):
        """The rule is 'read the loader', which also covers the stock case."""
        sym = _badge_palette_symbol(LIST_MENU_VANILLA, "gMenuInfoElements")
        self.assertEqual(sym, "gMenuInfoElements2_Pal")
        self.assertEqual(_incbin_path(GRAPHICS_C, sym),
                         "graphics/interface/pokemon_types.gbapal")

    def test_falls_back_to_stem_match_without_a_loader(self):
        sym = _badge_palette_symbol(
            "void Unrelated(void) { LoadPalette(gMenuInfoElements2_Pal, 0, 32); }",
            "gMenuInfoElements")
        self.assertEqual(sym, "gMenuInfoElements2_Pal")

    def test_no_loader_at_all_is_not_a_crash(self):
        self.assertEqual(_badge_palette_symbol("", "gMenuInfoElements"), "")


class EntryCollectionTest(unittest.TestCase):

    def _entries(self, species):
        return collect_entries(_StubSource(species), "/proj", None)

    def test_same_type_in_both_slots_yields_one_badge(self):
        ents = self._entries({
            "SPECIES_A": {"dex": 1, "name": "A",
                          "types": ["TYPE_DARK", "TYPE_DARK"]},
        })
        self.assertEqual(ents[0]["types"], ["TYPE_DARK"])

    def test_dual_types_are_kept_in_order(self):
        ents = self._entries({
            "SPECIES_A": {"dex": 1, "name": "A",
                          "types": ["TYPE_NORMAL", "TYPE_ROCK"]},
        })
        self.assertEqual(ents[0]["types"], ["TYPE_NORMAL", "TYPE_ROCK"])

    def test_entries_come_back_in_dex_order(self):
        ents = self._entries({
            "SPECIES_C": {"dex": 3, "name": "C", "types": []},
            "SPECIES_A": {"dex": 1, "name": "A", "types": []},
            "SPECIES_B": {"dex": 2, "name": "B", "types": []},
        })
        self.assertEqual([e["species"] for e in ents],
                         ["SPECIES_A", "SPECIES_B", "SPECIES_C"])

    def test_missing_types_do_not_raise(self):
        ents = self._entries({
            "SPECIES_A": {"dex": 1, "name": "A", "types": None},
        })
        self.assertEqual(ents[0]["types"], [])


def _half_row_offsets(fold: dict) -> str:
    """Rebuild a project's sFontHalfRowOffsets table.

    A half-row byte holds four 2-bit pixels. Each pixel's raw value is folded
    onto one of three colour slots, and the byte indexes a lookup table built
    as a base-3 odometer over those slots. *fold* says which slot each raw
    value lands on.
    """
    rows = []
    for byte in range(256):
        px = [(byte >> s) & 3 for s in (0, 2, 4, 6)]
        idx = sum(fold[v] * (3 ** p) for p, v in enumerate(px))
        rows.append(f"0x{idx:02X}")
    return ("static const u8 sFontHalfRowOffsets[] =\n{\n    "
            + ", ".join(rows) + ",\n};\n")


class _FakeProject:
    """A throwaway project root holding just the files under test."""

    def __init__(self, **files):
        self.root = tempfile.mkdtemp(prefix="fulldex_test_")
        for rel, body in files.items():
            path = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class GlyphColourSlotTest(unittest.TestCase):
    """A glyph pixel holds 0-3, but those are NOT four colours.

    The engine folds them onto three slots (background / foreground /
    shadow) through its own table, and more than one raw value lands on
    background. Reading a font sheet as if every index were its own colour
    paints the glyph box's fill as solid ink -- which is what a white block
    behind every label looks like.
    """

    def _slots(self, fold):
        proj = _FakeProject(**{"src/text_printer.c": _half_row_offsets(fold)})
        try:
            return parse_glyph_value_slots(proj.root)
        finally:
            proj.close()

    def test_value_three_folds_onto_background(self):
        got = self._slots({0: SLOT_BG, 1: SLOT_FG, 2: SLOT_SHADOW, 3: SLOT_BG})
        self.assertEqual(got[3], SLOT_BG,
                         "glyph value 3 must render as background, not ink")
        self.assertEqual(got[0], SLOT_BG)
        self.assertEqual(got[1], SLOT_FG)
        self.assertEqual(got[2], SLOT_SHADOW)

    def test_the_table_is_read_not_assumed(self):
        """A project that folds value 3 elsewhere must be followed."""
        got = self._slots({0: SLOT_BG, 1: SLOT_FG, 2: SLOT_SHADOW,
                           3: SLOT_SHADOW})
        self.assertEqual(got[3], SLOT_SHADOW)

    def test_missing_table_falls_back_safely(self):
        proj = _FakeProject(**{"src/text_printer.c": "// nothing here\n"})
        try:
            got = parse_glyph_value_slots(proj.root)
        finally:
            proj.close()
        self.assertEqual(got[3], SLOT_BG)
        self.assertEqual(got[1], SLOT_FG)


class CharmapTest(unittest.TestCase):

    def _map(self, body):
        proj = _FakeProject(**{"charmap.txt": body})
        try:
            return parse_charmap(proj.root)
        finally:
            proj.close()

    def test_reads_single_byte_entries(self):
        got = self._map("'A'         = BB\n'a'         = D5\n'0' = A1\n")
        self.assertEqual(got["A"], 0xBB)
        self.assertEqual(got["a"], 0xD5)
        self.assertEqual(got["0"], 0xA1)

    def test_skips_multi_byte_and_control_entries(self):
        got = self._map("'A' = BB\nPLAYER = FD 01\n$ = FF\n")
        self.assertEqual(list(got), ["A"])

    def test_character_codes_are_not_ascii(self):
        """The project's own mapping wins; never assume ASCII."""
        got = self._map("'A' = 01\n")
        self.assertEqual(got["A"], 0x01)


if __name__ == "__main__":
    unittest.main()
