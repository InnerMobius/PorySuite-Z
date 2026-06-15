# Overworld Editor — Arbitrary Sprite Dimensions Upgrade

Roadmap for a major upgrade to the Overworld Graphics tab's sprite import /
creation pipeline.  Status: **Phases 1–5 COMPLETE — geometry, generator,
pipeline integration, the New Sprite dialog overhaul, Phase 3b (composite
depth fix), Phase 3c (composite centering + table-collision fix; frame sizes
now multiples of 16), and Phase 5 (editor rendering, preview, and detail-panel
animation-table editing) — all verified by tests + a clean build.  A
side-track feature, **Frame Cycle** (stationary animated entities — Sequential
+ Random orders, with `MOVEMENT_TYPE_FRAME_CYCLE` engine support so the
animation survives conversations), shipped 2026-05-18/19 and is also
COMPLETE.  Phase 6 (verification) is SKIPPED — the user has confirmed sprites
across sizes load and render correctly in-game.  Phase 7 (large-sprite
collision footprint editor) is COMPLETE end-to-end as of 2026-05-21 — Phase
7a (data model + 19 tests), Phase 7b (engine patcher + 8 tests), Phase 7c
(Edit Collision Footprint dialog), Phase 7d (save path with auto-install of
the engine patcher).  Footprint authoring → save → rebuild → in-game collision
+ interaction is now a complete loop.**

**Post-plan addition (2026-06-15) — in-place Frame Size editor.** Beyond the New
Sprite flow, the detail panel now lets you RESIZE an EXISTING sprite's frame
(W×H spinboxes + common-size quick-pick), with a live derived frame count,
single-OAM-vs-composite classification, and a suggested anim table. Save routes
through the same `replace_sprite_sheet` pipeline (rewrites GraphicsInfo + pic
table + OAM/subsprite scaffolding + `spritesheet_rules.mk`). `ui/overworld_graphics_tab.py`.
Staged for v0.1.28b.

---

## Vision

Today the New Sprite flow only reliably handles a hardcoded handful of frame
sizes (16×16, 16×32, 32×32, 64×64) and silently mis-handles everything else.

Goal: **any sprite frame whose width and height are both multiples of 16 can be
imported, configured, and built correctly** — from a 16×16 NPC up to large
boss sprites — with the editor generating every piece of engine scaffolding
(OAM template, subsprite tables, spritesheet build rules, GraphicsInfo fields).
No hardcoded size list. The user designates the frame dimensions; the editor
produces everything the engine needs.

The validation rule collapses to one line: *are width and height both
multiples of 16, and within the project's OAM/VRAM budget?* Everything past that
is generated.  (16, not 8: an 8-but-not-16 size can only explode into 8×8
hardware pieces — 25+ OAM slots for a 40×40 — and sits 4px off the tile grid.)

## Motivating cases

- 32×32 / 64×64 boss sprite — should "just work". Today 64×64 builds, 32×32 is
  shaky, and **32×64 is a live build-breaking bug** (`_OAM_TABLE` points it at
  a `gObjectEventSpriteOamTables_32x64` symbol that does not exist).
- A 72×40 sheet (36×40 frames) — neither 36 nor 40 is a multiple of 16. The
  editor should catch this up front and offer to pad, not let the user create
  a sprite that fails at build time.
- A 96×48 sheet (48×48 frames) — 48×48 is legal (both ÷16) but the old editor
  refused it because of the hardcoded table. It should generate the subsprite
  table and accept it — this is exactly the King Zora case.

## What the engine actually supports (research findings)

OAM base templates present in `src/data/object_events/base_oam.h`:
8×8, 16×8, 16×16, 32×8, 32×16, 32×32, 16×32, 64×32, 64×64.

Subsprite tables present in `object_event_subsprites.h`:
16×16, 16×32, 32×32, 48×48, 64×32, 64×64, 88×32, 96×40, 128×64.

Two rendering modes:

- **Single-OAM** — a frame that is a valid GBA hardware sprite (≤64×64, one of
  the 12 hardware shapes). `.oam` = the matching base template, one hardware
  sprite. Standard NPCs (16×32), small NPCs (16×16), Seagallop (64×64).
