"""Tests for the Layer B form-change infrastructure patcher.

Runs against a throwaway copy of the two real engine headers (never mutating the
project's pokefirered copy), starting from a fully-unpatched state, and asserts
the patch is correct + idempotent (a re-patch is byte-identical). Layer B's
struct field sits after Layer A's, so apply_form_change_system applies BOTH.
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

from core.form_change_patch import apply_form_change_system  # noqa: E402

_ENGINE = os.path.join(_ROOT, "pokefirered")
_POKEMON_H = os.path.join(_ENGINE, "include", "pokemon.h")
_SPECIES_H = os.path.join(_ENGINE, "include", "constants", "species.h")
_skip = not (os.path.isfile(_POKEMON_H) and os.path.isfile(_SPECIES_H))

_LAYER_TOKENS = ("formSpeciesIdTable", "formChangeTable", "struct FormChange",
                 "GetFormSpeciesId", "GetSpeciesFormId", "GetFormChangeTargetSpecies")


def _strip(path, tokens):
    # remove the multi-line struct FormChange block first, then token lines
    text = re.sub(r"struct FormChange\n\{.*?\};\n\n?", "",
                  open(path, encoding="utf-8").read(), flags=re.DOTALL)
    kept = [l for l in text.splitlines(keepends=True)
            if not any(t in l for t in tokens)]
    open(path, "w", encoding="utf-8", newline="").writelines(kept)


def _mk(tmp):
    os.makedirs(os.path.join(tmp, "include", "constants"))
    os.makedirs(os.path.join(tmp, "src"))
    ph = os.path.join(tmp, "include", "pokemon.h")
    sp = os.path.join(tmp, "include", "constants", "species.h")
    shutil.copy2(_POKEMON_H, ph)
    shutil.copy2(_SPECIES_H, sp)
    _strip(ph, _LAYER_TOKENS)          # start from fully-unpatched (both layers)
    _strip(sp, ("FORM_SPECIES_END",))
    return ph, sp


@pytest.mark.skipif(_skip, reason="engine copy not present")
def test_layer_b_adds_infra():
    with tempfile.TemporaryDirectory() as d:
        ph, _ = _mk(d)
        r = apply_form_change_system(d)
        assert r["pokemon_h"] and r["form_change_types_h"] and r["form_change_c"]
        text = open(ph, encoding="utf-8").read()
        assert "struct FormChange" in text
        assert "const struct FormChange *formChangeTable;" in text
        assert "u16 GetFormChangeTargetSpecies(u16 species, u16 method, u16 param);" in text
        # the struct must precede SpeciesInfo (its pointer field needs the type)
        assert text.index("struct FormChange") < text.index("struct SpeciesInfo")
        types = open(os.path.join(d, "include", "constants",
                                  "form_change_types.h"), encoding="utf-8").read()
        assert "FORM_CHANGE_ITEM_HOLD" in types and "FORM_CHANGE_WEATHER" in types
        fc = open(os.path.join(d, "src", "form_change.c"), encoding="utf-8").read()
        assert "GetFormChangeTargetSpecies" in fc and "FORM_CHANGE_END" in fc


@pytest.mark.skipif(_skip, reason="engine copy not present")
def test_layer_b_idempotent():
    with tempfile.TemporaryDirectory() as d:
        ph, _ = _mk(d)
        apply_form_change_system(d)
        snap = open(ph, encoding="utf-8").read()
        r2 = apply_form_change_system(d)
        assert not any(r2.values())
        assert open(ph, encoding="utf-8").read() == snap        # byte-identical
        assert snap.count("formChangeTable") == 1                # single field
        assert snap.count("struct FormChange\n{") == 1           # single struct
