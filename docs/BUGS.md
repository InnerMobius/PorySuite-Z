# Known Bugs — PorySuite-Z

Tracked bugs, confirmed root causes, and fix status. This file persists across sessions so nothing gets lost or hallucinated.

---

## Piano Roll — Save Pipeline

### BUG: Piano roll save destroys PATT subroutine structure
- **Status:** FIXED (2026-04-08)
- **File:** `core/sound/song_writer.py`
- **Evidence:** MUS_EVIL track 6 — after piano roll save, PATT commands had no `.word` target labels. Dead code appeared after FINE. Notes missing (205 → 157), positions wrong.
- **Root cause:** The save pipeline tried to mix flattened notes (expanded from PATT calls) back into the original PATT/PEND structure. Produced dead code after FINE, orphaned notes, broken subroutine boundaries.
- **Fix:** When a track uses PATT/PEND subroutines, the save now strips them entirely and writes a fully linear track with a GOTO loop from the piano roll's loop region. PATT cannot survive the flatten→edit→save round-trip.

### BUG: Piano roll save halves volume every save cycle (VOL/TEMPO double-evaluation)
- **Status:** FIXED (2026-04-08)
- **File:** `core/sound/song_writer.py`
- **Evidence:** MUS_EVIL Vol 90 → MUS_TEST Vol 63 after one save. Each save applied `*mvl/mxv` on top of an already-evaluated value.
- **Root cause:** Parser evaluates `127*mvl/mxv` → 89 (byte value). Writer wrote `89*mvl/mxv` — applying the multiplier again. Same issue with TEMPO (`*tbs/2`).
- **Fix:** Added `_raw_vol()` and `_raw_tempo()` reverse-evaluation helpers. All 5 VOL and 2 TEMPO write sites use them. Round-trip is now lossless (127 → 89 → 127).

### BUG: Piano roll save swaps instruments (voice 9 → 68)
- **Status:** FIXED (2026-04-08) — was a side effect of PATT corruption
- **Evidence:** MUS_EVIL Track 4 showed "9: Glockenspiel" but MUS_TEST (corrupted save) showed "68: Classical Oboe".
- **Root cause:** The PATT corruption rewrote track commands, and the wrong VOICE value ended up in the saved track. After fixing the PATT stripping, save confirmed correct voice values (debug log verified: track 3=voice 9, track 4=voice 9, track 5=voice 21).

### BUG: Piano roll save strips BEND events from loop body
- **Status:** PARTIALLY FIXED (2026-04-08)
- **File:** `core/sound/song_writer.py`
- **Notes:** The save pipeline now correctly preserves BEND events from the canvas (including user edits from the Note Properties dialog). However, BENDs already missing from the loaded .s file cannot be recovered automatically. Users can add them back via the Note Properties dialog (right-click a note → Edit Note Properties).

### BUG: Piano roll save creates duplicate assembly labels across tracks
- **Status:** FIXED (2026-04-08)
- **Files:** `ui/piano_roll_window.py`, `ui/piano_roll_structure.py`
- **Evidence:** mus_kakariko.s has `intro:` at line 27 (track 1) and line 285 (track 2). Build error: "symbol 'intro' is already defined".
- **Root cause:** The user's friendly loop label (e.g. "intro") was written as-is into every track. Assembly labels are file-global, so multiple tracks with the same label name = assembler error.
- **Fix:** The save pipeline now prefixes each track's label with the track name: `{track.label}_{friendly_name}` (e.g. `mus_kakariko_1_intro`, `mus_kakariko_2_intro`). All unique. The Song Structure panel strips the prefix when displaying, so the user still sees "intro". `get_loop_label()` returns the friendly name, and the save re-prefixes per track.
- **DO NOT CHANGE:** The per-track label prefixing in `_sync_notes_to_song()` or the `_strip_track_prefix()` method in `SongStructurePanel`. Removing either re-creates the duplicate label assembler error.

### BUG: Piano roll save can create duplicate loop labels (within same track)
- **Status:** FIXED (2026-04-07)
- **Fix:** Skip new loop labels when `has_orig_structure` is True in `notes_to_track_commands`.

### BUG: Piano roll save loses user's instrument/volume/pan edits
- **Status:** FIXED (2026-04-07)
- **Fix:** `notes_to_track_commands` now always uses caller's VOICE/VOL/PAN values instead of original file's tick-0 commands.

### BUG: Piano roll save destroys PAN interleaving (MUS_TEST corruption)
- **Status:** FIXED (2026-04-07)
- **Fix:** Timeline-based WAIT generation — notes no longer auto-emit WAITs for their duration. All WAITs come from tick gaps between timeline events.

### BUG: Piano roll save explodes control events (PAN 18→1829, BEND 36→740)
- **Status:** FIXED (2026-04-08)
- **Files:** `ui/piano_roll_widget.py`, `core/sound/song_writer.py`
- **Evidence:** MUS_EVIL after save had 1,829 PAN commands (original: 18) and 740 BEND commands (original: 36). Only 1 note survived out of 64.
- **Root cause:** PATT flattening duplicates control events — a subroutine's PAN/BEND commands get copied once per PATT call. These duplicates were stored in the canvas `_control_events` list unfiltered, then passed to the save pipeline which wrote them all out. The massive control event flood bloated the timeline and interleaved with notes, causing the output file to be mostly PAN/BEND automation with almost no music.
- **Fix:** Added deduplication at TWO points: (1) At load time in `load_song_data()`, control events are deduped by (tick, type, value) after PATT flattening. (2) At save time in `notes_to_track_commands()`, incoming control events are deduped by (tick, cmd, value) before building the timeline. Both fixes prevent PATT expansion from multiplying control events.
- **DO NOT CHANGE:** The `_seen_ctrl` / `_seen_ctrl_exact` dedup sets in both files. Removing either one re-enables the control event explosion.

---

## Sound Editor — Data Corruption

### BUG: Opening Sound Editor after git pull deletes all MUS_ songs from config files
- **Status:** FIXED (2026-04-13)
- **Files:** `ui/sound_editor_tab.py`, `core/sound/song_table_manager.py`
- **Evidence:** Fresh `git fetch` + `git reset --hard` + `git clean -fd` → open Sound Editor → build fails with `MUS_CAUGHT_INTRO` undeclared. All MUS_ constants removed from songs.h.
- **Root cause:** `cleanup_orphaned_songs()` runs automatically on Sound Editor tab load (line 370). It checks for every MUS_ entry whose `.s` assembly file doesn't exist on disk and DELETES the entry from songs.h, song_table.inc, and midi.cfg. After a fresh git pull, `git clean -fd` removes all `.s` files (they're gitignored build artifacts). So the cleanup sees EVERY song as "orphaned" and nukes them all. Also deletes their .mid files.
- **Fix:** Removed the automatic `cleanup_orphaned_songs()` call from `load_project()`. Orphan cleanup should NEVER run automatically — it's a destructive operation that modifies source files. If needed in the future, it must be a manual action with a confirmation dialog.
- **DO NOT CHANGE:** Do NOT re-add automatic orphan cleanup to `load_project()`. The .s files are build artifacts that may not exist until the project is compiled. Their absence does NOT mean the song is orphaned.

