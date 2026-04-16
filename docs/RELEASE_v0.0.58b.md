# PorySuite-Z v0.0.58b

> pokefirered projects only.

A real feature release this time, not a hotfix. The user called out a limitation of the Add Ability dialog: it could only copy effects from other abilities, never create a new one with a full template. All the template infrastructure already existed in the codebase — it just wasn't wired into the add flow. Fixed. Also folded in a small but annoying dirty-flag bug that made newly-added abilities look pristine until Save. And late in the cycle — during user testing of the new template flow — a silent regression was found where the **Hail** weather pick produced **Rain** behaviour in-game. That's fixed too, with a proper end-to-end build harness proving both of the two layers that were broken.

## What's New in 0.0.58b

### Abilities Editor — Add Ability Now Takes Full Templates at Creation Time

The Add Ability dialog used to be copy-only: you could choose "copy battle effect from: [existing ability]" and "copy field effect from: [existing ability]", which meant if you wanted a brand-new `type_resist_halve TYPE_FIRE` ability, you had to first clone some arbitrary other ability, then rewrite the effect from scratch in the right-hand editor. That's busywork the tool should handle for you.

- **Template pickers on the dialog.** Two new groupboxes at the top of Add Ability: "Battle Effect Template" and "Field Effect Template". Each has a template dropdown (populated from the same `BATTLE_TEMPLATES` / `FIELD_TEMPLATES` tables the right-hand editor uses) and a dynamic parameter form. Pick `type_resist_halve`, the form renders a `type` dropdown with every `TYPE_*` in the project. Pick `stat_double`, you get a `stat` dropdown. Pick `guaranteed_escape`, no params needed.
- **Live C preview.** A small preview label under each picker shows the exact C that will be injected at Save time. Change the template or a parameter, the preview updates immediately.
- **"— or —" divider + mutual exclusion.** Template pickers sit above the existing copy-from dropdowns with a clear visual separation. Picking a template on one side greys out the copy-from combo on the same side (and vice versa). You cannot accidentally queue both — they'd stomp on each other at Save time.
- **Templates live in RAM until Save.** Clicking OK stashes the picked template on the in-memory ability dict (`data["_battle_effect"] = (template_id, params)` / `data["_field_effect"] = (template_id, params)`). Nothing is written to disk. The existing Save pipeline picks those keys up and runs the real `apply_battle_effect` / `apply_field_effect` at Save time, same as it already does for existing abilities. Cancel on the unsaved project and nothing happened.
- **Hint label rewritten.** The old dialog footer said "Pick a battle effect and/or field effect to copy." It now says "Template effects write C on Save (not now). Copy-from writes C immediately on OK. Leave everything (none) for a bare ability you'll customize later from the right-hand editor." — which is accurate and tells the user what the difference actually is.

### Newly-Added Abilities Now Tint Amber Until Save

A regression the user noticed while testing the new template flow: a freshly-added ability showed up in the list but painted like a pristine saved row — no amber tint, no visual distinction from abilities already baked into the project. Every other editor in the toolbar already paints unsaved edits amber; abilities was the outlier.

Root cause: the Abilities tab rebuilds its `QListWidget` from scratch on every add / rename / delete (it calls `self._list.clear()` and recreates the items). The dirty role (`Qt.ItemDataRole.UserRole + 500`, value 756) that the shared `_DirtyDelegate` paints from was being set on the item, then blown away by the next rebuild.

Fix: `AbilitiesTabWidget` now owns a persistent `_dirty_consts: set[str]` that survives the rebuild cycle. Every mutation path adds to it (`_on_add`, `_on_duplicate`, `_on_detail_changed`), delete discards, rename rekeys. `_rebuild_list()` re-applies the role to every item whose const is in the set. A new `clear_all_dirty()` method wipes both the set and the role on every item — wired into `mainwindow._clear_all_dirty_markers()` so the amber clears on successful save.

### Hail Weather Pick Silently Wrote Rain — Fixed

User testing of the new Add Ability template flow turned up a nasty silent regression: creating an ability with `weather_switchin` + Hail selected produced an ability that called **Rain** in-game, not Hail. The C got written, the project built, the rom loaded, the ability fired — and it summoned rain. No error, no warning, just wrong. Two bugs stacked on each other, both fixed:

