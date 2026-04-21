# PorySuite-Z v0.0.64b

> pokefirered projects only.

This release rounds out the Overworld Graphics editor, hardens the moves writer against a class of crash that already bit items in v0.0.62b, brings the Sound Editor fully into the dirty-flag system, and polishes a handful of rough edges found during daily use.

## What's New in 0.0.64b

### Overworld Graphics — Field Effect Sprites Editor

The Overworld Graphics tab now has two sub-tabs: **NPC Sprites** (unchanged) and **Field Effect Sprites**. The new tab covers the sprites the engine uses for in-world feedback — exclamation marks, question marks, music notes, emoticons, egg hatch, confetti, and similar. These were previously invisible to the tool.

You can browse and search all sprites in `graphics/field_effects/pics/` and `graphics/misc/`, view the full sheet, edit the 16-colour palette, drag-reorder slots, and use Index as Background — the same palette-editing toolkit available for NPC sprites and trainer pics. Most field effect sprites have no separate palette file; their colour table is baked into the PNG. Saving writes a `.pal` file when one exists, and bakes the new palette directly into the PNG when it doesn't. All edits participate in the dirty-flag system: the toolbar dot, title bar asterisk, and per-list amber tinting all behave the same as every other graphics tab.

### Overworld Graphics — Dynamic Palette Improvements

Three improvements to the Dynamic Overworld Palettes workflow:

- **Disable button.** When Dynamic Palettes are active, a red "Disable Dynamic Palettes…" button now appears. Clicking it reverses all five source-file patches and restores the originals. Partial failures report exactly which hunks could not be reversed automatically.
- **Risk scanner.** Before applying the patch, the tool now scans the project for sprites that use a null palette tag (these would show wrong colours under dynamic palettes) and warns if the project is approaching the 16-slot hardware limit. Warnings are shown in the apply dialog before anything is changed.
- **Tint sliders.** The water-reflection palette tint is now configurable. The apply dialog shows three R/G/B sliders (0–15 each, defaulting to 5/5/10) with a live before/after colour preview. The backup-confirmation checkbox must be ticked before the Apply button enables.

### Sound Editor — Dirty Flags and Inline Editing

The Sound Editor now fully participates in the app-wide dirty-flag system. Previously, changes to voicegroups, instruments, or piano roll notes had no visual feedback — no toolbar dot, no title-bar asterisk, no per-row amber tinting.

- **All three sub-tabs** (Songs, Instruments, Voicegroups) now light up the toolbar dot and title-bar asterisk on edit, and clear them on save. Tree rows and groupbox frames turn amber on unsaved edits, row by row.
- **Tempo, reverb, and volume** are now editable directly on the Songs tab without opening the piano roll. Changes write to disk immediately and mark the song row amber.
- **Copy/paste in the piano roll** now carries the volume, pan, and pitch-bend state established before the selection. Previously, pasting notes from the middle of a song used the wrong volume and pan because the events that set that state earlier in the track weren't included in the clipboard. The pasted notes now play at the same levels they had in the original context.

### Moves Writer — Gap-Padding Fix

The moves array writer now pads gaps when the project defines move constants with non-contiguous index values, the same fix applied to the items array in v0.0.62b. Without this, custom moves with high constant values could cause out-of-bounds reads at runtime. The writer reads the constants file, finds the highest defined index, and writes placeholder entries for every gap — keeping the array fully indexable by any constant value.

### Rename Dialog — Early Warning Counter

The character counter in the rename dialog now has three states: grey (fine), amber (at 85% of the limit), and red (at the limit). Previously it only switched to red at the hard cap, giving no advance notice before a name was cut off.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
