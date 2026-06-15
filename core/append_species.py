"""core/append_species.py — append a new (form) species across the engine.

A form is a distinct species ID with its own complete per-species data. This
module emits everything required for the build to resolve a new species:

  * the SPECIES_* constant (species.h, above the Unown form range)
  * the stats entry in gSpeciesInfo (species_info.h)
  * the four graphics-table rows (front/back/palette/shiny) + the icon table
    row + icon-palette-index + front/back coordinates
  * the INCBIN declarations the graphics rows reference (graphics/pokemon.h)

Rows are inserted DIRECTLY AFTER the base species' corresponding row, which
already carries a trailing comma / closing brace — so we never have to retrofit
a comma onto a prior last-entry. Every edit is idempotent (a per-file presence
check), so re-running changes nothing.

This is Phase 3 of the alternate-forms work. The form-ID table wiring
(`.formSpeciesIdTable`) and the widening of the hard-sized `[NUM_SPECIES]`
gameplay tables (evolution / learnsets / dex maps / footprint / elevation) are
handled separately (Phase 3b) — a species that is not yet referenced in a party
builds safely without them; they are needed before a form is actually used
in-game. All edits go through this module — game source is never hand-edited.
"""

import logging
import os
import re


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _write(p, text):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _has(text, token):
    """Whole-token presence test (word-boundaried). A plain `token in text` would
    FALSE-positive on a longer name that the token is a prefix of — e.g. checking
    for SPECIES_X would match inside SPECIES_X_Y, or gMonFrontPic_X inside
    gMonFrontPic_XY — and silently skip writing the shorter one. `_` is a word
    char, so `\\bTOKEN\\b` matches TOKEN only when it isn't followed by `_`/letters."""
    return re.search(r"\b" + re.escape(token) + r"\b", text) is not None


def _insert_after_line(text, anchor_regex, insertion):
    """Insert *insertion* on its own line(s) immediately after the line that
    matches *anchor_regex*. Raises if the anchor isn't found."""
    m = re.search(anchor_regex, text)
    if not m:
        raise RuntimeError(f"append_species: anchor not found: {anchor_regex}")
    eol = text.index("\n", m.end())
    return text[:eol + 1] + insertion + text[eol + 1:]


def _insert_after_block(text, block_start_regex, insertion):
    """Insert after a multi-line `[SPECIES_X] = { ... },` block — i.e. after the
    first `    },` that follows the block-start anchor."""
    m = re.search(block_start_regex, text)
    if not m:
        raise RuntimeError(f"append_species: block anchor not found: {block_start_regex}")
    close = re.search(r"\n    \},\n", text[m.end():])
    if not close:
        raise RuntimeError("append_species: block close not found")
    pos = m.end() + close.end()
    return text[:pos] + insertion + text[pos:]


def _species_info_block(const, s):
    g = s.get
    return (
        f"    [{const}] =\n"
        f"    {{\n"
        f"        .baseHP = {s['baseHP']},\n"
        f"        .baseAttack = {s['baseAttack']},\n"
        f"        .baseDefense = {s['baseDefense']},\n"
        f"        .baseSpeed = {s['baseSpeed']},\n"
        f"        .baseSpAttack = {s['baseSpAttack']},\n"
        f"        .baseSpDefense = {s['baseSpDefense']},\n"
        f"        .types = {{ {g('type1', 'TYPE_NORMAL')}, {g('type2', 'TYPE_NORMAL')} }},\n"
        f"        .catchRate = {g('catchRate', 3)},\n"
        f"        .expYield = {g('expYield', 100)},\n"
        f"        .evYield_HP = {g('evYield_HP', 0)},\n"
        f"        .evYield_Attack = {g('evYield_Attack', 0)},\n"
        f"        .evYield_Defense = {g('evYield_Defense', 0)},\n"
        f"        .evYield_Speed = {g('evYield_Speed', 0)},\n"
        f"        .evYield_SpAttack = {g('evYield_SpAttack', 0)},\n"
        f"        .evYield_SpDefense = {g('evYield_SpDefense', 0)},\n"
        f"        .itemCommon = {g('itemCommon', 'ITEM_NONE')},\n"
        f"        .itemRare = {g('itemRare', 'ITEM_NONE')},\n"
        f"        .genderRatio = {g('genderRatio', 'MON_GENDERLESS')},\n"
        f"        .eggCycles = {g('eggCycles', 120)},\n"
        f"        .friendship = {g('friendship', 0)},\n"
        f"        .growthRate = {g('growthRate', 'GROWTH_SLOW')},\n"
        f"        .eggGroups = {{ {g('eggGroup1', 'EGG_GROUP_UNDISCOVERED')}, {g('eggGroup2', 'EGG_GROUP_UNDISCOVERED')} }},\n"
        f"        .abilities = {{ {g('ability1', 'ABILITY_NONE')}, {g('ability2', 'ABILITY_NONE')} }},\n"
        f"        .safariZoneFleeRate = {g('safariZoneFleeRate', 0)},\n"
        f"        .bodyColor = {g('bodyColor', 'BODY_COLOR_RED')},\n"
        f"        .noFlip = {g('noFlip', 'FALSE')},\n"
        f"    }},\n"
    )


