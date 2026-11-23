"""core/ev_yield.py — the Suggest EV Yield engine. Pure logic, Qt-free.

Recommends the EV points a defeated creature awards, grounded in the
AUTHENTIC Generation 3 data in ``core/gen3_reference.py`` (extracted from
the vanilla FireRed base-stats table in the decomp's own git history).
Everything here is a clearly-labelled heuristic derived from those 386
historical examples — not an official Game Freak rule, and never an
automatic change: the caller shows a preview and the user decides.

The patterns this module leans on were measured from that table, not
assumed (the measuring happened while this module was written; the module
re-derives the aggregate numbers at import so the explanation text can
never drift from the data):

* Every vanilla species awards 1–3 EV points in total; no single stat
  exceeds 3.
* First-stage species that still evolve award 1 point in 127 of 129 cases
  (Chansey and Duskull are the 2-point exceptions).
* Middle stages award exactly 2 points, all 43 of them.
* Fully-evolved species award 2 (93 cases) or 3 (48 cases); higher-BST
  finals lean 3, but the 440–520 range genuinely mixes.
* Non-evolving species split between 1 (22) and 2 (30); legendaries all
  award 3.
* The awarded stat touches the species' highest base stat in 367 of 386
  cases, but 47 species split across two stats (Butterfree: 2 Sp. Atk +
  1 Sp. Def) — so the recommendation votes with comparable species
  rather than blindly taking the top stat.

Stage comes from the project's REAL evolution graph, oriented by
``core.stat_balance.classify_evolution_edges`` so a reversible evolution
(devolution) never miscounts as an incoming forward edge. When the graph
cannot say (an unresolved cycle), the result says so and the UI lets the
user pick the stage instead of guessing.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from core.gen3_reference import GEN3_SPECIES, Gen3Species
from core.stat_balance import (
    STAT_KEYS, STAT_LABELS, EvolutionEdges, bst,
)

#: Stage categories. "uncertain" means the evolution graph could not be
#: oriented for this species — the user chooses, nothing is guessed.
CATEGORIES: Tuple[str, ...] = (
    "first", "middle", "final", "standalone", "uncertain")

CATEGORY_LABELS: Dict[str, str] = {
    "first": "First stage (evolves, nothing evolves into it)",
    "middle": "Middle stage (evolves both ways)",
    "final": "Final stage (something evolves into it)",
    "standalone": "Does not evolve",
    "uncertain": "Stage could not be determined",
}

#: Gen 3 caps: the engine stores each per-stat yield in 2 bits (0–3).
MAX_PER_STAT = 3


def _category_of_gen3(s: Gen3Species) -> str:
    if s.stage == 0 and s.final:
        return "standalone"
    if s.stage == 0:
        return "first"
    if s.final:
        return "final"
    return "middle"


def _gen3_pool(category: str) -> List[Gen3Species]:
    """Comparable vanilla species for a category. Standalone includes the
    legendaries — a high-BST non-evolver's honest comparables ARE the
    legendaries. The other pools exclude them."""
    if category == "standalone":
        return [s for s in GEN3_SPECIES if _category_of_gen3(s) == "standalone"
                or s.legendary]
    return [s for s in GEN3_SPECIES
            if _category_of_gen3(s) == category and not s.legendary]


#: category → Counter of vanilla EV totals, derived live from the dataset
#: so the explanation text always matches the shipped numbers.
CATEGORY_TOTALS: Dict[str, Counter] = {
    c: Counter(s.ev_total for s in _gen3_pool(c))
    for c in ("first", "middle", "final", "standalone")
}

#: Split-vs-concentrated counts, also derived live.
N_GEN3 = len(GEN3_SPECIES)
N_SPLIT = sum(1 for s in GEN3_SPECIES if sum(1 for e in s.evs if e) > 1)


def classify_project_stage(species: str,
                           edges: EvolutionEdges) -> Tuple[str, str]:
    """``(category, basis)`` for a species in the PROJECT's oriented
    evolution graph. Reverse (devolution) edges are already excluded from
    ``edges.forward``, so an incoming devolution never makes something
    look like a final stage."""
    if edges.in_ambiguous_group(species):
        return ("uncertain",
                "This creature is part of an evolution cycle whose "
                "direction could not be determined from the project's "
                "data, so its stage is not guessed.")
    evolves = bool(edges.forward.get(species))
    has_pre = any(species in tgts for tgts in edges.forward.values())
    rev = edges.reverse_edges_touching(species)
    rev_note = (" (its reverse-evolution links were recognised and do not "
                "count toward stage)") if rev else ""
    if evolves and has_pre:
        return "middle", ("Something evolves into it and it evolves "
                          "further — a middle stage" + rev_note + ".")
    if evolves:
        return "first", ("It evolves and nothing evolves into it — a "
                         "first stage" + rev_note + ".")
    if has_pre:
        return "final", ("Something evolves into it and it evolves no "
                         "further — a final stage" + rev_note + ".")
    return "standalone", ("Nothing evolves into it and it does not "
                          "evolve" + rev_note + ".")


class EvAlternative(NamedTuple):
    label: str                 # short button-ish label
    evs: Dict[str, int]        # STAT_KEY → points
    text: str                  # one-line justification


class EvSuggestion(NamedTuple):
    evs: Dict[str, int]        # STAT_KEY → points (only non-zero keys)
    total: int
    category: str
    category_text: str         # how the stage was determined
    rationale: List[str]       # plain-English paragraphs
    examples: List[str]        # genuine vanilla comparables, formatted
    alternatives: List[EvAlternative]
    uncertain: bool            # stage unknown — user should confirm


def _format_evs(evs: Dict[str, int]) -> str:
    return ", ".join(f"+{v} {STAT_LABELS[k]}"
                     for k, v in sorted(evs.items(),
                                        key=lambda kv: (-kv[1], STAT_KEYS.index(kv[0])))
                     if v)


def _format_gen3_evs(s: Gen3Species) -> str:
    return ", ".join(f"+{v} {STAT_LABELS[STAT_KEYS[i]]}"
                     for i, v in enumerate(s.evs) if v)


def _neighbors(stats: Dict[str, int], pool: Sequence[Gen3Species],
               count: int = 8) -> List[Gen3Species]:
    """The *count* pool species most similar to *stats*: closest stat
    PROFILE (shares of the total, L1) with a mild BST-proximity term.
    Deterministic — ties break by name."""
    total = max(1, bst(stats))
    ours = [int(stats.get(k, 0) or 0) / total for k in STAT_KEYS]
    scored = []
    for s in pool:
        sbst = max(1, s.bst)
        theirs = [v / sbst for v in s.stats]
        profile_d = sum(abs(a - b) for a, b in zip(ours, theirs))
        scored.append((profile_d + abs(sbst - total) / 600.0, s.name, s))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [s for _d, _n, s in scored[:count]]


def _allocate(total: int, stats: Dict[str, int],
              votes: Dict[str, float]) -> Tuple[Dict[str, int], bool]:
    """Distribute *total* points. Returns ``(allocation, split)``.

    Ranking: the neighbours' votes first (how often comparable vanilla
    species award each stat), the creature's own stat value as the
    tiebreak. A split (2+1 or 1+1) happens only when the second stat has
    real support — mirroring that only 47 of 386 vanilla species split.
    """
    order = sorted(STAT_KEYS,
                   key=lambda k: (-votes.get(k, 0.0),
                                  -int(stats.get(k, 0) or 0),
                                  STAT_KEYS.index(k)))
    top, second = order[0], order[1]
    v_top = votes.get(top, 0.0)
    v_second = votes.get(second, 0.0)
    total = max(1, min(total, MAX_PER_STAT * 2))
    if total == 1:
        return {top: 1}, False
    if total == 2:
        if v_second > 0 and v_second >= 0.8 * v_top:
            return {top: 1, second: 1}, True
        return {top: 2}, False
    # total == 3 (vanilla never exceeds 3)
    if v_second > 0 and v_second >= 0.5 * v_top:
        return {top: 2, second: 1}, True
    return {top: 3}, False


def _total_from_neighbors(neigh: Sequence[Gen3Species],
                          category: str, total_bst: int) -> int:
    """The EV total the comparable species vote for; ties lean toward the
    side our BST sits on."""
    counts = Counter(s.ev_total for s in neigh)
    if not counts:
        return 1
    best = counts.most_common()
    top_n = best[0][1]
    tied = sorted(t for t, n in best if n == top_n)
    if len(tied) == 1:
        return tied[0]
    med = sorted(s.bst for s in neigh)[len(neigh) // 2]
    return max(tied) if total_bst >= med else min(tied)


def suggest_ev_yield(stats: Dict[str, int], category: str,
                     category_text: str = "",
                     display_name: str = "") -> EvSuggestion:
    """Build the recommendation for one creature. *category* is one of
    ``CATEGORIES``; pass ``"uncertain"`` to get a conservative suggestion
    plus the strong advice to pick the real stage.

    Reads *stats* (the six live base-stat values, unsaved edits included)
    and the reference dataset. Touches nothing."""
    uncertain = category not in ("first", "middle", "final", "standalone")
    eff_category = "first" if uncertain else category
    pool = _gen3_pool(eff_category)
    neigh = _neighbors(stats, pool)
    total_bst = bst(stats)
    total = _total_from_neighbors(neigh, eff_category, total_bst)

    votes: Dict[str, float] = {k: 0.0 for k in STAT_KEYS}
    for rank, s in enumerate(neigh):
        w = 1.0 / (1 + rank * 0.25)        # nearer neighbours count more
        for i, v in enumerate(s.evs):
            if v:
                votes[STAT_KEYS[i]] += w * v
    alloc, split = _allocate(total, stats, votes)

    # ── rationale ────────────────────────────────────────────────────────
    name = display_name or "This creature"
    cat_counts = CATEGORY_TOTALS[eff_category]
    n_pool = sum(cat_counts.values())
    dist = ", ".join(f"{n} of {n_pool} award {t}"
                     for t, n in sorted(cat_counts.items()))
    rationale: List[str] = []
    if uncertain:
        rationale.append(
            "The stage of this creature could not be read from the "
            "project's evolution data, so the numbers below assume a "
            "first-stage creature — the most conservative choice. Pick "
            "the real stage above to get a better-grounded suggestion.")
    rationale.append(
        f"{CATEGORY_LABELS[eff_category]}. In vanilla Gen 3 "
        f"(FireRed's own data), totals for this group run: {dist}. The "
        f"{len(neigh)} species with the most similar stat spread to "
        f"{name} mostly award {total} point{'s' if total != 1 else ''}, "
        f"so that total is suggested.")
    lead = _format_evs(alloc)
    strongest = sorted(STAT_KEYS,
                       key=lambda k: -int(stats.get(k, 0) or 0))[:2]
    rationale.append(
        f"{lead}: chosen by how comparable Gen 3 species distributed "
        f"their yields, with {name}'s own strongest stats "
        f"({', '.join(STAT_LABELS[k] for k in strongest)}) as the "
        f"tiebreak. "
        + (f"A split yield has real precedent — {N_SPLIT} of {N_GEN3} "
           f"vanilla species split across two stats. "
           if split else "")
        + "This is a heuristic drawn from historical examples, not an "
          "official balancing rule — your call stands either way.")

    examples = [f"{s.name} (BST {s.bst}): {_format_gen3_evs(s)}"
                for s in neigh[:4]]

    # ── alternatives ─────────────────────────────────────────────────────
    alternatives: List[EvAlternative] = []
    order = sorted(STAT_KEYS,
                   key=lambda k: (-votes.get(k, 0.0),
                                  -int(stats.get(k, 0) or 0),
                                  STAT_KEYS.index(k)))
    top, second = order[0], order[1]
    if split:
        alternatives.append(EvAlternative(
            "Concentrated", {top: total},
            f"All {total} points into {STAT_LABELS[top]} — the most "
            f"common vanilla shape ({N_GEN3 - N_SPLIT} of {N_GEN3} "
            f"species use one stat)."))
    elif total >= 2 and votes.get(second, 0) > 0:
        alternatives.append(EvAlternative(
            "Split", {top: total - 1, second: 1},
            f"{total - 1}+1 across {STAT_LABELS[top]} and "
            f"{STAT_LABELS[second]} — comparable species also award "
            f"{STAT_LABELS[second]}."))
    other_totals = sorted(t for t in cat_counts if t != total)
    if other_totals:
        t2 = other_totals[-1] if total < max(cat_counts) else other_totals[0]
        a2, _ = _allocate(t2, stats, votes)
        alternatives.append(EvAlternative(
            f"{t2}-point total", a2,
            f"{cat_counts[t2]} of {n_pool} vanilla species in this group "
            f"award {t2} point{'s' if t2 != 1 else ''} instead."))

    return EvSuggestion(
        evs={k: v for k, v in alloc.items() if v},
        total=total,
        category=eff_category,
        category_text=category_text,
        rationale=rationale,
        examples=examples,
        alternatives=alternatives,
        uncertain=uncertain,
    )