- **Composite** — a frame larger than 64×64, or a non-hardware shape (48×48,
  88×32, …). `.oam` is set to a *dummy* `gObjectEventBaseOam_8x8`, and the
  **subsprite table** composites the image from multiple hardware sprites.
  SS Anne (128×64) = four 64×32 pieces at tileOffsets 0/32/64/96.

Key coupling: a composite subsprite table's per-piece `tileOffset` values are
locked to the `.4bpp` tile layout, which is set by `-mwidth/-mheight` in
`spritesheet_rules.mk`. SS Anne uses `-mwidth 8 -mheight 4` (64×32 metatiles)
*because* its subsprite table decomposes into 64×32 pieces. PNG metatile
layout, subsprite table, and OAM must all agree or the sprite renders
scrambled.

Sprite "compression": there is no per-tile flip or per-tile dedup for sprites
(that is a background/tilemap feature). Sprites have one whole-sprite H-flip
and one V-flip bit. The overworld engine uses the H-flip to derive East-facing
frames from West-facing ones — which is why vanilla NPC sheets store 3
directions, not 4. Overworld object-event pics are stored raw/uncompressed
(`.4bpp`) because the engine DMAs one frame at a time; only item icons are
LZ-compressed on ROM.

## Current holes in the editor

1. `_OAM_TABLE` in `core/overworld_sprite_creator.py` is a 5-entry hardcoded
   stub.
2. The `(32,64)` entry references a non-existent subsprite table → build
   failure for any 32×64 sprite.
3. The unknown-size fallback constructs `gObjectEventBaseOam_{w}x{h}` /
   `gObjectEventSpriteOamTables_{w}x{h}` name strings that don't exist for most
   sizes → build failure.
4. No subsprite-table generation — the creator can only reference pre-existing
   tables.
5. The `spritesheet_rules.mk` `-mwidth/-mheight` auto-heal uses naive
   `frame//8`. Correct for single-OAM frames; wrong for composites, which must
   match the subsprite decomposition's piece size.
6. `NewSpriteDialog` lets the user pick any 8–128 px size with no validation —
   the UI promises sizes the backend cannot deliver.
7. The emote / VS-seeker frame-9 upgrade assumes a standard small NPC; it
   should not be offered for boss-size sprites.

## Engine constraints the plan must respect

- Frame dimensions must be multiples of 16. Hard rule. (8-but-not-16 sizes
  only explode into 8×8 pieces and sit 4px off the tile grid — see Vision.)
- A single GBA hardware sprite is ≤64×64 and one of 12 fixed shapes.
- OBJ hardware has **128 OAM slots total**. A composite sprite consumes one
  slot per subsprite piece. The overworld already spends slots on ~16 NPCs,
  the player, field effects, and the HUD — a composite NPC using 9 or 25
  pieces is a real budget hit.
- OBJ VRAM is finite. An overworld sprite costs `W*H/2` bytes of VRAM per
  frame (one frame resident at a time, DMA-swapped).
- `gbagfx`'s `-mwidth/-mheight` produces only **uniform** metatiles. The plan
  therefore decomposes every composite into a **uniform grid** of one piece
  size — never a mixed-size decomposition — so the build rule stays a single
  `-mwidth/-mheight` pair.

## Decomposition strategy

Given a frame `W×H` (both multiples of 16):

1. **Single-OAM** if `(W,H)` is a hardware shape (≤64 each, valid shape):
   one piece, `.oam` = matching base template.
2. **Composite** otherwise: pick the **largest uniform piece** `pw×ph` where
   `pw | W`, `ph | H`, `pw,ph ≤ 64`, `(pw,ph)` is a hardware shape — minimising
   piece count `(W/pw)×(H/ph)`. `.oam` = dummy `gObjectEventBaseOam_8x8`.

