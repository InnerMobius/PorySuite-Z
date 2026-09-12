"""core/female_art.py — give a species FEMALE-different art (same stats).

Companion to the engine's female-art override (Saten): the game shows a
different front/back sprite + palette when a mon is female, chosen by gender at
load time, with no stat change. This module is the AUTHORING/generation side —
it mirrors append_species' graphics machinery but, instead of adding rows to the
four standard graphics tables, it:

  * seeds a sibling art folder  graphics/pokemon/<slug>_female/  (from the male
    art, or from caller-supplied female PNG/.pal), so the build's normal graphics
    rules compile it exactly like any other species folder,
  * declares the female INCBIN symbols  gMon{Front,Back}Pic_<Sym>F /
    gMon{,Shiny}Palette_<Sym>F  in src/data/graphics/pokemon.h,
  * inserts one row into src/data/pokemon/female_pic_overrides.h (with its own
    externs, since that table compiles in a TU that doesn't include graphics.h).

Everything is idempotent. A species with no female art — and any project that
never calls this — is completely unaffected (the override table stays empty).
"""

import json
import os
import shutil

from core.append_species import _read, _write, _has, _parse_base


def female_slug(species_const):
    """Sibling art-folder slug for a species' female art (matches the form-folder
    convention so the build's graphics rules apply unchanged)."""
    return species_const[len("SPECIES_"):].lower() + "_female"


def has_female_art(project_root, species_const):
    """True if *species_const* already has a female-override row."""
    p = os.path.join(project_root, "src", "data", "pokemon", "female_pic_overrides.h")
    if not os.path.isfile(p):
        return False
    return ("{ " + species_const + "," ) in _read(p).replace("  ", " ")


def set_female_art(project_root, species_const, front_png=None, back_png=None,
                   normal_pal=None, shiny_pal=None):
    """Give *species_const* female art. Any of the four source paths that is
    supplied is copied in as the female art; anything omitted is seeded from the
    male art as a starting point (so the folder is valid and builds, and the user
    can replace files later). Returns a dict of what changed."""
    pf = lambda *a: os.path.join(project_root, *a)
    base = _parse_base(project_root, species_const)
    bsym = base["base_sym"]                 # e.g. "Royalblob"
    fsym = bsym + "F"                        # "RoyalblobF"
    slug = female_slug(species_const)        # "royalblob_female"
    changed = {}

    # 1. Seed the female source folder (imports win; else copy the male art).
    dst_dir = pf("graphics", "pokemon", slug)
    os.makedirs(dst_dir, exist_ok=True)
    sgj = pf("src", "data", "species_graphics.json")
    d = json.load(open(sgj, encoding="utf-8")) if os.path.isfile(sgj) else {}

    def male_src(key):
        e = d.get(f"{key}_{bsym}")
        return pf(*e["png"].split("/")) if (e and e.get("png")) else None

    for leaf, given, key in (("front.png", front_png, "gMonFrontPic"),
                             ("back.png", back_png, "gMonBackPic"),
                             ("normal.pal", normal_pal, "gMonPalette"),
                             ("shiny.pal", shiny_pal, "gMonShinyPalette")):
        dst = os.path.join(dst_dir, leaf)
        if given and os.path.isfile(given):
            shutil.copy2(given, dst)
        elif not os.path.isfile(dst):
            src = male_src(key)
            if src and os.path.isfile(src):
                shutil.copy2(src, dst)

    # 2. INCBIN definitions for the female art (src/data/graphics/pokemon.h).
    p = pf("src", "data", "graphics", "pokemon.h")
    t = _read(p)
    if not _has(t, f"gMonFrontPic_{fsym}"):
        decls = (
            f'\nconst u32 gMonFrontPic_{fsym}[] = INCBIN_U32("graphics/pokemon/{slug}/front.4bpp.lz");\n'
            f'const u32 gMonBackPic_{fsym}[] = INCBIN_U32("graphics/pokemon/{slug}/back.4bpp.lz");\n'
            f'const u32 gMonPalette_{fsym}[] = INCBIN_U32("graphics/pokemon/{slug}/normal.gbapal.lz");\n'
            f'const u32 gMonShinyPalette_{fsym}[] = INCBIN_U32("graphics/pokemon/{slug}/shiny.gbapal.lz");\n'
        )
        t = t.rstrip("\n") + "\n" + decls
        _write(p, t)
        changed["pokemon.h"] = True

    # 3. female_pic_overrides.h — self-contained externs + the override row.
    p = pf("src", "data", "pokemon", "female_pic_overrides.h")
    t = _read(p)
    if not _has(t, f"gMonFrontPic_{fsym}"):
        externs = (
            f"extern const u32 gMonFrontPic_{fsym}[];\n"
            f"extern const u32 gMonBackPic_{fsym}[];\n"
            f"extern const u32 gMonPalette_{fsym}[];\n"
            f"extern const u32 gMonShinyPalette_{fsym}[];\n"
        )
        anchor = "const struct GenderPicOverride gFemalePicOverrides[]"
        i = t.index(anchor)
        t = t[:i] + externs + "\n" + t[i:]
    row = (f"    {{ {species_const}, &gMonFrontPic_{fsym}, &gMonBackPic_{fsym}, "
           f"&gMonPalette_{fsym}, &gMonShinyPalette_{fsym} }},\n")
    if f"{{ {species_const}," not in t:
        term = "{ SPECIES_NONE }"
        i = t.index(term)
        line_start = t.rindex("\n", 0, i) + 1
        t = t[:line_start] + row + t[line_start:]
    _write(p, t)
    changed["female_pic_overrides.h"] = True

    # 4. species_graphics.json — editor manifest for the female sources.
    if os.path.isfile(sgj):
        folder = f"graphics/pokemon/{slug}"
        add = {
            f"gMonFrontPic_{fsym}": {"png": f"{folder}/front.png"},
            f"gMonBackPic_{fsym}": {"png": f"{folder}/back.png"},
            f"gMonPalette_{fsym}": {"png": f"{folder}/normal.pal"},
            f"gMonShinyPalette_{fsym}": {"png": f"{folder}/shiny.pal"},
        }
        ch = False
        for k, v in add.items():
            if d.get(k) != v:
                d[k] = v
                ch = True
        if ch:
            with open(sgj, "w", encoding="utf-8", newline="\n") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
                f.write("\n")
            changed["species_graphics.json"] = True

    return changed
