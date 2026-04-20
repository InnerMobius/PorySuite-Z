# Post-Release Code Audit Plan

**Trigger:** The v0.0.55b release received public criticism on Reddit for containing
AI-generated code with dead callbacks, unused patches, and fragile upstream
dependencies. This plan is the full cleanup response.

**Goals (in priority order):**
1. **Correctness** — every flow produces the right output, no false positives, no silent failures.
2. **No dead code** — every line is traceable end-to-end to a consumer. If nobody reads it, delete it.
3. **No duplication** — shared patterns live in one place, not copy-pasted across tabs.
4. **Smaller on disk** — oversized files get split, unused assets get removed.
5. **Documentation matches reality** — stale docs are worse than no docs.

**Rules:**
- No changes to `pokefirered/` game source files, ever.
- Every bug found goes into `BUGS.md`. Every fix goes into `CHANGELOG.md`. Both before moving on.
- User tests each phase before the next phase starts. No stacking unverified changes.
- Each phase ends with a plain-English test plan the user runs.

---

## Phase 0 — Porymap Bridge ✅ COMPLETE (2026-04-15)

Scope: `porymap_patches/`, `porymap_bridge/`, bridge wiring in `ui/unified_mainwindow.py`.

Done:
- Removed 3 dead C++ Q_INVOKABLE getters (`getMapHeader`, `getCurrentTilesets`, `getMapConnections`).
- Added `_check_anchor()` uniqueness guard to `apply_patches.py`.
- Pruned 9 unused JS callback handlers.
- Pruned 5 unused Python bridge signals.
- Updated `_on_bridge_map_opened` signature to match simplified signal.

Result: bridge is 5 callbacks, 5 signals, 2 user actions, 1 command — every line consumed.

---

## Phase 1 — Dirty-Flag Audit (Every Editor) ✅ MOSTLY COMPLETE

**Problem:** The auto-connected `_dirty` lambda in `mainwindow.py:__init__` fires
`setWindowModified(True)` on every widget change signal, including programmatic
loads. This means just viewing data marks the project modified.

**Fix pattern (installed and documented in CLAUDE.md):**
- `self._loading_depth` counter on `MainWindow`
- `_loading_guard()` context manager
- `_dirty` lambda no-ops while `_loading_depth > 0`
- Every load/refresh/populate method wraps its widget-setting block in the guard.
- Per-item amber row/card tinting (Pattern A/B/C — see CLAUDE.md "Tab load() / F5 Refresh Contract")
- F5/Refresh contract: `load()` must stop timers, clear in-memory dirty state, reset ALL visual dirty state (amber groupboxes, amber rows, status labels), and guard `_rebuild_grid()` with `_loading = True/False`.

### Step 1.1 — Species tab ✅ COMPLETE
### Step 1.2 — Pokémon Data sub-tabs ✅ COMPLETE
### Step 1.3 — Items editor ✅ COMPLETE
### Step 1.4 — Moves editor ✅ COMPLETE
### Step 1.5 — Abilities editor ✅ COMPLETE
Amber row tinting on unsaved entries, `_dirty_consts` set survives rebuilds, `clear_all_dirty()` on save. Add Ability dialog template support. Dirty row on newly added abilities fixed.

### Step 1.6 — Trainers editor ✅ COMPLETE
Includes VS Seeker tier persistence, trainer class pic lookup, flag label loader, F5 discard, validation fixes, dirty-flag wiring for all sub-tabs (Trainers / Classes / Graphics).

### Step 1.7 — Starters editor ✅ COMPLETE
Dirty dot now fires `sectionDirtyChanged("starters", True)` (was incorrectly firing "species"). Amber groupbox per starter. Species display names. Dead Ability row removed. Shiny Chance + Pokéball fields added and wired.

### Step 1.8 — Wild Encounters editor
- [ ] Encounter group selection → table load
- [ ] Probability widget population

### Step 1.9 — Credits editor ✅ COMPLETE (2026-04-18)
Pattern A amber rows. `modified`/`saved` signals wired to `sectionDirtyChanged("credits", True/False)`. `_dirty_symbols` set survives `_populate_list()` rebuilds. F5 clears all amber. Save clears all amber.

### Step 1.10 — Title Screen editor
- [ ] Image/palette preview loads
- [ ] Any editable widget that gets populated on tab switch

### Step 1.11 — Sound Editor (all sub-tabs)
- [ ] Song list load
- [ ] Track/row population when selecting a song
- [ ] Piano roll load
- [ ] Instrument editor load
- [ ] Voicegroup editor load