The generated subsprite table, for a `cols×rows` grid of `pw×ph` pieces:
- piece `(col,row)` tile count = `(pw/8)*(ph/8)`
- `tileOffset(col,row) = (row*cols + col) * (pw/8)*(ph/8)`
- `.x = col*pw - W/2`,  `.y = row*ph - H/2` — the piece's **top-left corner**
  offset from the composite centre.  NOT the centre offset: adding `pw/2`
  shoves the whole sprite half a piece off its tile (that was the Phase 3c
  centering bug — never re-add it).
- spritesheet rule: `-mwidth (pw/8) -mheight (ph/8)`

The generated table symbol carries a `Ps` infix
(`gObjectEventSpriteOamTables_Ps48x48`), so a composite can never bind to a
vanilla `_WxH` table — vanilla's 48×48 / 128×64 tables are hand-tuned
non-uniform-grid layouts incompatible with the metatile pipeline.  A
single-OAM sprite of one of the 5 vanilla single-OAM shapes
(16×16, 16×32, 32×32, 64×32, 64×64) keeps the vanilla table instead.

Cost: a 16-multiple size not divisible by 32 tiles only into 16×16 pieces — an
80×80 frame → 25 pieces → 25 OAM slots. Legal but expensive; the editor
**flags** it and suggests padding to a 32-divisible size.

---

## The plan — phases

### Phase 1 — Geometry foundation — COMPLETE
New module `core/overworld_sprite_geometry.py` — pure geometry, no file
writes, no engine mutation.
- `validate(w, h)` → `(ok, reasons)`: both dimensions positive multiples of
  16, within a 256 px ceiling.
- `decompose(w, h)` → a `Decomposition` dataclass: single-OAM vs composite
  classification, the OAM base symbol, and a uniform grid of one hardware
  piece size (each piece positioned + tile-offset) — single-piece for a
  hardware shape, the largest-uniform-piece grid otherwise.
- `cost_warnings` / `describe` — non-fatal OAM-slot / VRAM cost reporting.
- `scan_oam_templates` / `scan_subsprite_tables` — read-only runtime scans of
  `base_oam.h` + `object_event_subsprites.h`; no hardcoded lists.
- Verified by `tests/test_overworld_sprite_geometry.py` (28 tests, incl. a
  grid-invariant sweep over all 256 valid 16-multiple sizes asserting each
  decomposition is centred on (0,0); the `Ps` vs vanilla table-symbol rule).

`_OAM_TABLE` and the broken fallback are NOT deleted here — removing them in
isolation would break `create_overworld_sprite`, which still references them.
That deletion is folded into Phase 3, where the creator is rewired.

### Phase 2 — Subsprite table generator — COMPLETE
New module `core/overworld_subsprite_gen.py` — pure C-text emission.
- `ensure_subsprite_table` / `ensure_oam_base` — given a `Decomposition`,
  emit a `struct Subsprite[]` + six-entry `struct SubspriteTable[]` into
  `object_event_subsprites.h` (under the decomposition's `Ps`-infixed symbol
  for composites), and (for the one 16-multiple single-sprite shape vanilla
  lacks a base OAM for — 32×64) a `struct OamData` into `base_oam.h`.
- Idempotent — a no-op when the symbol already exists, vanilla or generated.
- Every generated block is fenced by `PORYSUITE-GEN` sentinel comments; that
  fence is the registry.  `remove_*` deletes only fenced blocks and never
  touches vanilla.  `scan_generated_*` lists what the tool has generated.
- The "is this size still needed" reference check stays with the caller (the
  sprite creator/deleter), exactly as it already works for pic tables and
  palettes.
- Verified by `tests/test_overworld_subsprite_gen.py` (23 tests against real
  vanilla headers: idempotency, vanilla-detection, generate/remove
  round-trip, generated-C-is-scannable cross-check).

### Phase 3 — Pipeline integration — COMPLETE
- `create_overworld_sprite` routes every frame size through the geometry +
  generator modules — validate, decompose, generate any missing OAM template
  / subsprite table, then write the GraphicsInfo and spritesheet rule to
  match.  `_OAM_TABLE` and the broken fallback are deleted.
