"""Pure-logic tests for the Suggest EV Yield engine (core/ev_yield.py)
and the authentic Gen 3 reference dataset behind it
(core/gen3_reference.py).

The dataset was extracted from the vanilla FireRed base-stats table in the
decomp's own git history, so these tests pin well-known SHIPPED Gen 3
values — if any of them fails, the dataset regenerated wrong, not the
games changed.
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from core.gen3_reference import GEN3_SPECIES, lookup  # noqa: E402
from core.ev_yield import (  # noqa: E402
    CATEGORY_TOTALS, MAX_PER_STAT, classify_project_stage, suggest_ev_yield,
)
from core.stat_balance import (  # noqa: E402
    STAT_KEYS, classify_evolution_edges,
)


def _stats(hp=0, atk=0, deff=0, spa=0, spd=0, spe=0):
    return {"hp": hp, "attack": atk, "defense": deff,
            "sp_attack": spa, "sp_defense": spd, "speed": spe}


# ───────────────────────────── the reference dataset itself ──

def test_dataset_covers_every_real_vanilla_species():
    assert len(GEN3_SPECIES) == 386
    names = [s.name for s in GEN3_SPECIES]
    assert len(set(names)) == 386


@pytest.mark.parametrize("name, stats, evs", [
    # Famous, unambiguous Gen 3 values (order: HP Atk Def SpA SpD Spe).
    ("Bulbasaur", (45, 49, 49, 65, 65, 45), (0, 0, 0, 1, 0, 0)),
    ("Chansey", (250, 5, 5, 35, 105, 50), (2, 0, 0, 0, 0, 0)),
    ("Magikarp", (20, 10, 55, 15, 20, 80), (0, 0, 0, 0, 0, 1)),
    ("Mewtwo", (106, 110, 90, 154, 90, 130), (0, 0, 0, 3, 0, 0)),
    ("Gyarados", (95, 125, 79, 60, 100, 81), (0, 2, 0, 0, 0, 0)),
    ("Snorlax", (160, 110, 65, 65, 110, 30), (2, 0, 0, 0, 0, 0)),
    # A split yield — Butterfree has given 2 Sp.Atk + 1 Sp.Def since Gen 3.
    ("Butterfree", (60, 45, 50, 80, 80, 70), (0, 0, 0, 2, 1, 0)),
])
def test_authentic_gen3_values_survive_regeneration(name, stats, evs):
    s = lookup(name)
    assert s is not None
    assert s.stats == stats
    assert s.evs == evs


def test_gen3_ev_caps_hold_everywhere():
    for s in GEN3_SPECIES:
        assert 1 <= s.ev_total <= 3, s.name
        assert max(s.evs) <= MAX_PER_STAT, s.name


def test_the_measured_gen3_patterns_hold():
    """The aggregate patterns the recommendation leans on, re-derived from
    the shipped data (these doubled as the research step the feature spec
    demanded)."""
    # Middle stages award exactly 2 points — all of them.
    assert set(CATEGORY_TOTALS["middle"]) == {2}
    # First stages award 1, with exactly two famous exceptions.
    first = CATEGORY_TOTALS["first"]
    assert first[1] >= 120 and first[2] == 2     # Chansey and Duskull
    # Finals split between 2 and 3.
    final = CATEGORY_TOTALS["final"]
    assert set(final) == {2, 3} and final[2] > final[3]
    # Non-evolvers (legendaries included in the pool) span 1–3.
    assert set(CATEGORY_TOTALS["standalone"]) == {1, 2, 3}
    # Legendaries always award 3 in Gen 3.
    for s in GEN3_SPECIES:
        if s.legendary:
            assert s.ev_total == 3, s.name


def test_stage_flags_match_known_lines():
    assert lookup("Bulbasaur").stage == 0 and not lookup("Bulbasaur").final
    assert lookup("Ivysaur").stage == 1 and not lookup("Ivysaur").final
    assert lookup("Venusaur").stage == 2 and lookup("Venusaur").final
    assert lookup("Ditto").stage == 0 and lookup("Ditto").final
    assert lookup("Gyarados").stage == 1 and lookup("Gyarados").final


# ───────────────────────────── stage from the project graph ──

def _evo(method, param, target):
    return {"method": method, "param": param, "targetSpecies": target}


def _edges():
    TS = ("EVO_ITEM", "ITEM_TIME_SHARD")
    return classify_evolution_edges({
        "SPECIES_OCTO": [_evo("EVO_LEVEL", 16, "SPECIES_OCTOROK")],
        "SPECIES_OCTOROK": [
            _evo("EVO_ITEM", "ITEM_WATER_STONE", "SPECIES_BIGOCTO"),
            _evo(*TS, "SPECIES_OCTO")],
        "SPECIES_BIGOCTO": [_evo(*TS, "SPECIES_OCTOROK")],
        "SPECIES_LONER": [],
    })


def test_stage_classification_sees_through_devolution():
    E = _edges()
    # Octorok devolves INTO Octo — that edge must not make Octo a final.
    assert classify_project_stage("SPECIES_OCTO", E)[0] == "first"
    assert classify_project_stage("SPECIES_OCTOROK", E)[0] == "middle"
    assert classify_project_stage("SPECIES_BIGOCTO", E)[0] == "final"
    assert classify_project_stage("SPECIES_LONER", E)[0] == "standalone"
    # The basis says the reverse links were recognised.
    assert "reverse" in classify_project_stage("SPECIES_OCTO", E)[1]


def test_unresolvable_cycle_reports_uncertain_not_a_guess():
    E = classify_evolution_edges({
        "SPECIES_X": [_evo("EVO_ITEM", "ITEM_MOON_STONE", "SPECIES_Y")],
        "SPECIES_Y": [_evo("EVO_ITEM", "ITEM_MOON_STONE", "SPECIES_X")],
    })
    cat, basis = classify_project_stage("SPECIES_X", E)
    assert cat == "uncertain"
    assert "not guessed" in basis


# ─────────────────────────────────── the recommendation ──

def test_first_stage_balanced_creature_gets_one_point():
    # Octo-shaped: modest, slightly HP-leaning first stage → the classic
    # vanilla first-stage answer, 1 point in a leading stat.
    s = suggest_ev_yield(_stats(52, 49, 48, 44, 51, 48), "first",
                         display_name="Octo")
    assert s.total == 1
    assert sum(s.evs.values()) == 1
    assert list(s.evs) == ["hp"]
    assert any("127 of 129" in r for r in s.rationale), (
        "the evidence counts come from the real dataset")
    assert any("heuristic" in r for r in s.rationale)
    assert s.examples, "genuine vanilla comparables must be shown"


def test_middle_stage_gets_two_points():
    s = suggest_ev_yield(_stats(57, 69, 66, 56, 57, 52), "middle")
    assert s.total == 2
    assert sum(s.evs.values()) == 2


def test_physical_attacker_awards_attack():
    s = suggest_ev_yield(_stats(70, 130, 70, 40, 60, 90), "final")
    assert max(s.evs, key=s.evs.get) == "attack"


def test_special_attacker_awards_sp_attack():
    s = suggest_ev_yield(_stats(60, 40, 60, 135, 85, 100), "final")
    assert max(s.evs, key=s.evs.get) == "sp_attack"


def test_defensive_creature_awards_a_defensive_stat():
    s = suggest_ev_yield(_stats(70, 60, 140, 40, 100, 30), "final")
    assert max(s.evs, key=s.evs.get) in ("defense", "hp", "sp_defense")


def test_fast_creature_awards_speed():
    s = suggest_ev_yield(_stats(55, 60, 50, 50, 50, 140), "final")
    assert max(s.evs, key=s.evs.get) == "speed"


def test_high_bst_final_tends_to_three_points():
    s = suggest_ev_yield(_stats(95, 125, 79, 60, 100, 81), "final")
    assert s.total in (2, 3)
    big = suggest_ev_yield(_stats(108, 130, 95, 80, 85, 102), "final")
    assert big.total == 3


def test_totals_never_leave_the_vanilla_envelope():
    for cat in ("first", "middle", "final", "standalone"):
        for st in (_stats(1, 1, 1, 1, 1, 1),
                   _stats(255, 255, 255, 255, 255, 255),
                   _stats(250, 5, 5, 35, 105, 50)):
            s = suggest_ev_yield(st, cat)
            assert 1 <= s.total <= 3
            assert sum(s.evs.values()) == s.total
            assert all(v <= MAX_PER_STAT for v in s.evs.values())
            assert set(s.evs) <= set(STAT_KEYS)


def test_uncertain_stage_is_conservative_and_says_so():
    s = suggest_ev_yield(_stats(52, 49, 48, 44, 51, 48), "uncertain")
    assert s.uncertain
    assert s.total == 1, "unknown stage falls back to the lowest tier"
    assert any("could not be read" in r for r in s.rationale)


def test_alternatives_exist_and_differ_from_the_suggestion():
    s = suggest_ev_yield(_stats(86, 97, 111, 61, 105, 40), "final")
    assert s.alternatives
    for alt in s.alternatives:
        assert alt.evs != s.evs or sum(alt.evs.values()) != s.total
        assert alt.text


def test_suggestions_are_deterministic():
    st = _stats(86, 97, 111, 61, 105, 40)
    a = suggest_ev_yield(st, "final")
    b = suggest_ev_yield(st, "final")
    assert a == b


def test_the_engine_never_mutates_its_inputs():
    st = _stats(52, 49, 48, 44, 51, 48)
    snapshot = dict(st)
    suggest_ev_yield(st, "first")
    assert st == snapshot


def test_balanced_creature_is_not_blindly_top_statted():
    """A perfectly flat spread (Ditto-shaped) must still produce a sane,
    explained recommendation — comparable species decide, not a naive
    argmax over equal numbers."""
    s = suggest_ev_yield(_stats(48, 48, 48, 48, 48, 48), "standalone",
                         display_name="Blob")
    assert s.total in (1, 2)
    assert sum(s.evs.values()) == s.total
    assert s.examples


def test_octo_end_to_end_from_real_project_data():
    """The user's own validation creature, straight through the real
    pipeline: project evolutions → stage → suggestion. Read-only."""
    evp = os.path.join(_ROOT, "pokefirered", "src", "data",
                       "evolutions.json")
    spp = os.path.join(_ROOT, "pokefirered", "src", "data", "species.json")
    if not (os.path.isfile(evp) and os.path.isfile(spp)):
        pytest.skip("no game project checked out next to the suite")
    with open(evp, encoding="utf-8") as fh:
        E = classify_evolution_edges(json.load(fh))
    with open(spp, encoding="utf-8") as fh:
        info = (json.load(fh).get("SPECIES_OCTO") or {}).get(
            "species_info") or {}
    stats = {"hp": info.get("baseHP", 0) or 0,
             "attack": info.get("baseAttack", 0) or 0,
             "defense": info.get("baseDefense", 0) or 0,
             "sp_attack": info.get("baseSpAttack", 0) or 0,
             "sp_defense": info.get("baseSpDefense", 0) or 0,
             "speed": info.get("baseSpeed", 0) or 0}
    cat, basis = classify_project_stage("SPECIES_OCTO", E)
    assert cat == "first"
    s = suggest_ev_yield(stats, cat, basis, "Octo")
    assert s.total == 1
    assert not s.uncertain
