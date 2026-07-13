"""Regression test for normalize_pic_table (core/overworld_sprite_creator).

Guards the kakrito / OLD_MAN_2 bug: a vanilla "standing" NPC whose frame table
only references a few face frames and REPEATS them (plus a stray foreign frame)
gets reused as a many-frame walker. The frame SIZE is unchanged, so the
import-time size-change path never rebuilds the table — the NPC then moves
WITHOUT animating. normalize_pic_table must rewrite the table to sequential
frames 0..N-1 of the sprite's OWN symbol, N = the sheet's real frame count.

Run: python tests/test_normalize_pic_table.py
"""

import importlib.util
import os
import struct
import sys
import tempfile
import types


def _load_osc():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg = types.ModuleType("core")
    pkg.__path__ = [os.path.join(root, "core")]
    sys.modules["core"] = pkg
    for m in ("core.overworld_subsprite_gen", "core.overworld_sprite_geometry"):
        p = os.path.join(root, "core", m.split(".")[1] + ".py")
        spec = importlib.util.spec_from_file_location(m, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[m] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location(
        "core.overworld_sprite_creator",
        os.path.join(root, "core", "overworld_sprite_creator.py"))
    osc = importlib.util.module_from_spec(spec)
    sys.modules["core.overworld_sprite_creator"] = osc
    spec.loader.exec_module(osc)
    return osc


def _png(path, w, h):
    """Write a minimal valid PNG (IHDR only is enough — normalize reads header)."""
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", 0))  # bogus CRC; we only read IHDR dims
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    osc = _load_osc()
    root = tempfile.mkdtemp()
    obj = os.path.join(root, "src", "data", "object_events")
    gfx = os.path.join(root, "graphics", "object_events", "pics", "people")
    os.makedirs(gfx, exist_ok=True)

    # 16x32 sprite, 10-frame VERTICAL strip (16 wide x 320 tall).
    _png(os.path.join(gfx, "old_man_2.png"), 16, 320)
    # a .4bpp fallback too (2560 = 10 * 256)
    with open(os.path.join(gfx, "old_man_2.4bpp"), "wb") as f:
        f.write(b"\x00" * 2560)

    _write(os.path.join(obj, "object_event_graphics.h"),
           'const u16 gObjectEventPic_OldMan2[] = '
           'INCBIN_U16("graphics/object_events/pics/people/old_man_2.4bpp");\n')

    _write(os.path.join(obj, "object_event_graphics_info.h"),
           "const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_OldMan2 = {\n"
           "    .width = 16,\n    .height = 32,\n"
           "    .images = sPicTable_OldMan2,\n};\n")

    # The BROKEN vanilla-style table: repeated face frames + a foreign frame.
    _write(os.path.join(obj, "object_event_pic_tables.h"),
           "static const struct SpriteFrameImage sPicTable_OldMan2[] = {\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 0),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 1),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 2),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 0),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 0),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 1),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 1),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 2),\n"
           "    overworld_frame(gObjectEventPic_OldMan2, 2, 4, 2),\n"
           "    overworld_frame(gObjectEventPic_OldWoman, 2, 4, 0),\n"
           "};\n")

    ok, applied, errors = osc.normalize_pic_table(root, "OldMan2")
    assert ok, f"normalize failed: {errors}"

    result = open(os.path.join(obj, "object_event_pic_tables.h")).read()
    # Every slot must be sequential 0..9 of the OWN symbol; no foreign frame.
    for i in range(10):
        assert f"overworld_frame(gObjectEventPic_OldMan2, 2, 4, {i})" in result, \
            f"frame {i} missing"
    assert "OldWoman" not in result, "foreign OldWoman frame still present"
    assert result.count("overworld_frame(") == 10, "wrong entry count"

    # Idempotent second run.
    ok2, applied2, _ = osc.normalize_pic_table(root, "OldMan2")
    assert ok2 and "already normalized" in " ".join(applied2), \
        f"not idempotent: {applied2}"

    print("PASS: normalize_pic_table rebuilds broken table to 0..9, idempotent")


if __name__ == "__main__":
    main()