### Step 1.12 — Overworld Graphics tab ✅ COMPLETE (v0.0.6b, 2026-04-18)
Pattern B card amber borders + Pattern C groupbox frame. `_palette_dirty` / `_sprite_png_dirty` sets. Two-pass save. F5 structural fix (explicit `load()` call in `_refresh_project()`).

### Step 1.13 — Trainer Graphics tab ✅ COMPLETE (v0.0.57b)
Pattern B card amber borders + Pattern C groupbox frame. Card grid with `_PicCard.set_dirty()`. QSplitter layout. Import PNG as Sprite. `_broadcast_palette` pushes to bus under both keys.

### Step 1.14 — EVENTide tabs (separate QMainWindow)
EVENTide has its own dirty tracking via `data_changed` signals. Audit:
- [ ] Maps → Map Manager sub-tab
- [ ] Maps → Layouts & Tilesets sub-tab
- [ ] Events tab
- [ ] Region Map tab
Confirm each tab's `data_changed` only fires on real user input, never during load.

### Step 1.15 — Verification (end of Phase 1)
- [x] Species, Pokédex, Moves, Items, Trainers, Abilities, Starters, Credits, Overworld GFX, Trainer GFX — all user-verified
- [ ] Wild Encounters, Title Screen, Sound Editor, EVENTide — pending
- [ ] Final clean-project smoke test: open project, click all tabs, confirm zero `*`

---

## Phase 1B — Sprite Rendering Pipeline ✅ COMPLETE (v0.0.6b, 2026-04-16)

Not in the original plan, but addressed as part of the correctness audit.

**Problem:** All sprite viewer sites used `QPixmap(path)` directly, ignoring the authoritative `.pal` file and any unsaved RAM edits. Editing a palette in any graphics tab had no effect on the rest of the app.

**Fix:** Two new shared modules enforce a single rendering path:
- `core/sprite_palette_bus.py` — process-wide palette bus, RAM-first, disk-fallback, emits `palette_changed(category, key)`.
- `core/sprite_render.py` — `load_sprite_pixmap(path, palette)` is the only sanctioned way to render a sprite PNG.

All viewer tabs (Pokédex, Items, Trainers, Trainer Classes, species tree, Info panel, Starters) migrated. All editor tabs (Species Graphics, Trainer Graphics, Overworld Graphics) push to the bus on every mutation. Rules documented in CLAUDE.md → "Sprite Rendering Pipeline."

---

## Phase 2 — Save Pipeline Correctness (Per Editor)

Separate from dirty-flag: does each editor's save **actually persist everything
the user edited**, and **not persist things they didn't**?

### Step 2.1 — Save coverage matrix
Build a table of every editable field × which header/JSON file it should land in ×
which save function handles it. Any field without a clear save owner is a bug.

### Step 2.2 — Dual-widget clobber audit
The memory file documents the clobber pattern (stats-page widget vs pokedex-panel
widget for the same data). Sweep every tab for the same pattern:
- [ ] Pokemon Data stats ↔ Pokédex panel (already fixed for category/description)
- [ ] Any other field shown in two places?
- [ ] EVENTide map header shown in multiple sub-tabs?

### Step 2.3 — Round-trip tests
For each editor: open project, edit one field, save, close, reopen, confirm
edit survived.

### Step 2.4 — No-op save
With no edits: save, confirm no JSON files get rewritten (mtime check). This
proves the `should_save = data != original_data` gate works for every editor.

**items.json update (v0.0.63b):** The "no-op save rewrites items.json" regression went through four rounds of root-cause hunting before the real culprit was found — `PokemonData._ensure_map` was normalizing only `self.data`, leaving `self.original_data` in its wrapped `{"items": [...]}` shape, so the diff always mismatched. Fix normalizes both sides via `_normalize_items`. Items no longer re-save after F5 / upstream pull / rebuild.

### Step 2.5 — Build-pipeline side effects (v0.0.63b)
- [x] `_prune_stale_song_s_files` also deletes stale `.o` files in `build/*/sound/songs/midi/` so upstream pulls that drop voicegroups (e.g. `voicegroup013`) don't leave ghost object files that fail the link.
- [x] `logOutput` QTextEdit excluded from the species auto-wire loop — build log output no longer flags Bulbasaur (or any species row) dirty on Make.
- [x] Dead `source_headers=` argument removed from all 8 `_load_json` callers in `core/pokemon_data_extractor.py`.