def append_species(project_root, spec):
    """Append one species described by *spec* across all engine tables.

    spec keys:
      const       e.g. "SPECIES_DEOXYS_DEFENSE"
      id_expr     e.g. "(NUM_SPECIES + 28)"
      base        base species short name, e.g. "DEOXYS" (used to anchor rows)
      base_sym    base graphics symbol, e.g. "Deoxys"
      sym         this species' graphics symbol, e.g. "DeoxysDefense"
      stats       dict for the gSpeciesInfo entry
      front/back/pal/shiny  INCBIN source paths (relative to graphics/)
      icon        tuple of icon INCBIN paths
      icon_pal    icon palette index (int)
      coords      (width, height, y_offset) for front+back coords

    Idempotent. Returns a dict of which files changed.
    """
    pf = lambda *a: os.path.join(project_root, *a)
    const = spec["const"]
    base = spec["base"]
    bsym = spec["base_sym"]
    sym = spec["sym"]
    # Graphics sharing is decoupled per channel so a form can share the base's
    # IMAGE (front/back/icon — render a frame of the base's stacked sheet) while
    # carrying its OWN palette (a recoloured form), or own both, or share both.
    share_both = spec.get("share_graphics", False)         # back-compat: shares both
    share_image = spec.get("share_image", share_both)      # front/back/icon
    share_palette = spec.get("share_palette", share_both)  # palette/shiny
    img_sym = bsym if share_image else sym                 # front/back/icon symbol
    pal_sym = bsym if share_palette else sym               # palette/shiny symbol
    w, h, yoff = spec["coords"]
    changed = {}

    # 1. species.h — the constant, after SPECIES_UNOWN_QMARK
    p = pf("include", "constants", "species.h")
    t = _read(p)
    if not _has(t, const):
        t = _insert_after_line(
            t, r"#define SPECIES_UNOWN_QMARK \(NUM_SPECIES \+ 27\)",
            f"#define {const} {spec['id_expr']}\n")
        _write(p, t); changed["species.h"] = True

    # 2. graphics/pokemon.h — INCBIN decls, after the base species' #endif blocks
    p = pf("src", "data", "graphics", "pokemon.h")
    t = _read(p)
    decls = ""
    if not share_image and not _has(t, f"gMonFrontPic_{sym}"):
        icon_args = ", ".join(f'"graphics/{x}"' for x in spec["icon"])
        decls += (
            f'const u32 gMonFrontPic_{sym}[] = INCBIN_U32("graphics/{spec["front"]}");\n'
            f'const u32 gMonBackPic_{sym}[] = INCBIN_U32("graphics/{spec["back"]}");\n'
            f'const u8 gMonIcon_{sym}[] = INCBIN_U8({icon_args});\n'
        )
    if not share_palette and not _has(t, f"gMonPalette_{sym}"):
        decls += (
            f'const u32 gMonPalette_{sym}[] = INCBIN_U32("graphics/{spec["pal"]}");\n'
            f'const u32 gMonShinyPalette_{sym}[] = INCBIN_U32("graphics/{spec["shiny"]}");\n'
        )
    if decls:
        # The base species' INCBINs may sit in #ifdef FIRERED / #ifdef LEAFGREEN
        # blocks (Deoxys does). Insert our unconditional decls after the LAST
        # such block (i.e. after the final footprint+#endif), before the next
        # species' decls.
        anchor = rf"gMonFootprint_{bsym}\[\] = INCBIN_U8[^\n]*\n#endif\n"
        matches = list(re.finditer(anchor, t))
        if not matches:
            # base isn't in an #ifdef block — anchor on its plain footprint decl
            m = re.search(rf"gMonFootprint_{bsym}\[\] = INCBIN_U8[^\n]*\n", t)
            if not m:
                raise RuntimeError("append_species: base graphics block not found")
            pos = m.end()
        else:
            pos = matches[-1].end()
        t = t[:pos] + "\n" + decls + t[pos:]
        _write(p, t); changed["pokemon.h"] = True

    # 2b. include/graphics.h — extern declarations the data tables compile
    # against (the INCBIN definitions live in pokemon.h, compiled in a different
    # TU; src/data.c + pokemon_icon.c see only these externs).
    p = pf("include", "graphics.h")
    t = _read(p)
    externs = ""
    if not share_image and not _has(t, f"gMonFrontPic_{sym}"):
        externs += (
            f"extern const u32 gMonFrontPic_{sym}[];\n"
            f"extern const u32 gMonBackPic_{sym}[];\n"
            f"extern const u8 gMonIcon_{sym}[];\n"
        )
    if not share_palette and not _has(t, f"gMonPalette_{sym}"):
        externs += (
            f"extern const u32 gMonPalette_{sym}[];\n"
            f"extern const u32 gMonShinyPalette_{sym}[];\n"
        )
    if externs:
        t = _insert_after_line(
            t, rf"extern const u8 gMonIcon_{bsym}\[\];", externs)
        _write(p, t); changed["graphics.h"] = True

    # 3. species_info.h — stats entry, after the base species' block
    p = pf("src", "data", "pokemon", "species_info.h")
    t = _read(p)
    if not _has(t, const):
        t = _insert_after_block(
            t, rf"\[SPECIES_{base}\] =\n", _species_info_block(const, spec["stats"]))
        _write(p, t); changed["species_info.h"] = True

    # 4. graphics tables — front/back/palette/shiny rows after the base rows
    table_edits = [
        ("src/data/pokemon_graphics/front_pic_table.h",
         rf"SPECIES_SPRITE\({base}, gMonFrontPic_{bsym}\),",
         f"    SPECIES_SPRITE({spec['const_short']}, gMonFrontPic_{img_sym}),\n"),
        ("src/data/pokemon_graphics/back_pic_table.h",
         rf"SPECIES_SPRITE\({base}, gMonBackPic_{bsym}\),",
         f"    SPECIES_SPRITE({spec['const_short']}, gMonBackPic_{img_sym}),\n"),
        ("src/data/pokemon_graphics/palette_table.h",
         rf"SPECIES_PAL\({base}, gMonPalette_{bsym}\),",
         f"    SPECIES_PAL({spec['const_short']}, gMonPalette_{pal_sym}),\n"),
        ("src/data/pokemon_graphics/shiny_palette_table.h",
         rf"SPECIES_SHINY_PAL\({base}, gMonShinyPalette_{bsym}\),",
         f"    SPECIES_SHINY_PAL({spec['const_short']}, gMonShinyPalette_{pal_sym}),\n"),
    ]
    for rel, anchor, row in table_edits:
        p = pf(*rel.split("/"))
        t = _read(p)
        if row.strip() not in t:
            t = _insert_after_line(t, anchor, row)
            _write(p, t); changed[rel] = True

    # 5. pokemon_icon.c — icon table row + icon palette index row
    p = pf("src", "pokemon_icon.c")
    t = _read(p)
    if f"[{const}] = gMonIcon_{img_sym}," not in t:
        t = _insert_after_line(
            t, rf"\[SPECIES_{base}\]\s*= gMonIcon_{bsym},",
            f"    [{const}] = gMonIcon_{img_sym},\n")
        t = _insert_after_line(
            t, rf"\[SPECIES_{base}\]\s*= {spec['icon_pal']},",
            f"    [{const}] = {spec['icon_pal']},\n")
        _write(p, t); changed["pokemon_icon.c"] = True

    # 6. front/back pic coordinates
    coord_block = (
        f"    [{const}] =\n    {{\n"
        f"        .size = MON_COORDS_SIZE({w}, {h}),\n"
        f"        .y_offset = {yoff},\n    }},\n"
    )
    for rel in ("src/data/pokemon_graphics/front_pic_coordinates.h",
                "src/data/pokemon_graphics/back_pic_coordinates.h"):
        p = pf(*rel.split("/"))
        t = _read(p)
        if not _has(t, const):
            t = _insert_after_block(t, rf"\[SPECIES_{base}\] =\n", coord_block)
            _write(p, t); changed[rel] = True

    return changed


