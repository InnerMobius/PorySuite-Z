"""core/stat_balance.py — pure logic for the Stat Balance Assistant.

Everything in here is deterministic, local, Qt-free and independently
testable. The presentation lives in ``ui/stat_balance_panel.py``; this module
owns:

* Base Stat Total (BST) calculation — exactly the six base stats, nothing else.
* The DESCRIPTIVE classification bands (contiguous, no gaps, no overlaps) —
  where a total sits on the Gen 3 spectrum, never a verdict.
* Intended-tier target ranges and the above/within/below feedback (the
  only place verdict-shaped language is allowed, because it compares
  against the USER'S own stated target).
* Per-stat level assessment (low / moderate / high) with a transparent,
  stated basis — never a bare verdict.
* Evolution-edge CLASSIFICATION (forward / reverse / ambiguous-cyclic) and
  neighbourhood traversal that survives branching and cycles (this is not
  hypothetical: the loaded project has Poliwag → Poliwhirl → Poliwrath → Poliwhirl,
  and the backward arrows are an intentional devolution mechanic).
* Nearest-BST lookups against the project roster, and vanilla-Gen-3
  comparison backed by the full authentic FireRed table
  (``core/gen3_reference.py``).
* ``BalancePrefsStore`` — editor-only persistence of per-species intent
  (tier / custom range). Lives in PorySuite's own data folder, NEVER in
  the game source tree.

Design rule, from the feature owner: *inform the designer, don't override
the designer*. Nothing in this module mutates species data, and no function
returns an instruction — only measurements, comparisons and clearly-labelled
heuristic observations.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.gen3_reference import GEN3_SPECIES, Gen3Species
from core.gen3_reference import lookup as gen3_lookup

_log = logging.getLogger("StatBalance")

# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

#: Canonical order, matching the editor's Base Stats group top to bottom.
STAT_KEYS: Tuple[str, ...] = (
    "hp", "attack", "defense", "sp_attack", "sp_defense", "speed")

STAT_LABELS: Dict[str, str] = {
    "hp": "HP", "attack": "Attack", "defense": "Defense",
    "sp_attack": "Sp. Atk", "sp_defense": "Sp. Def", "speed": "Speed",
}

#: The species-data field name for each stat (pokefirered JSON keys).
STAT_SOURCE_FIELDS: Dict[str, str] = {
    "hp": "baseHP", "attack": "baseAttack", "defense": "baseDefense",
    "sp_attack": "baseSpAttack", "sp_defense": "baseSpDefense",
    "speed": "baseSpeed",
}

#: The editor caps each stat at 255, so 6 × 255 is the largest possible BST.
MAX_STAT = 255
MAX_BST = MAX_STAT * len(STAT_KEYS)


def bst(stats: Dict[str, int]) -> int:
    """Sum of exactly the six base stats. EV yields, IVs and every other
    species property are excluded by construction: only ``STAT_KEYS`` are
    read, and a missing key counts as 0 rather than guessing."""
    return sum(int(stats.get(k, 0) or 0) for k in STAT_KEYS)


# ─────────────────────────────────────────────────────────────────────────────
# Classification bands
# ─────────────────────────────────────────────────────────────────────────────

#: (lo, hi, label, color). hi is inclusive; None = unbounded.
#: Contiguous and non-overlapping — locked by tests at every boundary.
#: DESCRIPTIVE, not judgmental: each label says where the number sits
#: relative to the vanilla Gen 3 spectrum, never whether that is good or
#: bad — "Very weak" phrasing contradicted a green "within your target"
#: message one line below, so verdict language lives ONLY in the
#: target-feedback line now. Colors are a cool→warm magnitude ramp, not a
#: red/green verdict.
BANDS: Tuple[Tuple[int, Optional[int], str, str], ...] = (
    (0,   179,  "Minimal — below every vanilla Gen 3 species",   "#90a4ae"),
    (180, 290,  "Low — early-game range in Gen 3",               "#64b5f6"),
    (291, 350,  "Modest — typical unevolved range in Gen 3",     "#4dd0e1"),
    (351, 440,  "Mid — typical middle-stage range in Gen 3",     "#81c784"),
    (441, 490,  "Solid — lower fully-evolved range in Gen 3",    "#aed581"),
    (491, 540,  "High — upper fully-evolved range in Gen 3",    "#ffd54f"),
    (541, 599,  "Very high — above most vanilla species",        "#ffb74d"),
    (600, None, "Top-end — legendary territory in Gen 3",        "#ce93d8"),
)

BAND_EXPLANATION = (
    "This classification is a descriptive reference: it says where the "
    "total sits on the vanilla Gen 3 spectrum, nothing more. It is not an "
    "official rule of Pokémon balance and not a judgment of this creature "
    "— whether the number is right is set by YOUR intended-strength "
    "target, and BST is only one factor anyway: moves, typing, abilities, "
    "availability and evolution timing matter just as much."
)


def classify_bst(total: int) -> Tuple[str, str]:
    """Return ``(label, color)`` for a BST. Totals below 0 clamp to the
    first band; anything at or above 600 lands in the last."""
    t = max(0, int(total))
    for lo, hi, label, color in BANDS:
        if hi is None or t <= hi:
            if t >= lo:
                return label, color
    return BANDS[-1][2], BANDS[-1][3]  # pragma: no cover — bands are total


# ─────────────────────────────────────────────────────────────────────────────
# Intended tiers (editor-only design intent)
# ─────────────────────────────────────────────────────────────────────────────

TIER_UNSPECIFIED = "Unspecified"
TIER_CUSTOM = "Custom"

#: name → suggested (lo, hi) BST range; None = no target.
#: Suggested defaults, not rules imposed on anyone.
TIERS: Tuple[Tuple[str, Optional[Tuple[int, int]]], ...] = (
    (TIER_UNSPECIFIED,               None),
    ("Early-game wild encounter",    (180, 290)),
    ("Standard unevolved creature",  (291, 350)),
    ("Middle evolution",             (351, 440)),
    ("Final evolution",              (441, 540)),
    ("Boss / exceptional creature",  (541, 680)),
    (TIER_CUSTOM,                    None),  # range supplied by the user
)

TIER_NAMES: Tuple[str, ...] = tuple(name for name, _ in TIERS)
_TIER_RANGES: Dict[str, Optional[Tuple[int, int]]] = dict(TIERS)


def tier_range(tier: str,
               custom: Optional[Tuple[int, int]] = None
               ) -> Optional[Tuple[int, int]]:
    """Resolve a tier name to its target BST range, or None for no target.

    ``Custom`` uses *custom* after validation; an invalid custom range
    (missing, reversed, out of bounds) resolves to None rather than
    producing nonsense feedback.
    """
    if tier == TIER_CUSTOM:
        rng = validate_custom_range(custom)
        return rng
    return _TIER_RANGES.get(tier)


def validate_custom_range(rng) -> Optional[Tuple[int, int]]:
    """Return a clean (lo, hi) or None. lo/hi must be ints within
    0..MAX_BST with lo <= hi; anything else is rejected, never guessed."""
    try:
        lo, hi = int(rng[0]), int(rng[1])
    except (TypeError, ValueError, IndexError):
        return None
    if lo < 0 or hi > MAX_BST or lo > hi:
        return None
    return lo, hi


def target_feedback(total: int,
                    rng: Optional[Tuple[int, int]]
                    ) -> Tuple[str, int, str]:
    """Compare a BST with a target range.

    Returns ``(status, delta, text)`` where status is one of
    ``"none" | "below" | "within" | "above"`` and delta is how far outside
    the range the total sits (0 when within or no range).
    """
    if not rng:
        return "none", 0, ""
    lo, hi = rng
    if total < lo:
        d = lo - total
        return "below", d, (f"{d} BST point{'s' if d != 1 else ''} below the "
                            f"selected target range ({lo}–{hi}).")
    if total > hi:
        d = total - hi
        return "above", d, (f"{d} BST point{'s' if d != 1 else ''} above the "
                            f"selected target range ({lo}–{hi}).")
    return "within", 0, f"Within the selected target range ({lo}–{hi})."


# ─────────────────────────────────────────────────────────────────────────────
# Stat combos
# ─────────────────────────────────────────────────────────────────────────────
#
# The Combat Role dropdown that used to live here was REMOVED (2026-11-22)
# after measuring its real effect: against the loaded project's full roster
# it changed a note's caution-vs-info severity in 77 of 489 species/tier
# cases and did nothing at all without a tier selected, which is how the
# panel is normally seen. The thing it existed for — recognising that a
# tank's high Defense is intent, not a problem — is handled role-free
# below: a high combo offset by weak stats elsewhere reads as deliberate
# specialisation on its own evidence. Do not reintroduce a role selector
# without a visible, demonstrably useful output for it.

#: Composite measures the contextual analysis looks at. Pairs of stats —
#: deliberately simple sums, because the point is transparency, not a
#: pretend battle simulation.
COMBOS: Tuple[Tuple[str, str, Tuple[str, str]], ...] = (
    ("physical_durability", "physical durability (HP + Defense)",
     ("hp", "defense")),
    ("special_durability", "special durability (HP + Sp. Def)",
     ("hp", "sp_defense")),
    ("physical_pressure", "physical offensive pressure (Attack + Speed)",
     ("attack", "speed")),
    ("special_pressure", "special offensive pressure (Sp. Atk + Speed)",
     ("sp_attack", "speed")),
)


# ─────────────────────────────────────────────────────────────────────────────
# Roster statistics (reference distributions from the loaded project)
# ─────────────────────────────────────────────────────────────────────────────

#: Below this many comparable species, percentile thresholds are too noisy
#: to present; the analysis falls back to the creature's own stat spread.
MIN_ROSTER_SAMPLE = 8


def _tercile_bounds(values: Sequence[int]) -> Tuple[float, float]:
    """(low_cut, high_cut): the 1/3 and 2/3 points of a sorted sample."""
    s = sorted(values)
    n = len(s)
    return s[(n - 1) // 3], s[(2 * (n - 1)) // 3]


class RosterStats:
    """Per-stat and per-combo tercile thresholds derived from a roster.

    *roster* maps species key → stats dict (STAT_KEYS). When *bst_range* is
    given, only species whose BST falls inside it contribute — that is what
    makes the per-stat feedback tier-aware. ``usable`` says whether the
    sample was big enough to mean anything.
    """

    def __init__(self, roster: Dict[str, Dict[str, int]],
                 bst_range: Optional[Tuple[int, int]] = None):
        rows = []
        for stats in roster.values():
            total = bst(stats)
            if total <= 0:
                continue  # empty/placeholder rows carry no information
            if bst_range and not (bst_range[0] <= total <= bst_range[1]):
                continue
            rows.append(stats)
        self.sample_size = len(rows)
        self.bst_range = bst_range
        self.usable = self.sample_size >= MIN_ROSTER_SAMPLE
        self.stat_cuts: Dict[str, Tuple[float, float]] = {}
        self.combo_cuts: Dict[str, Tuple[float, float]] = {}
        if self.usable:
            for k in STAT_KEYS:
                self.stat_cuts[k] = _tercile_bounds(
                    [int(r.get(k, 0) or 0) for r in rows])
            for key, _label, (a, b) in COMBOS:
                self.combo_cuts[key] = _tercile_bounds(
                    [int(r.get(a, 0) or 0) + int(r.get(b, 0) or 0)
                     for r in rows])

    def describe(self) -> str:
        if not self.usable:
            return ""
        if self.bst_range:
            return (f"compared with the {self.sample_size} project species "
                    f"in the {self.bst_range[0]}–{self.bst_range[1]} BST "
                    f"range")
        return f"compared with all {self.sample_size} project species"


def _level_from_cuts(value: float, cuts: Tuple[float, float]) -> str:
    lo_cut, hi_cut = cuts
    if value > hi_cut:
        return "high"
    if value < lo_cut:
        return "low"
    return "moderate"


def stat_levels(stats: Dict[str, int],
                roster_stats: Optional["RosterStats"] = None
                ) -> Dict[str, Tuple[str, str]]:
    """Per-stat ``{key: (level, basis)}`` with level low/moderate/high.

    With a usable roster sample the basis is the roster tercile comparison
    (tier-aware when the RosterStats was built with a range). Without one,
    the fallback is purely descriptive: how this creature's own total is
    spread across its stats (no judgment involved).
    """
    out: Dict[str, Tuple[str, str]] = {}
    if roster_stats and roster_stats.usable:
        basis = roster_stats.describe()
        for k in STAT_KEYS:
            lvl = _level_from_cuts(int(stats.get(k, 0) or 0),
                                   roster_stats.stat_cuts[k])
            out[k] = (lvl, f"{lvl.capitalize()} {basis} (heuristic terciles).")
        return out
    total = bst(stats)
    avg = total / len(STAT_KEYS) if total else 0
    for k in STAT_KEYS:
        v = int(stats.get(k, 0) or 0)
        if avg <= 0:
            out[k] = ("moderate", "No data to compare against.")
        elif v >= avg * 1.35:
            out[k] = ("high", "Carries an above-average share of this "
                              "creature's own stat total.")
        elif v <= avg * 0.65:
            out[k] = ("low", "Carries a below-average share of this "
                             "creature's own stat total.")
        else:
            out[k] = ("moderate", "Close to this creature's own per-stat "
                                  "average.")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Advisory notes
# ─────────────────────────────────────────────────────────────────────────────

def advisory_notes(stats: Dict[str, int],
                   roster_stats: Optional["RosterStats"]) -> List[Tuple[str, str]]:
    """Contextual observations as ``(kind, text)`` with kind
    ``"caution" | "info"``.

    Only runs against a usable, TIER-SCOPED roster sample: without a chosen
    target range there is no stated intent to compare against, and without
    enough comparable species a percentile is noise. Both halves of that
    rule come straight from the feature spec ("no automatic accusation",
    "if a warning is based on a heuristic, communicate that fact").

    Deliberate specialisation is recognised from the stats themselves: a
    high combo offset by weak stats elsewhere (Geodude's armour vs its
    Speed and Sp. Atk) reads as design, and only a high combo with NO
    offsetting weakness earns a caution.
    """
    notes: List[Tuple[str, str]] = []
    if not roster_stats or not roster_stats.usable or not roster_stats.bst_range:
        return notes

    basis = roster_stats.describe()
    levels = stat_levels(stats, roster_stats)
    low_stats = [k for k in STAT_KEYS if levels[k][0] == "low"]
    high_stats = [k for k in STAT_KEYS if levels[k][0] == "high"]

    for key, label, (a, b) in COMBOS:
        value = int(stats.get(a, 0) or 0) + int(stats.get(b, 0) or 0)
        lvl = _level_from_cuts(value, roster_stats.combo_cuts[key])
        if lvl != "high":
            continue
        if low_stats:
            weak = ", ".join(STAT_LABELS[k] for k in low_stats)
            notes.append((
                "info",
                f"High {label} for the selected tier, offset by low "
                f"{weak} — reads as deliberate specialisation "
                f"({basis})."))
        else:
            notes.append((
                "caution",
                f"High {label} for the selected tier — this creature may "
                f"hold up better than intended at that power level "
                f"({basis}; heuristic, and BST alone cannot account for "
                f"typing, abilities, moves or encounter levels)."))

    if len(high_stats) >= 3 and not low_stats:
        strong = ", ".join(STAT_LABELS[k] for k in high_stats)
        notes.append((
            "caution",
            f"Several strong stats ({strong}) with no weak ones for the "
            f"selected tier ({basis}; heuristic)."))
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# Evolution neighbourhood
# ─────────────────────────────────────────────────────────────────────────────

class EvolutionEdges:
    """The project's evolution graph with each edge ORIENTED.

    Decomp projects can make evolution reversible (the loaded project does:
    every line devolves back with a dedicated item, giving real cycles like
    Poliwag → Poliwhirl → Poliwrath → Poliwhirl). Treating every raw edge as forward
    progression made the panel call an intentional devolution "161 less BST
    than its evolution" — wrong, so edges are classified first:

    * ``forward`` — edges judged to be forward evolution.
    * ``reverse`` — ``(src, tgt)`` edges judged to be reverse evolution
      (devolution).
    * ``ambiguous_groups`` — sets of species forming a cycle whose
      direction could NOT be established; no orientation is invented for
      them (transparent uncertainty beats a guessed arrow).

    HOW an edge is judged reverse — from the project's own data, never
    from BST: a cycle-closing edge is reverse only when the project shows
    a consistent devolution *mechanism*. Within each cyclic group the edges
    are grouped by their (method, param) signature; if removing exactly one
    signature's edges breaks every cycle in the group, that signature is
    the devolution convention and those edges are reverse. A signature that
    resolved other groups is trusted for a small group it also breaks (the
    project-wide convention). Anything else stays ambiguous. In a vanilla
    project there are no cycles, so every edge is simply forward.
    """

    def __init__(self, forward: Dict[str, List[str]],
                 reverse: List[Tuple[str, str]],
                 ambiguous_groups: List[frozenset]):
        self.forward = forward
        self.reverse = reverse
        self.ambiguous_groups = ambiguous_groups

    def in_ambiguous_group(self, species: str) -> Optional[frozenset]:
        for g in self.ambiguous_groups:
            if species in g:
                return g
        return None

    def reverse_edges_touching(self, species: str) -> List[Tuple[str, str]]:
        return [(s, t) for s, t in self.reverse
                if s == species or t == species]


def _strongly_connected(adj: Dict[str, List[str]]) -> List[List[str]]:
    """Iterative Tarjan SCC — no recursion, so a pathological project
    cannot hit the recursion limit."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: Dict[str, bool] = {}
    stack: List[str] = []
    out: List[List[str]] = []
    counter = [0]

    for root in adj:
        if root in index:
            continue
        work = [(root, iter(adj.get(root, ())))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in adj:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, iter(adj.get(nxt, ()))))
                    advanced = True
                    break
                elif on_stack.get(nxt):
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == node:
                        break
                out.append(comp)
    return out