- **The weather lookup was type-confused.** `WEATHER_CHOICES` is a list of `(display_name, info_dict)` tuples. The dialog's dynamic param combo stored each choice's *info dict* as the combo's `currentData()`. The downstream codegen helper `_get_weather_info(weather_display)` was typed for a display-name string and did `for display, info in WEATHER_CHOICES: if display == weather_display`. The dict never equals any string, so the loop never matched, and the function silently fell through to `WEATHER_CHOICES[0][1]` — which is Rain. Every weather pick (Hail, Sandstorm, Sun) was emitting Rain C code. Rain "worked" only because Rain was the default fallback. Fix: `_get_weather_info` now accepts either form — dict or string — and routes both to the correct info dict.
- **The auto-synthesized Snow Warning script was missing its extern.** `_ensure_snow_warning_battle_script` already appended the script body to `data/battle_scripts_1.s` on Save, but NOT the matching `extern const u8 BattleScript_SnowWarningActivates[];` declaration in `include/battle_scripts.h`. Even after Layer 1 was fixed, `battle_util.c` refused to compile with `'BattleScript_SnowWarningActivates' undeclared`. Fix: the helper now patches both files, both idempotently. Safe to re-save any number of times.

A dedicated regression harness (`C:\tmp\porysuite-audit\hail_ability_regression.py`) was added to the audit suite. It covers both param forms:

| Scenario | Param form | Outcome |
|---|---|---|
| `ABILITY_HAIL_DEMO_A` | DICT (what the dialog stores) | Hail C emitted, script + extern written, `src/battle_util.o` compiles OK |
| `ABILITY_HAIL_DEMO_B` | STRING (what a disk round-trip stores) | Same |

Before the fix: both scenarios either emitted Rain C (Layer 1) or failed to compile (Layer 2).

User tested the fixed build against a real Charmander-funnylizard-Hail setup. Confirmed: hail fires, "Hail started!" string displays, Ice animation plays. Working as intended.

### Verification

An end-to-end build test was added to the audit harness (`C:\tmp\porysuite-audit\add_ability_and_build.py`) to prove the full add-with-template flow produces code that actually builds. Four scenarios, 4/4 OK:

| Scenario | Effect | Built object |
|---|---|---|
| `ABILITY_STURDY_SKIN` | battle `type_resist_halve` TYPE_FIRE | `build/firered_modern/src/pokemon.o` |
| `ABILITY_LUCKY_FOOT`  | field `guaranteed_escape`            | `build/firered_modern/src/battle_main.o` |
| `ABILITY_CHARM_MATCH` | field `nature_sync`                  | `build/firered_modern/src/wild_encounter.o` |
| `ABILITY_SWIFT_STRIKE`| battle `stat_double` STAT_ATK        | `build/firered_modern/src/pokemon.o` |

Each scenario runs the same pipeline the real Save button does: write `include/constants/abilities.h` first (so `ABILITY_FOO` is defined before any effect C references it), then `src/data/text/abilities.h`, then apply the template via `apply_battle_effect` / `apply_field_effect`, then `make CC=arm-none-eabi-gcc MODERN=1` the touched `.o` through MSYS2 bash + devkitPro.

Per CLAUDE.md "Hands Off pokefirered", no game source files were modified. Every scenario ran against a throwaway scratch mirror (top-level dirs junctioned, `src/`/`include/`/`data/` as real copies because preproc chokes on junctioned files).

## Files of Note

- Updated: `ui/abilities_tab_widget.py` — AddAbilityDialog rewrite with template pickers, `_dirty_consts` set, `clear_all_dirty()` method, dirty-role re-apply in `_rebuild_list`. Also: `_compose_template_notes` now handles both dict and string weather-param forms so the Hail hint appears whether the ability is freshly picked or loaded from disk.
- Updated: `core/ability_effect_templates.py` — `_get_weather_info` accepts either dict or string; `_ensure_snow_warning_battle_script` now writes both the `.s` body AND the `.h` extern; `apply_battle_effect` normalizes the weather param through the same lookup so prerequisites fire correctly for both flows.
- Updated: `ui/mainwindow.py` — `_clear_all_dirty_markers` now calls `ab.clear_all_dirty()` on the abilities tab.
- Updated: `core/app_info.py` — VERSION bump to `0.0.58b`.

## Known Limitations

- Template pickers on the Add dialog are mutually exclusive with copy-from on the SAME side (battle vs field). You can still mix-and-match across sides — e.g. template for battle, copy-from for field — which is intentional. If you want a full template for both sides, pick both.
- The STAT_SPEED no-op from v0.0.571b still applies: picking `stat_double` with `STAT_SPEED` emits a comment explaining the injection site in `CalculateBaseDamage` doesn't own the speed stat, and directs the user to `GetWhoStrikesFirst` in `battle_main.c` for manual editing.
- **If you created a `weather_switchin` ability on a pre-0.0.58b build and picked Hail / Sandstorm / Sun, the C code written to disk was incorrect (Rain).** The Abilities editor will display the ability as "Rain" on load because that's what the on-disk code says. To correct it: open the ability, re-select the intended weather from the Battle Effect's Weather dropdown, Save. The tool will overwrite the bad block and emit the correct code (plus the Hail script + extern if needed).
- No other editor behaviour is changed from v0.0.571b. Everything that already worked still works.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
