# Battle-Anim Preview Accuracy — Roadmap

**Goal:** make the Move Animations preview *substantially* match what the move
looks like in-game — placement, motion, layering, flips, timing — without
fully emulating the GBA battle-anim engine.

**Non-goals:** bit-exact reproduction of all ~340 sprite callbacks + hundreds of
visual-task C functions. We approximate; we label what we can't reproduce; we
never silently show something confidently wrong.

**Honesty contract:** anything not reproduced (bespoke callbacks, GPU blends,
affine/scale tricks) is either labelled "≈ approximate" or omitted with a
caption note — never faked as if exact.

---

## Where it stands (start of roadmap)

Done: timeline parse + structural editing; sprite/palette editing; frame
scrubber; branch-following (`call`/`goto`/`choosetwoturnanim`); ~60% of
createsprite invocations classified into motion archetypes (static-attacker/
target, on-mon, linear-to-target, arc-to-target, invisible); whole-move
playback sequencer; direction toggle.

Known wrong (user-reported): app crashes mid-playback (e.g. Curse); direction
toggle reads swapped; sprite H-flips ignored (Curse nail mirrored); extra
per-callback offsets ignored (nail off by 24px); ghost/late sprites never seen
(crash truncates); many task-only moves still show nothing.

---

## Phase 1 — Stability + the reported placement bugs  *(START HERE)*

1. **Kill the crash.** Add a file logger (`battle_anim.log`). Re-entrancy guard
   on the play step. Throttle/guard sound firing so rapid or overlapping
   `preview_song_by_constant` calls can't take down the audio backend. Stop
   playback on sub-tab switch / widget hide / move change / F5. Defensive
   try/except with logged tracebacks around every tick.
2. **Direction toggle.** Make Player/Enemy unambiguous and correct; verify on a
   clearly directional move (Ember bottom-left → top-right).
3. **Sprite H-flip.** Honor side-dependent horizontal flips. General rule:
   attacker-anchored sprites flip when the attacker is on the player side
   (matches `AnimCurseNail`, projectiles, fists). Carry a `flip` flag through
   geometry → render with a mirrored pixmap.
4. **Per-callback fixed offsets.** Where a curated callback adds a constant
   offset beyond the args (nail +24), encode it in the archetype record.

## Phase 2 — Faithful anchor + coordinate model

1. Port the real battler-coordinate semantics: `GetBattlerSpriteCoord` /
   `…Coord2` with the actual `BATTLER_COORD_{X,X_2,Y,Y_PIC_OFFSET}` deltas, and
   a per-species pic-offset approximation (so sprites sit on the mon, not a
   fixed center).
2. Expand the heuristic to catch direct-coordinate callbacks
   (`SetSpriteCoordsToAnimAttackerCoords`, direct `GetBattlerSpriteCoord(gBattleAnim*)`
   → x/y) so more of the long tail classifies instead of falling back.
3. Add motion archetypes for the next-most-common patterns: circular/vortex,
   lateral sine wave, grow/scale, fixed-screen-position, falls-from-top,
   spiral, fixed-on-attacker-then-static. Read + pin each from source.

## Phase 3 — Visual TASKS (the missing half)

Most status moves are `createvisualtask`, not sprites. Draw representative
attacker/target **mon placeholders** in the preview and reproduce the common
mon-acting tasks as transforms on them:
- `AnimTask_ShakeMon` / `ShakeMon2` / `ShakeMonInPlace` → jitter that battler.
- `DoHorizontalLunge` / slide / `SlideMonToOffset`/`…OriginalPos` → lunge/return.
- `AnimTask_GrowAndShrink` / scale tasks → pulse.
- `BlendColorCycle` / palette-blend sprites / `AnimTask_*Flash` → tint the scene.
Classify tasks the same curated+heuristic way; everything else → caption note.

## Phase 4 — Timeline + lifetime fidelity

1. Sprite lifetimes: destroy a layer when its motion completes (+ short tail)
   instead of accumulating to the end, so the composite matches what's actually
   on-screen at a given moment.
2. Timing opcodes: honor `loopsewithpan` (repeat count/interval),
   `waitforvisualfinish` (advance past task duration), `setarg`/loop constructs.
3. Conditional branches: optionally show the alternate `jumpifmoveturn` /
   `jumpargeq` path (two-turn charge vs release).

## Phase 5 — Affine / scale / rotation + blends

Approximate affine anims (scale/rotate) and alpha blends where the template
declares them, from `affineAnims` + common task patterns. Best-effort, labelled.

## Phase 6 — Verification + polish

Per-sprite accuracy badges (exact-motion vs ≈approx vs not-reproduced); an
accuracy legend; a per-move "coverage" readout; optional side-by-side notes.
Optional later: a true "play in mGBA" button for ground truth (separate effort).

---

## Definition of "a lot more accurate"

- No crashes, ever, during playback or selection.
- Direction + flips correct → sprites appear on the right side, facing right way.
- The common damaging moves (projectile/particle/hit-splat) read correctly:
  origin, travel, impact, layering.
- Status / task-only moves show *something* faithful (mon shake/lunge/flash),
  not a blank scene.
- Everything else is clearly labelled approximate or not-reproduced.