### Step 2.5 — Partial failure recovery
If header write fails mid-save, confirm no JSON gets corrupted and the UI
state is consistent with what's on disk.

---

## Phase 3 — Dead Code Elimination

**Targets:** unused functions, unused imports, unused classes, unused signals.

### Step 3.1 — Python dead code sweep
- [ ] `pyflakes` or `vulture` scan on the whole tree
- [ ] Manually review anything the tool flags with confidence score ≥ 80%
- [ ] Delete functions/classes/imports that are never called or imported
- [ ] Re-run tests

### Step 3.2 — Unused Qt signals
- [ ] For each `pyqtSignal` declared, grep for a `.connect(` of that signal
- [ ] Any signal with zero connections → remove
- [ ] Already done for bridge_watcher in Phase 0, extend to every module

### Step 3.3 — Unused UI widgets in `ui_mainwindow.py`
- [ ] Compare widget `setObjectName` entries to actual `self.ui.<name>` references
- [ ] Widgets in the `.ui` file that are never read or written → candidates for removal
- [ ] Be careful: some widgets are shown visually even if the app never touches their value

### Step 3.4 — Dead imports
- [ ] `import X` where `X` is never used
- [ ] `from Y import Z` where `Z` is never used

### Step 3.5 — Commented-out code
- [ ] Any `# old version:` or large blocks of `#` code — delete. Git history preserves it.

---

## Phase 4 — Duplication Elimination

### Step 4.1 — Copy-pasted load patterns
Each tab does the same "populate combo from source_data" dance slightly differently.
- [ ] Factor out `_populate_combo(combo, options, current)` (already exists at line 6750) — audit all tabs use it
- [ ] Same for table population, same for list population

### Step 4.2 — Copy-pasted validation
- [ ] Character-per-line limits (the `DexDescriptionEdit` pattern from CLAUDE.md) —
      verify every text field uses `attach_dex_limit_ui` instead of rolling its own
- [ ] No-wheel-scroll combo fix — verify applied globally, not per-combo

### Step 4.3 — Copy-pasted dirty-flag wiring
- [ ] Every editor should use the same `_loading_guard` pattern from Phase 1
- [ ] Don't let each tab reinvent its own guard

### Step 4.4 — Copy-pasted file I/O
- [ ] Header parsing: every parser should use `source_data` helpers, not re-open `.h` files
- [ ] Header writing: same via the dedicated writer modules

---

## Phase 5 — File-Size Reduction

