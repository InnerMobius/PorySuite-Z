# PorySuite-Z v0.0.57b

> pokefirered projects only.

Second release of the **Post-Release Code Audit**. This one is a deep sweep across the Trainers section plus a handful of latent data-integrity bugs caught elsewhere along the way. The user spent the day stress-testing the Trainers workflow and calling out every regression live — this release is the response.

## What's New in 0.0.57b

### Full Trainers Section Audit

The Trainers / Trainer Classes / Trainer Graphics sub-tabs were the last part of the toolbar that hadn't been through a correctness audit. It is now done.

- **No more free-text combo leaks.** Seven combo boxes across the Trainers editor (Trainer Pic, Class, Held Item, Moves 1-4, Bag Slots 1-4, and Species) were editable free-text boxes that would silently save whatever the user typed — including typos — as a "constant" that broke the next build. All of them now use the new `_SearchableConstCombo` helper: type-to-search still works via a popup, but on focus-out the combo snaps back to the last valid constant. Free text cannot be saved. Existing unknown constants in the project are preserved on load so an unusual hack's custom identifiers still round-trip.
- **Add-Class dialog validator.** The constant field on the Add-Class dialog now accepts only `^[A-Z][A-Z0-9_]*$` — no lowercase, no spaces, no leading digits.
- **Trainer Class `(none)` pic no longer breaks the build.** Previously setting a class pic to "(none)" and saving emitted `[FAC_X] = ,` in `src/data/trainer_class_lookup.h`, which is a C syntax error. The writer now skips empty values.
- **Rematch-tier party persistence.** Editing a VS Seeker rematch tier's party, switching to another tier, then switching back used to discard the edit. Now each tier switch flushes the outgoing tier's party state through a new `tier_party_modified` signal before clearing the slot widgets. The tier dropdown's own label also refreshes in-place so it reflects the post-edit lineup instead of the stale pre-edit one.
- **Rematch-tier aliasing fix.** Editing a rematch tier's party was writing the Pokémon into the BASE trainer's `sParty_` symbol. `_flush_current` now routes tier writes to a `tier_sym_override` so the correct party symbol is updated.
- **Header sprite lookup for compound-class names.** COOLTRAINER, POKEMANIAC, and every other multi-word class showed a "?" placeholder for their trainer pic because `_parse_trainer_pic_map` was matching `TRAINER_PIC_COOLTRAINER_M` against the filename stem, and the underscore placement didn't line up. Both the Trainers tab and the Trainer Classes tab now bridge via the `gTrainerFrontPic_*` C symbol and normalise both sides, so compound names match reliably.
- **Blank-overwrite guard is now per-field.** The old guard only protected the trainer when BOTH class AND pic were empty. Any single empty field still wiped its saved value. Now each field is guarded individually.
- **F5 Refresh actually discards Trainer Class edits.** A `has_edits()` guard meant to protect class-editor state from being clobbered by non-refresh reloads was accidentally fighting F5 too. Refresh now bypasses the guard and clears amber markers in sync with the data reload.

### Trainer Graphics — Full Rewrite

The old single-dropdown + single-palette-row Trainer Graphics tab is gone. Replaced with a Pokemon-Graphics-class editor.

- **Scrollable card grid.** Every trainer pic appears as a thumbnail card with name + `TRAINER_PIC_*` constant underneath. Click to load. Live search filter narrows by name or constant. Amber border on unsaved cards, blue border on the selected one.
- **Drag-to-reorder palette row.** Same `DraggablePaletteRow` widget used by Pokemon Graphics. Drop a colour on the leftmost slot ("BG") to pick the transparent index — the sprite PNG is reindexed automatically so the image looks the same, only which colour is treated as transparent changes. Colour picker still works on double-click.
- **Import PNG as Sprite…** New button that picks an indexed PNG (≤16 colours, 8-bit indexed) and replaces the trainer's sprite image entirely — pixels AND palette. RGB PNGs are rejected with a clear dialog telling the user to convert first.
- **Import .pal File** and **Import Palette from PNG** — same pair of buttons the Pokemon Graphics tab has.
- **Save Sprite as PNG** and **Save Palette as .pal** — standalone exports that don't require saving the whole project.
- **Responsive two-panel layout.** The tab body is now a `QSplitter` — the user can drag the divider to rebalance the grid vs the editor. Minimum widths (not fixed widths) on both sides keep both visible when the window isn't maximized. Sprite preview is 192×192 (3× vanilla) so the image is readable.
- **Per-card dirty tracking.** Unsaved palette edits and PNG imports are tracked independently per pic. `flush_to_disk` writes both the palette and the reindexed PNG on Save, and only clears the dirty flag on successful writes — a failed save keeps the card amber for the next attempt.