---

## Piano Roll — Playback

### BUG: BEND state incorrectly reset on loop wrap
- **Status:** FIXED (2026-04-08)
- **Fix:** Removed `ts.bend = 0.0` reset in `realtime_sequencer.py` loop wrap. The real GBA M4A engine carries BEND state through GOTO loops.

### BUG: Piano roll loop playback ignores loop region — cursor continues past loop end
- **Status:** FIXED (2026-04-08)
- **File:** `ui/piano_roll_window.py`, `core/sound/realtime_sequencer.py`
- **Evidence:** mus_evil loop_end computed as 1343 (max across all tracks) but Song Structure showed 1151. Cursor played 67+ seconds before looping instead of ~58 seconds.
- **Root cause:** `load_song_data` computed loop_end as the maximum GOTO tick across ALL tracks. Different tracks had GOTOs at different tick positions after PATT flattening (e.g. track 0 at 1151, another track at 1343). The sequencer used 1343, but audible notes ended around 1151.
- **Fix:** After loading, the canvas loop region is overridden with track 0's `get_flattened_loop_info()` — track 0 is the structural authority (same as what Song Structure panel displays).

### BUG: Piano roll loop save doesn't persist — edited loop back position lost on reload
- **Status:** FIXED (2026-04-08)
- **File:** `core/sound/song_writer.py`
- **Evidence:** User set loop back to tick 960 in Song Structure, saved, reopened — loop back showed original value (1151).
- **Root cause:** `notes_to_track_commands` appended the GOTO after processing ALL timeline events. If notes existed past the loop end tick, `current_tick` was already past the desired GOTO position, so the GOTO landed at the wrong tick. The .s file had GOTO at ~1151 instead of 960.
- **Fix:** Loop GOTO is now inserted into the timeline at the correct tick with priority 3, so it sorts before any later notes. The emit loop breaks on GOTO/FINE, excluding notes past the loop end from the saved file.

### BUG: TIE/sustained notes cut off after ~4 seconds
- **Status:** FIXED (2026-04-07)
- **Fix:** Duration cap raised from 4 seconds to 60 seconds in `realtime_sequencer.py`.

### BUG: BEND (pitch shifting) not processed during playback
- **Status:** FIXED (2026-04-07)
- **Fix:** Added BEND/BENDR control event extraction from flattened commands, processing in audio callback, and pitch offset application in `_queue_note`.

### BUG: Instrument names all showing "Piano" in sidebar
- **Status:** FIXED (2026-04-07)
- **Fix:** `extract_track_infos` in `piano_roll_tracks.py` — VOL `break` was exiting loop before VOICE was found. Fixed with separate boolean flags.

### BUG: Loop region only from track 0 (MUS_OOT_GANON 8-measure restart)
- **Status:** FIXED (2026-04-07)
- **Fix:** Scan all tracks for latest `loop_end` and earliest `loop_start` in `piano_roll_widget.py`.

---

## Piano Roll — UI

### FEATURE: Note Properties dialog (BEND editing)
- **Status:** ADDED (2026-04-08)
- **File:** `ui/piano_roll_widget.py`
- **Details:** Right-click a note → Edit Note Properties. Shows note info and editable table of BEND/BENDR/VOL/PAN control events at that note's position. Add/delete events. Edits update playback immediately and are saved to the .s file.

### FEATURE: MIDI import instrument dropdowns
- **Status:** ADDED (2026-04-08)
- **File:** `ui/dialogs/midi_import_dialog.py`
- **Details:** Instrument mapping page uses named QComboBox dropdowns instead of number spinners. Color-coded: red for empty/filler slots, green for real instruments.

---

## Unified Window

### BUG: Phantom "Unsaved Changes" dialog after saving from Sound Editor
- **Status:** FIXED (2026-04-08)
- **File:** `ui/unified_mainwindow.py`
- **Evidence:** User saved in Sound Editor, then tried to close app — got "Unsaved Changes" prompt.
- **Root cause:** The Sound Editor emits `modified()` for actions that are immediately persisted to disk (e.g. .s file import, song rename). On Ctrl+S, the save logic checked each sub-component for unsaved changes — if none individually reported changes (because work was already on disk), `setWindowModified(False)` never ran. The dirty flag stayed stuck.
- **Fix:** `setWindowModified(False)` now always runs after a save attempt, regardless of whether any sub-component reported saving.

### BUG: "Unsaved Changes" dialog on close/refresh even with no user edits
- **Status:** FIXED (2026-04-09)
- **File:** `ui/unified_mainwindow.py`, `eventide/ui/event_editor_tab.py`
- **Evidence:** Open PorySuite-Z, make zero edits, try to close or F5 refresh — "You have unsaved changes" appears. Debug log showed `PorySuite (isWindowModified)` as the dirty source on startup, and `EVENTide (isWindowModified)` after saving.
- **Root cause (startup):** PorySuite's deferred widget population via `QTimer.singleShot(0, _deferred_load_items)` fires after the dirty-flag override is installed, populating widgets that emit change signals → `setWindowModified(True)`.
- **Root cause (after save):** The save pipeline calls `notify_trainers_changed()` etc. → `_refresh_eventide_constants()` → `_refresh_gfx_combo()` which clears and repopulates `gfx_combo`. This fires `currentIndexChanged` → `_mark_dirty()` → `data_changed.emit()` → EVENTide `setWindowModified(True)`. The save flow only cleared EVENTide's dirty flag inside a `if map_dir and map_data` block — if no map was loaded, the flag stayed stuck.
- **Fix:** Three changes: (1) Deferred dirty clear 200ms after load/refresh. (2) `_refresh_gfx_combo()` now blocks signals on gfx_combo during repopulation. (3) EVENTide's `setWindowModified(False)` now runs after save regardless of whether a map was loaded.
- **DO NOT CHANGE:** The `blockSignals(True/False)` around gfx_combo in `_refresh_gfx_combo()`, the deferred dirty clears in `load_data()`/`_refresh_project()`, or the unconditional EVENTide dirty clear after save. Removing any of these re-enables the phantom dirty flag.

