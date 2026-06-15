"""core/legacy_forme_patch.py — retire a species' VANILLA legacy forme system.

pokefirered ships exactly one mon (Deoxys) whose alternate formes are hardcoded
into the engine the old way:

  * a per-build base-stat array ``s<Camel>BaseStats`` (under ``#ifdef FIRERED`` /
    ``LEAFGREEN``) applied at battle time by ``Get<Camel>Stat`` (read by
    ``GetMonData``) and ``Set<Camel>Stats`` (called from ``battle_main.c``), and
  * a sprite-sheet frame copy ``Duplicate<Camel>Tiles`` (decompress.c) that swaps
    the displayed 64x64 frame.

That system is build-locked (one forme per ROM version) and not editable. This
module *strips* it so the species becomes a plain single-forme mon — its
``gSpeciesInfo`` stats and frame-0 sprite — which is the clean starting point for
either (a) repurposing the slot into an ordinary creature, or (b) modernizing it
into real, editable forme-species linked by the form table.

Everything is keyed off the species CONSTANT → its CamelCase graphics name, never
a hardcoded "Deoxys", so it works on a renamed slot or any project that ported
the same pattern. It is idempotent (re-running changes nothing) and build-safe:

  * ``Get<Camel>Stat`` callers are all ``ret = Get…(); if (!ret) ret = mon->X;``
    so removing the call + falling back to ``mon->X`` is behaviour-preserving for
    a normal mon.
  * ``Set<Camel>Stats`` only rewrote Deoxys party stats; dropping it + its calls
    leaves every other species untouched.
  * ``Duplicate<Camel>Tiles`` stays as an (empty) hook — the form-system patcher
    fills it with generalized frame logic when real formes need it — so general
    sprite decompression keeps calling a valid function.

``ShouldIgnore<Camel>Form`` is intentionally LEFT ALONE: it only selects which
sprite to load in link/trade contexts, is referenced by five unrelated sites, and
does not touch the stat arrays or functions being removed.

Game source is only ever edited through this patcher, never by hand.
"""

import os
import re


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _camel(const):
    """SPECIES_DEOXYS -> Deoxys (the graphics/function CamelCase the engine uses)."""
    base = const[len("SPECIES_"):] if const.startswith("SPECIES_") else const
    return "".join(p.capitalize() for p in base.lower().split("_"))


def _remove_ifdef_block_containing(text, needle):
    """Delete the ``#if … #endif`` block (incl. any ``#elif`` arms) that contains
    *needle*. Returns (new_text, changed). Index-based — robust to the two-arm
    ``#if FIRERED / #elif LEAFGREEN / #endif`` shape that wraps the stat arrays."""
    idx = text.find(needle)
    if idx < 0:
        return text, False
    start = text.rfind("\n#if", 0, idx)
    if start < 0:
        return text, False
    start += 1  # keep the newline before #if
    # Walk forward honouring nesting to find the matching #endif.
    depth = 0
    i = start
    end = -1
    for m in re.finditer(r"^#(if|ifdef|ifndef|endif)\b", text[start:], re.MULTILINE):
        kind = m.group(1)
        pos = start + m.start()
        if kind in ("if", "ifdef", "ifndef"):
            depth += 1
        elif kind == "endif":
            depth -= 1
            if depth == 0:
                end = start + m.end()
                break
    if end < 0:
        return text, False
    # consume one trailing newline so we don't leave a blank gap
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:], True


def _remove_c_function(text, signature_re):
    """Delete a C function whose signature matches *signature_re* through its
    closing brace at column 0 (``\\n}``). Returns (new_text, changed)."""
    m = re.search(signature_re, text)
    if not m:
        return text, False
    start = m.start()
    # find the function body's closing brace at column 0
    body = text.find("\n{", m.end() - 1)
    if body < 0:
        return text, False
    close = text.find("\n}", body + 2)
    if close < 0:
        return text, False
    end = close + 2
    if end < len(text) and text[end] == "\n":
        end += 1
    # also drop a leading blank line left dangling
    while start > 0 and text[start - 1] == "\n" and (start < 2 or text[start - 2] == "\n"):
        start -= 1
    return text[:start] + text[end:], True


