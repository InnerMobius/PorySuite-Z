# PorySuite-Z v0.0.571b

> pokefirered projects only.
> Hotfix on top of v0.0.57b.

After the v0.0.57b hotfix for the `egg_hatch_speed` ability field-effect template, the user asked for an audit of every other ability-effect template to confirm none of them shipped the same class of bug. They did. Seven more templates had latent build-breaks or broken-code-generation paths that would only surface when the user tried to save an ability with that specific effect selected and then ran Make Modern. All seven are now fixed. v0.0.571b is the release that carries those fixes.

## What's Fixed in 0.0.571b

### Abilities Editor — Audit-Wide Template Fix

Seven ability-effect templates in `core/ability_effect_templates.py` were emitting C code that referenced outer-function local variables that aren't in scope at the injection point — exactly the same shape of bug that broke Halve Egg Hatch Steps. Each template was rewritten to declare its own locals inside a self-contained braced block, and three templates also had their apply-logic (the code that decides WHERE to inject) corrected.

**Field effects fixed:**

- **Type Encounter.** Was using `gBaseStats` / `type1` / `type2` — wrong struct for this tree; pokefirered ships `gSpeciesInfo[].types[0..1]`. Was also referencing `i` and `species` at a point BEFORE the host function declared them. Rewritten to use `gSpeciesInfo` and to declare its own `u8 t_idx, t_max; u16 t_sp;` locals. Injection moved to right above `level = ChooseWildMonLevel(...)` so `info`, `area`, and `slot` are actually in scope.
- **Nature Sync.** Used a C compound literal that pokefirered's `preproc` chokes on in some paths. Called `GetNatureFromPersonality`, which is a `static` function in `pokemon.c` and therefore not visible from `wild_encounter.c`. The nature math (`(enemy / 25) * 25 + (lead % 25)`) is now inlined directly, no cross-file static call. Apply-logic also rewritten — it used to insert at the first brace-balanced close, which on `GenerateWildMon` landed between an `if (species != SPECIES_UNOWN) { ... }` and its `else { ... }`, causing `'else' without a previous 'if'`. It now walks from the function signature to the function's own closing brace.
- **Gender Attract.** Same apply-logic bug as Nature Sync. Fixed the same way.
- **Guaranteed Escape.** Emitted a bare `return BATTLE_RUN_SUCCESS;` without wrapping braces, which broke the `_remove_marker_block` helper and made Clear Field Effect leave orphan scaffolding. Apply-logic was also inserting BEFORE the vanilla Run Away return, making the injected block unreachable. Body is now wrapped in `{ }` and injection now lands AFTER the vanilla return.

**Battle effects fixed:**

- **Type Resist** and **Multi-Type Resist.** Referenced `moveType` and mutated `damage`, but at the injection site in `CalculateBaseDamage` the variable is named `type` and the running damage factor at that stage is `gBattleMovePower`. Renamed accordingly.
- **Stat Double with STAT_SPEED.** Speed isn't one of `CalculateBaseDamage`'s locals — it lives in `GetWhoStrikesFirst` in `battle_main.c`. Emitting `speed *= 2` into `CalculateBaseDamage` produced `'speed' undeclared`. STAT_SPEED (and any future unrecognised stat) now emits a no-op comment with a plain-English note telling the user that speed-class boosts need to be edited in `GetWhoStrikesFirst` manually. STAT_ATK / STAT_DEF / STAT_SPATK / STAT_SPDEF continue to work normally.
- **Plus/Minus.** Was calling `BATTLE_PARTNER(battler)`, but the `CalculateBaseDamage` local is named `battlerIdAtk`. Also renamed the partner local to `pm_partner` so it can't clash with anything nearby.

### Verification

A three-tier test harness was built alongside the fix and kept around for future template audits:

1. **Harness compile** — synthetic C test files with exactly the locals each injection site provides. Compiles every template with `arm-none-eabi-gcc -mthumb -mcpu=arm7tdmi`. 10/10 OK.
2. **End-to-end build** — scratch mirror of the user's real pokefirered tree, apply each template, then `make CC=arm-none-eabi-gcc MODERN=1` the touched object file via MSYS2 bash with devkitPro. 10/10 OK.
3. **Remove + idempotent reapply** — proves that apply-once equals apply-twice (idempotent) and apply-then-remove equals pristine (clean removal). 5/5 OK.

Per the project's hands-off-pokefirered rule, no game source files were modified — everything was tested against throwaway scratch mirrors.

## Files of Note

- Updated: `core/ability_effect_templates.py` — seven template rewrites plus three apply-logic corrections.
- Updated: `core/app_info.py` — VERSION bump to `0.0.571b`.

## Known Limitations

- STAT_SPEED on the Stat Double battle effect emits a no-op with a note — it does NOT auto-edit `GetWhoStrikesFirst`. This is intentional; the injection there is more invasive than a drop-in, and the honest no-op is better than silently producing code that doesn't compile.
- No other effect-template behaviour is changed from v0.0.57b. Everything that already worked still works.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
