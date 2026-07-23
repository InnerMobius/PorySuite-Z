# Time-of-Day Wild Encounters — Master Plan

**Goal:** let projects that have a time-of-day system set different wild-encounter tables per phase
(grass / surf / rock smash / fishing), while projects without one (including vanilla pokefirered) keep
working unchanged and byte-identical.

**Decisions locked (2026-07-23):** 3 phases — Morning / Day / Night (Evening reserved, not used). Full
in-app editor in scope, built after the engine + data plumbing is verified.

---

## Ground truth (all read from source, not assumed)

- **FireRed carts have no RTC.** Vanilla pokefirered has no time-of-day at all. This project built its own:
  an in-game minute counter in `VAR_TIME_MINUTES`, advanced per free-field frame in `src/time_of_day.c`.
- **Single source of truth:** `PorySuite_GetTimeOfDay()` returns a phase constant. Enum in
  `include/time_of_day.h`: `TIME_MORNING=0, TIME_DAY=1, TIME_EVENING=2, TIME_NIGHT=3, TIME_PHASE_COUNT=4`.
  Live function maps 04:00–09:59→MORNING, 10:00–17:59→DAY, else NIGHT. **EVENING is defined but never
  returned.**
- **Encounter engine** (`src/wild_encounter.c`): a flat `gWildMonHeaders[]` keyed by `(mapGroup, mapNum)`.
  Each header carries `landMonsInfo / waterMonsInfo / rockSmashMonsInfo / fishingMonsInfo`. Lookup
  (`GetCurrentMapWildMonHeaderId`) finds the first header matching the player's map.
- **Precedent already in the engine:** the Altering Cave selects among several consecutive headers for the
  *same* map with `i += VarGet(VAR_ALTERING_CAVE_WILD_SET)` (bounded by `NUM_ALTERING_CAVE_TABLES`). This is
  exactly the shape time-of-day needs — a table swap by index.
- **Codegen:** `src/data/wild_encounters.json` → `src/data/wild_encounters.h` via `jsonproc` over the Inja
  template `src/data/wild_encounters.json.txt` (`json_data_rules.mk`). The template emits one set of arrays +
  one header per `encounters[]` entry, and already gates optional output on `existsIn(encounter, "…_mons")`.
- **App today:** `core/encounter_data.py` is READ-ONLY (feeds the Pokédex habitat panel). No encounter
  editor tab exists.

## The correctness trap (caught at design time)

The phase enum is **not dense**: NIGHT = 3, but three tables occupy slots 0/1/2. A raw `i += GetTimeOfDay()`
overshoots at night into the next map's block. **The engine must offset by a phase→slot map, not the raw
enum value.** The tool generates that map from the phases the project actually uses:
`sTimeOfDayWildSlot[TIME_PHASE_COUNT] = { MORNING:0, DAY:1, EVENING:→0 fallback, NIGHT:2 }`. A project that
later activates Evening regenerates a different map. Never hardcode the slot order.

## Chosen mechanism

Mirror the Altering Cave. A time-aware map emits **N consecutive headers** (one per active phase), and the
lookup adds the phase's *slot* when the map is time-aware. Struct unchanged, Try/Generate paths unchanged,
one new branch in the lookup — coexisting with the Altering Cave branch, not replacing it.

## Data format — strict superset of vanilla

One `encounters[]` entry per map, as today. A time-aware map gains an optional `"time_of_day"` object:

```json
{
  "map": "MAP_ROUTE1", "base_label": "sRoute1",
  "time_of_day": {
    "morning": { "land_mons": {…}, "fishing_mons": {…} },
    "day":     { "land_mons": {…} },
    "night":   { "land_mons": {…}, "water_mons": {…} }
  }
}
```

- **No `time_of_day` key → exactly the vanilla entry → byte-identical output.** A vanilla
  `wild_encounters.json` has zero such keys, so its generated `.h` does not move a single byte.
- Phase keys are the project's own phase names (lower-cased enum tails), never a hardcoded list.
- A phase may omit a category; that phase's header gets `NULL` there, exactly like a vanilla map with no
  water.

---

## Phase breakdown

### P1 — Capability + read model (no engine changes, no risk)

**Capability parser `core/time_of_day.py` — DONE, audited (round 18).** Fixed after audit: dead `#if 0`
code no longer reads as a live clock; weak stubs in any spelling (`__attribute__((weak))`, `__weak`,
`WEAK`) are skipped; the weak check is scoped to the function's own declarator (a weak neighbour no longer
poisons it); parenthesised `return (TIME_X);` is recognised; the count sentinel is found by name anywhere,
not only if last. **Known residual limitation:** a clock guarded by `#ifdef SOME_MACRO` cannot be resolved
statically — suppressing arbitrary `#ifdef` would break legitimate `#ifdef FIRERED`-style guards, so only
unambiguous `#if 0` is treated as dead. Acceptable: the tool's own stub is canonical, and the failure mode
is a rare hand-rolled pattern.

