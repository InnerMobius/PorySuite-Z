"""Regression tests for ``_build_rematch_map`` in the Trainers tab.

The bug: vanilla FRLG's VS-Seeker rematch table uses ``{TRAINER_X,
TRAINER_X}`` (base const == its own first rematch tier) for 136 of its
221 entries — the rematch reuses the original battle's data.  The
rematch-map builder added every ``tiers[1:]`` const to the "hide from
list" variant set, which wrongly hid those 136 base trainers from the
Trainers list entirely (e.g. Youngster Joey, Bug Catcher Greg, Lass
Sally…).  A base trainer is a real, standalone, editable trainer and
must always stay visible.

Fix: subtract every base const from the variant set, so a self-rematch
base stays visible while genuine rematch-only variants (TRAINER_X_2 /
_3 / _4) remain hidden.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _load_build_rematch_map():
    """Load ``_build_rematch_map`` from ui/trainers_tab_widget.py WITHOUT
    triggering the heavy ``core/__init__`` import chain (which needs a
    configured project's ``local_env``).

    ``_build_rematch_map`` is pure (lists/dicts in, tuple out), so we stub
    the module's heavy Qt/data imports and exec the file under a synthetic
    name.  Only the names the module imports by reference are stubbed; the
    function under test touches none of them.
    """
    # Stub the heavy dependency modules with no-op placeholders.
    def _stub(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    _stub("core")
    _stub("core.sprite_render", load_sprite_pixmap=lambda *a, **k: None)
    _stub("core.sprite_palette_bus",
          get_bus=lambda *a, **k: None, CAT_TRAINER_PIC="CAT_TRAINER_PIC")
    # ui package + game_text_edit stub (avoid importing the real ui/__init__).
    ui_pkg = sys.modules.get("ui")
    if ui_pkg is None:
        ui_pkg = types.ModuleType("ui")
        ui_pkg.__path__ = [os.path.join(ROOT_DIR, "ui")]
        sys.modules["ui"] = ui_pkg
    _stub("ui.game_text_edit",
          GameTextEdit=object,
          inc_to_display=lambda s: s,
          display_to_inc=lambda s: s)

    path = os.path.join(ROOT_DIR, "ui", "trainers_tab_widget.py")
    spec = importlib.util.spec_from_file_location(
        "ui._trainers_tab_widget_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module._build_rematch_map


_build_rematch_map = _load_build_rematch_map()


def _entry(trainers, map_name="MAP_ROUTE25"):
    return {"trainers": list(trainers), "map": map_name}


def test_self_rematch_base_stays_visible():
    # {JOEY, JOEY} — base is its own first rematch tier.
    entries = [_entry(["TRAINER_YOUNGSTER_JOEY", "TRAINER_YOUNGSTER_JOEY"])]
    base_map, any_map, variants = _build_rematch_map(entries)

    # Base is recorded.
    assert "TRAINER_YOUNGSTER_JOEY" in base_map
    # And it is NOT hidden — the whole point of the fix.
    assert "TRAINER_YOUNGSTER_JOEY" not in variants, (
        "a self-rematch base must stay visible in the trainer list"
    )


def test_distinct_tier_variants_still_hidden():
    # {CHAD, CHAD_2, SKIP, CHAD_3, CHAD_4} — distinct rematch variants.
    entries = [_entry([
        "TRAINER_YOUNGSTER_CHAD",
        "TRAINER_YOUNGSTER_CHAD_2",
        "SKIP",
        "TRAINER_YOUNGSTER_CHAD_3",
        "TRAINER_YOUNGSTER_CHAD_4",
    ])]
    base_map, any_map, variants = _build_rematch_map(entries)

    # Base visible.
    assert "TRAINER_YOUNGSTER_CHAD" in base_map
    assert "TRAINER_YOUNGSTER_CHAD" not in variants
    # Distinct variant tiers stay hidden (reachable via tier dropdown).
    assert "TRAINER_YOUNGSTER_CHAD_2" in variants
    assert "TRAINER_YOUNGSTER_CHAD_3" in variants
    assert "TRAINER_YOUNGSTER_CHAD_4" in variants
    # SKIP is never a const.
    assert "SKIP" not in variants


def test_variant_that_is_also_a_base_elsewhere_stays_visible():
    # Edge case: a const used as a rematch tier in one entry but as the
    # base of another entry must stay visible (being a base wins).
    entries = [
        _entry(["TRAINER_A", "TRAINER_B"]),          # B is a tier here
        _entry(["TRAINER_B", "TRAINER_B_2"]),        # …but B is a base here
    ]
    base_map, any_map, variants = _build_rematch_map(entries)
    assert "TRAINER_B" in base_map
    assert "TRAINER_B" not in variants, (
        "a const that is a base anywhere must never be hidden"
    )
    assert "TRAINER_B_2" in variants


def test_mixed_table_hides_only_true_variants():
    entries = [
        _entry(["TRAINER_YOUNGSTER_JOEY", "TRAINER_YOUNGSTER_JOEY"]),
        _entry(["TRAINER_CAMPER_ETHAN", "TRAINER_CAMPER_ETHAN"]),
        _entry([
            "TRAINER_YOUNGSTER_CHAD",
            "TRAINER_YOUNGSTER_CHAD_2",
            "SKIP",
            "TRAINER_YOUNGSTER_CHAD_3",
        ]),
    ]
    base_map, any_map, variants = _build_rematch_map(entries)
    # All three bases visible.
    for base in ("TRAINER_YOUNGSTER_JOEY", "TRAINER_CAMPER_ETHAN",
                 "TRAINER_YOUNGSTER_CHAD"):
        assert base in base_map
        assert base not in variants
    # Only Chad's distinct variants hidden.
    assert variants == {
        "TRAINER_YOUNGSTER_CHAD_2", "TRAINER_YOUNGSTER_CHAD_3"}


def test_skip_only_and_empty_entries_are_safe():
    # Entries whose base is SKIP/empty are ignored, no crash.
    entries = [
        _entry(["SKIP", "TRAINER_X"]),
        _entry(["", ""]),
        _entry(["TRAINER_Y", "TRAINER_Y"]),
    ]
    base_map, any_map, variants = _build_rematch_map(entries)
    assert "TRAINER_Y" in base_map
    assert "TRAINER_Y" not in variants
    # SKIP-based entry contributes no base.
    assert "SKIP" not in base_map