### Step 5.1 — `ui/mainwindow.py` is too big
Currently ~10000 lines. Split candidates (keep as a plan, don't shotgun-refactor):
- [ ] Move species-data editing methods to `ui/species_editor_mixin.py`
- [ ] Move pokedex editing to `ui/pokedex_editor_mixin.py`
- [ ] Move evolution editing to `ui/evolution_editor_mixin.py`
- [ ] Each mixin: one responsibility, <1500 lines
- [ ] `mainwindow.py` shrinks to orchestration + the pieces that don't fit a mixin

### Step 5.2 — `ui/ui_mainwindow.py` (Qt Designer generated)
- [ ] Regenerate from `.ui` file; confirm it's minimal
- [ ] Delete widgets in `.ui` that Phase 3.3 identified as unused

### Step 5.3 — Assets
- [ ] `docs/` PNG screenshots: 25 files — confirm all are referenced in README
- [ ] Any `.png` / `.ui` / `.qrc` in the tree that's never loaded → delete

### Step 5.4 — `__pycache__` / `.pyc` in the repo
- [ ] `.gitignore` covers them? Confirm no `.pyc` tracked.

---

## Phase 6 — Documentation Reality Check

### Step 6.1 — Every `.md` claim verified against code
- [ ] `docs/CLAUDECONTEXT.md` — architecture claims match current code
- [ ] `docs/UNIFIED_EDITOR_PLAN.md` — every "COMPLETE" is actually complete
- [ ] `docs/DATAFLOW.md` — the save pipeline diagram matches the audited pipeline
- [ ] `docs/TROUBLESHOOTING.md` — no stale bugs listed as "ongoing" that are fixed
- [ ] `eventide/docs/CLAUDECONTEXT.md` — same
- [ ] `eventide/docs/README.md` — same
- [ ] `README.md` — feature list reflects what's shipping in the next release

### Step 6.2 — BUGS.md hygiene
- [ ] Every FIXED entry stays (anti-regression reference)
- [ ] Every OPEN entry has a status line explaining what's blocking
- [ ] Remove speculation entries with no evidence

### Step 6.3 — Credits
- [ ] Original PorySuite credited
- [ ] Porymap credited
- [ ] AI disclosure prominent
- [ ] No hardcoded assumptions about the hack being vanilla

---

## Phase 7 — Porymap Patch Hardening

Even after Phase 0 cleanup, the remaining patches still touch upstream source.
Harden them further.

### Step 7.1 — Patch diff against clean Porymap
- [ ] Apply patches to a clean Porymap checkout, diff, confirm only our changes present
- [ ] Count lines changed — minimize

### Step 7.2 — Patch version pinning
- [ ] Record which Porymap git SHA the patches were tested against
- [ ] Warn on apply if the user's Porymap is newer than tested

### Step 7.3 — Upstream-friendly rewrite consideration
- [ ] For each remaining patch, ask: could this be done via Porymap's scripting API instead?
- [ ] Callbacks we added (`onEventSelected`, `onMapSaved`) — upstream to Porymap if maintainers accept
- [ ] `writeBridgeFile` / `readCommandFile` — could use `utility.log` + parse, if perf allows

### Step 7.4 — Graceful degradation on unpatched Porymap
- [ ] Every call site that uses a patched API already has a `try/catch` fallback
- [ ] Confirm the app is still usable (with reduced bridge features) against stock Porymap
- [ ] Bridge script already checks `typeof utility.readCommandFile !== "function"` — extend to every patched API

---

## Phase 8 — EVENTide Deep Audit

EVENTide is a separate QMainWindow and has its own wiring. Full pass:
- [ ] Map tree load / selection / reload paths
- [ ] Event editor: create, delete, move, properties edit paths
- [ ] Script editor integration (external editor launch, file watch)
- [ ] Porymap launch / close / reload cycle
- [ ] Heal location / connection / warp editing
- [ ] Region map editor
- [ ] Layouts & tilesets sub-tab (merged in 0.0.55b)
- [ ] Every `data_changed` signal traced end-to-end to `update_action()`

---

## Phase 9 — Sound Engine Correctness

Sound editor has its own known gotchas (see `sound_engine_fixes.md` memory).
- [ ] Confirm all FIXED entries in BUGS.md are still fixed (run regression tests)
- [ ] Piano roll save round-trip for the 5 most complex songs
- [ ] Voicegroup editor save correctness
- [ ] Instrument preview playback uses the actual saved instrument, not a stale one

---

## Phase 10 — Final Verification Pass

### Step 10.1 — Clean-project smoke test
1. Fresh clone of a pokefirered project.
2. Open in PorySuite.
3. Click through every editor tab in turn.
4. Confirm zero `*` markers appear.
5. Close app — no save prompt.

### Step 10.2 — Full edit-save-reload pass
1. In each editor, change one value.
2. Save.
3. Close, reopen.
4. Confirm every edit persisted.

### Step 10.3 — Build verification
1. After a full edit pass, run `make` in WSL.
2. Game must build without errors.
3. Load in emulator, verify a visible edit (e.g. starter species) is present in-game.

### Step 10.4 — Second release
- [ ] All phases 1–9 green
- [ ] Bump version
- [ ] New CHANGELOG entry summarizing the audit
- [ ] README updated with any behavior changes
- [ ] Tag + release

---

## Tracking

- **Phase 0**: ✅ COMPLETE
- **Phase 1**: ✅ MOSTLY COMPLETE — Species, Pokédex, Moves, Items, Abilities, Trainers, Starters, Credits, Overworld GFX, Trainer GFX all done. Remaining: Wild Encounters, Title Screen, Sound Editor, EVENTide.
- **Phase 1B (Sprite Rendering Pipeline)**: ✅ COMPLETE
- **Phase 2**: NOT STARTED
- **Phase 3**: NOT STARTED
- **Phase 4**: NOT STARTED
- **Phase 5**: NOT STARTED
- **Phase 6**: Partially ongoing (CLAUDE.md, CLAUDECONTEXT.md, UNIFIED_EDITOR_PLAN.md kept current)
- **Phase 7**: NOT STARTED
- **Phase 8**: NOT STARTED
- **Phase 9**: NOT STARTED
- **Phase 10**: NOT STARTED

When a step completes, mark it `✅`. When a bug is found inside a step, log it
to `BUGS.md` and link from here.

**Do not skip phases. Do not stack unverified changes. One phase at a time,
each verified by the user before the next begins.**