def wire_form_table(project_root, table_name, members):
    """Create/extend form_species_tables.h with a per-base form-ID table, include
    it in pokemon.c (before species_info.h), and set .formSpeciesIdTable on every
    member species' gSpeciesInfo entry. *members* is the ordered species list
    (base first), e.g. ["SPECIES_DEOXYS", "SPECIES_DEOXYS_DEFENSE"]. Idempotent."""
    pf = lambda *a: os.path.join(project_root, *a)
    changed = {}

    fst = pf("src", "data", "pokemon", "form_species_tables.h")
    block = ("static const u16 " + table_name + "[] = {\n"
             + "".join(f"    {m},\n" for m in members)
             + "    FORM_SPECIES_END,\n};\n")
    hdr = "// Per-base alternate-form species tables (PorySuite-Z form system).\n\n"
    if os.path.isfile(fst):
        t = _read(fst)
        pat = re.compile(
            r"static const u16 " + re.escape(table_name) + r"\[\] = \{.*?\};\n",
            re.DOTALL)
        new = pat.sub(block, t, count=1) if pat.search(t) else t.rstrip() + "\n\n" + block
        if new != t:
            _write(fst, new); changed["form_species_tables.h"] = True
    else:
        _write(fst, hdr + block); changed["form_species_tables.h"] = True

    pc = pf("src", "pokemon.c")
    t = _read(pc)
    inc = '#include "data/pokemon/form_species_tables.h"'
    if inc not in t:
        _write(pc, t.replace(
            '#include "data/pokemon/species_info.h"',
            inc + '\n#include "data/pokemon/species_info.h"', 1))
        changed["pokemon.c"] = True

    si = pf("src", "data", "pokemon", "species_info.h")
    t = _read(si)
    field = f".formSpeciesIdTable = {table_name},"
    for m in members:
        mm = re.search(rf"\[{m}\] =\n", t)
        if not mm:
            continue
        close = re.search(r"\n    \},\n", t[mm.end():])
        if not close or "formSpeciesIdTable" in t[mm.end():mm.end() + close.start()]:
            continue
        pos = mm.end() + close.start()
        t = t[:pos] + "\n        " + field + t[pos:]
        changed[f"species_info:{m}"] = True
    _write(si, t)
    return changed


def _insert_first_entry(text, array_name, entry):
    """Insert *entry* as the FIRST element of a designated-initializer array
    (right after its opening brace). Designated indices are order-independent,
    so this needs no trailing-comma fixups and works regardless of where the
    base entry sits. Idempotent."""
    if entry in text:
        return text
    m = re.search(re.escape(array_name) + r"\b.*?\{", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"widen: opening brace for {array_name} not found")
    return text[:m.end()] + "\n    " + entry + text[m.end():]


def _upsert_first_entry(text, array_name, entry):
    """Like _insert_first_entry, but if a row with the SAME [index] already
    exists (possibly carrying a stale value), drop it first so the regenerated
    value replaces it instead of duplicating the key. Idempotent."""
    if entry in text:
        return text
    key = entry[:entry.index("]") + 1]  # "[SPECIES_X]" or "[SPECIES_X - 1]"
    # Drop the stale row. The value may span MULTIPLE lines (e.g. a tutor bitmask:
    # `TUTOR(A)\n | TUTOR(B),`), so match non-greedily through newlines up to the
    # `,` that ends the entry (followed by the next `[index]` or the closing `}`).
    # A single-line `[^\n]*` here would leave a stale multi-line entry behind and
    # duplicate the key. Mirrors _remove_indexed_entry.
    text = re.sub(r"[ \t]*" + re.escape(key) + r"\s*=\s*.*?,\n(?=\s*(?:\[|\}))",
                  "", text, count=1, flags=re.DOTALL)
    return _insert_first_entry(text, array_name, entry)


def _fix_species_clamps(project_root):
    """Raise the display/graphics species clamps from NUM_SPECIES to
    NUM_TOTAL_SPECIES. pokefirered sanitizes any ``species > NUM_SPECIES`` to
    SPECIES_NONE in the name / icon / front-pic getters (it shows the ?-sprite
    and a blank ``??????????`` name), which garbles every appended form-species
    in the party / summary / battle. Idempotent — ``NUM_TOTAL_SPECIES`` does not
    contain the ``NUM_SPECIES`` token, so a re-run finds nothing to replace."""
    pf = lambda *a: os.path.join(project_root, *a)
    changed = {}
    for rel in ("src/battle_anim_mons.c", "src/battle_main.c", "src/decompress.c",
                "src/pokemon.c", "src/pokemon_icon.c"):
        p = pf(*rel.split("/"))
        if not os.path.isfile(p):
            continue
        t = _read(p)
        nt = t.replace("species > NUM_SPECIES", "species > NUM_TOTAL_SPECIES")
        if nt != t:
            _write(p, nt); changed[rel] = True
    return changed