### BUG: Importing a song wipes ALL other tool-edited songs on next build
- **Status:** FIXED (2026-04-09)
- **Files:** `core/sound/midi_importer.py`, `ui/unified_mainwindow.py`
- **Evidence:** mus_evil.s wiped to 0-track skeleton (28 lines, NumTrks=0) by mid2agb after importing another song. Happened repeatedly — every time a song was imported, other songs got destroyed on the next build.
- **Root cause:** `register_song()` in `midi_importer.py` appends a line to `midi.cfg` using `open(..., 'a')`. This updates midi.cfg's modification time to NOW. But it did NOT touch any existing .s files afterward. Since midi.cfg is a Makefile dependency for EVERY .s file (`%.s: %.mid midi.cfg`), ALL .s files are now "stale" relative to midi.cfg. The next `make` re-runs mid2agb on every song. Songs with placeholder .mid files (26-byte empties from .s imports) get overwritten with 0-track garbage. The newly imported song survives (its .s was touched separately), but every other tool-edited song is destroyed.
- **Fix:** `register_song()` now touches ALL .s files in the midi directory after appending to midi.cfg, matching what `write_midi_cfg()` already does. Also added pre-build defense: `_on_make()` touches all .s files before delegating to `make`.
- **DO NOT CHANGE:** The .s-touching loop at the end of `register_song()` in `midi_importer.py`. Removing it means every song import silently makes all other .s files stale, and the next build wipes them. Also do not remove the pre-build `.s` touching loop in `_on_make()` — it's a defense-in-depth layer.

### BUG: Git pull / F5 refresh doesn't reload Sound Editor data
- **Status:** FIXED (2026-04-08)
- **File:** `ui/unified_mainwindow.py`
- **Fix:** `_refresh_project()` and `_after_refresh` (git pull callback) now explicitly call `_sound_editor.load_project()` and `_credits_editor.load_project()`.

---

## Piano Roll — UI

### BUG: Single click on empty space places accidental notes
- **Status:** FIXED (2026-04-08)
- **File:** `ui/piano_roll_widget.py`
- **Evidence:** User accidentally clicked canvas, placed a note, then hit backspace — lost work.
- **Fix:** Single-click on empty space now deselects. Double-click places a note. Double-click existing note opens properties.

### BUG: Sidebar track click hides all other tracks
- **Status:** FIXED (2026-04-08)
- **File:** `ui/piano_roll_window.py`
- **Evidence:** Clicking a track name in sidebar changed the Track dropdown filter, hiding all other tracks.
- **Fix:** Sidebar click now sets active track (for placing new notes) without changing visibility. Status bar shows "Active track: N (new notes go here)".

### BUG: .s file import blocks when file already exists
- **Status:** FIXED (2026-04-08)
- **File:** `ui/dialogs/s_file_import_dialog.py`
- **Fix:** File existence check now respects `overwrite` flag. Shows amber "will reimport/overwrite" instead of blocking red error. Existing constants pass validation.

### BUG: Song Structure "Loop Back" dropdown shows PATT subroutine labels
- **Status:** FIXED (2026-04-08)
- **File:** `ui/piano_roll_structure.py`
- **Evidence:** User clicked "+ Loop Back" and the dropdown showed dozens of internal labels like `mus_evil_3_007`, `mus_evil_4_008` etc. Only one real section ("intro") existed.
- **Root cause:** `load_from_song()` collected LABEL commands from ALL tracks without filtering. Mid2agb-generated files have PATT subroutine labels (one per pattern per track) — these are internal targets, not user-visible sections.
- **Fix:** Before collecting labels, scan all tracks for PATT commands and build a `patt_targets` set. Labels that are PATT targets are excluded from the section list. Only genuine section labels (user-created or loop markers) appear in the dropdown.
- **DO NOT CHANGE:** The `patt_targets` filtering in `load_from_song()`. Removing it floods the dropdown with hundreds of internal labels.

### BUG: Loop back tick drifts on save/reload (192 → 190)
- **Status:** FIXED (2026-04-08)
- **File:** `core/sound/song_writer.py`
- **Evidence:** User sets loop section "intro" at tick 192, saves, reopens piano roll — Song Structure shows tick 190 instead of 192.
- **Root cause:** In the emit loop of `notes_to_track_commands`, LABEL commands were emitted with `continue` BEFORE the gap WAIT was calculated. If a TIE's EOT at tick 190 advanced `current_tick` to 190, the label at tick 192 was emitted immediately (current_tick still 190), then the W02 gap to the next event came AFTER the label. On reload, the parser sees the label before W02, so it gets `tick=190` instead of 192.
- **Fix:** Moved the WAIT gap calculation ABOVE the LABEL check. Now the gap to the label's tick is emitted as WAITs BEFORE the label line, so the parser reads the correct tick position.
- **DO NOT CHANGE:** The order of gap WAIT emission vs LABEL emission in the emit loop of `notes_to_track_commands`. The WAIT MUST come before the label, not after.

### BUG: Loop label name reverts to auto-generated `_B1` on save/reload
- **Status:** FIXED (2026-04-08)
- **File:** `ui/piano_roll_window.py`, `ui/piano_roll_structure.py`
- **Evidence:** User names section "intro", saves, reopens — label shows as `mus_evil_1_B1` instead of "intro".
- **Root cause:** `_sync_notes_to_song()` used `track.loop_label` (from the parsed file, empty for fresh imports) and fell back to `{track.label}_B1`. It never consulted the Song Structure panel for the user's chosen label name.
- **Fix:** Added `get_loop_label()` to Song Structure panel. Save pipeline now checks structure panel first: `structure_label or track.loop_label or fallback_B1`.
- **DO NOT CHANGE:** The priority order in `_sync_notes_to_song`: structure panel label > parsed file label > auto-generated `_B1`.

---

## Piano Roll — Scroll/Zoom

### BUG: Scroll wheel scrolls vertically instead of horizontally in piano roll
- **Status:** FIXED (2026-04-08)
- **File:** `ui/piano_roll_widget.py`
- **Evidence:** Plain scroll wheel moved the view up/down through pitches. In a horizontally-oriented piano roll, scroll should move through the timeline (left/right).
- **Root cause:** The `QScrollArea` has a built-in wheel handler that scrolls vertically. The parent `PianoRollWidget.wheelEvent()` override never fired because the scroll area intercepted wheel events at the viewport level first.
- **Fix:** Installed an `eventFilter` on both the scroll area and its viewport. All wheel events are intercepted: plain scroll = horizontal, Shift+scroll = vertical, Ctrl+scroll = horizontal zoom, Ctrl+Shift+scroll = vertical zoom.
- **DO NOT CHANGE:** The `eventFilter` method on `PianoRollWidget` or the `installEventFilter` calls. Removing them reverts scroll to vertical-only.

