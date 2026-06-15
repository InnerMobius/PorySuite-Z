"""core/form_system_patch.py — engine patcher for alternate-form support.

Brings a vanilla-ish pokefirered engine up to "form-aware" by adding the minimal
pokeemerald-expansion-style infrastructure:

  * a ``const u16 *formSpeciesIdTable`` pointer on ``struct SpeciesInfo``
  * a ``FORM_SPECIES_END`` sentinel constant
  * ``GetFormSpeciesId`` / ``GetSpeciesFormId`` runtime helpers in a new
    ``src/form_species.c`` (auto-compiled — the Makefile globs ``src/**/*.c``)

This is **Phase 2a (infrastructure only)**. It is idempotent and additive: with
no forms defined, every species' ``formSpeciesIdTable`` is implicitly NULL and
the helpers return the species unchanged, so a vanilla project still builds and
behaves identically. The per-base form tables, the appended form-species, and
the widening of the hard-sized ``[NUM_SPECIES]`` tables are added by the codegen
only when forms actually exist (Phase 2b / Phase 3) — none of that is needed
until a form is created, and adding it before then would be dead code.

All edits target the tool's project copy via this patcher — game source is never
hand-edited. Re-running produces no changes (byte-identical).
"""

import os

_FIELD_MARKER = "formSpeciesIdTable"
_PROTO_MARKER = "GetFormSpeciesId"
_END_MARKER = "FORM_SPECIES_END"

# struct SpeciesInfo ends with the bodyColor/noFlip bitfields then `};`.
_STRUCT_CLOSE = "            u8 noFlip : 1;\n};"
_STRUCT_CLOSE_PATCHED = (
    "            u8 noFlip : 1;\n"
    "    const u16 *formSpeciesIdTable;\n"
    "};"
)
_FIELD_LINE_THEN_CLOSE = "    const u16 *formSpeciesIdTable;\n};\n"

_UNOWN_QMARK = "#define SPECIES_UNOWN_QMARK (NUM_SPECIES + 27)\n"

_FORM_SPECIES_C = '''#include "global.h"
#include "pokemon.h"
#include "constants/species.h"

// ── Alternate-form runtime helpers (added by PorySuite-Z form patcher) ──
//
// A form is a distinct species ID linked to its base by a per-base form table:
//   gSpeciesInfo[base].formSpeciesIdTable = { base, form1, ..., FORM_SPECIES_END }
// Each form's own SpeciesInfo points at the SAME table, so GetSpeciesFormId can
// map a form species back to its index. With no forms defined the pointer is
// NULL and both helpers return the species unchanged.

u16 GetFormSpeciesId(u16 species, u8 formId)
{
    const u16 *table = gSpeciesInfo[species].formSpeciesIdTable;
    if (table != NULL)
    {
        u8 i;
        for (i = 0; table[i] != FORM_SPECIES_END; i++)
        {
            if (i == formId)
                return table[i];
        }
    }
    return species;
}

u8 GetSpeciesFormId(u16 species)
{
    const u16 *table = gSpeciesInfo[species].formSpeciesIdTable;
    if (table != NULL)
    {
        u8 i;
        for (i = 0; table[i] != FORM_SPECIES_END; i++)
        {
            if (table[i] == species)
                return i;
        }
    }
    return 0;
}
'''


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_if_changed(path, text):
    """Write only when content differs — byte-equality guard avoids phantom
    git diffs on a no-op re-patch. Returns True if the file was written."""
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == text:
                    return False
    except Exception:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def _patch_pokemon_h(pokemon_h):
    """Add the formSpeciesIdTable field to struct SpeciesInfo + the helper
    prototypes. Idempotent."""
    text = _read(pokemon_h)
    original = text

    if _FIELD_MARKER not in text:
        if _STRUCT_CLOSE not in text:
            raise RuntimeError(
                "form patch: struct SpeciesInfo close not found in pokemon.h")
        text = text.replace(_STRUCT_CLOSE, _STRUCT_CLOSE_PATCHED, 1)

    if _PROTO_MARKER not in text:
        if _FIELD_LINE_THEN_CLOSE not in text:
            raise RuntimeError(
                "form patch: cannot locate struct close for prototypes")
        protos = (_FIELD_LINE_THEN_CLOSE +
                  "\nu16 GetFormSpeciesId(u16 species, u8 formId);\n"
                  "u8 GetSpeciesFormId(u16 species);\n")
        text = text.replace(_FIELD_LINE_THEN_CLOSE, protos, 1)

    if text != original:
        _write_if_changed(pokemon_h, text)
        return True
    return False