def widen_gameplay_tables(project_root, spec):
    """Phase 3c — make a form species runtime-safe: bump the hard-sized
    [NUM_SPECIES] gameplay arrays to NUM_TOTAL_SPECIES and give the form an
    entry (sharing the base's data) in the pointer / implicitly-sized tables so
    nothing indexes out of bounds or dereferences NULL when the form is used in
    a party. Idempotent."""
    pf = lambda *a: os.path.join(project_root, *a)
    form = spec["form"]
    changed = {}

    # 1. NUM_TOTAL_SPECIES (the array bound) = highest form id + 1. Regenerated
    # each call so adding more forms grows the bound.
    if _set_num_total_species(pf("include", "constants", "species.h")):
        changed["species.h"] = True

    # 2. resize the explicit [NUM_SPECIES] arrays.
    resizes = [
        (pf("src", "data", "pokemon", "evolution.h"),
         "gEvolutionTable[NUM_SPECIES][EVOS_PER_MON]",
         "gEvolutionTable[NUM_TOTAL_SPECIES][EVOS_PER_MON]"),
        (pf("src", "data", "pokemon", "level_up_learnset_pointers.h"),
         "gLevelUpLearnsets[NUM_SPECIES]", "gLevelUpLearnsets[NUM_TOTAL_SPECIES]"),
        (pf("src", "data", "pokemon_graphics", "enemy_mon_elevation.h"),
         "gEnemyMonElevation[NUM_SPECIES]", "gEnemyMonElevation[NUM_TOTAL_SPECIES]"),
        (pf("src", "pokemon.c"),
         "sSpeciesToHoennPokedexNum[NUM_SPECIES - 1]",
         "sSpeciesToHoennPokedexNum[NUM_TOTAL_SPECIES - 1]"),
        (pf("src", "pokemon.c"),
         "sSpeciesToNationalPokedexNum[NUM_SPECIES - 1]",
         "sSpeciesToNationalPokedexNum[NUM_TOTAL_SPECIES - 1]"),
        # extern declarations that carry the size must match the definition
        (pf("include", "data.h"),
         "gEnemyMonElevation[NUM_SPECIES]", "gEnemyMonElevation[NUM_TOTAL_SPECIES]"),
    ]
    for path, old, new in resizes:
        t = _read(path)
        if old in t:
            _write(path, t.replace(old, new, 1)); changed[old] = True

    # 3. form entries (shared with the base) in the pointer / implicit tables.
    entries = [
        (pf("src", "data", "pokemon", "level_up_learnset_pointers.h"),
         "gLevelUpLearnsets", f"[{form}] = {spec['learnset_sym']},"),
        (pf("src", "data", "pokemon_graphics", "footprint_table.h"),
         "gMonFootprintTable", f"[{form}] = {spec['footprint_sym']},"),
        (pf("src", "data", "text", "species_names.h"),
         "gSpeciesNames", f'[{form}] = _("{spec["name"]}"),'),
        (pf("src", "data", "pokemon", "tmhm_learnsets.h"),
         "sTMHMLearnsets", f"[{form}] = TMHM_LEARNSET(0),"),
        (pf("src", "data", "pokemon", "tutor_learnsets.h"),
         "sTutorLearnsets", f"[{form}] = {spec['tutor']},"),
        (pf("src", "pokemon.c"),
         "sSpeciesToNationalPokedexNum", f"[{form} - 1] = {spec['natdex']},"),
    ]
    for path, arr, entry in entries:
        t = _read(path)
        new_t = _upsert_first_entry(t, arr, entry)
        if new_t != t:
            _write(path, new_t); changed[f"{arr}:entry"] = True

    # Display/graphics clamps reject species > NUM_SPECIES (→ ?-sprite, blank
    # name, garbled icon); raise them so appended forms render everywhere.
    changed.update(_fix_species_clamps(project_root))

    return changed


# ── General form creation (the API the UI calls) ──────────────────────────────


def _set_num_total_species(species_h):
    """Set #define NUM_TOTAL_SPECIES to (NUM_SPECIES + highest_form_offset + 1).
    Regenerated each call so adding forms grows the array bound. Idempotent."""
    t = _read(species_h)
    offsets = [int(n) for n in re.findall(
        r"#define\s+SPECIES_\w+\s+\(\s*NUM_SPECIES\s*\+\s*(\d+)\s*\)", t)]
    if not offsets:
        return False
    define = f"#define NUM_TOTAL_SPECIES (NUM_SPECIES + {max(offsets) + 1})"
    if "NUM_TOTAL_SPECIES" in t:
        new = re.sub(r"#define NUM_TOTAL_SPECIES \([^)]*\)", define, t, count=1)
    else:
        new = _insert_after_line(t, r"#define SPECIES_UNOWN_QMARK ",
                                 "\n" + define + "\n")
    if new == t:
        return False
    _write(species_h, new)
    return True


def _next_form_id(project_root):
    """Next free form-id offset = max existing (NUM_SPECIES + N) + 1."""
    t = _read(os.path.join(project_root, "include", "constants", "species.h"))
    offsets = [int(n) for n in re.findall(
        r"#define\s+SPECIES_\w+\s+\(\s*NUM_SPECIES\s*\+\s*(\d+)\s*\)", t)]
    return (max(offsets) + 1) if offsets else 1


def _form_members(project_root, table_name, base_const, new_form):
    """Ordered member list for a base's form table: base first, existing forms,
    then the new one (deduped)."""
    fst = os.path.join(project_root, "src", "data", "pokemon", "form_species_tables.h")
    members = [base_const]
    if os.path.isfile(fst):
        m = re.search(re.escape(table_name) + r"\[\] = \{(.*?)\};",
                      _read(fst), re.DOTALL)
        if m:
            # \b avoids matching the SPECIES_END *inside* FORM_SPECIES_END; the
            # _END filter drops any stray sentinel token from an older table.
            members = [s for s in re.findall(r"\bSPECIES_\w+", m.group(1))
                       if s not in ("SPECIES_END", "FORM_SPECIES_END")]
    if base_const not in members:
        members.insert(0, base_const)
    if new_form not in members:
        members.append(new_form)
    return members