### FEATURE: Middle-click drag to zoom horizontally
- **Status:** ADDED (2026-04-08)
- **File:** `ui/piano_roll_widget.py`
- **Details:** Hold middle mouse button and drag up to zoom in, down to zoom out. Zoom is anchored to the tick under the cursor so the view zooms into that spot. Uses `_Mode.ZOOM_H` state, `zoom_changed` signal, and viewport-relative scroll anchoring in parent.

### BUG: MIDI import "No Loop" mode still produces .s file with PATT/PEND/labels
- **Status:** FIXED (2026-04-08)
- **File:** `ui/dialogs/midi_import_dialog.py`, `_postprocess_structure()`
- **Evidence:** User imports MIDI with "No Loop" but the resulting .s file still has PATT/PEND subroutine calls and internal labels like `mus_name_3_007` from mid2agb. Opening in piano roll shows junk in Song Structure.
- **Root cause:** The old `no_loop` post-processor only stripped GOTO commands via text matching. It left all PATT/PEND/LABEL commands intact. Also had a bug: line matching `.word` with `_grp` was targeting voicegroup lines, not GOTO target lines.
- **Fix:** Rewrote `no_loop` mode to use the proper parse→flatten→rewrite pipeline. The .s file is parsed, all tracks are flattened (expanding PATT subroutines), control events are deduped, and the file is rewritten as clean linear tracks with only notes, waits, and controls — no PATT, PEND, GOTO, or internal labels.
- **DO NOT CHANGE:** The parse→flatten→rewrite approach in `_postprocess_structure` for `no_loop` mode. The old text-based stripping was incomplete and buggy.