- `_ensure_spritesheet_rule` is called with the decomposition's hardware-piece
  size (`metatile_w/h`), not `frame//8` — correct for composites.
- Multi-frame composites are stored as vertical frame strips
  (`_write_vertical_frame_strip`); gbagfx tiles metatiles row-major down the
  image, so horizontal frames would interleave.
- `delete_overworld_sprite` removes a generated OAM template / subsprite table
  when the deleted sprite was the last of its size.
- Verified by `tests/test_overworld_sprite_creator_geometry.py` (11 tests:
  single-OAM, composite, the 32×64 build-break fix, vertical-strip transpose,
  delete cleanup, depth-fix wiring).

Deferred to a follow-up: making the pre-build self-heal
(`ensure_all_overworld_spritesheet_rules`) geometry-aware.  It is a legacy
safety net for horizontal single-OAM sprites and does not interfere with
Phase 3's output (its `PNG.width > frame_w` guard skips vertical composites),
so it was left untouched to avoid regression risk on the "two heads" fix.

### Phase 3b — Composite-sprite depth fix — COMPLETE
Found while play-testing a 48×80 composite: tall composite sprites did not
depth-sort against the player — the player walked on top of them.  Vanilla
`SortSprites` breaks subpriority ties using the sprite's top corner
(`oam.y`); for a tall sprite that corner is several tiles above the feet, so
the sprite loses every tie and draws behind the player.
- `core/sprite_depth_patch.py` (NEW) — `ensure_sprite_depth_fix` rewrites the
  `SortSprites` tie-break in `src/sprite.c` to compare the sprites' feet
  (`oam.y - 2*centerToCornerVecY`).  Idempotent; detected by a `PORYSUITE-DEPTH`
  source signature.
- `create_overworld_sprite` (step 4c) applies it after the OAM/subsprite
  scaffolding, so the engine fix lands automatically the first time a New
  Sprite is created.
- Verified by `tests/test_sprite_depth_patch.py` (14 tests, including a
  byte-exact check of the vanilla anchor against the genuine engine source).
- Full detail: CHANGELOG 2026-05-17 and `docs/BUGS.md`.

### Phase 3c — Composite centering + table-collision fix — COMPLETE
Found while play-testing King Zora (a 48×48 composite) — it rendered shredded
and off-centre.  Two bugs in the Phase 1–3 pipeline:
- `_compute_pieces` offset every piece by `+pw/2,+ph/2` — that is the piece
  CENTRE, but the engine reads a subsprite's x/y as its TOP-LEFT corner, so
  every composite drew half a piece off its tile.  Fixed (drop the `+pw/2`);
  `GridInvariantTest` now asserts composites stay centred.
- `decompose` named composite tables `gObjectEventSpriteOamTables_WxH`, which
  collided with vanilla's hand-tuned non-grid tables at 48×48 / 128×64.  Now
  every generated table gets a `Ps` infix (`..._Ps48x48`); single-OAM sprites
  of the 5 vanilla shapes still reuse the vanilla table.
Also: frame sizes are now multiples of **16** (was 8) — see Vision above.
Full detail: CHANGELOG 2026-05-17 and `docs/BUGS.md`.

### Phase 4 — New Sprite dialog overhaul — COMPLETE
- Frame size validated against the geometry module's `validate()` — the same
  16-multiple check the creator runs — so the dialog can never accept a size
  the build would reject.
- Live size readout: picking a Frame Layout shows "single hardware sprite, 1
  OAM slot, X B VRAM/frame" / "composite of N WxH pieces, …", amber with a
  warning when the size is OAM-slot- or VRAM-heavy.
- Frame Layout dropdown — every whole-frame 16-aligned slicing of the imported
  sheet, recognised standard sizes named (Small NPC / Standard NPC / Boss …);
  no pixel typing.
- Robust auto-detect of frame size from the imported PNG (`detect_frame_size`
  / `frame_count_options`).
- **Auto-pad helper** — an odd sheet (frames not a multiple of 16) gets a
  "Pad to fit…" button: it asks the frame count, pads each frame up to the
  next legal canvas (`pad_sprite_sheet`, bottom-centred, transparent margin),
  and re-imports the padded result.