def _parse_base(project_root, base_const):
    """Read a base species' shared data from the engine so a form can inherit
    it (graphics symbol, learnset, footprint, name, dex number, coordinates)."""
    pf = lambda *a: os.path.join(project_root, *a)
    base = base_const[len("SPECIES_"):]
    out = {"base": base, "natdex": "NATIONAL_DEX_" + base, "coords": (64, 64, 10)}

    m = re.search(rf"SPECIES_SPRITE\({base},\s*gMonFrontPic_(\w+)\)",
                  _read(pf("src", "data", "pokemon_graphics", "front_pic_table.h")))
    out["base_sym"] = m.group(1) if m else base.title().replace("_", "")

    m = re.search(rf"\[{base_const}\]\s*=\s*(\w+),",
                  _read(pf("src", "data", "pokemon", "level_up_learnset_pointers.h")))
    out["learnset_sym"] = m.group(1) if m else "sNoneLevelUpLearnset"

    m = re.search(rf"\[{base_const}\]\s*=\s*(\w+),",
                  _read(pf("src", "data", "pokemon_graphics", "footprint_table.h")))
    out["footprint_sym"] = m.group(1) if m else "gMonFootprint_Bulbasaur"

    m = re.search(rf'\[{base_const}\]\s*=\s*_\("([^"]*)"\)',
                  _read(pf("src", "data", "text", "species_names.h")))
    out["name"] = m.group(1) if m else base

    # the base's full tutor bitmask (TUTOR(A) | TUTOR(B) | ...), so the form
    # shares it; bare 0 (empty mask) if the base has no tutor entry.
    m = re.search(
        rf"\[{base_const}\]\s*=\s*(TUTOR\(.*?\)(?:\s*\|\s*TUTOR\(.*?\))*)",
        _read(pf("src", "data", "pokemon", "tutor_learnsets.h")), re.DOTALL)
    out["tutor"] = m.group(1) if m else "0"

    m = re.search(
        rf"\[{base_const}\]\s*=\s*\{{\s*\.size = MON_COORDS_SIZE\((\d+),\s*(\d+)\),"
        rf"\s*\.y_offset = (\d+)",
        _read(pf("src", "data", "pokemon_graphics", "front_pic_coordinates.h")),
        re.DOTALL)
    if m:
        out["coords"] = (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.search(rf"\[{base_const}\]\s*=\s*(\d+),",
                  _read(pf("src", "pokemon_icon.c")))
    out["icon_pal"] = m.group(1) if m else "0"
    return out


def _extract_frame_png(src, dst, frame):
    """Crop frame *frame* (a width×width square) of a stacked indexed PNG to
    *dst*, preserving the indexed colour table. Falls back to copying the whole
    sheet if the image lib is unavailable or the load fails."""
    try:
        from PyQt6.QtGui import QImage
        img = QImage(src)
        if img.isNull():
            raise RuntimeError("load failed")
        w = img.width()
        n = max(1, img.height() // w) if w else 1
        i = max(0, min(int(frame), n - 1))
        img.copy(0, i * w, w, w).save(dst, "PNG")
    except Exception:
        import shutil
        shutil.copy2(src, dst)


def _seed_form_graphics(project_root, base_sym, slug, frame, own_image, own_palette):
    """Seed the form's own files in graphics/pokemon/<slug>/ from the base: copy
    the base's normal/shiny .pal (own palette), and extract frame N of the base's
    front/back sheet + copy the base's icon (own image). Base source paths come
    from species_graphics.json. Skips files that already exist (so a user's edits
    to a re-added form survive)."""
    import json
    import shutil
    sgj = os.path.join(project_root, "src", "data", "species_graphics.json")
    d = json.load(open(sgj, encoding="utf-8")) if os.path.isfile(sgj) else {}

    def base_src(key):
        e = d.get(f"{key}_{base_sym}")
        if e and e.get("png"):
            return os.path.join(project_root, e["png"].replace("/", os.sep))
        return None

    dst_dir = os.path.join(project_root, "graphics", "pokemon", slug)
    os.makedirs(dst_dir, exist_ok=True)

    if own_palette:
        for leaf, key in (("normal.pal", "gMonPalette"),
                          ("shiny.pal", "gMonShinyPalette")):
            src = base_src(key)
            dst = os.path.join(dst_dir, leaf)
            if src and os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)

    if own_image:
        front = base_src("gMonFrontPic")
        base_dir = os.path.dirname(front) if front else None
        for leaf, key in (("front.png", "gMonFrontPic"),
                          ("back.png", "gMonBackPic")):
            src = base_src(key)
            dst = os.path.join(dst_dir, leaf)
            if src and os.path.isfile(src) and not os.path.isfile(dst):
                _extract_frame_png(src, dst, frame)
        idst = os.path.join(dst_dir, "icon.png")
        if not os.path.isfile(idst):
            isrc = base_src("gMonIcon") or (
                os.path.join(base_dir, "icon.png") if base_dir else None)
            if isrc and os.path.isfile(isrc):
                shutil.copy2(isrc, idst)


def _write_species_graphics(project_root, sym, slug, image=False, palette=False):
    """Add the form's own gMon*_<sym> → {png} entries to species_graphics.json —
    the app's image-name → source-file map, the piece that makes a form's own art
    / palette actually show in the editor. Idempotent."""
    import json
    p = os.path.join(project_root, "src", "data", "species_graphics.json")
    if not os.path.isfile(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    folder = f"graphics/pokemon/{slug}"
    add = {}
    if image:
        add[f"gMonFrontPic_{sym}"] = {"png": f"{folder}/front.png"}
        add[f"gMonBackPic_{sym}"] = {"png": f"{folder}/back.png"}
        add[f"gMonIcon_{sym}"] = {"png": f"{folder}/icon.png"}
    if palette:
        add[f"gMonPalette_{sym}"] = {"png": f"{folder}/normal.pal"}
        add[f"gMonShinyPalette_{sym}"] = {"png": f"{folder}/shiny.pal"}
    changed = False
    for k, v in add.items():
        if d.get(k) != v:
            d[k] = v
            changed = True
    if changed:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return add


def create_form(project_root, base_const, suffix, stats,
                own_image=False, own_palette=False):
    """Create SPECIES_{BASE}_{SUFFIX} as a form of *base_const*.

    Graphics are two independent choices:
      own_image   — the form has its OWN front/back sheet (seeded by extracting
                    its frame from the base); otherwise it renders a FRAME of the
                    base's shared sheet (frame index = its position in the form
                    table; the engine copies that frame over frame 0).
      own_palette — the form has its OWN normal/shiny .pal (seeded from the base,
                    for the user to recolour); otherwise it shares the base's.
    Differs from the base only by *stats*. Returns the new form const. Idempotent."""
    base = _parse_base(project_root, base_const)
    base_name = base_const[len("SPECIES_"):]
    suf = suffix.upper()
    form_const = f"SPECIES_{base_name}_{suf}"
    form_sym = base["base_sym"] + "".join(p.title() for p in suffix.split("_"))
    slug = f"{base_name}_{suf}".lower()
    table_name = f"s{base['base_sym']}FormSpeciesIdTable"
    members = _form_members(project_root, table_name, base_const, form_const)
    # frame in the base's stacked sheet = the form's position in the form table
    frame = members.index(form_const) if form_const in members else max(1, len(members) - 1)

    spec = {
        "const": form_const, "const_short": f"{base_name}_{suf}",
        "id_expr": f"(NUM_SPECIES + {_next_form_id(project_root)})",
        "base": base_name, "base_sym": base["base_sym"], "sym": form_sym,
        "share_image": not own_image, "share_palette": not own_palette,
        "coords": base["coords"], "stats": stats, "icon_pal": base["icon_pal"],
    }
    if own_image or own_palette:
        _seed_form_graphics(project_root, base["base_sym"], slug, frame,
                            own_image, own_palette)
        if own_image:
            spec["front"] = f"pokemon/{slug}/front.4bpp.lz"
            spec["back"] = f"pokemon/{slug}/back.4bpp.lz"
            spec["icon"] = (f"pokemon/{slug}/icon.4bpp",)
        if own_palette:
            spec["pal"] = f"pokemon/{slug}/normal.gbapal.lz"
            spec["shiny"] = f"pokemon/{slug}/shiny.gbapal.lz"
        _write_species_graphics(project_root, form_sym, slug,
                                image=own_image, palette=own_palette)

    append_species(project_root, spec)
    wire_form_table(project_root, table_name, members)
    widen_gameplay_tables(project_root, {
        "form": form_const, "learnset_sym": base["learnset_sym"],
        "footprint_sym": base["footprint_sym"], "name": base["name"],
        "tutor": base["tutor"], "natdex": base["natdex"],
    })
    return form_const


def _remove_block(text, index_token):
    """Remove a designated `[index] =\\n{ ... },` initializer block. The block has
    no nested newline-`},` (struct fields + inline `.types = {..}` are one-liners),
    so a non-greedy match to the first `\\n    },` close is exact."""
    pat = re.compile(r"[ \t]*\[" + re.escape(index_token) + r"\] =\n\s*\{.*?\n\s*\},\n",
                     re.DOTALL)
    return pat.sub("", text, count=1)


def _remove_indexed_entry(text, index_token, count=0):
    """Remove `[index] = <value>,` rows (value may span lines, e.g. a tutor
    bitmask). count=0 removes every matching row (the icon file has two)."""
    pat = re.compile(
        r"[ \t]*\[" + re.escape(index_token) + r"\]\s*=\s*.*?,\n(?=\s*(?:\[|\}))",
        re.DOTALL)
    return pat.sub("", text, count=count)


def _remove_line(text, prefix_pat):
    """Remove the first whole line whose content starts with prefix_pat (a regex)."""
    return re.sub(r"[ \t]*" + prefix_pat + r"[^\n]*\n", "", text, count=1)


def delete_form(project_root, form_const):
    """Inverse of create_form: remove a form species entirely — its constant,
    gSpeciesInfo block, graphics/icon/coordinate rows, gameplay entries, and
    form-table membership; regenerate the base's form table + NUM_TOTAL_SPECIES;
    and if the form had its OWN art (now unreferenced), drop its INCBINs/externs.
    Idempotent: a no-op if the form is already absent."""
    pf = lambda *a: os.path.join(project_root, *a)
    short = form_const[len("SPECIES_"):]
    changed = {}

    # remember the graphics symbol this form pointed at (to decide art cleanup)
    gfx_sym = None
    mm = re.search(rf"SPECIES_SPRITE\({short},\s*gMonFrontPic_(\w+)\)",
                   _read(pf("src", "data", "pokemon_graphics", "front_pic_table.h")))
    if mm:
        gfx_sym = mm.group(1)

    # locate the owning base + form table
    fst = pf("src", "data", "pokemon", "form_species_tables.h")
    base_const = table_name = None
    if os.path.isfile(fst):
        for tm in re.finditer(r"static const u16 (\w+)\[\] = \{(.*?)\};",
                              _read(fst), re.DOTALL):
            mem = [s for s in re.findall(r"\bSPECIES_\w+", tm.group(2))
                   if s not in ("SPECIES_END", "FORM_SPECIES_END")]
            if form_const in mem:
                table_name, base_const = tm.group(1), mem[0]
                break

    # 1. struct-block tables: gSpeciesInfo stats + front/back coordinates
    for rel in ("src/data/pokemon/species_info.h",
                "src/data/pokemon_graphics/front_pic_coordinates.h",
                "src/data/pokemon_graphics/back_pic_coordinates.h"):
        p = pf(*rel.split("/")); t = _read(p); nt = _remove_block(t, form_const)
        if nt != t:
            _write(p, nt); changed[rel] = True

    # 1b. Layer B form-change tables: remove the form's OWN table + field, and strip
    #     any rule in ANY other species' table that TARGETS this form. Otherwise the
    #     deleted constant lingers as a dangling reference and form_change_tables.h
    #     fails to compile. (delete_form is for created form-species; unlink keeps
    #     the species so its const stays defined — that path doesn't dangle.)
    wire_form_change_table(project_root, form_const, [])   # its own table + field
    fct_b = pf("src", "data", "pokemon", "form_change_tables.h")
    if os.path.isfile(fct_b):
        t = _read(fct_b)
        # Rows are single-line `{METHOD, TARGET, PARAM},`; drop those whose TARGET is
        # this form. The `\s*,` right after the const blocks a prefix-name false match
        # (SPECIES_X vs SPECIES_X_Y).
        nt = re.sub(r"[ \t]*\{[^{}\n]*,\s*" + re.escape(form_const) +
                    r"\s*,[^{}\n]*\},\n", "", t)
        if nt != t:
            _write(fct_b, nt); changed["form_change_tables.h:targets"] = True

    # 2. designated-index gameplay rows (icon file has two: icon + pal index)
    for rel, tok, cnt in [
        ("src/data/pokemon/level_up_learnset_pointers.h", form_const, 1),
        ("src/data/pokemon_graphics/footprint_table.h", form_const, 1),
        ("src/data/text/species_names.h", form_const, 1),
        ("src/data/pokemon/tmhm_learnsets.h", form_const, 1),
        ("src/data/pokemon/tutor_learnsets.h", form_const, 1),
        ("src/pokemon.c", form_const + " - 1", 1),
        ("src/pokemon_icon.c", form_const, 0),
    ]:
        p = pf(*rel.split("/")); t = _read(p); nt = _remove_indexed_entry(t, tok, cnt)
        if nt != t:
            _write(p, nt); changed[rel] = True

    # 3. graphics SPECIES_SPRITE / SPECIES_PAL rows
    for rel, prefix in [
        ("src/data/pokemon_graphics/front_pic_table.h", rf"SPECIES_SPRITE\({short},"),
        ("src/data/pokemon_graphics/back_pic_table.h", rf"SPECIES_SPRITE\({short},"),
        ("src/data/pokemon_graphics/palette_table.h", rf"SPECIES_PAL\({short},"),
        ("src/data/pokemon_graphics/shiny_palette_table.h", rf"SPECIES_SHINY_PAL\({short},"),
    ]:
        p = pf(*rel.split("/")); t = _read(p); nt = _remove_line(t, prefix)
        if nt != t:
            _write(p, nt); changed[rel] = True

    # 4. the species constant
    sp = pf("include", "constants", "species.h"); t = _read(sp)
    nt = re.sub(rf"#define {re.escape(form_const)} \(NUM_SPECIES \+ \d+\)\n", "", t, count=1)
    if nt != t:
        _write(sp, nt); changed["species.h:const"] = True

    # 5. form-table membership: regenerate without this form, or drop the table
    #    (and the base's field) if it was the base's last form.
    if table_name and base_const:
        m = re.search(re.escape(table_name) + r"\[\] = \{(.*?)\};", _read(fst), re.DOTALL)
        members = ([s for s in re.findall(r"\bSPECIES_\w+", m.group(1))
                    if s not in ("SPECIES_END", "FORM_SPECIES_END")] if m else [])
        remaining = [x for x in members if x != form_const]
        if [x for x in remaining if x != base_const]:
            wire_form_table(project_root, table_name, remaining)
            changed["form_species_tables.h"] = True
        else:
            ft = _read(fst)
            ft2 = re.sub(r"static const u16 " + re.escape(table_name) +
                         r"\[\] = \{.*?\};\n\n?", "", ft, count=1, flags=re.DOTALL)
            if ft2 != ft:
                _write(fst, ft2); changed["form_species_tables.h"] = True
            si = pf("src", "data", "pokemon", "species_info.h"); st = _read(si)
            st2 = re.sub(r"[ \t]*\.formSpeciesIdTable = " + re.escape(table_name) +
                         r",\n", "", st)
            if st2 != st:
                _write(si, st2); changed["species_info.h:base_field"] = True

    # 6. shrink NUM_TOTAL_SPECIES back to the new highest form id + 1
    if _set_num_total_species(pf("include", "constants", "species.h")):
        changed["species.h:num_total"] = True

    # 7. if the form had its OWN art and nothing else references that symbol now,
    #    drop the INCBINs (pokemon.h) + externs (graphics.h). Share-graphics forms
    #    point at the base's symbol, which stays referenced → skipped.
    if gfx_sym:
        refs = sum(_read(pf("src", "data", "pokemon_graphics", r)).count(f"_{gfx_sym})")
                   for r in ("front_pic_table.h", "back_pic_table.h",
                             "palette_table.h", "shiny_palette_table.h"))
        if refs == 0:
            syms = (f"gMonFrontPic_{gfx_sym}", f"gMonBackPic_{gfx_sym}",
                    f"gMonPalette_{gfx_sym}", f"gMonShinyPalette_{gfx_sym}",
                    f"gMonIcon_{gfx_sym}")
            for rel in ("src/data/graphics/pokemon.h", "include/graphics.h"):
                p = pf(*rel.split("/")); t = _read(p); nt = t
                for s in syms:
                    nt = re.sub(r"[^\n]*\b" + re.escape(s) + r"\b[^\n]*\n", "", nt)
                if nt != t:
                    _write(p, nt); changed[rel] = True

            # 8. species_graphics.json manifest — drop the same own-art symbols so
            #    the (gitignored) image map never keeps orphan entries pointing at
            #    a deleted form's folder (the gap that left stale DeoxysAttack/
            #    DeoxysDefense keys behind earlier).
            sgj = pf("src", "data", "species_graphics.json")
            if os.path.isfile(sgj):
                try:
                    import json as _json
                    with open(sgj, "r", encoding="utf-8") as f:
                        sg = _json.load(f)
                    removed = [k for k in syms if k in sg]
                    for k in removed:
                        del sg[k]
                    if removed:
                        with open(sgj, "w", encoding="utf-8", newline="\n") as f:
                            _json.dump(sg, f, indent=2, ensure_ascii=False)
                            f.write("\n")
                        changed["species_graphics.json"] = True
                except Exception:
                    logging.getLogger(__name__).exception(
                        "delete_form: species_graphics.json cleanup failed")

    return changed


# ── Linking an EXISTING species as a form (no new species created) ───────────


def form_family_of(project_root, species_const):
    """Name of the form-family table *species_const* already belongs to (base or
    any member), or None. Used to warn before linking a species that is already
    part of another family — a species can belong to only one."""
    fst = os.path.join(project_root, "src", "data", "pokemon", "form_species_tables.h")
    if not os.path.isfile(fst):
        return None
    for tm in re.finditer(r"static const u16 (\w+)\[\] = \{(.*?)\};",
                          _read(fst), re.DOTALL):
        members = [s for s in re.findall(r"\bSPECIES_\w+", tm.group(2))
                   if s not in ("SPECIES_END", "FORM_SPECIES_END")]
        if species_const in members:
            return tm.group(1)
    return None


def link_existing_form(project_root, base_const, existing_const):
    """Link an EXISTING species into *base_const*'s form family WITHOUT creating a
    new species. The linked species keeps its own data + graphics; it just joins
    the family — its ``.formSpeciesIdTable`` is set to the base's table, so a form
    change INTO it reverts back to the base. Returns the new member list.
    Idempotent. apply_form_system must have run (the struct field must exist)."""
    base = _parse_base(project_root, base_const)
    table_name = f"s{base['base_sym']}FormSpeciesIdTable"
    members = _form_members(project_root, table_name, base_const, existing_const)
    wire_form_table(project_root, table_name, members)
    return members


def unlink_form(project_root, base_const, member_const):
    """Inverse of link_existing_form: drop *member_const* from *base_const*'s form
    family WITHOUT deleting the species (it keeps its own dex entry). Regenerates
    the table (or removes it + the base's field when only the base remains) and
    clears the unlinked member's own ``.formSpeciesIdTable``. Returns True if
    changed. Use this instead of delete_form for a LINKED (pre-existing) member."""
    pf = lambda *a: os.path.join(project_root, *a)
    base = _parse_base(project_root, base_const)
    table_name = f"s{base['base_sym']}FormSpeciesIdTable"
    fst = pf("src", "data", "pokemon", "form_species_tables.h")
    if not os.path.isfile(fst):
        return False
    m = re.search(re.escape(table_name) + r"\[\] = \{(.*?)\};", _read(fst), re.DOTALL)
    if not m:
        return False
    members = [s for s in re.findall(r"\bSPECIES_\w+", m.group(1))
               if s not in ("SPECIES_END", "FORM_SPECIES_END")]
    if member_const not in members:
        return False
    remaining = [x for x in members if x != member_const]

    # Clear the family field from EVERY member of this table (it sits last in each
    # gSpeciesInfo block, so there's no trailing newline). The survivors get it
    # back when the table is regenerated below, so only the unlinked member ends
    # up without it.
    si = pf("src", "data", "pokemon", "species_info.h")
    st = _read(si)
    st2 = re.sub(rf"\n[ \t]*\.formSpeciesIdTable = {re.escape(table_name)},", "", st)
    if st2 != st:
        _write(si, st2)

    # Regenerate the table without the member (re-adds the field to the
    # survivors), or drop the table entirely when only the base is left.
    if [x for x in remaining if x != base_const]:
        wire_form_table(project_root, table_name, remaining)
    else:
        ft = _read(fst)
        ft2 = re.sub(r"static const u16 " + re.escape(table_name) +
                     r"\[\] = \{.*?\};\n\n?", "", ft, count=1, flags=re.DOTALL)
        if ft2 != ft:
            _write(fst, ft2)
    return True


# ── Layer B: in-game form-change tables (the data the resolver reads) ─────────


def _fc_table_name(species_const):
    """Form-change table symbol for a species, e.g. sDeoxysDefenseFormChangeTable."""
    short = species_const[len("SPECIES_"):]
    return "s" + "".join(p.title() for p in short.split("_")) + "FormChangeTable"


def wire_form_change_table(project_root, species_const, entries):
    """Create / replace / remove *species_const*'s in-game form-change table in
    form_change_tables.h, include that header in pokemon.c, and set (or clear)
    ``.formChangeTable`` on the species' gSpeciesInfo entry. *entries* is a list
    of dicts ``{method, target, param}`` (strings); an empty list removes the
    table + field. Idempotent."""
    pf = lambda *a: os.path.join(project_root, *a)
    changed = {}
    table_name = _fc_table_name(species_const)
    fct = pf("src", "data", "pokemon", "form_change_tables.h")
    # The table rows reference FORM_CHANGE_* methods plus ITEM_*/WEATHER_* params,
    # so the file pulls those constant headers itself (pokemon.c doesn't include
    # weather.h). Time-of-day params are plain ints and need no header.
    hdr = ("// Per-species in-game form-change tables (PorySuite-Z Layer B).\n"
           '#include "constants/form_change_types.h"\n'
           '#include "constants/items.h"\n'
           '#include "constants/weather.h"\n'
           '#include "constants/flags.h"\n'
           '#include "constants/battle.h"\n\n')

    block = None
    if entries:
        rows = "".join(
            "    {{{}, {}, {}}},\n".format(e["method"], e["target"], e.get("param", "0"))
            for e in entries)
        block = (f"static const struct FormChange {table_name}[] = {{\n"
                 f"{rows}    {{FORM_CHANGE_END}},\n}};\n")

    # 1. form_change_tables.h — create / replace / remove the table block. The
    #    header (comment + the FORM_CHANGE_* include) is normalized every call so
    #    the file is self-contained — pokemon.c does not include form_change_types.h.
    raw = _read(fct) if os.path.isfile(fct) else ""
    body = re.sub(r'\A// Per-species[^\n]*\n', "", raw)
    body = re.sub(
        r'#include "constants/(form_change_types|items|weather|flags|battle)\.h"\n',
        "", body).lstrip("\n")
    pat = re.compile(r"static const struct FormChange " + re.escape(table_name) +
                     r"\[\] = \{.*?\};\n", re.DOTALL)
    if block:
        if pat.search(body):
            body = pat.sub(block, body, count=1)
        else:
            body = (body.rstrip() + "\n\n" + block) if body.strip() else block
    else:
        body = pat.sub("", body, count=1)
    new = (hdr + body) if body.strip() else ""
    if new != raw:
        _write(fct, new); changed["form_change_tables.h"] = True

    # 2. include in pokemon.c before species_info.h (once; only when a table exists)
    pc = pf("src", "pokemon.c")
    t = _read(pc)
    inc = '#include "data/pokemon/form_change_tables.h"'
    if block and inc not in t:
        _write(pc, t.replace(
            '#include "data/pokemon/species_info.h"',
            inc + '\n#include "data/pokemon/species_info.h"', 1))
        changed["pokemon.c"] = True

    # 3. set / clear .formChangeTable on the species' gSpeciesInfo entry
    si = pf("src", "data", "pokemon", "species_info.h")
    t = _read(si)
    field = f".formChangeTable = {table_name},"
    if block:
        mm = re.search(rf"\[{species_const}\] =\n", t)
        if mm:
            close = re.search(r"\n    \},\n", t[mm.end():])
            seg = t[mm.end():mm.end() + close.start()] if close else ""
            if close and "formChangeTable" not in seg:
                pos = mm.end() + close.start()
                t = t[:pos] + "\n        " + field + t[pos:]
                _write(si, t); changed["species_info.h"] = True
    else:
        nt = re.sub(r"[ \t]*\.formChangeTable = " + re.escape(table_name) + r",\n",
                    "", t, count=1)
        if nt != t:
            _write(si, nt); changed["species_info.h"] = True

    return changed


def set_form_change(project_root, species_const, entries):
    """UI-facing: ensure Layer B infrastructure is present, LINK every rule target
    into *species_const*'s form family, then write its in-game form-change table.
    *entries* is a list of dicts ``{method, target, param}``; an empty list removes
    the table.

    The auto-link is essential: a rule "X becomes Y" makes Y a form of X, and the
    in-game resolver keys off ``.formSpeciesIdTable`` (it bails immediately when the
    species has none) — so without wiring the family table, NOTHING morphs (and a
    morphed form could never revert, since the target needs the family to find its
    base). This makes the editor's "Becomes: any mon in the dex" actually work: pick
    any species as a target and it's linked as a form on save."""
    from core.form_change_patch import apply_form_change_system
    apply_form_change_system(project_root)
    targets = [e["target"] for e in entries
               if e.get("target") and e["target"] != species_const]
    if targets:
        base = _parse_base(project_root, species_const)
        table_name = f"s{base['base_sym']}FormSpeciesIdTable"
        members = _form_members(project_root, table_name, species_const, species_const)
        for tgt in targets:
            if tgt not in members:
                members.append(tgt)
        wire_form_table(project_root, table_name, members)
    return wire_form_change_table(project_root, species_const, entries)


def read_form_changes(project_root, species_const):
    """Parse a species' current in-game form-change entries from
    form_change_tables.h. Returns a list of {method, target, param} (strings);
    empty if the species has no table."""
    fct = os.path.join(project_root, "src", "data", "pokemon", "form_change_tables.h")
    if not os.path.isfile(fct):
        return []
    m = re.search(re.escape(_fc_table_name(species_const)) + r"\[\] = \{(.*?)\};",
                  _read(fct), re.DOTALL)
    if not m:
        return []
    out = []
    for row in re.finditer(r"\{([^}]*)\}", m.group(1)):
        parts = [p.strip() for p in row.group(1).split(",") if p.strip()]
        if not parts or parts[0] == "FORM_CHANGE_END":
            continue
        out.append({"method": parts[0],
                    "target": parts[1] if len(parts) > 1 else "",
                    "param": parts[2] if len(parts) > 2 else "0"})
    return out