def _patch_species_h(species_h):
    """Add the FORM_SPECIES_END sentinel after the Unown form range. Idempotent."""
    text = _read(species_h)
    if _END_MARKER in text:
        return False
    if _UNOWN_QMARK not in text:
        raise RuntimeError("form patch: SPECIES_UNOWN_QMARK not found in species.h")
    text = text.replace(
        _UNOWN_QMARK, _UNOWN_QMARK + "\n#define FORM_SPECIES_END 0xFFFF\n", 1)
    _write_if_changed(species_h, text)
    return True


def _create_form_species_c(c_path):
    """Create src/form_species.c with the runtime helpers. Idempotent."""
    return _write_if_changed(c_path, _FORM_SPECIES_C)


def _self_check(pokemon_h, species_h, form_c):
    """Fail loudly rather than leave the engine half-patched."""
    ph = _read(pokemon_h)
    if _FIELD_MARKER not in ph:
        raise RuntimeError("form patch self-check: struct field missing")
    if _PROTO_MARKER not in ph:
        raise RuntimeError("form patch self-check: prototypes missing")
    if _END_MARKER not in _read(species_h):
        raise RuntimeError("form patch self-check: FORM_SPECIES_END missing")
    if not os.path.isfile(form_c) or "GetFormSpeciesId" not in _read(form_c):
        raise RuntimeError("form patch self-check: form_species.c missing/empty")


_FORM_FRAME_MARKER = "PorySuite-Z form frame"

_DEOXYS_TILES_OLD = (
    "static void DuplicateDeoxysTiles(void *pointer, s32 species)\n"
    "{\n"
    "    if (species == SPECIES_DEOXYS)\n"
    "        CpuCopy32(pointer + 0x800, pointer, 0x800);\n"
    "}"
)
_DEOXYS_TILES_NEW = (
    "static void DuplicateDeoxysTiles(void *pointer, s32 species)\n"
    "{\n"
    "    // PorySuite-Z form frame: a form that SHARES the base's stacked sheet\n"
    "    // renders frame N of it (copy that frame over frame 0). A form with its\n"
    "    // OWN sheet (different .data pointer) is left alone. GetSpeciesFormId is\n"
    "    // 0 for a base species, preserving vanilla behaviour (including Deoxys).\n"
    "    u8 formFrame = GetSpeciesFormId(species);\n"
    "    if (formFrame > 0)\n"
    "    {\n"
    "        const u16 *t = gSpeciesInfo[species].formSpeciesIdTable;\n"
    "        if (t != NULL && gMonFrontPicTable[species].data == gMonFrontPicTable[t[0]].data)\n"
    "            CpuCopy32((u8 *)pointer + formFrame * 0x800, pointer, 0x800);\n"
    "    }\n"
    "    else if (species == SPECIES_DEOXYS)\n"
    "        CpuCopy32((u8 *)pointer + 0x800, pointer, 0x800);\n"
    "}"
)


def _patch_decompress(decompress_c):
    """Generalize DuplicateDeoxysTiles so ANY form species renders frame N of
    its base's stacked sheet (vanilla only special-cased Deoxys). Idempotent;
    no-op if the file is missing or its structure differs (so a project that
    already reworked this function isn't clobbered)."""
    if not os.path.isfile(decompress_c):
        return False
    text = _read(decompress_c)
    if _FORM_FRAME_MARKER in text:
        return False
    if _DEOXYS_TILES_OLD not in text:
        return False
    text = text.replace(_DEOXYS_TILES_OLD, _DEOXYS_TILES_NEW, 1)
    return _write_if_changed(decompress_c, text)


def apply_form_system(project_root):
    """Apply the Phase-2a form infrastructure to *project_root*.

    Idempotent: re-running on an already-patched project changes nothing and
    returns all-False. Raises on a structurally unexpected engine (missing
    anchors) rather than emitting half-patched C.
    """
    inc = os.path.join(project_root, "include")
    pokemon_h = os.path.join(inc, "pokemon.h")
    species_h = os.path.join(inc, "constants", "species.h")
    form_c = os.path.join(project_root, "src", "form_species.c")

    for p in (pokemon_h, species_h):
        if not os.path.isfile(p):
            raise FileNotFoundError(f"form patch: missing {p}")

    result = {
        "pokemon_h": _patch_pokemon_h(pokemon_h),
        "species_h": _patch_species_h(species_h),
        "form_species_c": _create_form_species_c(form_c),
        "decompress_c": _patch_decompress(
            os.path.join(project_root, "src", "decompress.c")),
    }
    _self_check(pokemon_h, species_h, form_c)
    return result