- **Animation** dropdown surfaces every `sAnimTable_*` the project defines,
  each labelled with its frame count (`core.anim_table_upgrade.scan_anim_tables`)
  — no longer three hardcoded choices.
- Non-indexed PNGs route into the manual colour indexer inline rather than
  erroring.
- Full detail: CHANGELOG 2026-05-17.

### Phase 5 — Editor rendering & feature gating — COMPLETE
- DONE — the 4-direction preview (`FourDirectionPreview.load_sprite`) slices by
  the sprite's real frame size AND detects strip orientation: a single-OAM
  sprite is a horizontal strip, a multi-frame composite is a vertical strip
  (Phase 3's layout).  Previously a vertical-strip composite's whole column
  was treated as one frame.
- DONE — the detail panel shows the sprite's dimensions + classification
  ("48×48px · composite, 9 pieces" / "· single hardware sprite").
- DROPPED — gating the emote / VS-seeker upgrade by frame size was a mistake:
  32×32 bike-rider sprites are core VS-seeker trainers, so a "16×16 / 16×32
  only" gate breaks a legitimate case.  It was added and reverted same day.
  The real need is the opposite — see the REMAINING item below.
- N/A — East = flipped-West is already respected: `load_sprite` derives the
  East row by mirroring West, and the New Sprite dialog only counts frames
  physically present in the sheet, so no redundant East frames are asked for.
- RESOLVED — generalising the emote / VS-seeker upgrade for arbitrary frame
  counts is NOT needed.  Once the size gate is gone the tool is already
  size-agnostic: its only fixed assumption is "frame 9 is the pose".  A sprite
  of ANY pixel size laid out as a standard 10-frame sheet (frames 0–8 + the
  frame-9 pose) works with the existing tool — a 32×32 sprite just uses ten
  32×32 frames; bike riders likewise.  The supported convention is therefore
  "standard 10-frame layout, any frame size".
- DONE — the detail panel's Animation Preview group has an "Animation:"
  dropdown listing every `sAnimTable_*` the project defines (each labelled
  with its frame count).  Picking a different table reassigns the sprite's
  animation post-creation: the preview re-renders live, the grid card +
  groupbox turn amber, and on save `_rewrite_sprite_anims` patches the
  sprite's GraphicsInfo `.anims` field in place (the same patcher edit the
  emote upgrade uses).  `load()` / F5 fully resets the new dirty state.

### Side feature — Frame Cycle — COMPLETE (2026-05-18 / 2026-05-19)
A new **"Frame Cycle…"** button in the Animation Preview group turns the
selected sprite into a stationary animated entity (painting, torch, idle
decoration) that loops every sheet frame without ever h-flipping on facing
change.  Not part of the original 7 phases — added when the painter / schule
sprite use-case surfaced — but in the same editor surface as Phase 5 so it
lives here.

- **Sequential mode** — `sAnim_<Name>Cycle` / `sAnimTable_<Name>Cycle`
  generated in `object_event_anims.h`: one `ANIMCMD_FRAME` loop over every
  sheet frame except index 9 (the VS-seeker / emote pose), ending in
  `ANIMCMD_JUMP(0)`, with all 21 standard animation slots pointing at the
  same loop (so direction changes never produce a flipped frame).  Uniform
  per-frame hold.
- **Random mode** — same table shape, but the loop is a 64-entry
  pre-shuffled sequence with no two consecutive equal frames, and each
  `ANIMCMD_FRAME` gets its own random hold in `[fastest, slowest]` ticks —
  reads as varied-tempo idle motion (`LOOK_AROUND`-style).  Shuffle + holds
  are deterministic, seeded from the sprite's GraphicsInfo name, so
  regeneration is idempotent.