Remaining P1: the read model (loader into a writable encounter structure).

- `core/time_of_day.py`: parse `include/time_of_day.h` for the `TIME_*` enum → ordered phases + their enum
  indices; parse `src/time_of_day.c` for which phases `PorySuite_GetTimeOfDay()` actually returns → the
  ACTIVE phase set and the dense phase→slot map. Detect whether the project has a time system at all
  (`PorySuite_GetTimeOfDay` defined + enum present). Everything downstream gates on this.
- Extend the encounter loader into a writable model that also reads existing `time_of_day` blocks.
- Gate: vanilla project → capability absent, single tables, load→save round-trips byte-identical.

### P2 — Writer (JSON round-trip discipline)
- Write `wild_encounters.json` back; unchanged content → identical bytes (same discipline as the Fame
  Checker writer). `convert_map_to_time_of_day` copies the current table into each active phase as a start.
- Gate: no-edit save = no diff; convert then revert = no diff; vanilla untouched.

### P3 — Engine patch (patcher refactors the engine; NOT hand-edited)

**Data format finalised + codegen DONE, verified with jsonproc (no ROM build needed for this half):**
- `time_of_day` is an ordered **ARRAY** of `{ "phase": key, <categories> }`, NOT an object — jsonproc/Inja
  reorders object keys ALPHABETICALLY (verified: morning/day/night came out day/morning/night), which would
  emit per-phase headers out of order and send night encounters to the day table. Array position IS the
  table slot. `core/encounter_edit.py` updated to array form; all P2 byte-identical guarantees re-verified.
- Inja template patched (verified copy in scratchpad) to expand a `time_of_day` array into N arrays + N
  consecutive headers, gated on `existsIn(encounter, "time_of_day")`. **Proven byte-identical to the stock
  template for every non-time map** (installed-path output == repo `wild_encounters.h`); a converted map
  expands to 3 arrays + 3 consecutive headers in morning/day/night order.

**Engine patch DONE + BUILDS — `core/wild_encounter_tod_patch.py`:**
- Generates `src/data/wild_encounters_tod_slots.h` — phase→slot via designated initialisers keyed by the
  phase enum (`[TIME_MORNING]=0,[TIME_DAY]=1,[TIME_NIGHT]=2`) + `WILD_TOD_PHASE_COUNT`, from the capability.
- Patches `GetCurrentMapWildMonHeaderId` (PORYSUITE-TOD sentinels, idempotent): an `else if` to the
  Altering-Cave branch that, when `gWildMonHeaders[i+1]` is the same map, does
  `i += sWildTodSlot[PorySuite_GetTimeOfDay()]` (clamped). Peek-next detection — NO membership table needed;
  Altering Cave keeps its own branch first so they never collide. Plus the slot-header include.
- Installs the verified template (guarded by the `time_of_day` marker; idempotent).
- **Verified with a full `make firered_modern` build (EXIT=0)** on the live project with Route 1 converted to
  a 3-phase demo: 3 phase arrays + 3 consecutive FireRed headers in morning/day/night order; symbols present
  in the ELF/map; LeafGreen variant correctly `#ifdef`'d out. Idempotent (2nd apply = no-op). Applied to a
  git-clean project so it reverts exactly.

**Left:** P4 editor, and the user's own in-game confirmation (drivable once P4 lets them pick maps).

### P4 — Editor tab
- Wild-encounters editor is for EVERY project — a plain per-map grass/surf/rock/fishing table editor is
  the baseline all users get.
- **The time-of-day controls appear ONLY when the project has a clock.** No greyed-out button, no
  explanatory note — when `parse_time_of_day().present` is False the per-phase sub-tabs and the "convert
  to time-of-day" action are simply not rendered, so a vanilla user sees an ordinary single-table editor
  as if the feature didn't exist. (Decision 2026-07-23: hide, don't disable. "Editor for the masses.")
- When the project DOES have a clock: per-phase sub-tabs (built from the project's own active phases),
  the "convert this map to time-of-day" button, and a way back to a single table.
- All standing UI rules (no-scroll dropdowns, per-item dirty markers, F5 `load()` reset contract, char
  limits on any text field).

## Standing constraints
- **Do NOT hand-edit pokefirered.** The engine/template/codegen edits in P3 are applied by the tool's
  patcher, the same way other engine features are.
- **Project-agnostic:** phases, phase names, slot map, and category set all parsed from the project. Never
  assume Morning/Day/Night, never assume Kanto maps.
- Auditor runs in tangent through every phase.
