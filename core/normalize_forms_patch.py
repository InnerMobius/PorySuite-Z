"""Normalize a multi-form species down to a single, normal, editable sprite.

Some vanilla species ship as a bundle of forms that the Graphics tab can't edit
as one mon:

* **Unown** — 28 letter/symbol forms (A-Z, !, ?), art in per-letter subfolders,
  a suffixed ``gMonFrontPic_Unown**A**`` symbol, 27 appended form-sprite species
  (``SPECIES_UNOWN_B..QMARK``), and letter-picking code across the engine.
* **Deoxys** — Normal/Attack/Defense/Speed forms (stacked sprite sheets, runtime
  ``DuplicateDeoxysTiles``).
* **Castform** — Normal/Sunny/Rainy/Snowy weather forms.

"Normalizing" collapses a species to ONE sprite set (its base/Normal form),
repoints every form entry at that single sprite so no form can ever render
something different, deletes the now-unused per-form art, and makes the slot
behave like any ordinary single-sprite Pokémon in both the engine and the app.

**Constants are deliberately left in place.** Deleting ``SPECIES_UNOWN_B..``/
``SPECIES_OLD_UNOWN_B..`` would renumber every species after them and break the
build, so those constants stay DEFINED — but fully INERT: every one now resolves
to the single normalized sprite, so it produces no distinct form. Each edited
file gets a comment saying exactly that.

This is a patcher (the tool refactors the engine); callers should rebuild after.
"""
from __future__ import annotations

import json
import os
import re
import shutil

_NORMALIZED_NOTE = (
    "// Normalized by PorySuite: this species was collapsed to a single sprite.\n"
    "// Its extra form-species constants remain DEFINED but INERT — every one\n"
    "// resolves to the single sprite below, so no distinct form is produced.\n"
)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ── Unown ────────────────────────────────────────────────────────────────────