- **Engine refactor — `MOVEMENT_TYPE_FRAME_CYCLE`.**  `MOVEMENT_TYPE_NONE`'s
  empty callback couldn't recover the animation after a talk script set
  `animPaused = TRUE`, so the entity froze after every conversation.  New
  idempotent patcher `ensure_frame_cycle_movement_type` installs a dedicated
  movement type whose callback re-clears `sprite->animPaused` (and
  `objectEvent->disableAnim`) every idle frame.  Five edits across
  `include/constants/event_object_movement.h` and
  `src/event_object_movement.c`, all guarded; the Frame Cycle button
  installs it automatically.  Porymap auto-discovers the new constant from
  the header.
- **Duration clamping** — `ANIMCMD_FRAME.duration` is a 6-bit bitfield (max
  63).  The Random spinboxes initially allowed values up to 120, which
  silently wrapped mod 64 in the ROM (120 → 56, 100 → 36, 64 → 0) — the
  cycle looked frozen or sped up.  Codegen now clamps to `[1, 63]` and the
  UI spinboxes are `setRange(1, 63)` so the wrap is structurally impossible.

#### Still deferred
- **Per-direction frame control** (the original "A" item) — a directional
  sprite with different frames per facing and **no East mirror** — not yet
  built.  Frame Cycle covers the stationary-entity use case; the
  directional-no-mirror case is a different shape (different frames per
  facing, not one loop on all facings) and is still open.

### Phase 6 — Verification — SKIPPED (user-verified)
- Originally: build-test one sprite per tier (16×16, 16×32, 32×32, 48×48,
  64×64, 64×96, 128×64, pathological 80×80) and in-game render check.
- The user confirmed (2026-05-19) that sprites at arbitrary sizes already
  load and render correctly in-game — including a random size on top of
  `test_temp` — so this phase is closed without a formal sweep.

### Phase 7 — Large-sprite collision footprint editor — IN PROGRESS
A large composite still blocks only ONE collision tile (its object-event
anchor) — the player walks through the rest of its body.  This phase lets the
user define a multi-tile collision footprint per sprite.

**Design (agreed 2026-05-17, building from 2026-05-19):**

- **Per-character** (per GraphicsInfo) — the footprint belongs to the sprite,
  not the placement; every instance of that sprite blocks the same shape.
- **8×8-cell grid** — fine visual control over the sprite art.  Storage and
  the editor stay at 8×8 cell granularity; the engine collision hook
  collapses to 16×16 tiles for the v1 implementation (vanilla pokefirered's
  collision check works in tile coords).  A future free-pixel-movement
  refactor can swap the engine hook for cell-level collision without
  changing the editor or the on-disk format — the data is already there.
- **Footprint = collision AND interaction.**  Every footprint cell behaves
  like the vanilla 1-tile object hitbox — it blocks the player AND pressing
  A while facing any cell triggers the object's script.  Unified, so a big
  sprite's collision can never wall the player off from interacting with it.
- **Optional** — default is the vanilla single-tile block; the footprint is
  opt-in per sprite, with clear in-dialog warnings (a footprint over a
  doorway traps the player; collision moves with a walking sprite; off-map
  cells are ignored).
- **Engine side** — a per-GraphicsInfo `ObjectFootprint` struct + a sparse
  `gObjectEventFootprints[]` lookup indexed by `OBJ_EVENT_GFX_*` (NULL = no
  footprint = vanilla behaviour), plus a hook in the collision /
  interaction check.  Delivered through the patcher (engine refactor).
- The editor popup lives in the Overworld Graphics tab, opened per sprite.

**Sub-phases:**

#### Phase 7a — Data model — COMPLETE (2026-05-19)
`core/overworld_footprint.py` (NEW) — pure data, stdlib only.

- `Footprint` dataclass keyed by `gfx_const`; `cells[row][col]` row-major,
  origin = sheet PNG's top-left.
- `empty_footprint(gfx_const, frame_w_px, frame_h_px)` — sized from frame
  dimensions; rejects non-multiples of 8.
- `serialize_footprints(...)` / `parse_footprints(text)` / `parse_project_footprints(root)`
  — C ↔ Python.  Per-sprite blocks fenced with `PORYSUITE-GEN BEGIN/END
  footprint <const>` sentinels; lookup table is regenerated wholesale.
  Empty footprints are skipped on emit so cleared sprites don't leave
  orphan data.  Corrupt blocks are silently dropped on parse (the
  affected sprite falls back to "no footprint").  Mismatched
  BEGIN/END fences fail the parse, so a half-rewritten file can never
  be misread as complete.