### Trainer Class Renaming

- **Rename…** button on the Trainer Classes tab, matching the flow that Pokémon / Items / Moves / Abilities already use. Opens the shared `RenameDialog`, auto-derives the new `TRAINER_CLASS_*` constant suffix from the typed display name, previews all source hits, and commits the rename across `include/constants/opponents.h`, `src/data/trainers.h`, `src/battle_main.c`, `src/data/trainer_class_names.h`, `src/data/trainers.json`, scripts, and maps.
- Intentionally does NOT touch `FACILITY_CLASS_*`. They're paired in vanilla pokefirered but logically separate; renaming one shouldn't force the other.
- New `core/refactor_service.rename_trainer_class()` method + new `apply_pending` branch drive the actual file rewrite.

### Trainer-Class Pic Combo Explainer

The tiny inline note under the class-level Trainer Pic combo now clearly explains scope: this pic is used ONLY in Battle Tower / Trainer Tower / Union Room facility battles where the opponent comes from a class lookup. Regular trainer battles use the per-trainer pic on the Trainers tab. Setting it to `(none)` is valid for classes that never appear in a facility.

### Dirty-Flag Audit Continued

Amber-row + sidebar-dot coverage extended to the rest of the toolbar's data editors:

- **Trainers tab** — Custom `_TrainerListDelegate` paints a 90-alpha amber overlay on edited trainers. A `_dirty_consts` set survives the search filter's list rebuilds. New `mark_dirty` / `current_const` / `clear_all_dirty` API.
- **Trainer Classes tab** — Stock `_DirtyDelegate` installed on the class list.
- **Trainer Graphics tab** — Per-card dirty dot, plus a small amber "●" next to the pic selection that tracks the currently-visible pic's state.
- **Abilities tab** — Amber dirty row + sidebar dot (toolbar position 5 of 10 in the ongoing audit).
- All three Trainers sub-tabs share a single sidebar dot via `sectionDirtyChanged("trainers", True)`.

### Pokédex Drag-to-Reorder — Now Actually Saves

Two bugs in one feature:

1. **Reordering either dex list didn't mark the project dirty.** `QListWidget` was handling the reorder internally, but the `model().rowsMoved` signal was never connected. The reorder happened live but silently. Now connected on both National and Regional dex lists with full dirty-flag wiring.
2. **Regional dex reorders were genuinely being discarded at save time.** The save pipeline rebuilt `national_dex` from the UI's current order but had no equivalent block for `regional_dex`. Added. National was correct; regional was silently lost.

Affected rows in the reorder range now also paint amber (consistent with every other dirty-tracking pattern in the app).

### Abilities Editor — Two Data-Integrity Fixes

Hooking up the Abilities tab's dirty-flag wiring immediately surfaced two long-standing bugs:

- **`ABILITY_NONE` no longer shows as a user-facing list row.** It was a sentinel cluttering the list with nothing to edit and a populated usage panel of hundreds of species. Still written to `include/constants/abilities.h` — only hidden from the UI.
- **Edits to battle/field effect no longer revert on row switch.** `_load_effect_editors` was re-reading from disk on every row switch. Disk still said "no effect" because the user hadn't saved yet. Session stash on the in-memory `data` dict now takes priority over disk detection. Empty stash correctly overrides to `None` so deliberate clears are preserved.

### Image Indexer + Pokemon Graphics — "Index as Background" + Palette Polish

