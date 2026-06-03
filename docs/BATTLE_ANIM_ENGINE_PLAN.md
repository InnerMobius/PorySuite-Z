# Battle Animation Engine — Implementation Plan

**Status:** approved 2026-06-03. Supersedes the hand-ported VM approach
(`core/battle_anim_vm.py`, `core/battle_anim_tasks.py`), which is retired once
this lands.

## The decision

Hand-porting ~420 sprite/task callbacks into Python is whack-a-mole: it can't
cover every move, produces garbage for the unported majority, and breaks on
projects that edit a move. Instead we **run pokefirered's own animation C**
(compiled to WebAssembly) as an invisible in-process backend; the editor's
Battle Anims tab calls it for per-frame sprite positions and renders them in its
existing Qt preview.

**Proven (spike, commit `a15ca10`):** the entire anim subsystem (sprite.c,
task.c, battle_anim*.c) compiles + links + runs on the host with one tiny
force-include shim; built 32-bit, the real Ember ran exactly (92→160, destroy
frame 21). WASM is a 32-bit target, so those pointer assumptions hold and the
artifact is one file for Windows/Linux/macOS/ARM.

## Goal (viewer first)

The Battle Anims tab's **existing preview** plays every move correctly — same
Play button, same scrubber, same panel. Nothing the user touches changes; only
the math underneath (the wrong "archetype guesser") is replaced by the real
engine. Correct VIEWING is the prerequisite for the editing features
(sprite-position tweaks, swaps) that come after.

## Architecture

- **Engine** = a `.wasm` built from the project's anim C + a host harness
  (`enginehost/`). Computes motion only (OAM per frame). No pixels, no UI.
- **Runtime** = `wasmtime` (pip), in-process. The tab calls exported functions
  and reads results from wasm linear memory — a normal Python call, no
  subprocess, no window.
- **Rendering** = the tab's existing `BattleScenePreview` + `sprite_render` +
  `sprite_palette_bus`. The engine says where each sprite is; Qt draws it with
  the project's PNGs/palettes (already wired).
- **Script parsing** = `core/battle_anim_script.py` (already dynamic — handles
  edited scripts). Python feeds the parsed commands to the engine.

Key simplification: the engine computes the **whole move's frames in one call**
→ the tab plays/scrubs that table instantly, zero per-frame engine calls.

## Phases

- **P1 — WASM build of the engine.** Compile `enginehost/` + the project's anim
  C to `wasm32-wasi` (wasi-sdk clang). Verify the Ember spike runs via
  `wasmtime` (cross-platform parity with the native spike). Generate the
  template/task name→index table at build time so Python can request any sprite.
- **P2 — Build pipeline (`core/anim_engine_build.py`).** Auto-generate the
  dummy-data stub from the link's undefined-data symbols, compile, emit
  `anim_engine.wasm` + a `name table` JSON. Ship the prebuilt vanilla wasm;
  rebuild only when a project's anim C hash changes (advanced/rare — later).
- **P3 — Python driver (`core/battle_anim_engine.py`).** Load the wasm via
  wasmtime; API: `reset(scene)`, `create_sprite(name, battler, subpri, args)`,
  `create_task(name, args)`, `run(frames)` → per-frame OAM for all sprites +
  mon state (x, y, x2, y2, tileNum, matrix, hFlip, vFlip, priority, subpriority,
  paletteNum, invisible).
- **P4 — Render in the tab.** Map OAM → pixels via the existing pipeline
  (tag→PNG, frame→slice, flip/affine→transform, subpriority→z). Replace the
  VM-driven playback in `ui/battle_anim_tab.py`; Play/scrub drive the table.
- **P5 — Mon effects + backgrounds.** Read mon invisible/affine for Dig/Fly/
  Bulk Up; BG-scroll globals + BG image for Surf-class moves. Same preview.
- **P6 — Retire the guesser.** Delete `battle_anim_vm.py` / `battle_anim_tasks.py`
  and their tab wiring once engine playback is solid (no dead code).

## Dependency handling (folded in)

Today: the **Program Setup** page (`core/programsetup.py`) installs pip deps
in-app (`find_spec` check + a "pip install" button) for PyQt6/numpy/sounddevice/
mido/etc.; launch (`app.py`) shows Setup only if a "setup complete" marker is
absent.

Changes:
- **`anim_engine.wasm` ships bundled** with the app update (a data file like
  `res/`). No install.
- **`wasmtime`** → one new line in `requirements.txt` and **one new entry in the
  Setup page's component list** (same `find_spec`/pip-install pattern as the
  existing seven). Not a new kind of burden.
- **Launch re-verifies components, not just the marker.** Upgrade the launch
  gate so a missing required component (this dep or any future one) re-opens
  Program Setup automatically even when the marker exists.
- **Graceful in-tab fallback.** The Battle Anims tab checks for `wasmtime` + the
  `.wasm`; if missing it shows "Animation engine not installed — Open Program
  Setup" instead of erroring.

UX after update: launch → app notices the one missing piece → Setup opens →
single "pip install wasmtime" click → done. The engine file came with the update.

## Risks / notes

- Rebuilding the wasm for projects that edit the anim **C** needs wasi-sdk on the
  user's machine (rare; most edits are scripts/graphics, handled without a
  rebuild). Default: ship prebuilt vanilla wasm.
- Palette-cycling moves (Sing rainbow) — engine computes palette writes; reading
  them to drive colors is a P5 refinement, approximate until then.
- Per-species mon coords + sprite frames come from `GraphicsDataCache` + the
  sprite pipeline (already available).
