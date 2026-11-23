"""Pure-logic tests for the Stat Balance Assistant (core/stat_balance.py).

No Qt anywhere in here — this is the separation the feature was built
around: everything the panel displays is computed by functions this file
can pin down exactly, including every classification boundary the spec
lists (179→180, 290→291, 350→351, 440→441, 490→491, 540→541, 599→600).
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

import core.stat_balance as _sb  # noqa: E402
from core.stat_balance import (  # noqa: E402
    BANDS, GEN3_REFERENCE, MAX_BST, PREFS_DEFAULT, STAT_KEYS,
    TIER_CUSTOM, TIER_NAMES, TIER_UNSPECIFIED, BalancePrefsStore,
    RosterStats, advisory_notes, bst, classify_bst,
    classify_evolution_edges, evolution_paths, nearest_entries,
    regression_note, species_pref_key, stat_levels, target_feedback,
    tier_range, validate_custom_range, vanilla_comparison,
)


def _stats(hp=0, atk=0, deff=0, spa=0, spd=0, spe=0):
    return {"hp": hp, "attack": atk, "defense": deff,
            "sp_attack": spa, "sp_defense": spd, "speed": spe}


#: The owner's real validation case: Octo, 330 BST.
OCTO = _stats(74, 49, 69, 39, 51, 48)


# ─────────────────────────────────────────────── BST ──

def test_bst_sums_exactly_the_six_stats_once():
    assert bst(_stats(1, 2, 3, 4, 5, 6)) == 21
    assert bst(OCTO) == 330


def test_bst_ignores_everything_that_is_not_a_base_stat():
    s = dict(OCTO)
    # EV yields, IVs, and anything else a species dict might carry must
    # never leak into the total.
    s.update({"evYield_HP": 2, "catchRate": 255, "expYield": 60,
              "baseHP": 999, "iv_hp": 31})
    assert bst(s) == 330


def test_bst_treats_missing_and_none_stats_as_zero():
    assert bst({}) == 0
    assert bst({"hp": None, "attack": 10}) == 10


# ─────────────────────────────── classification bands ──

#: Every transition the spec calls out, plus the exact labels either side.
#: Labels are DESCRIPTIVE (where the number sits on the Gen 3 spectrum) —
#: never verdicts like "very weak", which contradicted the green
#: "within your target" line for an early-game creature at 290.
BOUNDARIES = [
    (179, "Minimal — below every vanilla Gen 3 species",
     180, "Low — early-game range in Gen 3"),
    (290, "Low — early-game range in Gen 3",
     291, "Modest — typical unevolved range in Gen 3"),
    (350, "Modest — typical unevolved range in Gen 3",
     351, "Mid — typical middle-stage range in Gen 3"),
    (440, "Mid — typical middle-stage range in Gen 3",
     441, "Solid — lower fully-evolved range in Gen 3"),
    (490, "Solid — lower fully-evolved range in Gen 3",
     491, "High — upper fully-evolved range in Gen 3"),
    (540, "High — upper fully-evolved range in Gen 3",
     541, "Very high — above most vanilla species"),
    (599, "Very high — above most vanilla species",
     600, "Top-end — legendary territory in Gen 3"),
]


@pytest.mark.parametrize("below, below_label, above, above_label", BOUNDARIES)
def test_every_boundary_flips_label_and_color(below, below_label,
                                              above, above_label):
    bl, bc = classify_bst(below)
    al, ac = classify_bst(above)
    assert bl == below_label
    assert al == above_label
    assert bc != ac, "adjacent bands must not share a color"


def test_bands_are_contiguous_and_non_overlapping():
    for (lo1, hi1, *_), (lo2, _hi2, *_) in zip(BANDS, BANDS[1:]):
        assert hi1 is not None and lo2 == hi1 + 1, (
            "bands must tile the number line with no gap and no overlap")
    assert BANDS[0][0] == 0
    assert BANDS[-1][1] is None


def test_classification_extremes_and_limits():
    first, last = BANDS[0][2], BANDS[-1][2]
    assert classify_bst(0)[0] == first
    assert classify_bst(6)[0] == first            # editor minimum
    assert classify_bst(MAX_BST)[0] == last
    assert classify_bst(10_000)[0] == last
    assert classify_bst(-5)[0] == first


def test_band_labels_are_descriptive_not_verdicts():
    # The judgmental vocabulary that contradicted the tier feedback is
    # banned from the descriptive classification line.
    for _lo, _hi, label, _c in BANDS:
        low = label.lower()
        for word in ("weak", "strong", "exceptional", "elite"):
            assert word not in low, (label, word)


def test_octo_classifies_exactly_as_the_spec_says():
    assert classify_bst(bst(OCTO))[0] ==         "Modest — typical unevolved range in Gen 3"
    assert classify_bst(290)[0] == "Low — early-game range in Gen 3"
    assert classify_bst(291)[0] ==         "Modest — typical unevolved range in Gen 3"


# ─────────────────────────────────── tiers and targets ──

def test_tier_table_has_the_agreed_defaults():
    assert dict(zip(TIER_NAMES, TIER_NAMES))  # names unique
    assert tier_range("Early-game wild encounter") == (180, 290)
    assert tier_range("Standard unevolved creature") == (291, 350)
    assert tier_range("Middle evolution") == (351, 440)
    assert tier_range("Final evolution") == (441, 540)
    assert tier_range("Boss / exceptional creature") == (541, 680)
    assert tier_range(TIER_UNSPECIFIED) is None


def test_custom_range_is_validated_never_guessed():
    assert tier_range(TIER_CUSTOM, (230, 270)) == (230, 270)
    assert tier_range(TIER_CUSTOM, (270, 230)) is None      # reversed
    assert tier_range(TIER_CUSTOM, (-1, 200)) is None       # negative
    assert tier_range(TIER_CUSTOM, (0, MAX_BST + 1)) is None # beyond cap
    assert tier_range(TIER_CUSTOM, None) is None
    assert validate_custom_range(("a", "b")) is None
    assert validate_custom_range((200, 200)) == (200, 200)  # single point ok


def test_target_feedback_matches_the_spec_examples():
    status, delta, text = target_feedback(330, (180, 290))
    assert (status, delta) == ("above", 40)
    assert "40 BST points above" in text

    status, delta, text = target_feedback(285, (230, 270))
    assert (status, delta) == ("above", 15)
    assert "15 BST points above" in text

    status, delta, text = target_feedback(170, (180, 290))
    assert (status, delta) == ("below", 10)
    assert "below" in text

    status, delta, text = target_feedback(250, (180, 290))
    assert (status, delta) == ("within", 0)


def test_target_feedback_boundaries_are_inclusive_and_singular():
    assert target_feedback(180, (180, 290))[0] == "within"
    assert target_feedback(290, (180, 290))[0] == "within"
    assert target_feedback(291, (180, 290))[1] == 1
    assert "1 BST point above" in target_feedback(291, (180, 290))[2]
    assert target_feedback(330, None) == ("none", 0, "")


# ─────────────────────────────────────── stat levels ──

def _roster_spread(n=30, seed=3):
    """A synthetic roster of n species with varied, deterministic stats."""
    out = {}
    v = seed
    for i in range(n):
        v = (v * 73 + 19) % 97
        base = 30 + (v % 60)
        out[f"SPECIES_R{i}"] = _stats(
            base + (i % 7) * 5, base + (i % 5) * 6, base + (i % 3) * 8,
            base + (i % 11) * 3, base + (i % 4) * 7, base + (i % 6) * 4)
    return out


def test_stat_levels_fall_back_to_own_spread_without_a_roster():
    lv = stat_levels(_stats(120, 50, 50, 50, 50, 10))
    assert lv["hp"][0] == "high"
    assert lv["speed"][0] == "low"
    assert "own stat" in lv["hp"][1]      # the basis is stated, not implied


def test_stat_levels_use_roster_terciles_when_the_sample_is_usable():
    roster = _roster_spread()
    rs = RosterStats(roster)
    assert rs.usable
    giant = stat_levels(_stats(250, 250, 250, 250, 250, 250), rs)
    tiny = stat_levels(_stats(1, 1, 1, 1, 1, 1), rs)
    assert all(v[0] == "high" for v in giant.values())
    assert all(v[0] == "low" for v in tiny.values())
    assert "project species" in giant["hp"][1]


def test_roster_stats_scopes_to_the_target_range_and_reports_sample():
    roster = _roster_spread()
    in_range = [s for s in roster.values() if 180 <= bst(s) <= 290]
    rs = RosterStats(roster, (180, 290))
    assert rs.sample_size == len(in_range)
    # A roster dense inside the range gives a usable, range-stamped basis.
    dense = RosterStats(_tier_roster(), (180, 290))
    assert dense.usable
    assert "180–290" in dense.describe()
    assert str(dense.sample_size) in dense.describe()


def test_small_sample_declares_itself_unusable():
    rs = RosterStats({"A": _stats(10, 10, 10, 10, 10, 10)}, (180, 290))
    assert not rs.usable
    # And stat_levels silently falls back instead of pretending.
    lv = stat_levels(_stats(120, 50, 50, 50, 50, 10), rs)
    assert "own stat" in lv["hp"][1]


def test_zero_stat_placeholder_species_do_not_pollute_the_roster():
    roster = _roster_spread()
    polluted = dict(roster)
    for i in range(50):
        polluted[f"SPECIES_NONE_{i}"] = _stats()  # all-zero placeholder rows
    assert RosterStats(polluted).sample_size == RosterStats(roster).sample_size


# ──────────────────────────────────── advisory notes ──

def _tier_roster():
    """A roster dense inside 180–290 so tier-scoped terciles are usable."""
    out = {}
    for i in range(24):
        hp = 35 + i                    # 35..58
        out[f"SPECIES_T{i}"] = _stats(hp, 40, 38 + (i % 9), 35, 36 + (i % 7),
                                      34 + (i % 5))
    return out


def test_no_notes_without_a_chosen_target_range():
    rs = RosterStats(_tier_roster())          # usable but UNSCOPED
    assert advisory_notes(OCTO, rs) == []
    assert advisory_notes(OCTO, None) == []


def test_high_durability_with_no_weakness_is_a_caution():
    rs = RosterStats(_tier_roster(), (180, 290))
    assert rs.usable
    bulky = _stats(90, 70, 85, 60, 60, 55)     # strong everywhere for tier
    notes = advisory_notes(bulky, rs)
    cautions = [t for k, t in notes if k == "caution"]
    assert any("physical durability" in t for t in cautions)
    assert any("heuristic" in t for t in cautions), (
        "a heuristic warning must say it is one")


def test_tradeoffs_read_as_specialisation_not_warnings():
    rs = RosterStats(_tier_roster(), (180, 290))
    # Geodude-shaped: huge Defense, terrible Speed + Sp. Atk. The old
    # Combat Role dropdown existed to excuse this shape; the stats
    # themselves carry the evidence now.
    geodude_like = _stats(60, 55, 100, 20, 30, 15)
    notes = advisory_notes(geodude_like, rs)
    assert notes, "the strong Defense should at least be observed"
    assert all(k == "info" for k, _t in notes), (
        "extreme strength offset by real weaknesses is specialisation, "
        "not a caution")
    assert any("deliberate specialisation" in t for _k, t in notes)


def test_all_round_strength_with_no_weakness_is_the_caution_case():
    rs = RosterStats(_tier_roster(), (180, 290))
    rounded = _stats(70, 60, 60, 60, 60, 55)   # strong everywhere for tier
    notes = advisory_notes(rounded, rs)
    assert any(k == "caution" and "no weak ones" in t for k, t in notes)


def test_the_combat_role_selector_is_gone_for_good():
    # Removed 2026-11-22 after measurement showed it almost never changed
    # the output (see core/stat_balance.py). This guard keeps its logic
    # and prefs from quietly returning.
    for name in ("ROLES", "ROLE_UNSPECIFIED", "_ROLE_EXPECTED_HIGH",
                 "_ROLE_EXPECTED_LOW"):
        assert not hasattr(_sb, name), name
    assert "role" not in PREFS_DEFAULT


# ─────────────────────────────────── evolution paths ──

def test_species_with_no_evolution_is_a_single_stage():
    paths = evolution_paths("SPECIES_LONER", {}, {"SPECIES_LONER": 400})
    assert paths == [[("SPECIES_LONER", 400)]]


def test_three_stage_line_walks_back_and_forward():
    fwd = {"SPECIES_A": ["SPECIES_B"], "SPECIES_B": ["SPECIES_C"]}
    bsts = {"SPECIES_A": 320, "SPECIES_B": 410, "SPECIES_C": 520}
    # From the middle stage, the whole line appears.
    paths = evolution_paths("SPECIES_B", fwd, bsts)
    assert paths == [[("SPECIES_A", 320), ("SPECIES_B", 410),
                      ("SPECIES_C", 520)]]


def test_branching_evolutions_become_separate_paths_not_one_chain():
    fwd = {"SPECIES_OCTOROK": ["SPECIES_BIGOCTO", "SPECIES_SEAOCTO"],
           "SPECIES_OCTO": ["SPECIES_OCTOROK"]}
    bsts = {"SPECIES_OCTO": 330, "SPECIES_OCTOROK": 410,
            "SPECIES_BIGOCTO": 500, "SPECIES_SEAOCTO": 480}
    paths = evolution_paths("SPECIES_OCTOROK", fwd, bsts)
    assert len(paths) == 2
    assert all(p[0] == ("SPECIES_OCTO", 330) for p in paths)
    tails = {p[-1][0] for p in paths}
    assert tails == {"SPECIES_BIGOCTO", "SPECIES_SEAOCTO"}


def test_the_projects_real_cycle_terminates():
    # Octo → Octorok → BigOcto → Octorok is live data in the loaded
    # project. The traversal must cut the loop, not hang or crash.
    fwd = {"SPECIES_OCTO": ["SPECIES_OCTOROK"],
           "SPECIES_OCTOROK": ["SPECIES_BIGOCTO"],
           "SPECIES_BIGOCTO": ["SPECIES_OCTOROK"]}
    bsts = {"SPECIES_OCTO": 330, "SPECIES_OCTOROK": 410,
            "SPECIES_BIGOCTO": 500}
    paths = evolution_paths("SPECIES_OCTO", fwd, bsts)
    assert paths == [[("SPECIES_OCTO", 330), ("SPECIES_OCTOROK", 410),
                      ("SPECIES_BIGOCTO", 500)]]


def test_ambiguous_pre_evolution_stops_the_backward_walk():
    # Two species evolve into SPECIES_MID: picking one for a linear chain
    # would misrepresent a branch, so the back-walk must stop.
    fwd = {"SPECIES_P1": ["SPECIES_MID"], "SPECIES_P2": ["SPECIES_MID"],
           "SPECIES_MID": ["SPECIES_TOP"]}
    bsts = {"SPECIES_P1": 300, "SPECIES_P2": 310, "SPECIES_MID": 400,
            "SPECIES_TOP": 500}
    paths = evolution_paths("SPECIES_MID", fwd, bsts)
    assert paths == [[("SPECIES_MID", 400), ("SPECIES_TOP", 500)]]


def test_malformed_targets_are_rendered_with_zero_not_crashed_on():
    fwd = {"SPECIES_A": ["SPECIES_TYPO"]}
    paths = evolution_paths("SPECIES_A", fwd, {"SPECIES_A": 320})
    assert paths == [[("SPECIES_A", 320), ("SPECIES_TYPO", 0)]]


def test_regression_note_flags_but_never_accuses():
    note = regression_note([("SPECIES_A", 500), ("SPECIES_B", 400)])
    assert "100 less BST" in note and "deliberate" in note
    assert regression_note([("SPECIES_A", 320), ("SPECIES_B", 410)]) == ""
    # Unknown-BST stages (0) stay silent rather than "losing" 400 points.
    assert regression_note([("SPECIES_A", 400), ("SPECIES_T", 0)]) == ""


# ──────────────────────────────── reference comparisons ──

def test_nearest_entries_are_deterministic_and_respect_exclude():
    table = (("Alpha", 300), ("Beta", 320), ("Gamma", 340))
    assert nearest_entries(318, table, count=1) == [("Beta", 320)]
    assert nearest_entries(318, table, count=1, exclude=("Beta",)) == \
        [("Alpha", 300)]
    # Equidistant: the lower BST wins, every run.
    assert nearest_entries(320, (("Hi", 330), ("Lo", 310)), count=1) == \
        [("Lo", 310)]


def test_gen3_reference_table_is_the_full_authentic_dex():
    names = [n for n, _ in GEN3_REFERENCE]
    assert len(names) == len(set(names)), "no duplicate reference names"
    assert len(GEN3_REFERENCE) == 386, (
        "every real vanilla FireRed species, extracted from the decomp's "
        "own original base-stats table")
    assert all(150 < b <= 680 for _n, b in GEN3_REFERENCE)
    # Known Gen 3 anchors (values that were CHANGED in later generations
    # stay at their Gen 3 numbers):
    assert ("Bulbasaur", 318) in GEN3_REFERENCE
    assert ("Butterfree", 385) in GEN3_REFERENCE   # 395 from Gen 6 onward
    assert ("Mewtwo", 680) in GEN3_REFERENCE
    assert nearest_entries(330, GEN3_REFERENCE, count=1)[0][1] <= 340


def test_vanilla_comparison_detects_exact_modified_and_custom():
    bulba = _stats(45, 49, 49, 65, 65, 45)
    kind, ref = vanilla_comparison("Bulbasaur", bulba)
    assert kind == "exact" and ref.name == "Bulbasaur" and ref.bst == 318

    tweaked = dict(bulba, hp=80)
    kind, ref = vanilla_comparison("Bulbasaur", tweaked)
    assert kind == "modified" and ref.bst == 318

    kind, ref = vanilla_comparison("bulbasaur", bulba)   # case-insensitive
    assert kind == "exact"

    assert vanilla_comparison("Octo", OCTO) == (None, None), (
        "a custom creature gets NO invented vanilla relationship")


# ───────────────────────────── evolution edge classification ──

def _evo(method, param, target):
    return {"method": method, "param": param, "targetSpecies": target}


def test_vanilla_shaped_data_is_all_forward_no_reverse():
    evos = {
        "SPECIES_A": [_evo("EVO_LEVEL", 16, "SPECIES_B")],
        "SPECIES_B": [_evo("EVO_LEVEL", 32, "SPECIES_C")],
        "SPECIES_E": [_evo("EVO_ITEM", "ITEM_FIRE_STONE", "SPECIES_F"),
                      _evo("EVO_ITEM", "ITEM_WATER_STONE", "SPECIES_G")],
    }
    E = classify_evolution_edges(evos)
    assert E.reverse == [] and E.ambiguous_groups == []
    assert E.forward["SPECIES_A"] == ["SPECIES_B"]
    assert sorted(E.forward["SPECIES_E"]) == ["SPECIES_F", "SPECIES_G"]


def _zeldamon_family():
    """The real project's shape: one line, branching, every branch
    devolving back with one consistent item."""
    TS = ("EVO_ITEM", "ITEM_TIME_SHARD")
    return {
        "SPECIES_OCTO": [_evo("EVO_LEVEL", 16, "SPECIES_OCTOROK")],
        "SPECIES_OCTOROK": [
            _evo("EVO_ITEM", "ITEM_WATER_STONE", "SPECIES_BIGOCTO"),
            _evo("EVO_ITEM", "ITEM_NORMAL_STONE", "SPECIES_SEAOCTO"),
            _evo(*TS, "SPECIES_OCTO")],
        "SPECIES_BIGOCTO": [_evo(*TS, "SPECIES_OCTOROK")],
        "SPECIES_SEAOCTO": [_evo(*TS, "SPECIES_OCTOROK")],
    }


def test_consistent_devolution_convention_is_recognised():
    E = classify_evolution_edges(_zeldamon_family())
    assert ("SPECIES_OCTOROK", "SPECIES_OCTO") in E.reverse
    assert ("SPECIES_BIGOCTO", "SPECIES_OCTOROK") in E.reverse
    assert ("SPECIES_SEAOCTO", "SPECIES_OCTOROK") in E.reverse
    # The forward graph is the clean branching line, cycle-free.
    assert E.forward["SPECIES_OCTO"] == ["SPECIES_OCTOROK"]
    assert sorted(E.forward["SPECIES_OCTOROK"]) ==         ["SPECIES_BIGOCTO", "SPECIES_SEAOCTO"]
    assert "SPECIES_OCTO" not in E.forward.get("SPECIES_OCTOROK", [])
    assert E.ambiguous_groups == []
    assert E.reverse_edges_touching("SPECIES_OCTO") ==         [("SPECIES_OCTOROK", "SPECIES_OCTO")]


def test_project_wide_convention_resolves_a_small_pair_too():
    evos = _zeldamon_family()
    # A separate two-species family: structurally ambiguous on its own
    # (either edge's removal breaks the 2-cycle), but the project-wide
    # devolution item settles it.
    evos["SPECIES_KEESE"] = [_evo("EVO_LEVEL", 20, "SPECIES_VIRE")]
    evos["SPECIES_VIRE"] = [
        _evo("EVO_ITEM", "ITEM_TIME_SHARD", "SPECIES_KEESE")]
    E = classify_evolution_edges(evos)
    assert ("SPECIES_VIRE", "SPECIES_KEESE") in E.reverse
    assert E.forward["SPECIES_KEESE"] == ["SPECIES_VIRE"]
    assert E.ambiguous_groups == []


def test_an_undecidable_cycle_stays_honestly_ambiguous():
    # Mutual pair, same mechanism both ways, nothing else in the project:
    # no convention exists, so NO direction may be invented.
    evos = {
        "SPECIES_X": [_evo("EVO_ITEM", "ITEM_MOON_STONE", "SPECIES_Y")],
        "SPECIES_Y": [_evo("EVO_ITEM", "ITEM_MOON_STONE", "SPECIES_X")],
    }
    E = classify_evolution_edges(evos)
    assert E.reverse == []
    assert len(E.ambiguous_groups) == 1
    assert E.in_ambiguous_group("SPECIES_X") ==         frozenset({"SPECIES_X", "SPECIES_Y"})
    # Unresolved edges stay out of the forward graph: no fake chains.
    assert E.forward.get("SPECIES_X", []) == []


def test_malformed_rows_and_self_loops_are_skipped():
    evos = {
        "SPECIES_A": [_evo("EVO_LEVEL", 16, "SPECIES_A"),   # self loop
                      {"method": "EVO_LEVEL"},               # no target
                      None,
                      _evo("EVO_LEVEL", 20, "SPECIES_B")],
    }
    E = classify_evolution_edges(evos)
    assert E.forward["SPECIES_A"] == ["SPECIES_B"]
    assert E.reverse == [] and E.ambiguous_groups == []


def test_the_projects_real_evolution_file_resolves_cleanly():
    # The acid test: the loaded project's actual evolutions.json, with its
    # 180+ devolution edges, must resolve with zero ambiguity and give
    # Octo a clean forward line.
    path = os.path.join(_ROOT, "pokefirered", "src", "data",
                        "evolutions.json")
    if not os.path.isfile(path):
        pytest.skip("no game project checked out next to the suite")
    with open(path, encoding="utf-8") as fh:
        evos = json.load(fh)
    E = classify_evolution_edges(evos)
    assert E.ambiguous_groups == []
    assert ("SPECIES_OCTOROK", "SPECIES_OCTO") in E.reverse
    assert "SPECIES_OCTO" not in E.forward.get("SPECIES_OCTOROK", [])


# ──────────────────────────────────────── persistence ──

def test_prefs_default_is_unspecified_everything(tmp_path):
    store = BalancePrefsStore(str(tmp_path / "sb.json"))
    prefs = store.get(r"C:\proj\game", "SPECIES_OCTO")
    assert prefs == PREFS_DEFAULT
    assert prefs["tier"] == TIER_UNSPECIFIED


def test_prefs_round_trip_and_reload_from_disk(tmp_path):
    path = str(tmp_path / "sb.json")
    store = BalancePrefsStore(path)
    store.set(r"C:\proj\game", "SPECIES_OCTO",
              {"tier": "Early-game wild encounter",
               "custom_min": 230, "custom_max": 270})
    fresh = BalancePrefsStore(path)        # simulate app restart
    prefs = fresh.get(r"C:\proj\game", "SPECIES_OCTO")
    assert prefs["tier"] == "Early-game wild encounter"
    assert "role" not in prefs
    assert (prefs["custom_min"], prefs["custom_max"]) == (230, 270)


def test_legacy_stored_roles_are_pruned_not_resurrected(tmp_path):
    # Files written before the Combat Role removal may carry "role" keys.
    # Reading ignores them; the next write prunes them from disk.
    path = str(tmp_path / "sb.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"projects": {"c:\\p": {"SPECIES_OCTO": {
            "tier": "Final evolution", "role": "Glass cannon"}}}}, fh)
    store = BalancePrefsStore(path)
    prefs = store.get(r"C:\p", "SPECIES_OCTO")
    assert prefs["tier"] == "Final evolution"
    assert "role" not in prefs
    store.set(r"C:\p", "SPECIES_OCTO", prefs)   # any rewrite prunes
    with open(path, encoding="utf-8") as fh:
        on_disk = json.dumps(json.load(fh))
    assert "role" not in on_disk


def test_projects_do_not_leak_into_each_other(tmp_path):
    store = BalancePrefsStore(str(tmp_path / "sb.json"))
    store.set(r"C:\proj\zeldamon", "SPECIES_OCTO", {"tier": "Final evolution"})
    assert store.get(r"C:\proj\other", "SPECIES_OCTO")["tier"] == \
        TIER_UNSPECIFIED


def test_species_and_forms_do_not_leak_into_each_other(tmp_path):
    store = BalancePrefsStore(str(tmp_path / "sb.json"))
    base = species_pref_key("SPECIES_OCTO")
    form = species_pref_key("SPECIES_OCTO", "SPECIES_OCTO_SEA")
    assert base != form
    store.set(r"C:\p", base, {"tier": "Final evolution"})
    assert store.get(r"C:\p", form)["tier"] == TIER_UNSPECIFIED
    assert store.get(r"C:\p", "SPECIES_OCTOROK")["tier"] == TIER_UNSPECIFIED


def test_setting_back_to_defaults_removes_the_row(tmp_path):
    path = str(tmp_path / "sb.json")
    store = BalancePrefsStore(path)
    store.set(r"C:\p", "SPECIES_OCTO", {"tier": "Final evolution"})
    store.set(r"C:\p", "SPECIES_OCTO", dict(PREFS_DEFAULT))
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data == {"projects": {}}


def test_corrupt_or_alien_prefs_files_are_tolerated(tmp_path):
    path = str(tmp_path / "sb.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json at all")
    store = BalancePrefsStore(path)
    assert store.get(r"C:\p", "SPECIES_OCTO") == PREFS_DEFAULT

    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"projects": {"c:\\p": {"SPECIES_OCTO": {
            "tier": "Not A Real Tier", "role": 7,
            "custom_min": 900, "custom_max": 100}}}}, fh)
    store = BalancePrefsStore(path)
    # Garbage values fall back to defaults instead of crashing the panel.
    assert store.get(r"C:\p", "SPECIES_OCTO") == PREFS_DEFAULT


def test_same_project_dir_spelled_differently_is_one_project(tmp_path):
    store = BalancePrefsStore(str(tmp_path / "sb.json"))
    store.set(r"C:\proj\game", "SPECIES_OCTO", {"tier": "Final evolution"})
    assert store.get(r"C:\PROJ\game\.", "SPECIES_OCTO")["tier"] == \
        "Final evolution"
