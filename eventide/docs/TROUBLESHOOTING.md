# EVENTide — Troubleshooting

## Common Issues

### "Oak's script is blank / shows 0x0"
**Cause**: Oak's object_event in map.json has `"script": "0x0"` — his real scripts are on coord_events (triggers like OakTriggerLeft, OakTriggerRight).
**Fix**: This was fixed by loading all event types. Select `[Trigger] OakTriggerLeft` or `[Trigger] OakTriggerRight` from the dropdown to see his scripts.

### "I only see 3 events but the map has more"
**Cause**: Before the all-event-types fix, only object_events loaded. Triggers, signs, and map scripts were invisible.
**Fix**: The dropdown now shows `[NPC]`, `[Trigger]`, `[Sign]`, `[Hidden Item]`, and `[MapScript]` entries. Hidden items show the item name (e.g. `[Hidden Item] Rare Candy`). If you still see missing events, check the log panel for warnings.

### "Message text is empty or truncated"
**Cause**: The text label might be in `data/text/*.inc` (e.g. sign_lady.inc, fame_checker.inc) rather than the map-local `text.inc`.
**Fix**: `parse_all_texts()` searches both locations. If text is still missing, check that the label in the script matches an actual label in a .inc file.

### "Commands appear in wrong order"
**Cause**: Historical bug where insert-before-empty-line logic reversed the list during initial page load.
**Fix**: Page load now uses `addItem` (append) instead of insert. This was fixed in the RMXP rewrite.

### "Script not found in scripts.inc" warning in log
**Cause**: An event references a script label that doesn't exist in that map's scripts.inc file. The script might be in a shared file or defined elsewhere.
**What to do**: This is a warning, not an error. The event will show an empty command list. The script might be a common routine defined in a shared include file.

### "Unreferenced labels in scripts.inc" warning in log
**Cause**: scripts.inc has labels that no event in map.json points to directly. These might be helper functions called by other scripts, movement labels, or leftover dead code.
**What to do**: Usually harmless. Movement labels and .equ directives are filtered out of this warning. If you see labels you know should be connected, check the map.json for typos.

### "Properties are grayed out for triggers/signs"
**Expected behavior**: Trigger and sign events have their position and type set in map.json — you edit their scripts, not their placement. Only NPC object_events have fully editable properties (ID, position, graphic). Map scripts have no spatial properties at all.

### Save doesn't change map.json for triggers/signs
**Expected behavior**: The editor saves script changes to scripts.inc. Trigger/sign/map_script positional data in map.json is preserved as-is. Only NPC object_event properties (local_id, x, y, graphics_id) are written back from the editor fields.

### "Find Unused Flag" shows no flags
**Cause**: Your project doesn't have `FLAG_UNUSED_*` constants defined in `include/constants/flags.h`.
**Fix**: Add unused flag definitions to the flags header, or use a different naming convention. The scanner looks specifically for constants starting with `FLAG_UNUSED_`.

### Quest template created but goto targets don't match
**Cause**: If you rename the main script label after creating the template, the sub-label names still reference the old name.
**Fix**: Use the "Rename" button on each page — it automatically updates goto/call references across all pages of the current event.

### Sprite disappears or properties show wrong map's data after switching maps
**Cause**: Fixed bug where switching maps could corrupt object entries. The old map's UI state was being written into the new map's object list because `_current_obj_idx` wasn't reset before rebuilding. For example, if you were viewing a MapScript on IndigoPlateau at index 2, then opened PalletTown, Oak (also index 2) would get his graphics_id overwritten to empty and his script set to the IndigoPlateau label.
**Fix**: `_load_map` now resets `_current_obj_idx = -1` before doing anything, so `_collect_current()` safely bails out and doesn't corrupt the new map's data.

### Trainer Battle edit dialog shows wrong field values (shifted by one)
**Cause**: Fixed bug where the widget assumed `parts[0]` was a numeric battle type, but pokefirered uses named variants (`trainerbattle_single`, `trainerbattle_double`, etc.) where the type is in the command name. With `trainerbattle_single TRAINER, INTRO, DEFEAT`, the TRAINER ended up in the Type slot, INTRO in the Trainer slot, etc.
**Fix**: Rewrote `_TrainerBattleWidget` to be variant-aware. It reads the command name to determine the type, and each variant has the correct field layout. The Type dropdown was removed since it's determined by the command.