- Verified by `tests/test_overworld_footprint.py` (19 tests:
  shape/coercion, mutation, tile collapse, codegen round-trip,
  sparse-init invariant, corrupt-block recovery, project header IO).

#### Phase 7b — Engine patcher — COMPLETE (2026-05-21)
`core/footprint_engine_patch.py` (NEW) — idempotent engine refactor that
installs the runtime scaffolding so the data Phase 7a serialises actually
affects the game.  Same discipline as `core/sprite_depth_patch.py`:
byte-exact match against vanilla, fail loudly if upstream drifts, fenced
sentinels for idempotent re-install.

- `include/object_footprint.h` (NEW) — `struct ObjectFootprint { u8 width;
  u8 height; const u8 *cells; }` + the `extern const struct ObjectFootprint
  *const gObjectEventFootprints[]` declaration.  Signature
  `PORYSUITE-FOOTPRINT v1` inside the file = the install probe.
- `src/data/object_events/object_event_footprints.h` (NEW, seeded empty)
  — so the engine reference compiles even before the user authors any
  footprint.  Once any footprint is saved this file is owned by
  `core/overworld_footprint.py`'s serialiser; the patcher NEVER
  overwrites an existing file.
- `src/event_object_movement.c` (MODIFIED, idempotent) — gated edits,
  each sentinel-fenced so partial reinstalls work:
  - Top-level `#include "object_footprint.h"`.
  - Data-table `#include "data/object_events/object_event_footprints.h"`
    next to the other object-event data includes.
  - A static helper `ObjectEventFootprintHitsTile(objectEvent, tx, ty)`
    inserted directly above `GetObjectEventIdByPosition`.  It maps
    each solid 8×8 cell of the sprite's footprint to its world-pixel
    rectangle (bottom-center anchored to `currentCoords`), then
    AABB-overlaps against the target tile.  Pure ASCII (agbcc-friendly).
  - A `// PORYSUITE-FOOTPRINT interact begin / end` block inside
    `GetObjectEventIdByPosition` — the A-button interaction lookup
    used by `field_control_avatar.c::GetInteractedObjectEventScript`.
    Lets the player talk to any painted cell of a multi-tile sprite,
    not only its anchor tile.  The vanilla anchor / elevation match
    is preserved, so NULL-footprint sprites are unchanged.
  - A `// PORYSUITE-FOOTPRINT collide begin / end` block inside
    `DoesObjectCollideWithObjectAt` (the vanilla movement-collision
    test).  Same shape: vanilla path runs first; if no anchor match,
    consult the footprint with the same elevation guard.
