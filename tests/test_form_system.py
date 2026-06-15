"""Tests for the Phase-2a engine form-infrastructure patcher.

Runs against a throwaway copy of the two real engine headers (so it never
mutates the project's pokefirered copy), and asserts the patch is correct and
idempotent — a re-patch must produce byte-identical files.
"""

import os
import re
import sys
import shutil
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

import pytest  # noqa: E402

from core.form_system_patch import apply_form_system  # noqa: E402

_ENGINE = os.path.join(_ROOT, "pokefirered")
_POKEMON_H = os.path.join(_ENGINE, "include", "pokemon.h")
_SPECIES_H = os.path.join(_ENGINE, "include", "constants", "species.h")
_DECOMPRESS_C = os.path.join(_ENGINE, "src", "decompress.c")
_skip = not (os.path.isfile(_POKEMON_H) and os.path.isfile(_SPECIES_H))


def _strip(path, tokens):
    """Reduce a copied header back to UNPATCHED regardless of which form layers
    the live engine copy already carries. Removes the multi-line Layer-B
    `struct FormChange` block first, then any single-line form-infra token."""
    text = re.sub(r"struct FormChange\n\{.*?\};\n\n?", "",
                  open(path, encoding="utf-8").read(), flags=re.DOTALL)
    kept = [l for l in text.splitlines(keepends=True)
            if not any(t in l for t in tokens)]
    open(path, "w", encoding="utf-8", newline="").writelines(kept)


def _mk_project(tmp):
    os.makedirs(os.path.join(tmp, "include", "constants"))
    os.makedirs(os.path.join(tmp, "src"))
    ph = os.path.join(tmp, "include", "pokemon.h")
    sp = os.path.join(tmp, "include", "constants", "species.h")
    shutil.copy2(_POKEMON_H, ph)
    shutil.copy2(_SPECIES_H, sp)
    if os.path.isfile(_DECOMPRESS_C):
        dc = os.path.join(tmp, "src", "decompress.c")
        shutil.copy2(_DECOMPRESS_C, dc)
        _strip(dc, ("PorySuite-Z form frame",))
    _strip(ph, ("formSpeciesIdTable", "formChangeTable", "GetFormSpeciesId",
                "GetSpeciesFormId", "GetFormChangeTargetSpecies"))
    _strip(sp, ("FORM_SPECIES_END",))


def _files(tmp):
    return (
        os.path.join(tmp, "include", "pokemon.h"),
        os.path.join(tmp, "include", "constants", "species.h"),
        os.path.join(tmp, "src", "form_species.c"),
    )


@pytest.mark.skipif(_skip, reason="engine copy not present")
def test_patch_adds_infrastructure():
    with tempfile.TemporaryDirectory() as d:
        _mk_project(d)
        r = apply_form_system(d)
        assert r == {"pokemon_h": True, "species_h": True,
                     "form_species_c": True, "decompress_c": True}
        ph_p, sp_p, fc_p = _files(d)
        ph = open(ph_p, encoding="utf-8").read()
        assert "const u16 *formSpeciesIdTable;" in ph
        assert "u16 GetFormSpeciesId(u16 species, u8 formId);" in ph
        assert "u8 GetSpeciesFormId(u16 species);" in ph
        # field sits inside the struct, before its close
        struct = ph[ph.index("struct SpeciesInfo"):]
        assert struct.index("formSpeciesIdTable") < struct.index("};")
        sp = open(sp_p, encoding="utf-8").read()
        assert "#define FORM_SPECIES_END 0xFFFF" in sp
        fc = open(fc_p, encoding="utf-8").read()
        assert "GetFormSpeciesId" in fc and "FORM_SPECIES_END" in fc


@pytest.mark.skipif(_skip, reason="engine copy not present")
def test_patch_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        _mk_project(d)
        apply_form_system(d)
        ph_p, sp_p, fc_p = _files(d)
        snap = {p: open(p, encoding="utf-8").read() for p in (ph_p, sp_p, fc_p)}
        r2 = apply_form_system(d)
        assert r2 == {"pokemon_h": False, "species_h": False,
                      "form_species_c": False, "decompress_c": False}
        for p, before in snap.items():
            assert open(p, encoding="utf-8").read() == before  # byte-identical


@pytest.mark.skipif(_skip, reason="engine copy not present")
def test_single_field_and_define_on_double_apply():
    with tempfile.TemporaryDirectory() as d:
        _mk_project(d)
        apply_form_system(d)
        apply_form_system(d)
        ph = open(_files(d)[0], encoding="utf-8").read()
        sp = open(_files(d)[1], encoding="utf-8").read()
        assert ph.count("const u16 *formSpeciesIdTable;") == 1
        assert ph.count("u16 GetFormSpeciesId(u16 species, u8 formId);") == 1
        assert sp.count("#define FORM_SPECIES_END 0xFFFF") == 1