def strip_legacy_forme(project_root, species_const):
    """Retire *species_const*'s legacy forme system. Returns {relpath: True} for
    each file changed (empty dict if already stripped / pattern absent).

    Idempotent and generic. Touches only the stat/frame hooks named after the
    species; every other species and the rest of the engine are untouched.
    """
    camel = _camel(species_const)
    pf = lambda *a: os.path.join(project_root, *a)
    changed = {}

    stat_sym = f"s{camel}BaseStats"
    get_fn = f"Get{camel}Stat"
    set_fn = f"Set{camel}Stats"
    dup_fn = f"Duplicate{camel}Tiles"

    # ── pokemon.c — stat array + GetXStat (def, proto, callers) + SetXStats ──
    pc_path = pf("src", "pokemon.c")
    if os.path.isfile(pc_path):
        t = _read(pc_path)
        orig = t

        # 1) the s<Camel>BaseStats #if/#elif/#endif block (the per-forme stats)
        t, _ = _remove_ifdef_block_containing(t, stat_sym)

        # 2) Get<Camel>Stat static prototype + definition
        t = re.sub(rf"static u16 {re.escape(get_fn)}\([^;]*\);\n", "", t, count=1)
        t, _ = _remove_c_function(t, rf"static u16 {re.escape(get_fn)}\(")

        # 3) the five GetMonData callers:
        #       ret = Get<Camel>Stat(mon, STAT_X);
        #       if (!ret)
        #           ret = mon->field;
        #    → ret = mon->field;   (the normal, non-forme stat)
        t = re.sub(
            rf"(\n)( *)ret = {re.escape(get_fn)}\(mon, STAT_\w+\);\n"
            rf" *if \(!ret\)\n *ret = (mon->\w+);",
            r"\1\2ret = \3;",
            t,
        )

        # 4) Set<Camel>Stats definition
        t, _ = _remove_c_function(t, rf"void {re.escape(set_fn)}\(void\)")

        if t != orig:
            _write(pc_path, t)
            changed["src/pokemon.c"] = True

    # ── include/pokemon.h — Set<Camel>Stats public prototype ──
    ph_path = pf("include", "pokemon.h")
    if os.path.isfile(ph_path):
        t = _read(ph_path)
        nt = re.sub(rf"void {re.escape(set_fn)}\(void\);\n", "", t, count=1)
        if nt != t:
            _write(ph_path, nt)
            changed["include/pokemon.h"] = True

    # ── battle_main.c — the Set<Camel>Stats() call sites ──
    bm_path = pf("src", "battle_main.c")
    if os.path.isfile(bm_path):
        t = _read(bm_path)
        nt = re.sub(rf"[ \t]*{re.escape(set_fn)}\(\);\n", "", t)
        if nt != t:
            _write(bm_path, nt)
            changed["src/battle_main.c"] = True

    # ── decompress.c — neutralize the Duplicate<Camel>Tiles frame copy ──
    # Keep the function + its calls (it's the shared sprite-decompress hook the
    # form-system patcher later fills with generalized frame logic); just remove
    # the legacy `if (species == SPECIES_X) CpuCopy…` body.
    dc_path = pf("src", "decompress.c")
    if os.path.isfile(dc_path):
        t = _read(dc_path)
        body_re = re.compile(
            rf"(static void {re.escape(dup_fn)}\(void \*pointer, s32 species\)\n\{{\n)"
            rf"    if \(species == {re.escape(species_const)}\)\n"
            rf"        CpuCopy32\([^;]*\);\n"
            rf"(\}})"
        )
        repl = (
            r"\1"
            "    // PorySuite-Z: legacy forme frame-copy retired - forme is now a\n"
            "    // real species (the form-system patcher re-fills this if needed).\n"
            "    (void)pointer;\n"
            "    (void)species;\n"
            r"\2"
        )
        nt, n = body_re.subn(repl, t, count=1)
        if n:
            _write(dc_path, nt)
            changed["src/decompress.c"] = True

    return changed


# ── Modernize: vanilla legacy formes → expansion-style forme-species ─────────