### "Go To →" says target not found
**Cause**: The target script label exists in a shared/common file (like `EventScript_Return`) rather than in this map's scripts.inc.
**What to do**: Common scripts are defined globally and shared across all maps. They can't be navigated to from a single map's editor. The target label is still valid — it just lives elsewhere.

### Trainer battle "Continue Script" dropdown forced picking a script
**Cause**: The continue script dropdown didn't have a blank/none option — it started on the first script label in the list, making it look required.
**Fix**: The dropdown now starts blank. The continue script is optional. Most regular trainers don't have one — only gym leaders and story NPCs use them (for badge scenes, cutscenes, etc.). Leaving it blank produces a standard `trainerbattle_single` with just trainer, intro text, and defeat text.

### After saving, scripts.inc had duplicate "end" commands or lost movement data
**Cause**: `write_scripts_inc` rebuilt the entire file from scratch using only event-script labels, dropping everything else (movement tables, macros, .equ directives). It also unconditionally appended `end` after every script even if one was already there, and injected `lock`/`release` into sub-scripts that shouldn't have them.
**Fix**: The writer now reads the existing file and only replaces the label blocks it manages. Movement data, macros, and all non-event content is preserved. Duplicate end/lock/release injection is prevented by checking the actual commands list.

### Build fails with "symbol already defined" after saving in EVENTide
**Cause**: `parse_all_texts()` loads text from both the map's local `text.inc` AND global `data/text/*.inc` files. On save, ALL of those labels were written into the map's local text.inc, creating duplicates of global symbols.
**Fix**: Only local text labels (and newly created ones) are written back to the map's text.inc. Global labels are used for lookups only.

### Build fails with "unexpected trailing comma" in map.json
**Cause**: EVENTide saved files with Windows CRLF line endings. The build tools run in MSYS2/Linux and choke on the `\r` characters.
**Fix**: All EVENTide file writes now force Unix LF line endings via `newline='\n'`.

## Log Panel
The log panel at the bottom of the EVENTide window shows:
- Load counts for each event type when a map opens
- Verification results (warnings or "all events and scripts OK")
- Save confirmations
- Any parse errors

Always check the log panel first when something seems wrong.

### Trainers tab is completely empty / shows "Open a project to edit trainers"
**Cause**: Fixed bug where the "Set up battle script" button was connected to a method that didn't exist on the panel class it was in. This crashed the panel constructor inside `load()`, and since the error was caught by a bare except, the trainer list never populated — silently.
**Fix**: The button now emits a signal on the panel, which the parent widget connects to its handler during `load()`. Trainers load normally.

### Title bar shows `*` (unsaved changes) without making any edits
**Cause**: Fixed bug where almost any navigation — switching toolbar pages, clicking Pokemon sub-tabs (Stats, Evolutions, Moves, Graphics), selecting different Pokemon in the species tree, or viewing different trainers — triggered the `*` indicator. Root cause: PorySuite's code calls `setWindowModified(True)` internally whenever it populates UI fields. Loading a trainer fires widget signals, switching sub-tabs saves and reloads species data, and the unified window was forwarding all of these as "unsaved changes."
**Fix**: Three layers of suppression now prevent false dirty flags:
1. Page-switch operations (flush + lazy-load) are wrapped with `_suppress_dirty`
2. PorySuite's Pokemon sub-tab and species tree signals are disconnected and re-wired through suppression wrappers (`_ps_suppress_dirty`)
3. The trainer detail panel has a `_loading` guard that prevents `changed` from firing during field population
The `*` now only appears when you actually edit something — type in a field, change a dropdown, modify a party member, etc.

### Rematch variant trainers (BEN_2, BEN_3, etc.) cluttering the trainer list
**Cause**: Fixed bug where VS Seeker rematch variants showed as separate entries in the trainer list. TRAINER_YOUNGSTER_BEN_2, _3, _4 are rematch tiers of the base trainer, not separate trainers.
**Fix**: `_build_rematch_map()` now returns a set of all variant constants. `_rebuild_list()` skips any constant in that set. Rematch tiers are accessible through the "Battle tier" dropdown in the Party tab on the base trainer.

### Rematches tab showed nothing for rematchable trainers
**Cause**: Fixed bug where the rematch lookup only matched the base trainer constant (tiers[0]). If you somehow selected a variant constant, or if the selected trainer was the base but the lookup key didn't match, the tab showed "not in the VS Seeker rematch table."
**Fix**: `_build_rematch_map()` now builds a reverse lookup that maps ANY tier constant back to its rematch entry. The separate Rematches tab was removed entirely — rematch tiers are now shown in the Party tab as a dropdown.