- For projects that REPLACE vanilla movement with a free-pixel system
  (Zeldamon-style), the patcher also extends `field_player_avatar.c`
  on top — see the FreeStep helper + free-collide edits below.  This
  pass is optional: it triggers only on detecting
  `static bool8 FreeStepNpcXBlocked(s8 dx)` in the file, and silently
  skips on vanilla projects (which don't have that function).
- `ensure_footprint_engine_support(root)` / `is_engine_installed(root)`
  — public API.  A run on an already-installed project reports an
  empty change list.  A run against a pokefirered with a hand-modified
  `GetObjectEventIdByPosition` (or `DoesObjectCollideWithObjectAt`)
  refuses rather than silently mis-applying.
- Verified by `tests/test_footprint_engine_patch.py` (12 tests: install
  shape, idempotency, existing-data preservation, byte-exact
  vanilla-anchor checks for `GetObjectEventIdByPosition` AND
  `DoesObjectCollideWithObjectAt`, refusal on modified source, and the
  optional free-movement path).

#### Phase 7c — Editor UI — COMPLETE (2026-05-21)
`ui/overworld_footprint_dialog.py` (NEW) — modal dialog opened by a new
**"Edit Collision Footprint…"** button next to "Show in Folder" in the
Overworld Graphics tab's detail panel.

- The dialog renders the sprite's first frame through the live palette
  (caller-supplied QPixmap) scaled up so an 8×8 cell becomes 16–32 screen
  pixels (auto-fit, ~480 px target dimension).  Thin grid lines mark
  the 8×8 cell boundaries the user paints in; thick lines mark the
  16×16 tile grid the engine actually checks (any cell that overlaps
  a tile makes that tile solid).
- Drag-paint: clicking toggles a cell and the *new* value becomes the
  brush — dragging across cells sets them all to that value, so a
  whole row can be filled or wiped with one stroke.
- Quick actions: **Clear All** / **Fill All** + OK / Cancel.  Cancel
  is non-destructive — the dialog works on a copy of the incoming
  footprint and only the OK path returns it.
- Warning text in the dialog: doorway trap, walking-sprite collision,
  off-map cells silently ignored.

#### Phase 7d — Save path — COMPLETE (2026-05-21)
Wired into `ui/overworld_graphics_tab.py`:

- `__init__` — new `_footprints: Dict[str, Footprint]` and
  `_footprint_dirty: set[str]`.
- `load()` — clears `_footprint_dirty` and reparses
  `_footprints` from `parse_project_footprints(root)` (empty when the
  header doesn't exist yet, matching every other plugin's contract).
- `_on_edit_footprint` — renders the sprite's first frame with the
  live palette (Index-as-BG-aware), opens `FootprintEditorDialog`
  with the current footprint, and on accept marks the sprite dirty.
- `_make_thumbnail` — card amber border now includes
  `_footprint_dirty` alongside the palette and anim-table dirty sets
  (Pattern B).
- `has_unsaved_changes` — reports True while any footprint is dirty.
- `flush_to_disk` Pass 2c — auto-installs the engine support
  (`ensure_footprint_engine_support(root)`) on first footprint save,
  then writes the regenerated `object_event_footprints.h` from
  `serialize_footprints(all_fps)`.  Empty footprints drop from the
  emitted lookup table so a fully-cleared sprite returns to vanilla
  1-tile collision.
- F5 reverts every unsaved footprint edit through the standard
  `load()` reset path (Tab `load()` / F5 Refresh Contract).

#### How to test
1. Open the project, select a multi-tile sprite (e.g. King Zora 48×48
   or the schule 32×32 painter).
2. Click **Edit Collision Footprint…** in the detail panel.  The
   dialog appears with the sprite scaled up and a paintable grid.
3. Paint the cells that should block the player + trigger interaction.
4. Click **OK** → the sprite's grid card turns amber, the toolbar
   shows unsaved changes.
5. **File → Save**.  The engine patcher auto-installs (creates
   `include/object_footprint.h`, the seed
   `src/data/object_events/object_event_footprints.h`, and the
   `GetObjectEventIdByXY` extension in `src/event_object_movement.c`)
   and the footprint header gets the new entry.
6. Rebuild (Make Modern).  The sprite now blocks the player on every
   painted cell, and pressing A facing any painted cell triggers the
   object's script.
7. F5 before saving should discard the footprint edit and restore the
   previous state.

---

## Risks & open questions

- **OAM-slot exhaustion.** Composite sprites spend slots fast; many on one map
  can break rendering. Phase 4's cost display + pad suggestions mitigate, but a
  project-wide OAM budget view may be worth a follow-up.
- **Generated subsprite tables modify `object_event_subsprites.h`.** This is
  legitimate engine refactoring (per the project's engine-refactoring mandate)
  but needs solid idempotency + cleanup, same discipline as the DOWP patcher.
- **Existing-sprite size change.** Re-importing an existing sprite at a new
  size must regenerate its OAM/subsprite/rule and not orphan the old table.
- **Animation tables vs. frame count** are independent of frame *size* — the
  emote/anim work already done stays valid; only the size pipeline changes.

---

_Authored 2026-05-15 as the roadmap for the Overworld Editor dimensions
upgrade. Supersedes the hardcoded `_OAM_TABLE` approach entirely._