### BUG: MIDI import fails with "failed to read event text" on some MIDIs
- **Status:** FIXED (2026-04-08)
- **File:** `core/sound/midi_importer.py`
- **Evidence:** castle.mid from `C:\GBA\Music\random midis\` failed with "mid2agb failed: error: failed to read event text".
- **Root cause:** Two issues: (1) mid2agb can't handle most MIDI meta events (text, lyrics, markers, track names, etc.). (2) mid2agb can't handle Type 0 MIDIs (single track with all channels merged) — it expects Type 1 (one track per channel).
- **Fix:** Before calling mid2agb, the MIDI is loaded with `mido` and cleaned in two steps: (1) Strip all meta events except `set_tempo`, `time_signature`, and `end_of_track` (allowlist approach — `_KEEP_META` set). (2) If the MIDI is Type 0, convert to Type 1 by splitting channel messages into separate tracks (`_split_type0_to_type1()`). mid2agb then receives a clean, properly formatted MIDI.
- **DO NOT CHANGE:** The `_KEEP_META` allowlist and the `_split_type0_to_type1()` function in `midi_importer.py`. Removing either re-enables import failures on real-world MIDIs.
- **NOTE:** castle.mid and castle2.mid still fail even with these fixes — mid2agb is fundamentally incompatible with some MIDIs. Marked as "known incompatible."

### BUG: MIDI import defaults to "Automatic" mode — keeps mid2agb PATT labels
- **Status:** FIXED (2026-04-08)
- **File:** `ui/dialogs/midi_import_dialog.py`
- **Evidence:** After importing mus_battletest_final, the Song Structure panel showed PATT sections and "Play Pattern" entries. User clicked through without changing the default mode.
- **Root cause:** The import dialog defaulted to "Automatic" mode which keeps mid2agb's internal PATT/PEND subroutine structure. Users see "nothing" on the structure page (no sections listed) and click through — the labels stay. "Automatic" is the wrong default because mid2agb's PATT optimization is an internal detail, not useful musical structure.
- **Fix:** Changed the default import mode from "Automatic" to "No Loop (clean)". The structure page now auto-applies the No Loop preset when first visited, so even if the user clicks straight through, the post-processor strips all PATT/PEND/GOTO labels. Users who want mid2agb's raw output can still click the "Automatic" button.
- **DO NOT CHANGE:** The default `_structure_mode = 'no_loop'` and the `_populate_structure_page` auto-preset logic. Changing back to 'automatic' re-enables the stale PATT label problem.

### BUG: GM voicegroup missing drum/percussion instruments
- **Status:** FIXED (2026-04-08)
- **File:** `core/sound/gm_voicegroup.py`
- **Evidence:** "Generate GM" voicegroup had no kick, snare, hi-hat, triangle, etc. The instruments panel showed many samples not in the generated voicegroup.
- **Root cause:** Two issues: (1) The `_SAMPLE_TO_GM` mapping table didn't include any drum kit samples (kick, snare, cymbals, toms, etc. from voicegroup002's drumkit). (2) DirectSound samples that lost their slot in the GM mapping (competing for the same percussion slot) were silently dropped — only non-DirectSound instruments got placed in remaining empty slots.
- **Fix:** Added all drum kit samples to the mapping table (kick→117, snare→118, triangle→114, clap→119, etc.). Changed the "remaining instruments" pass to include unplaced DirectSound samples first (before square/noise variants). Overflow past 128 slots only includes DirectSound and programmable wave instruments to avoid square/noise bloat.
- **DO NOT CHANGE:** The `_SAMPLE_TO_GM` drum entries and the `unplaced_ds` logic in `generate_gm_voicegroup()`.

---

## Song Management

### BUG: Build (make) overwrites tool-edited .s files with empty skeletons from placeholder .mid
- **Status:** FIXED (2026-04-08)
- **Files:** `core/sound/song_writer.py`, `ui/dialogs/midi_import_dialog.py`, `ui/dialogs/s_file_import_dialog.py`, `ui/sound_editor_tab.py`
- **Evidence:** mus_evil.s was wiped to a 0-track empty skeleton after running `make`. The file had valid music data from a piano roll save, but the build replaced it with garbage generated from a 26-byte placeholder .mid.
- **Root cause:** pokefirered's Makefile has a `%.s: %.mid midi.cfg` dependency rule. If EITHER the .mid OR midi.cfg is newer than the .s, `make` runs mid2agb which OVERWRITES the .s. Two triggers: (1) **midi.cfg dependency** — midi.cfg is a prerequisite for EVERY .s file. ANY write to midi.cfg (song delete, rename, import registration, orphan cleanup) makes ALL .s files out of date. The next `make` re-runs mid2agb on every song. For songs with placeholder .mid files (26-byte empties created for .s-imported songs), mid2agb produces 0-track garbage. (2) **Placeholder .mid newer than .s** — if a placeholder .mid was created after the last piano roll save, or both had the same filesystem-second timestamp.
- **Fix:** Two layers of protection: (1) **midi.cfg writes touch all .s files** — `write_midi_cfg()` now iterates every .s in the midi directory and touches it with `os.utime()` after writing midi.cfg, so all .s files are newer than midi.cfg. (2) **Every .s write backdates the .mid** — `save_song_file()`, `_postprocess_structure()`, `_SImportWorker`, and `_replace_song()` all call `os.utime(mid_path, (s_mtime - 2, s_mtime - 2))` after writing .s, guaranteeing make sees .s as newer than .mid.
- **DO NOT CHANGE:** The `os.utime()` calls in `write_midi_cfg()` (touches all .s) and the .mid backdating in the four .s-writing paths. Removing any of them allows `make` to overwrite tool-edited .s files with garbage from placeholder .mid files. This silently destroys user's music data.

### BUG: Orphaned song registrations left behind after failed imports or inconsistent deletes
- **Status:** FIXED (2026-04-08)
- **Files:** `core/sound/song_table_manager.py`, `core/sound/midi_importer.py`, `ui/sound_editor_tab.py`
- **Evidence:** After deleting songs (mus_kakariko, mus_test, mus_battletest_final, mus_fairy_fountain, mus_fairy_fountain_2), their entries remained in `song_table.inc`, `songs.h`, and `midi.cfg`. Build failed with "undefined reference" errors because the .s files were gone but the registrations still existed.
- **Root cause:** Two issues: (1) When a MIDI import fails (mid2agb error), the cleanup code tried to delete the original filename (`battletest_FINAL.mid`) instead of the label-based name (`mus_battletest_final.mid`) that was actually saved. The orphaned .mid broke the build, and partial import state could leave registrations without .s files. (2) No mechanism existed to detect or clean up orphaned registrations — songs registered in config files but with no .s file on disk.
- **Fix:** (1) Fixed `import_midi()` cleanup path to use `label + ".mid"` matching what `run_mid2agb()` actually writes. (2) Added `cleanup_orphaned_songs()` function that does TWO things: scans for MUS_* entries with missing .s files and removes them from all three config files, AND scans for stray .mid files whose stem doesn't match any registered song label and deletes them. (3) Sound editor runs this cleanup automatically on project load and F5 refresh.
- **DO NOT CHANGE:** The `cleanup_orphaned_songs()` auto-run in `sound_editor_tab.py`'s `load_project()`. Removing it means orphaned registrations and stray MIDIs silently accumulate and break builds. Also do not change the cleanup filename in `import_midi()` back to `os.path.basename(midi_path)` — must use `label + ".mid"`. Do not remove the stray .mid scanner — the Makefile wildcard picks up ALL .mid files in the directory and tries to build them.

---

## Instruments Tab

### BUG: Sample loop controls show unchecked/zero even when .bin has loop enabled
- **Status:** FIXED (2026-04-08)
- **Files:** `ui/instruments_tab.py`, `ui/sound_editor_tab.py`
- **Evidence:** Sc88Pro String Ensemble 60 has status=0x4000 and loop_start=4301 in its .bin file, but the Loop checkbox showed unchecked and Loop Point showed 0.
- **Root cause:** Sample data (`_sample_data`) was lazy-loaded — only loaded on first playback attempt via `_ensure_samples_loaded()`. When the user clicked an instrument in the list, `_sample_data` was still `None`, so the loop control population code (`if self._sample_data:`) was skipped entirely. The controls defaulted to unchecked/zero.
- **Fix:** Two changes: (1) Connected `_tab_widget.currentChanged` to `_on_tab_changed()` which calls `_ensure_samples_loaded()` when switching to Instruments or Voicegroups tabs. Samples now load when the tab is first visited. (2) `set_sample_data()` in InstrumentsTab now re-displays the currently selected instrument after receiving data, so if you selected an instrument before samples loaded, the controls refresh.
- **DO NOT CHANGE:** The `_on_tab_changed()` connection in `sound_editor_tab.py` or the display refresh in `set_sample_data()`. Removing either causes loop controls to show empty for the first instrument the user clicks.

### BUG: WAV import skips rate/size options for samples under 13379 Hz
- **Status:** FIXED (2026-04-08)
- **File:** `ui/instruments_tab.py`
- **Evidence:** Importing an 11025 Hz accordion WAV (30 KB) showed a simple Yes/No dialog with no downsample options. 30 KB is huge for a GBA instrument — most are 1–5 KB.
- **Root cause:** Rate selection dialog only appeared when `wav_rate > 13379`. Lower-rate WAVs skipped straight to a basic confirm. But long samples at any rate can still be wasteful.
- **Fix:** Rate/size dialog now ALWAYS appears, offering downsample tiers (22050, 13379, 8000 Hz) for any rate above each tier. Shows a size warning when the sample exceeds 10 KB.
- **DO NOT CHANGE:** The always-show-options behavior. The old `if wav_rate > GBA_MAX_TYPICAL` gate hid size problems from the user.

### BUG: Imported WAV sample doesn't appear in instrument list
- **Status:** FIXED (2026-04-08)
- **File:** `ui/instruments_tab.py`
- **Evidence:** User imported a WAV, got success message, but instrument list looked the same. The sample was created as a .bin but not assigned to any voicegroup slot. Import buttons were also hidden inside the details panel of another instrument — no way to import without first selecting something.
- **Root cause:** `_on_import_sample()` created the .bin file but never assigned it to an instrument slot. Import buttons were only visible in the right-panel instrument details, requiring an existing instrument to be selected first.
- **Fix:** (1) Moved "Import WAV" and "Import Instrument" buttons to the left panel below the instrument list — always visible when a project is loaded. (2) Added `_assign_sample_to_slot()` helper: if a DirectSound instrument is selected, assigns to that slot. If not, asks which voicegroup to add it to and replaces a filler slot with the new instrument. (3) Same flow for .psinst import.
- **DO NOT CHANGE:** The `_assign_sample_to_slot()` method or the left-panel button placement. Without these, imported samples become orphan .bin files with no visible instrument.

### BUG: QCheckBox not imported in instruments_tab.py
- **Status:** FIXED (2026-04-08)
- **File:** `ui/instruments_tab.py`
- **Root cause:** Loop controls used `QCheckBox("Loop")` but `QCheckBox` wasn't in the PyQt6.QtWidgets import list. Would crash on any code path that creates the UI.
- **Fix:** Added `QCheckBox` to the import statement.

### BUG: Instrument edits in Instruments tab not saved — "unsaved changes" on refresh
- **Status:** FIXED (2026-04-08)
- **Files:** `ui/instruments_tab.py`, `ui/voicegroups_tab.py`, `ui/sound_editor_tab.py`
- **Evidence:** User edits instrument ADSR/sample/pan in Instruments tab, saves, then hits Refresh — gets "unsaved changes" prompt even though they just saved. Closing and reopening was the only way to see changes.
- **Root cause:** The Instruments tab modifies instrument objects in the shared `_voicegroup_data`, but never told the Voicegroups tab which voicegroups were dirty. So `vg_tab.has_unsaved_changes()` returned False and `save_to_disk()` was never called for voicegroup changes made from the Instruments tab.
- **Fix:** Added `mark_voicegroups_dirty(names)` to VoicegroupsTab. Instruments tab now holds a reference (`_voicegroups_tab_ref`) and calls it from `_apply_to_all_copies()` with the list of affected voicegroup names. The save pipeline now picks up these changes.
- **DO NOT CHANGE:** The `_voicegroups_tab_ref` cross-link in `sound_editor_tab.py` or the `mark_voicegroups_dirty()` call in `_apply_to_all_copies()`. Removing either silently drops instrument edits on save.

---

## Dirty Flag Gaps (Phase 8B)

### BUG: _on_child_modified() doesn't respect suppress flags
- **Status:** FIXED (2026-04-09)
- **File:** `ui/unified_mainwindow.py`
- **Root cause:** `_on_child_modified()` called `self.setWindowModified(True)` unconditionally. Unlike the PorySuite dirty override wrapper (which checks `_suppress_dirty` and `_ps_suppress_dirty`), this callback had no guard. Fired during load/navigation operations and re-dirtied the window.
- **Fix:** Added checks for `self._suppress_dirty` and `porysuite_main._ps_suppress_dirty` before setting the dirty flag.
- **DO NOT CHANGE:** The suppress flag checks in `_on_child_modified()`. Removing them re-enables phantom dirty marking during navigation.

### BUG: Most editable widgets don't mark dirty on change
- **Status:** FIXED (2026-04-09)
- **File:** `ui/mainwindow.py`
- **Root cause:** 21+ widgets (base stats, EVs, held items, types, egg groups, catch rate, friendship, abilities, starters, etc.) had no change signal connected to dirty tracking. Each new widget required manual wiring — any missed widget was invisible to save tracking.
- **Fix:** Structural fix: a single loop iterates ALL widgets defined in `ui_mainwindow.py` and connects the appropriate change signal (`valueChanged` for spinboxes/sliders, `currentIndexChanged` for combo boxes, `textChanged` for line edits and text edits) to `setWindowModified(True)`. No more per-widget manual wiring needed.
- **DO NOT CHANGE:** The structural dirty-marking loop in `__init__` that iterates `dir(self.ui)`. Removing it breaks dirty tracking for the entire editor.

### BUG: Instrument loop controls don't mark voicegroups dirty
- **Status:** FIXED (2026-04-09)
- **File:** `ui/instruments_tab.py`
- **Root cause:** Loop handlers wrote to .bin file and emitted `modified` but never called `mark_voicegroups_dirty()`.
- **Fix:** Added `_mark_loop_dirty()` helper that finds all voicegroups using the current instrument and marks them dirty. All three loop handlers call it.
- **DO NOT CHANGE:** The `_mark_loop_dirty()` calls in `_on_loop_toggled`, `_on_loop_seconds_changed`, `_on_loop_waveform_dragged`. Removing them means loop changes won't be saved.

### BUG: Starters tab ability combo boxes non-functional
- **Status:** FIXED (2026-04-09)
- **Files:** `ui/mainwindow.py`
- **Root cause:** Ability combo boxes existed in UI but were disabled, never populated, never saved.
- **Fix:** Enabled all three combo boxes, populated with "Default (random)" / "Ability Slot 1" / "Ability Slot 2" options, loaded saved `ability_num` values, added to save pipeline, connected `currentIndexChanged` for dirty marking.

### BUG: Abilities editor doesn't populate effects for existing abilities
- **Status:** FIXED (2026-04-09)
- **Files:** `ui/mainwindow.py`, `core/ability_effect_templates.py`, `ui/abilities_tab_widget.py`
- **Root cause (layer 1):** `load_abilities_editor()` called `self.source_data.docker_util.repo_root()` but `source_data` (PokemonDataManager) doesn't have that attribute. The call threw AttributeError, caught silently, and `root` stayed as empty string. Renamed to `self.local_util.repo_root()`.
- **Root cause (layer 2):** Even after the rename, `self.local_util` was initialized at line ~2790 but `load_abilities_editor()` ran at line ~2561 — about 230 lines earlier. So `self.local_util` was `None` at call time, still causing AttributeError.
- **Root cause (layer 3):** Detection only scanned for `case ABILITY_XXX:` blocks in battle_util.c. Abilities like Sand Veil, Sturdy, Battle Armor, Huge Power are implemented as inline `if` checks in battle_script_commands.c and pokemon.c — never had case blocks, so detection always returned None for them.
- **Fix:** (1) Renamed `self.source_data.docker_util` → `self.local_util` at all 3 call sites. (2) Moved `self.local_util = LocalUtil(self.project_info)` to right after `self.project_info` is set in `load_data()`. (3) Added `_get_nearby_block()` helper and expanded `detect_battle_effect()` to scan battle_script_commands.c and pokemon.c for inline patterns. 14 new templates added. (4) Added "Known Effect" fallback labels using `_BATTLE_CATEGORIES` / `_FIELD_EFFECTS` so abilities always show what they do, even if no editable template matches. (5) **Further expanded 2026-04-09**: Added 21 more templates bringing total to 52, achieving 74/74 detection of all vanilla abilities. Unified pokemon.c detection uses per-line tight context to avoid bleed between adjacent ability checks. Added name-based fallbacks for abilities implemented in assembly scripts.
- **DO NOT CHANGE:** The early placement of `self.local_util = LocalUtil(self.project_info)` in `load_data()`. Moving it later re-breaks all ability effect detection. Also do not remove the inline detection patterns in `detect_battle_effect()` — they cover abilities that have no case blocks. Do not change the detection ORDER in the unified pokemon.c section (Plus/Minus → Hustle → Marvel Scale → Guts) — reordering causes false positives due to adjacent code.

### BUG: Abilities editor — audit findings (7 bugs + detection false positives)
- **Status:** FIXED (2026-04-09)
- **Files:** `core/ability_effect_templates.py`, `ui/abilities_tab_widget.py`, `ui/mainwindow.py`
- **Findings and fixes:**
  1. **`intimidatedMon` typo** — Generated C code used `intimitatedMon` (missing 'd'). Would not compile. Fixed all 4 occurrences.
  2. **Trick Room / Tailwind code references non-existent Gen 4+ constants** — `STATUS_FIELD_TRICK_ROOM`, `gFieldStatuses`, `gFieldTimers`, `SIDE_STATUS_TAILWIND` don't exist in pokefirered (Gen 3). Generated code now documents exactly what struct fields/constants/scripts must be added, using `gBattleStruct->trickRoomTimer` and `gSideTimers[side].tailwindTimer` which the user needs to add to battle.h.
  3. **`stat_double` template: incomplete stat→variable mapping** — Only mapped STAT_ATK → "attack", defaulting all others to "spAttack". Now correctly maps all 5 stats (attack, defense, speed, spAttack, spDefense).
  4. **`intimidate` template ignores stat parameter** — Code always generated STAT_ATK logic even when user picked a different stat. Now uses the selected stat and notes that a custom battle script is needed for non-ATK stats.
  5. **Save pipeline: parameter changes not detected** — Condition `if new_btid != cur_btid or new_btid:` would miss parameter-only changes. Fixed to compare both template ID and params.
  6. **New abilities not synced to JSON** — `save_abilities_editor()` only called `set_ability_data()` which skips new entries. Added `add_ability()` calls for new abilities and deletion handling for removed ones.
  7. **`type_change_boost` damage applied too early** — `gBattleMoveDamage` isn't calculated yet when `dynamicMoveType` is set. Split into two locations: type change in `battle_script_commands.c` (before Cmd_typecalc), power boost in `pokemon.c` (CalculateBaseDamage).
- **DO NOT CHANGE:** The `intimidatedMon` spelling — it matches pokefirered source exactly. Do not "fix" it to `intimitatedMon`.

### BUG: Abilities editor — second audit (6 codegen bugs + 1 detection fix)
- **Status:** FIXED (2026-04-13)
- **Files:** `core/ability_effect_templates.py`
- **Findings and fixes:**
  1. **Cute Charm codegen produced confusion instead of infatuation** — Detection returned `MOVE_EFFECT_CONFUSION` for Cute Charm, but Cute Charm causes infatuation via `STATUS2_INFATUATED_WITH()`. Code would generate confusion effect on contact instead of the correct infatuation. Fixed: detection returns special `"INFATUATION"` marker, codegen branches to produce the correct `STATUS2_INFATUATED_WITH(gBattlerTarget)` code matching pokefirered source exactly.
  2. **`serene_grace` wrote to ROM data** — Generated `gBattleMoves[gCurrentMove].secondaryEffectChance *= 2` which writes to const ROM data. This would crash or silently fail on GBA. Fixed: generates a local `effectChance` variable and documents where to use it.
  3. **`block_specific_stat` used non-existent `STAT_CHANGE_IS_NEGATIVE`** — This constant doesn't exist in pokefirered. Generated code would not compile. Fixed: rewrote to match pokefirered's actual `ChangeStatBuffs()` pattern with `!certain && statId == STAT_xxx` and `STAT_CHANGE_ALLOW_PTR`. Also changed target file from `battle_util.c` to `battle_script_commands.c` (where `ChangeStatBuffs` actually lives).
  4. **`weather_speed` targeted wrong file** — Generated code went to `battle_util.c` but weather speed doubling (Swift Swim, Chlorophyll) is in `battle_main.c` `GetWhoStrikesFirst()`. Fixed target file.
  5. **`pressure` targeted wrong file** — Generated code went to `battle_main.c` but Pressure is handled by `PressurePPLose()` in `battle_util.c`. Fixed target file.
  6. **Keen Eye / Hyper Cutter detected as `block_stat_reduction` instead of `block_specific_stat`** — The `block_stat_reduction` pattern (which checks for `STAT_CHANGE_WORKED`) fired first because both abilities appear in `battle_script_commands.c`. Fixed: added specific `statId == STAT_xxx` check BEFORE the generic `block_stat_reduction` check in the bsc detection section.
- **DO NOT CHANGE:** The detection order in the bsc section: `block_specific_stat` (with `statId ==`) must come BEFORE `block_stat_reduction`. Reordering causes Keen Eye/Hyper Cutter to be detected as generic Clear Body-style abilities instead of stat-specific blockers.

### FEATURE: Trainers tab — Add to Rematch Table
- **Status:** FIXED (2026-04-09)
- **File:** `ui/trainers_tab_widget.py`
- **Fix:** Added "Add to VS Seeker Rematch Table" button on the Party tab, visible when a trainer is NOT in the rematch table. Auto-detects the trainer's map by searching `data/maps/*/scripts.inc` for the trainer constant — no user input needed. Creates a new entry in `sRematches[]` with the detected map and empty rematch tiers, writes to vs_seeker.c, reloads the rematch data.

---

## Pokédex Wild Encounters (Phase 9)

### BUG: _camel_to_spaced() broke floor designations and consecutive uppercase
- **Status:** FIXED (2026-04-13)
- **File:** `core/encounter_data.py`, `_camel_to_spaced()` function
- **Evidence:** "SSAnne_B1F" → "S S Anne B1 F" (wrong), should be "SS Anne B1F"
- **Root cause:** Old algorithm inserted a space before every uppercase letter that didn't follow a space, breaking acronyms (SS) and floor designations (1F, B1F, B2F)
- **Fix:** Replaced with regex-based approach: `([a-z])([A-Z])` for CamelCase splits, `([A-Z]+)([A-Z][a-z])` for acronym→word boundaries. Preserves B1F, SS, etc.
- **DO NOT CHANGE:** The regex patterns in `_camel_to_spaced()`. Reverting to the character-by-character approach re-breaks floor and acronym handling.
- **Note:** This function is only the FALLBACK path (custom maps without region_map_section entries). Vanilla pokefirered maps resolve through region_map_sections.json.

### BUG: slot_count doubled by FR/LG variant merging
- **Status:** FIXED (2026-04-13)
- **File:** `core/encounter_data.py`, `_merge_records()` function
- **Evidence:** Pidgey Route 1 showed slot_count=12 (should be 6). FR and LG each had 6 Pidgey slots, but `+=` summed them to 12.
- **Root cause:** Single-phase merge by `(location, method)` used `slot_count += r.slot_count`, which summed slots across all encounter tables (including FR/LG variants). For multi-floor dungeons, it also summed across floors.
- **Fix:** Two-phase merge. Phase 1: group by `(base_label, method)` and SUM slots (counts within a single encounter table). Phase 2: group by `(location, method)` and take MAX slot_count (collapses floors/versions without inflating). Added `_table_id` field to EncounterRecord for Phase 1 discrimination.
- **DO NOT CHANGE:** The two-phase merge structure or the `_table_id` field. Reverting to single-phase `+=` re-doubles slot counts. Reverting to single-phase `max` always returns 1.

---

## Tilemap Editor (Phase 10A)

### BUG: Tilemap palette colors wrong (black stripes / all-black canvas)
- **Status:** FIXED (2026-04-13)
- **File:** `core/tilemap_data.py`, `ui/tilemap_editor_tab.py`
- **Evidence:** Battle backgrounds (terrain.bin etc.) rendered entirely black. naming_screen/background.bin had black stripes. Root cause was two-fold: (1) `ensure_slots(16)` filled unloaded palette slots with all-black colors. (2) `_recolor_tile` then used those all-black palettes for tiles referencing slots 2, 3, 15, etc., turning everything black.
- **Root cause:** Most GBA 4bpp tile sheets have only ONE palette (16 colors) in their PNG. But the tilemap references multiple palette slots (0, 2, 3 for terrain; 0, 15 for naming screen) because the game loads the same palette into different VRAM slots at runtime. The editor was treating missing slots as "black" instead of falling back to the PNG's single palette.
- **Fix:** `PaletteSet` now tracks `_loaded_slots` — which slots have real data vs placeholders. `_recolor_tile` falls back to palette 0 when the requested slot has no real data. `ensure_slots()` no longer marks expanded slots as loaded. Manual loading via `set_palette_at()` properly marks the slot as loaded.
- **DO NOT REMOVE:** The `_loaded_slots` tracking and the fallback logic in `_recolor_tile`. Without these, any tilemap using palette indices > 0 with a single-palette PNG will render as black.

### BUG: Width spinner breaks tilemap layout instead of re-wrapping rows
- **Status:** FIXED (2026-04-13)
- **File:** `ui/tilemap_editor_tab.py`
- **Evidence:** terrain.bin is 2048 entries (64x32). Changing W to 32 should give 32x64 (same data, different row stride). Instead, it kept H=32 and truncated to 1024 entries, corrupting the layout.
- **Root cause:** `_on_dimensions_changed` used `new_count = new_w * new_h` where `new_h` was the OLD height. It truncated/padded entries instead of re-wrapping the flat array.
- **Fix:** When W changes, H is auto-recalculated as `ceil(total_entries / new_w)`. When H changes, W is auto-recalculated similarly. The total entry count from the file never changes — it's just re-wrapped. The other spinner updates automatically.
- **DO NOT REMOVE:** The auto-recalculation logic. Without it, dimension changes silently destroy tilemap data.

### BUG: Tile sheet doesn't match tilemap (ok_button.png picked for keyboard_upper.bin)
- **Status:** FIXED (2026-04-13)
- **File:** `ui/tilemap_editor_tab.py`, `core/tilemap_data.py`
- **Evidence:** keyboard_upper.bin references tile indices 0-29 with palettes 0,1. ok_button.png only has ~15 tiles. The tilemap's tiles are loaded by the game at specific VRAM offsets set in C code.
- **Root cause:** No VRAM tile offset support. The editor assumed tile index 0 = first tile in the sheet, but the game loads different sheets at different VRAM offsets.
- **Fix:** Added "Tile Offset" spinbox. User can set the VRAM base offset for the current sheet. Tiles outside the sheet's range render as blank (dark background), so user can see which tiles belong to the current sheet and which come from elsewhere.
- **DO NOT REMOVE:** The `tile_offset` parameter in `render_tilemap()` and the offset spinbox in the toolbar.

### BUG: 8bpp tilemap rendering with wrong colors (title screen logo garbled)
- **Status:** FIXED (2026-04-13)
- **File:** `core/tilemap_data.py`, `ui/tilemap_editor_tab.py`, `ui/palette_utils.py`
- **Evidence:** game_title_logo.bin (title screen) rendered with garbled colors. The PNG has 256 colors (8bpp) across 13 sub-palettes, but the renderer was applying only a single 16-color sub-palette per tile (4bpp logic).
- **Root cause:** Three issues: (1) `_recolor_tile` always set a 16-entry color table, but 8bpp tiles have pixel values 0-255 that need a 256-entry table. Pixels with index >15 had no color and rendered black/wrong. (2) `read_jasc_pal` in palette_utils.py truncated all .pal files to 16 colors, destroying the 256-color palette data. (3) `from_pal_files` treated each .pal file as one palette slot, never splitting 256-entry files into sub-palettes.
- **Fix:** Added `is_8bpp` detection to `TileSheet` (checks color table size >16). Added `_recolor_tile_8bpp` that applies a flat 256-entry color table. `render_tilemap` detects 8bpp sheets and uses the flat table. `read_jasc_pal` now reads all colors (16 or 256). `from_pal_files` splits 256-color .pal files into 16-color sub-palettes. Palette spinner disabled in 8bpp mode (hardware ignores palette bits). Tile picker uses full 256-color table in 8bpp mode.
- **DO NOT REMOVE:** The `is_8bpp` detection, `_recolor_tile_8bpp`, `build_flat_color_table`, and the 256-color .pal file handling in `read_jasc_pal` / `from_pal_files`.

---

## Corrupted Test Files

### MUS_EVIL (`porysuite/pokefirered/sound/songs/midi/mus_evil.s`)
- Was corrupted AGAIN by piano roll save — control event explosion (PAN 18→1829, BEND 36→740, notes 64→1).
- Restored from MUS_TEST on 2026-04-08 by copying mus_test.s with label replacement (mus_test→mus_evil). Now identical music content, no loops. User will add loops manually to test.

### MUS_EVIL2 (`porysuite/pokefirered/sound/songs/midi/mus_evil2.s`)
- Track 6: BEND commands missing from loop body. Loop plays without pitch bending. Original MIDI has bends throughout.
- Users can add bends back via Note Properties dialog.

### MUS_TEST (`porysuite/pokefirered/sound/songs/midi/mus_test.s`)
- Piano-roll-corrupted version from before the fixes. All PAN events stacked after EOT with no timing. Can be reimported from MIDI.