def _flat_base_stats(project_root, base_const):
    """Read *base_const*'s species_info from species.json and flatten it into the
    flat stats dict create_form / _species_info_block consume (types → type1/type2,
    eggGroups → eggGroup1/2, abilities → ability1/2). A forme inherits all of this;
    only its six base stats differ. Empty dict if species.json is absent."""
    import json
    p = os.path.join(project_root, "src", "data", "species.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    entry = d.get(base_const) or {}
    si = entry.get("species_info", entry)
    flat = {}
    for k in ("baseHP", "baseAttack", "baseDefense", "baseSpeed", "baseSpAttack",
              "baseSpDefense", "catchRate", "expYield", "evYield_HP", "evYield_Attack",
              "evYield_Defense", "evYield_Speed", "evYield_SpAttack", "evYield_SpDefense",
              "itemCommon", "itemRare", "genderRatio", "eggCycles", "friendship",
              "growthRate", "safariZoneFleeRate", "bodyColor", "noFlip"):
        if k in si:
            flat[k] = si[k]
    types = si.get("types") or []
    if types:
        flat["type1"] = types[0]
        flat["type2"] = types[1] if len(types) > 1 else types[0]
    eggs = si.get("eggGroups") or []
    if eggs:
        flat["eggGroup1"] = eggs[0]
        flat["eggGroup2"] = eggs[1] if len(eggs) > 1 else eggs[0]
    abil = si.get("abilities") or []
    if abil:
        flat["ability1"] = abil[0]
        flat["ability2"] = abil[1] if len(abil) > 1 else "ABILITY_NONE"
    return flat


def _collapse_base_graphics(project_root, camel):
    """Flatten a legacy two-arm ``#ifdef FIRERED / #ifdef LEAFGREEN`` graphics
    block in pokemon.h into a single unconditional definition: keep the FIRST
    arm's sheets (Normal pose = its frame 0), drop the second arm, and single-arg
    the icon (drop the per-forme second icon). Afterwards the base is an ordinary
    single-sheet mon and forme_detect no longer reports legacy formes. No-op if
    already collapsed or the structure differs. Returns bool."""
    p = os.path.join(project_root, "src", "data", "graphics", "pokemon.h")
    if not os.path.isfile(p):
        return False
    t = _read(p)
    foot = rf"const u8 gMonFootprint_{re.escape(camel)}\[\][^\n]*\n"
    pat = re.compile(
        r"#if(?:def)?[^\n]*\n"
        r"((?:[^\n]*\n)*?" + foot + r")"   # arm 1 body (group 1), up to its footprint
        r"#endif\n+"
        r"#if(?:def)?[^\n]*\n"
        r"(?:[^\n]*\n)*?" + foot +         # arm 2 body (discarded)
        r"#endif\n"
    )
    m = pat.search(t)
    if not m:
        return False
    body = re.sub(
        rf'(const u8 gMonIcon_{re.escape(camel)}\[\] = INCBIN_U8\(\s*"[^"]+")[^;]*\);',
        r"\1);", m.group(1))
    nt = t[:m.start()] + body + t[m.end():]
    if nt == t:
        return False
    _write(p, nt)
    return True


def modernize_forme(project_root, species_const):
    """Migrate a vanilla legacy-forme mon (Deoxys-style) into expansion-style
    forme-species: each alternate forme becomes a real, fully-editable species —
    its OWN sprite (sliced from its source sheet/frame) + palette + the forme's
    stats — linked to the base by the form table. The base keeps its Normal stats
    and frame-0 sprite. Opt-in, called per-mon. Returns the created form constants
    (empty list if the mon has no legacy formes / is already modernized).

    Order matters: detect the formes (capturing their per-forme stats) FIRST, apply
    the form system, create the forme-species (the base's #ifdef graphics still
    anchor the inserts), then strip the legacy stat/render hooks, then collapse the
    #ifdef last. Strip runs AFTER create on purpose — stripping removes the legacy
    stat arrays detect_formes reads, so doing it before create would make a re-run
    after a partial failure recreate the formes with the BASE's stats. With strip
    last and create idempotent, a re-run preserves the already-created correct stats.
    """
    from core.forme_detect import detect_formes
    from core.form_system_patch import apply_form_system
    from core import append_species as aps
    import shutil

    camel = _camel(species_const)
    formes = detect_formes(project_root, camel)
    if len(formes) <= 1:
        return []   # no legacy formes to modernize (or already done)

    base_name = species_const[len("SPECIES_"):]
    base_flat = _flat_base_stats(project_root, species_const)
    base_dir = os.path.join(project_root, "graphics", "pokemon", base_name.lower())

    def _abs(rel):
        return os.path.join(project_root, rel.replace("/", os.sep)) if rel else None

    # 1. ensure the form-table infrastructure exists (this generalizes the legacy
    #    tile-duplication via GetSpeciesFormId, superseding the vanilla hardcode, so
    #    the later strip's tile-dup neutralization is just a harmless no-op cleanup).
    apply_form_system(project_root)

    # 2. each ALT forme → a real forme-species with its OWN sliced art. Uses the
    #    stats captured by detect_formes ABOVE, before any strip.
    created = []
    for fm in formes[1:]:                       # skip Normal (the base)
        suffix = fm["name"].upper().replace(" ", "_")
        slug = f"{base_name}_{suffix}".lower()
        dst = os.path.join(project_root, "graphics", "pokemon", slug)
        os.makedirs(dst, exist_ok=True)
        # Pre-slice this forme's own sprite from ITS source sheet + frame so
        # create_form(own_image) picks these up instead of re-deriving from the
        # base's single JSON sheet (which can't represent multi-sheet formes).
        for leaf, key in (("front.png", "front_png"), ("back.png", "back_png")):
            src = _abs(fm.get(key))
            if src and os.path.isfile(src):
                aps._extract_frame_png(src, os.path.join(dst, leaf), fm["frame"])
        isrc = _abs(fm.get("icon_png"))
        if isrc and os.path.isfile(isrc):
            shutil.copy2(isrc, os.path.join(dst, "icon.png"))
        for leaf in ("normal.pal", "shiny.pal"):
            s = os.path.join(base_dir, leaf)
            if os.path.isfile(s) and not os.path.isfile(os.path.join(dst, leaf)):
                shutil.copy2(s, os.path.join(dst, leaf))
        stats = dict(base_flat)
        stats.update(fm.get("stats") or {})    # override the six base stats
        aps.create_form(project_root, species_const, suffix, stats,
                        own_image=True, own_palette=True)
        created.append(f"SPECIES_{base_name}_{suffix}")

    # 3. retire the legacy stat/render hooks now that the forme-species exist
    #    (base → Normal stats + frame 0). AFTER create so a mid-way failure never
    #    strips the stat arrays before the formes have captured them.
    strip_legacy_forme(project_root, species_const)
    # 4. flatten the base's legacy #ifdef graphics block (single Normal sheet).
    _collapse_base_graphics(project_root, camel)
    return created