def _has_cycle(adj: Dict[str, List[str]], nodes: frozenset,
               dropped: set) -> bool:
    """Cycle check restricted to *nodes*, ignoring edges in *dropped*."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    for start in nodes:
        if color[start] != WHITE:
            continue
        work = [(start, iter(adj.get(start, ())))]
        color[start] = GREY
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in color or (node, nxt) in dropped:
                    continue
                if color[nxt] == GREY:
                    return True
                if color[nxt] == WHITE:
                    color[nxt] = GREY
                    work.append((nxt, iter(adj.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                work.pop()
    return False


def classify_evolution_edges(
        evos: Dict[str, List[Dict]]) -> EvolutionEdges:
    """Orient a project's raw evolution data (see ``EvolutionEdges``).

    *evos* maps species → list of evolution dicts with ``method``,
    ``param`` and ``targetSpecies`` (exactly what ``get_evolutions``
    returns). Malformed rows and self-loops are skipped.
    """
    edges: List[Tuple[str, str, Tuple]] = []
    adj: Dict[str, List[str]] = {}
    for src, rows in (evos or {}).items():
        for row in rows or []:
            tgt = (row or {}).get("targetSpecies")
            if not isinstance(tgt, str) or not tgt or tgt == src:
                continue
            sig = ((row or {}).get("method"), (row or {}).get("param"))
            edges.append((src, tgt, sig))
            adj.setdefault(src, []).append(tgt)
            adj.setdefault(tgt, [])

    cyclic_groups = [frozenset(c) for c in _strongly_connected(adj)
                     if len(c) > 1]
    reverse: List[Tuple[str, str]] = []
    ambiguous: List[frozenset] = []

    if cyclic_groups:
        per_group: List[Tuple[frozenset, Dict[Tuple, List[Tuple[str, str]]]]] = []
        for grp in cyclic_groups:
            by_sig: Dict[Tuple, List[Tuple[str, str]]] = {}
            for src, tgt, sig in edges:
                if src in grp and tgt in grp:
                    by_sig.setdefault(sig, []).append((src, tgt))
            per_group.append((grp, by_sig))

        resolved_sigs: set = set()
        unresolved: List[Tuple[frozenset, Dict]] = []
        for grp, by_sig in per_group:
            n_edges = sum(len(es) for es in by_sig.values())
            # A "convention" that is every edge in the group explains
            # nothing (e.g. a mutual pair using the same item both ways) —
            # that group is genuinely ambiguous.
            working = [sig for sig, es in by_sig.items()
                       if len(es) < n_edges
                       and not _has_cycle(adj, grp, set(es))]
            if len(working) == 1:
                reverse.extend(by_sig[working[0]])
                resolved_sigs.add(working[0])
            else:
                unresolved.append((grp, by_sig))
        # Second pass: a signature that resolved other groups is the
        # project's convention; trust it where it also works.
        for grp, by_sig in unresolved:
            n_edges = sum(len(es) for es in by_sig.values())
            match = [sig for sig in by_sig
                     if sig in resolved_sigs
                     and len(by_sig[sig]) < n_edges
                     and not _has_cycle(adj, grp, set(by_sig[sig]))]
            if len(match) == 1:
                reverse.extend(by_sig[match[0]])
            else:
                ambiguous.append(grp)

    rev_set = set(reverse)
    forward: Dict[str, List[str]] = {}
    ambiguous_all = set().union(*ambiguous) if ambiguous else set()
    for src, tgt, _sig in edges:
        if (src, tgt) in rev_set:
            continue
        # Inside an unresolved cycle no direction is trusted; those edges
        # are reported as a group, not drawn as chains.
        if src in ambiguous_all and tgt in ambiguous_all:
            continue
        forward.setdefault(src, []).append(tgt)
    return EvolutionEdges(forward, reverse, ambiguous)


def evolution_paths(species: str,
                    forward: Dict[str, List[str]],
                    bst_of: Dict[str, int],
                    max_back: int = 2,
                    max_paths: int = 4,
                    max_forward: int = 3) -> List[List[Tuple[str, int]]]:
    """Evolution lines through *species*, as lists of ``(species, bst)``.

    * ``forward`` maps species → list of evolution targets (straight from
      the project's evolution data; malformed names simply have no entry).
    * Walks BACK while the pre-evolution is unambiguous (a species with two
      pre-evolutions is a branch seen from the wrong end — representing it
      as one linear chain would be a lie, so the walk stops there).
    * Walks FORWARD along every branch, up to *max_paths* paths.
    * A visited set cuts cycles: the loaded project really has
      Poliwag → Poliwhirl → Poliwrath → Poliwhirl, and the traversal must terminate.
    * Never infers a stage from BST; only real evolution edges are used.
    """
    reverse: Dict[str, List[str]] = {}
    for src, targets in forward.items():
        for t in targets:
            reverse.setdefault(t, []).append(src)

    # Backwards: linear and unambiguous only.
    back: List[str] = []
    seen = {species}
    cur = species
    for _ in range(max_back):
        pres = reverse.get(cur, [])
        if len(pres) != 1 or pres[0] in seen:
            break
        cur = pres[0]
        seen.add(cur)
        back.append(cur)
    back.reverse()

    # Forwards: depth-first over branches, visited-guarded.
    paths: List[List[str]] = []

    def _walk(node: str, trail: List[str], depth: int) -> None:
        if len(paths) >= max_paths:
            return
        targets = [t for t in forward.get(node, []) if t not in trail]
        if depth >= max_forward or not targets:
            paths.append(trail)
            return
        for t in targets:
            if len(paths) >= max_paths:
                return
            _walk(t, trail + [t], depth + 1)

    _walk(species, back + [species], 0)

    return [[(sp, int(bst_of.get(sp, 0) or 0)) for sp in p] for p in paths]


def regression_note(path: List[Tuple[str, int]]) -> str:
    """One line when a later stage LOSES substantial BST — flagged for
    review, never called a mistake (some hacks do this deliberately)."""
    for (a, abst), (b, bbst) in zip(path, path[1:]):
        if abst and bbst and bbst < abst - 10:
            return (f"Note: {b} has {abst - bbst} less BST than {a}. That "
                    f"can be deliberate (trade-off evolutions exist) — "
                    f"flagged only so it gets a look.")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Reference comparisons
# ─────────────────────────────────────────────────────────────────────────────

#: Every vanilla FireRed species as (name, Gen 3 BST). Backed by
#: ``core/gen3_reference.py``, which is generated from the vanilla
#: base-stats table in the decomp's own git history — authentic
#: Generation 3 values, NOT later-generation revisions.
GEN3_REFERENCE: Tuple[Tuple[str, int], ...] = tuple(
    (s.name, s.bst) for s in GEN3_SPECIES)


def vanilla_comparison(name: str, stats: Dict[str, int]
                       ) -> Tuple[Optional[str], Optional[Gen3Species]]:
    """How this creature relates to vanilla Gen 3, by DISPLAY NAME.

    Returns ``("exact", species)`` when a vanilla species of the same name
    exists and the six stats are identical to its Gen 3 values — reference
    comparisons would be redundant then. ``("modified", species)`` when the
    name matches but the stats differ (the interesting case: show the
    difference). ``(None, None)`` for a fully custom creature; no
    relationship is invented from stats alone.
    """
    ref = gen3_lookup(name)
    if ref is None:
        return None, None
    ours = (int(stats.get("hp", 0) or 0), int(stats.get("attack", 0) or 0),
            int(stats.get("defense", 0) or 0),
            int(stats.get("sp_attack", 0) or 0),
            int(stats.get("sp_defense", 0) or 0),
            int(stats.get("speed", 0) or 0))
    return ("exact" if ours == ref.stats else "modified"), ref


def nearest_entries(total: int,
                    table: Iterable[Tuple[str, int]],
                    count: int = 2,
                    exclude: Iterable[str] = ()) -> List[Tuple[str, int]]:
    """The *count* entries whose BST sits closest to *total*. Ties resolve
    toward the lower BST, then name, so results are deterministic."""
    skip = set(exclude)
    rows = [(abs(b - total), b, name) for name, b in table
            if name not in skip and b > 0]
    rows.sort()
    return [(name, b) for _d, b, name in rows[:count]]


# ─────────────────────────────────────────────────────────────────────────────
# Editor-only persistence of per-species intent
# ─────────────────────────────────────────────────────────────────────────────

#: "role" is gone (see the Combat Role removal note above); _clean drops a
#: stored role on the next write, so old files prune themselves.
PREFS_DEFAULT = {"tier": TIER_UNSPECIFIED,
                 "custom_min": None, "custom_max": None}


def species_pref_key(species: str, form: Optional[str] = None) -> str:
    """Stable identifier for prefs: the species constant, plus the form
    constant when a form is being edited, so a form's intent never lands
    on its base species (or vice versa)."""
    return f"{species}::{form}" if form else str(species)


class BalancePrefsStore:
    """Per-project, per-species design intent, stored as one JSON file in
    PorySuite's own ``data/`` folder (the durable home — ``projects.json``
    and ``git_remotes.json`` already live there).

    NOTHING here touches the game source tree: this is editor metadata by
    design, so no ROM source file is rewritten to remember a dropdown.

    Shape::

        {"projects": {"<normalised abs project dir>": {
            "SPECIES_X":        {"tier": ..., "role": ..., ...},
            "SPECIES_X::FORM_Y": {...}
        }}}
    """

    def __init__(self, path: str):
        self._path = path
        self._data: Dict = {}
        self._loaded = False

    # -- internals --------------------------------------------------------

    @staticmethod
    def _project_key(project_dir: str) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(
            project_dir or "")))

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict) and isinstance(raw.get("projects"), dict):
                self._data = raw
            else:
                self._data = {}
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError):
            _log.exception("Could not read %s; starting empty (the file is "
                           "left untouched until the next preference edit)",
                           self._path)
            self._data = {}
        self._data.setdefault("projects", {})

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self._path)
        except OSError:
            _log.exception("Could not write %s; the preference change is "
                           "kept for this session only", self._path)

    @staticmethod
    def _clean(prefs: Dict) -> Dict:
        """Keep only known keys with valid values; defaults are dropped so
        the file stays small and 'Unspecified' never needs storing."""
        out = {}
        tier = prefs.get("tier")
        if tier in TIER_NAMES and tier != TIER_UNSPECIFIED:
            out["tier"] = tier
        rng = validate_custom_range(
            (prefs.get("custom_min"), prefs.get("custom_max")))
        if rng:
            out["custom_min"], out["custom_max"] = rng
        return out

    # -- API ---------------------------------------------------------------

    def get(self, project_dir: str, species_key: str) -> Dict:
        """Prefs for one species in one project; always a full dict with
        defaults filled in. Unknown projects/species get pure defaults —
        the editor never assumes a new species is meant for any tier."""
        self._load()
        stored = (self._data["projects"]
                  .get(self._project_key(project_dir), {})
                  .get(species_key, {}))
        out = dict(PREFS_DEFAULT)
        out.update(self._clean(stored if isinstance(stored, dict) else {}))
        return out

    def set(self, project_dir: str, species_key: str, prefs: Dict) -> None:
        self._load()
        pkey = self._project_key(project_dir)
        if not pkey or not species_key:
            return
        clean = self._clean(prefs)
        projects = self._data["projects"]
        if clean:
            projects.setdefault(pkey, {})[species_key] = clean
        else:
            # All defaults → remove the row (and the project when empty).
            projects.get(pkey, {}).pop(species_key, None)
            if pkey in projects and not projects[pkey]:
                del projects[pkey]
        self._save()
