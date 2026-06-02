"""Tests for ``core/battle_anim_data.py`` — the battle-anim sprite parser.

Pure stdlib module, loaded directly with importlib (no core/__init__
chain).  Tests run against the real project tree at
``porysuite/pokefirered`` for the integration assertions, plus synthetic
fixtures for the parsing edge cases.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
_PROJECT = os.path.join(_ROOT, "pokefirered")


def _load():
    path = os.path.join(_ROOT, "core", "battle_anim_data.py")
    spec = importlib.util.spec_from_file_location("battle_anim_data", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("battle_anim_data", mod)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# ───────────────────────────────────────────── pure parsing units ──

def test_incbin_symbol_map_picks_right_extension():
    text = (
        'const u32 gBattleAnimSpriteGfx_Bone[] = '
        'INCBIN_U32("graphics/battle_anims/sprites/bone.4bpp.lz");\n'
        'const u32 gBattleAnimSpritePal_Bone[] = '
        'INCBIN_U32("graphics/battle_anims/sprites/bone.gbapal.lz");\n'
    )
    gfx = mod._incbin_symbol_map(text, ".4bpp.lz")
    pal = mod._incbin_symbol_map(text, ".gbapal.lz")
    assert gfx == {"gBattleAnimSpriteGfx_Bone":
                   "graphics/battle_anims/sprites/bone.4bpp.lz"}
    assert pal == {"gBattleAnimSpritePal_Bone":
                   "graphics/battle_anims/sprites/bone.gbapal.lz"}


def test_parse_pic_table_reads_symbol_size_tag():
    text = (
        "const struct CompressedSpriteSheet gBattleAnimPicTable[] =\n"
        "{\n"
        "    {gBattleAnimSpriteGfx_Bone, 0x0200, ANIM_TAG_BONE},\n"
        "    {gBattleAnimSpriteGfx_Spark, 0x0300, ANIM_TAG_SPARK},\n"
        "};\n"
    )
    entries = mod._parse_pic_table(text)
    assert entries == [
        ("gBattleAnimSpriteGfx_Bone", 0x200, "ANIM_TAG_BONE"),
        ("gBattleAnimSpriteGfx_Spark", 0x300, "ANIM_TAG_SPARK"),
    ]


def test_parse_palette_table_maps_tag_to_symbol():
    text = (
        "const struct CompressedSpritePalette gBattleAnimPaletteTable[] =\n"
        "{\n"
        "    {gBattleAnimSpritePal_Bone, ANIM_TAG_BONE},\n"
        "    {gBattleAnimSpritePal_Spark, ANIM_TAG_SPARK},\n"
        "};\n"
    )
    pal = mod._parse_palette_table(text)
    assert pal == {
        "ANIM_TAG_BONE": "gBattleAnimSpritePal_Bone",
        "ANIM_TAG_SPARK": "gBattleAnimSpritePal_Spark",
    }


def test_display_name_humanizes_tag():
    s = mod.BattleAnimSprite(
        tag="ANIM_TAG_AIR_WAVE", gfx_symbol="x", pal_symbol="y",
        vram_size=0, png_path="", pal_path="")
    assert s.display_name == "Air Wave"
    s2 = mod.BattleAnimSprite(
        tag="ANIM_TAG_BONE", gfx_symbol="x", pal_symbol="y",
        vram_size=0, png_path="", pal_path="")
    assert s2.display_name == "Bone"


def test_png_from_gfx_relpath_swaps_extension():
    p = mod._png_from_gfx_relpath(
        os.path.join("C:", os.sep, "proj"),
        "graphics/battle_anims/sprites/bone.4bpp.lz")
    assert p.endswith(os.path.join("bone.png"))
    assert "battle_anims" in p


def test_missing_files_return_empty(tmp_path):
    # No graphics.c / battle_anim.h in an empty dir -> [].
    assert mod.parse_battle_anim_sprites(str(tmp_path)) == []


# ─────────────────────────────────────────── integration (real tree) ──

@pytest.mark.skipif(
    not os.path.isdir(_PROJECT),
    reason="pokefirered test project not present",
)
class TestAgainstRealProject:

    def test_parses_full_roster(self):
        sprites = mod.parse_battle_anim_sprites(_PROJECT)
        # The project ships ~289 pic-table entries; every gfx symbol
        # resolves to an INCBIN, so we expect the full set (allow a small
        # margin in case upstream trims a few).
        assert len(sprites) >= 250, f"only parsed {len(sprites)}"

    def test_known_sprite_resolves_png(self):
        sprites = {s.tag: s for s in mod.parse_battle_anim_sprites(_PROJECT)}
        assert "ANIM_TAG_BONE" in sprites
        bone = sprites["ANIM_TAG_BONE"]
        assert bone.gfx_symbol == "gBattleAnimSpriteGfx_Bone"
        assert bone.png_path.endswith(os.path.join("bone.png"))
        assert bone.png_exists, f"missing {bone.png_path}"
        assert bone.display_name == "Bone"

    def test_palette_path_resolves_to_pal_or_gbapal(self):
        sprites = {s.tag: s for s in mod.parse_battle_anim_sprites(_PROJECT)}
        bone = sprites["ANIM_TAG_BONE"]
        # Either a .pal sidecar or the .gbapal binary must exist on disk.
        assert bone.pal_path, "no palette path resolved for ANIM_TAG_BONE"
        assert os.path.isfile(bone.pal_path)
        assert bone.pal_path.endswith((".pal", ".gbapal"))

    def test_all_png_paths_point_into_sprites_dir(self):
        sprites = mod.parse_battle_anim_sprites(_PROJECT)
        sdir = mod.battle_anim_sprites_dir(_PROJECT)
        for s in sprites:
            assert s.png_path.startswith(sdir), (
                f"{s.tag} png_path outside sprites dir: {s.png_path}")

    def test_no_duplicate_tags(self):
        sprites = mod.parse_battle_anim_sprites(_PROJECT)
        tags = [s.tag for s in sprites]
        assert len(tags) == len(set(tags)), "duplicate ANIM_TAG in parsed set"

    def test_frame_sizes_resolve_known_sprites(self):
        sizes = mod.parse_anim_frame_sizes(_PROJECT)
        # Coverage: a good chunk of tags resolve via their templates.
        assert len(sizes) >= 150, f"only {len(sizes)} frame sizes resolved"
        # Known sprites with known OAM sizes.
        assert sizes.get("ANIM_TAG_BONE") == (32, 32)
        assert sizes.get("ANIM_TAG_SWORD") == (32, 64)

    def test_template_tags_resolve_in_real_project(self):
        tags = mod.parse_template_tags(_PROJECT)
        # Hundreds of templates map to an ANIM_TAG.
        assert len(tags) >= 150, f"only {len(tags)} template->tag mappings"
        # A known one: the ember sprite template.
        assert tags.get("gEmberSpriteTemplate") == "ANIM_TAG_SMALL_EMBER"


def test_oam_size_from_name():
    assert mod._oam_size_from_name("gOamData_AffineOff_ObjNormal_32x32") == (32, 32)
    assert mod._oam_size_from_name("gOamData_AffineNormal_ObjNormal_16x32") == (16, 32)
    assert mod._oam_size_from_name("gSomethingWithoutSize") is None


def test_build_oam_size_map_from_struct():
    text = (
        "const struct OamData gOamData_X =\n{\n"
        "    .shape = SPRITE_SHAPE(16x32),\n"
        "    .size = SPRITE_SIZE(16x32),\n"
        "};\n"
    )
    out = mod._build_oam_size_map([text])
    assert out == {"gOamData_X": (16, 32)}


def test_parse_template_tags_maps_symbol_to_tag(tmp_path):
    bah = tmp_path / "src" / "data"
    bah.mkdir(parents=True)
    (bah / "battle_anim.h").write_text(
        "const struct SpriteTemplate gEmberSpriteTemplate =\n{\n"
        "    .tileTag = ANIM_TAG_SMALL_EMBER,\n"
        "    .oam = &gOamData_X,\n};\n"
        "const struct SpriteTemplate gNoTagTemplate =\n{\n"
        "    .tileTag = TAG_NONE,\n};\n",
        encoding="utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    tags = mod.parse_template_tags(str(tmp_path))
    assert tags.get("gEmberSpriteTemplate") == "ANIM_TAG_SMALL_EMBER"
    # A template whose tileTag isn't an ANIM_TAG is omitted.
    assert "gNoTagTemplate" not in tags


def test_parse_anim_frame_sizes_resolves_via_template(tmp_path):
    # Synthetic: a template links a tag to a sized OAM struct.
    bah = tmp_path / "src" / "data"
    bah.mkdir(parents=True)
    (bah / "battle_anim.h").write_text(
        "const struct OamData gOamData_Big =\n{\n"
        "    .size = SPRITE_SIZE(64x64),\n};\n"
        "const struct SpriteTemplate gFooTemplate =\n{\n"
        "    .tileTag = ANIM_TAG_FOO,\n"
        "    .oam = &gOamData_Big,\n"
        "    .callback = AnimFoo,\n};\n",
        encoding="utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    sizes = mod.parse_anim_frame_sizes(str(tmp_path))
    assert sizes.get("ANIM_TAG_FOO") == (64, 64)