- **Right-click any swatch → "Index as Background"** — swaps the clicked colour with the leftmost slot using lockstep pixel+palette swap (via `core.gba_image_utils.swap_palette_entries`). The image stays visually identical, just which colour is treated as transparent changes.
- **Image Indexer palette drag now behaves like Pokemon Graphics** — palette-only reorder without touching pixels, matching user expectation after the Pokemon-side drag-reorder feature shipped in v0.0.56b.
- **Image Indexer Load .pal dispatcher** — three-path resolution (exact-match, subset, remap) picks the least-lossy option for each .pal imported.
- **Pokemon Graphics transparent-colour auto-prompt removed.** It was corrupting sprites. The explicit "Index as Background" / drag-to-BG flows replace it.

### Rematch Tier-Gate Flag Label Fallback

The VS Seeker tier-gate flag-picker dialog was supposed to display friendly labels from `porysuite_labels.json` ("Beat Misty (FLAG_BADGE02_GET)") but multiple iteration attempts in this cycle couldn't make the live-widget query work reliably. After three rounds of regression we stopped, audited the code clean, and locked the dialog to a simple module-level disk reader: if the labels file has entries, labels show; if not, bare constants show. Feature documented as `KNOWN-LIMITATION` in BUGS.md with an explicit anti-regression rule against reintroducing live-widget walkers.

## Bug Fixes

- **Ability field effect "Halve Egg Hatch Steps" no longer breaks the build.** The tool's template was injecting C code that referenced `i` before the host function's `u32 i` was declared (`src/daycare.c:1146: 'i' undeclared`). Template now declares its own local loop variables. The field-effect remover also got a new marker-block helper so "Clear Field Effect" leaves `daycare.c` fully clean instead of leaving orphan scaffolding behind.
- Trainer Pic shows correctly for COOLTRAINER / POKEMANIAC / every other compound-word class (no more "?" placeholder).
- Rematch-tier party edits survive tier switches.
- Tier dropdown label refreshes in-place after editing the current tier's party.
- Rematch-tier party edits no longer overwrite the base trainer's party symbol.
- Single empty field on the Trainers tab no longer wipes its existing saved value.
- Free-text garbage typed into a trainer combo can no longer be saved as a constant.
- Trainer Class "(none)" pic no longer emits `[FAC] = ,` build-breaking C syntax.
- F5 Refresh actually discards pending Trainer Class edits.
- Editing a palette on the Trainer Graphics tab no longer loses the edit if `write_jasc_pal` returns failure.
- Pokédex drag-to-reorder marks the project dirty.
- Regional Pokédex reorders now survive save.
- `ABILITY_NONE` hidden from the abilities list.
- Ability battle/field effect edits preserved across row switches.
- Image Indexer palette reorder no longer remaps pixels (palette-only, matching Pokemon Graphics).
- Pokemon Graphics transparent-colour auto-prompt that was corrupting sprites has been removed.

## Files of Note

- New: `core/refactor_service.rename_trainer_class()` method.
- New: `core/gba_image_utils.swap_palette_entries()` — shared lockstep pixel+palette swap.
- Updated: `ui/trainer_graphics_tab.py` — full rewrite (card grid + QSplitter body + drag-palette + import/export).
- Updated: `ui/trainers_tab_widget.py` — `_SearchableConstCombo`, tier-party persistence, header pic lookup, dirty delegate, labels loader cleanup.
- Updated: `ui/trainer_class_editor.py` — Rename… button + rename pipeline + compound-name pic parser.
- Updated: `ui/abilities_tab_widget.py` — `ABILITY_NONE` hidden + session-stash effect restoration.
- Updated: `ui/mainwindow.py` — Pokédex drag-reorder dirty/save, Trainers section dirty wiring, Trainer Class rename slot.
- Updated: `core/app_info.py` — VERSION bump.

## Known Limitations

- Rematch tier-gate flag-picker dialog shows bare constants unless the project has a populated `porysuite_labels.json` on disk. Live querying the Label Manager widget was attempted and reverted after three regressions — see BUGS.md entry "Rematch Settings dialog — flag labels loader simplified".
- Trainer-class rename does NOT touch `FACILITY_CLASS_*`. Users who want both renamed run two rename passes.
- Post-Release Audit phases 3+ (save-pipeline correctness across all editors) still in progress — see `docs/POST_RELEASE_AUDIT_PLAN.md`.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