def normalize_unown(root: str) -> dict:
    """Collapse Unown to a single (letter-A) sprite. Returns a result dict:
    {ok, changed, message, deleted, notes}. Every step is individually
    idempotent, so a re-run is a no-op AND a half-applied state is completed."""
    res = {"ok": False, "changed": False, "message": "", "deleted": [], "notes": []}
    root = os.path.abspath(root)
    gfx = os.path.join(root, "graphics", "pokemon", "unown")
    pokemon_h = os.path.join(root, "src", "data", "graphics", "pokemon.h")
    graphics_h = os.path.join(root, "include", "graphics.h")
    front_tbl = os.path.join(root, "src", "data", "pokemon_graphics", "front_pic_table.h")
    back_tbl = os.path.join(root, "src", "data", "pokemon_graphics", "back_pic_table.h")
    icon_c = os.path.join(root, "src", "pokemon_icon.c")

    if not os.path.isdir(gfx):
        res["message"] = "graphics/pokemon/unown not found."
        return res

    # 1) Promote the letter-A art to the standard single set at the folder root
    #    (only if the letter-A folder still exists).
    if os.path.isdir(os.path.join(gfx, "a")):
        for name in ("front.png", "back.png", "icon.png"):
            src = os.path.join(gfx, "a", name)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(gfx, name))
        res["changed"] = True

    # Line patterns for the per-letter symbol declarations/definitions (B-Z, !, ?).
    _letter_def = re.compile(
        r'^const \w+ gMon(?:FrontPic|BackPic|Icon)_Unown'
        r'(?:[B-Z]|ExclamationMark|QuestionMark)\[\] = INCBIN_\w+\([^\n]*\);\n',
        re.MULTILINE)
    _letter_extern = re.compile(
        r'^extern const \w+ gMon(?:FrontPic|BackPic|Icon)_Unown'
        r'(?:[B-Z]|ExclamationMark|QuestionMark)\[\];\n',
        re.MULTILINE)

    # 2) pokemon.h — rename the A INCBINs to the plain symbol pointing at the
    #    new top-level art, then delete every B..Z/!/? INCBIN definition.
    ph = _read(pokemon_h)
    if ph is None:
        res["message"] = "Could not read src/data/graphics/pokemon.h."
        return res
    orig = ph
    ph = ph.replace(
        'const u32 gMonFrontPic_UnownA[] = INCBIN_U32("graphics/pokemon/unown/a/front.4bpp.lz");',
        _NORMALIZED_NOTE +
        'const u32 gMonFrontPic_Unown[] = INCBIN_U32("graphics/pokemon/unown/front.4bpp.lz");')
    ph = ph.replace(
        'const u32 gMonBackPic_UnownA[] = INCBIN_U32("graphics/pokemon/unown/a/back.4bpp.lz");',
        'const u32 gMonBackPic_Unown[] = INCBIN_U32("graphics/pokemon/unown/back.4bpp.lz");')
    ph = ph.replace(
        'const u8 gMonIcon_UnownA[] = INCBIN_U8("graphics/pokemon/unown/a/icon.4bpp");',
        'const u8 gMonIcon_Unown[] = INCBIN_U8("graphics/pokemon/unown/icon.4bpp");')
    ph, n_defs = _letter_def.subn("", ph)
    if ph != orig:
        _write(pokemon_h, ph)
        res["changed"] = True
        res["notes"].append(f"pokemon.h: A->Unown, removed {n_defs} letter definitions")

    # 2b) include/graphics.h — the extern declarations MUST match the definitions
    #     (else the pic tables reference an undeclared symbol). Same rename + drop.
    gh = _read(graphics_h)
    if gh is not None:
        orig = gh
        gh = gh.replace("extern const u32 gMonFrontPic_UnownA[];",
                        "extern const u32 gMonFrontPic_Unown[];")
        gh = gh.replace("extern const u32 gMonBackPic_UnownA[];",
                        "extern const u32 gMonBackPic_Unown[];")
        gh = gh.replace("extern const u8 gMonIcon_UnownA[];",
                        "extern const u8 gMonIcon_Unown[];")
        gh, n_ext = _letter_extern.subn("", gh)
        if gh != orig:
            _write(graphics_h, gh)
            res["changed"] = True
            res["notes"].append(f"graphics.h: A->Unown externs, removed {n_ext} letter externs")

    # 3) front/back pic tables + icon table — repoint EVERY Unown-art symbol
    #    (UnownA, UnownB..Z, Unown!/?) to the single symbol. OLD_UNOWN rows point
    #    at gMonFrontPic_DoubleQuestionMark and are intentionally left alone.
    # Only the known Unown form suffixes (A-Z, !, ?) are repointed/deleted — an
    # explicit set so an unrelated custom gMonFrontPic_Unown<word> can't be hit.
    _form_sym = r'_Unown(?:[A-Z]|ExclamationMark|QuestionMark)\b'
    _anchor = "    SPECIES_SPRITE(UNOWN, "
    _note_indented = _NORMALIZED_NOTE.replace("//", "    //")

    def _repoint(path, sym_prefix):
        txt = _read(path)
        if txt is None:
            return
        new = re.sub(sym_prefix + _form_sym, sym_prefix + '_Unown', txt)
        # Add the explanatory note directly above THIS file's SPECIES_UNOWN row,
        # only if it isn't already there (row-local check — safe when siblings
        # share the same table file).
        if _anchor in new and (_note_indented + _anchor) not in new:
            new = new.replace(_anchor, _note_indented + _anchor, 1)
        if new != txt:
            _write(path, new)
            res["changed"] = True

    _repoint(front_tbl, "gMonFrontPic")
    _repoint(back_tbl, "gMonBackPic")

    ic = _read(icon_c)
    if ic is not None:
        ic2 = re.sub(r'gMonIcon' + _form_sym, 'gMonIcon_Unown', ic)
        if ic2 != ic:
            _write(icon_c, ic2)
            res["changed"] = True
            res["notes"].append("pokemon_icon.c: icon table repointed to the single sprite")

    # 4) species_graphics.json — the app's symbol->PNG map. Repoint the base
    #    symbols to the new top-level art and drop the per-letter entries.
    sg_path = os.path.join(root, "src", "data", "species_graphics.json")
    sg = _read(sg_path)
    if sg is not None:
        try:
            data = json.loads(sg)
            before = json.dumps(data, sort_keys=True)
            # Values MUST be {"png": path} dicts — the app's SpeciesGraphics.get_image
            # does `img_data["png"]`, which throws on a bare string.
            for base, png in (("gMonFrontPic_Unown", "graphics/pokemon/unown/front.png"),
                              ("gMonBackPic_Unown", "graphics/pokemon/unown/back.png"),
                              ("gMonIcon_Unown", "graphics/pokemon/unown/icon.png")):
                data[base] = {"png": png}
                data.pop(base + "A", None)
            for k in [k for k in list(data)
                      if re.match(r'gMon(FrontPic|BackPic|Icon)_Unown'
                                  r'([B-Z]|ExclamationMark|QuestionMark)$', k)]:
                data.pop(k, None)
            if json.dumps(data, sort_keys=True) != before:
                with open(sg_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                res["changed"] = True
                res["notes"].append("species_graphics.json: base symbols repointed, letter keys removed")
        except ValueError:
            res["notes"].append("species_graphics.json: left unchanged (parse error)")

    # 5) delete the now-unused per-letter subfolders (only the known letter/symbol
    #    folders — the promoted front/back/icon.png live at the root and stay).
    _form_dirs = [chr(c) for c in range(ord("a"), ord("z") + 1)] + \
                 ["exclamation_mark", "question_mark"]
    for sub in _form_dirs:
        p = os.path.join(gfx, sub)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            res["deleted"].append(f"graphics/pokemon/unown/{sub}/")
            res["changed"] = True

    res["ok"] = True
    if res["changed"]:
        res["message"] = (
            "Unown normalized to a single sprite (letter A). The 27 letter/symbol "
            "forms now all resolve to that one sprite, their art was deleted, and "
            "their constants remain defined but inert.")
    else:
        res["message"] = "Unown is already normalized."
    return res


# ── Deoxys ───────────────────────────────────────────────────────────────────

_DEOXYS_IFDEF = (
    '#ifdef FIRERED\n'
    'const u32 gMonFrontPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/front.4bpp.lz");\n'
    'const u32 gMonPalette_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/normal.gbapal.lz");\n'
    'const u32 gMonBackPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/back.4bpp.lz");\n'
    'const u32 gMonShinyPalette_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/shiny.gbapal.lz");\n'
    'const u8 gMonIcon_Deoxys[] = INCBIN_U8("graphics/pokemon/deoxys/icon.4bpp", "graphics/pokemon/deoxys/icon_attack.4bpp");\n'
    'const u8 gMonFootprint_Deoxys[] = INCBIN_U8("graphics/pokemon/deoxys/footprint.1bpp");\n'
    '#endif\n'
    '\n'
    '#ifdef LEAFGREEN\n'
    'const u32 gMonFrontPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/front_def.4bpp.lz");\n'
    'const u32 gMonPalette_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/normal.gbapal.lz");\n'
    'const u32 gMonBackPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/back_def.4bpp.lz");\n'
    'const u32 gMonShinyPalette_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/shiny.gbapal.lz");\n'
    'const u8 gMonIcon_Deoxys[] = INCBIN_U8("graphics/pokemon/deoxys/icon.4bpp", "graphics/pokemon/deoxys/icon_defense.4bpp");\n'
    'const u8 gMonFootprint_Deoxys[] = INCBIN_U8("graphics/pokemon/deoxys/footprint.1bpp");\n'
    '#endif\n'
)

_DEOXYS_SINGLE = (
    _NORMALIZED_NOTE +
    'const u32 gMonFrontPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/front.4bpp.lz");\n'
    'const u32 gMonPalette_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/normal.gbapal.lz");\n'
    'const u32 gMonBackPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/back.4bpp.lz");\n'
    'const u32 gMonShinyPalette_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/shiny.gbapal.lz");\n'
    'const u8 gMonIcon_Deoxys[] = INCBIN_U8("graphics/pokemon/deoxys/icon.4bpp");\n'
    'const u8 gMonFootprint_Deoxys[] = INCBIN_U8("graphics/pokemon/deoxys/footprint.1bpp");\n'
)

_DEOXYS_TILEDUP = (
    "    else if (species == SPECIES_DEOXYS)\n"
    "        CpuCopy32((u8 *)pointer + 0x800, pointer, 0x800);\n"
)

# The stacked icon put an "attack" icon at +0x400; with a single icon that jump
# now reads past the symbol. Drop the special-case so Deoxys returns its one icon.
_DEOXYS_ICON_OFFSET = (
    "    if (species == SPECIES_DEOXYS && extra == TRUE)\n"
    "        iconSprite += 0x400;\n"
)


def _crop_top_square(path, size=64):
    """Crop a stacked sprite PNG to its top *size*x*size* frame, in place.
    No-op if Pillow is unavailable or the image is already that height."""
    try:
        from PIL import Image
    except Exception:
        return False
    try:
        im = Image.open(path)
        if im.height <= size:
            return False
        im.crop((0, 0, min(size, im.width), size)).save(path)
        return True
    except Exception:
        return False


def normalize_deoxys(root: str) -> dict:
    """Collapse Deoxys to a single Normal-form sprite: drop the FIRERED/LEAFGREEN
    version blocks and the stacked-sheet + tile-duplication machinery, crop the
    front/back sheets to their top 64x64 frame, delete the extra variant art."""
    res = {"ok": False, "changed": False, "message": "", "deleted": [], "notes": []}
    root = os.path.abspath(root)
    gfx = os.path.join(root, "graphics", "pokemon", "deoxys")
    pokemon_h = os.path.join(root, "src", "data", "graphics", "pokemon.h")
    decompress_c = os.path.join(root, "src", "decompress.c")

    if not os.path.isdir(gfx):
        res["message"] = "graphics/pokemon/deoxys not found."
        return res

    # 1) pokemon.h — replace the two #ifdef version blocks with one unconditional
    #    single-frame, single-icon definition.
    ph = _read(pokemon_h)
    if ph is None:
        res["message"] = "Could not read pokemon.h."
        return res
    if _DEOXYS_IFDEF in ph:
        _write(pokemon_h, ph.replace(_DEOXYS_IFDEF, _DEOXYS_SINGLE, 1))
        res["changed"] = True
        res["notes"].append("pokemon.h: dropped FIRERED/LEAFGREEN blocks -> single Deoxys sprite + icon")

    # 2) decompress.c — remove the Deoxys frame-1-over-frame-0 tile duplication
    #    (with the sheet cropped to one frame there is nothing to duplicate).
    dc = _read(decompress_c)
    if dc is not None and _DEOXYS_TILEDUP in dc:
        _write(decompress_c, dc.replace(_DEOXYS_TILEDUP, "", 1))
        res["changed"] = True
        res["notes"].append("decompress.c: removed the Deoxys tile-duplication branch")

    # 2b) pokemon_icon.c — with the stacked "attack" icon gone, the +0x400 jump
    #     to it now reads past the single icon into the next symbol. Drop it.
    icon_c = os.path.join(root, "src", "pokemon_icon.c")
    icn = _read(icon_c)
    if icn is not None and _DEOXYS_ICON_OFFSET in icn:
        _write(icon_c, icn.replace(_DEOXYS_ICON_OFFSET, "", 1))
        res["changed"] = True
        res["notes"].append("pokemon_icon.c: removed the Deoxys stacked-icon offset")

    # 3) crop the front/back stacked sheets to their top 64x64 Normal frame
    try:
        from PIL import Image  # noqa: F401
        _have_pil = True
    except Exception:
        _have_pil = False
    for name in ("front.png", "back.png"):
        if _crop_top_square(os.path.join(gfx, name), 64):
            res["changed"] = True
            res["notes"].append(f"{name}: cropped to single 64x64 frame")
    if not _have_pil:
        res["notes"].append(
            "NOTE: Pillow isn't available, so front/back weren't trimmed to one "
            "frame — they still render the Normal frame and build fine; re-run "
            "the normalize once Pillow is installed to remove the wasted frame.")

    # 4) species_graphics.json — point Deoxys at its plain Normal art (matching
    #    the dict {"png": ...} format the file already uses for it).
    sg_path = os.path.join(root, "src", "data", "species_graphics.json")
    sg = _read(sg_path)
    if sg is not None:
        try:
            data = json.loads(sg)
            before = json.dumps(data, sort_keys=True)
            for sym, png in (("gMonFrontPic_Deoxys", "graphics/pokemon/deoxys/front.png"),
                             ("gMonBackPic_Deoxys", "graphics/pokemon/deoxys/back.png"),
                             ("gMonIcon_Deoxys", "graphics/pokemon/deoxys/icon.png")):
                data[sym] = {"png": png}
            if json.dumps(data, sort_keys=True) != before:
                with open(sg_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                res["changed"] = True
                res["notes"].append("species_graphics.json: Deoxys repointed to Normal art")
        except ValueError:
            res["notes"].append("species_graphics.json: left unchanged (parse error)")

    # 5) delete the now-unused variant art (Defense front/back, Attack/Defense icons)
    for stem in ("front_def", "back_def", "icon_attack", "icon_defense"):
        for ext in (".png", ".4bpp", ".4bpp.lz"):
            p = os.path.join(gfx, stem + ext)
            if os.path.isfile(p):
                os.remove(p)
                res["deleted"].append(f"graphics/pokemon/deoxys/{stem}{ext}")
                res["changed"] = True

    res["ok"] = True
    res["message"] = ("Deoxys normalized to a single Normal-form sprite."
                      if res["changed"] else "Deoxys is already normalized.")
    return res


# ── Castform ─────────────────────────────────────────────────────────────────

# Castform's weather forms live in a 4-frame sheet (stitched from the
# normal/sunny/rainy/snowy subfolders by the build) that the battle engine
# indexes by gBattleMonForms. Collapsing that sheet safely would need build-rule
# surgery across the stitch + the engine's frame/palette indexing. Instead we
# take the SAFE route: show the Normal frame in the app, and stop the form from
# ever changing so it always renders frame 0 in-game. The weather art stays on
# disk (inert) rather than risk a battle-engine out-of-bounds.

_CASTFORM_FUNC_OPEN = (
    "u8 CastformDataTypeChange(u8 battler)\n"
    "{\n"
    "    u8 formChange = 0;\n"
)
_CASTFORM_FUNC_OPEN_FIXED = (
    "u8 CastformDataTypeChange(u8 battler)\n"
    "{\n"
    "    // Normalized by PorySuite: Castform no longer weather-forms — it stays\n"
    "    // on its Normal form (frame 0) so it renders as a single sprite.\n"
    "    if (gBattleMons[battler].species == SPECIES_CASTFORM)\n"
    "        return CASTFORM_NO_CHANGE;\n"
    "    u8 formChange = 0;\n"
)


def normalize_castform(root: str) -> dict:
    """Make Castform behave as a single Normal-form sprite: show its Normal art
    in the app and stop its weather form-change in-game. The extra weather sheet
    is left in place (safe) rather than risk the battle engine's frame indexing."""
    res = {"ok": False, "changed": False, "message": "", "deleted": [], "notes": []}
    root = os.path.abspath(root)
    gfx = os.path.join(root, "graphics", "pokemon", "castform")
    battle_util = os.path.join(root, "src", "battle_util.c")

    if not os.path.isdir(gfx):
        res["message"] = "graphics/pokemon/castform not found."
        return res

    # 1) promote the Normal-frame art to the folder root so the Graphics tab can
    #    show/edit it (the top-level PNG is not a build input — the sheet is
    #    stitched from the subfolders — so this is display-only and safe).
    for name in ("front.png", "back.png"):
        src = os.path.join(gfx, "normal", name)
        dst = os.path.join(gfx, name)
        if os.path.isfile(src) and (not os.path.isfile(dst)
                                    or os.path.getsize(src) != os.path.getsize(dst)):
            shutil.copyfile(src, dst)
            res["changed"] = True
            res["notes"].append(f"promoted normal/{name} -> {name} (app display)")

    # 2) stop the weather form-change so Castform stays on frame 0 (Normal).
    bu = _read(battle_util)
    if bu is not None and _CASTFORM_FUNC_OPEN in bu \
            and _CASTFORM_FUNC_OPEN_FIXED not in bu:
        _write(battle_util, bu.replace(_CASTFORM_FUNC_OPEN, _CASTFORM_FUNC_OPEN_FIXED, 1))
        res["changed"] = True
        res["notes"].append("battle_util.c: disabled the Castform weather form-change")

    # 3) species_graphics.json — point Castform at its Normal art.
    sg_path = os.path.join(root, "src", "data", "species_graphics.json")
    sg = _read(sg_path)
    if sg is not None:
        try:
            data = json.loads(sg)
            before = json.dumps(data, sort_keys=True)
            for sym, png in (("gMonFrontPic_Castform", "graphics/pokemon/castform/front.png"),
                             ("gMonBackPic_Castform", "graphics/pokemon/castform/back.png")):
                data[sym] = {"png": png}
            if json.dumps(data, sort_keys=True) != before:
                with open(sg_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                res["changed"] = True
                res["notes"].append("species_graphics.json: Castform repointed to Normal art")
        except ValueError:
            res["notes"].append("species_graphics.json: left unchanged (parse error)")

    res["notes"].append(
        "NOTE: the weather sprite sheet/subfolders were left on disk (inert). "
        "Fully deleting them needs build-stitch + battle-engine changes — a "
        "separate careful pass.")
    res["ok"] = True
    res["message"] = ("Castform normalized: single Normal sprite, no weather "
                      "form-change in-game." if res["changed"]
                      else "Castform is already normalized.")
    return res


# ── dispatcher (for the "Normalize this species" UI button) ───────────────────

# Registry of species-constant -> normalizer.
_NORMALIZERS = {
    "SPECIES_UNOWN": normalize_unown,
    "SPECIES_DEOXYS": normalize_deoxys,
    "SPECIES_CASTFORM": normalize_castform,
}


def known_multiform_species() -> set:
    """Every species this tool knows how to normalize, regardless of whether a
    given project still needs it (drives the button's visibility so the feature
    stays discoverable even after a species is already collapsed)."""
    return set(_NORMALIZERS.keys())


def normalizable_species(root: str) -> list:
    """Which multi-form species in THIS project can be normalized right now.
    Returns a list of species constants (empty if none apply / already done)."""
    out = []
    ph = _read(os.path.join(root, "src", "data", "graphics", "pokemon.h")) or ""
    unown = os.path.join(root, "graphics", "pokemon", "unown")
    if "gMonFrontPic_UnownA" in ph or os.path.isdir(os.path.join(unown, "a")):
        out.append("SPECIES_UNOWN")
    deoxys = os.path.join(root, "graphics", "pokemon", "deoxys")
    # Deoxys is still multi-form while the version-gated block or its Defense
    # variant art is present.
    if _DEOXYS_IFDEF in ph or os.path.isfile(os.path.join(deoxys, "front_def.png")):
        out.append("SPECIES_DEOXYS")
    # Castform is still weather-forming while its form-change hasn't been disabled
    # or its Normal art hasn't been surfaced for the app.
    castform = os.path.join(root, "graphics", "pokemon", "castform")
    bu = _read(os.path.join(root, "src", "battle_util.c")) or ""
    if os.path.isdir(castform) and (
            _CASTFORM_FUNC_OPEN_FIXED not in bu
            or not os.path.isfile(os.path.join(castform, "front.png"))):
        out.append("SPECIES_CASTFORM")
    return out


def normalize_species(root: str, species_const: str) -> dict:
    """Normalize one multi-form species to a single sprite. Routes to the
    species-specific normalizer; returns its result dict (see normalize_unown)."""
    fn = _NORMALIZERS.get(species_const)
    if fn is None:
        return {"ok": False, "changed": False, "deleted": [], "notes": [],
                "message": f"No normalizer is available for {species_const} yet."}
    return fn(root)
