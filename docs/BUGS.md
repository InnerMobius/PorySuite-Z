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

### BUG: Piano roll save can create duplicate loop labels
- **Status:** FIXED (2026-04-07)
- **Fix:** Skip new loop labels when `has_orig_structure` is True in `notes_to_track_commands`.

### BUG: Piano roll save loses user's instrument/volume/pan edits
- **Status:** FIXED (2026-04-07)
- **Fix:** `notes_to_track_commands` now always uses caller's VOICE/VOL/PAN values instead of original file's tick-0 commands.

### BUG: Piano roll save destroys PAN interleaving (MUS_TEST corruption)
- **Status:** FIXED (2026-04-07)
- **Fix:** Timeline-based WAIT generation — notes no longer auto-emit WAITs for their duration. All WAITs come from tick gaps between timeline events.

---

## Piano Roll — Playback

### BUG: BEND state incorrectly reset on loop wrap
- **Status:** FIXED (2026-04-08)
- **Fix:** Removed `ts.bend = 0.0` reset in `realtime_sequencer.py` loop wrap. The real GBA M4A engine carries BEND state through GOTO loops.

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

## Corrupted Test Files

### MUS_EVIL (`porysuite/pokefirered/sound/songs/midi/mus_evil.s`)
- Was corrupted by previous piano roll save (broken PATT calls, dead notes after FINE).
- Re-saved after PATT stripping fix — now a clean linear file. Verified: 205 notes, correct instruments and volumes.

### MUS_EVIL2 (`porysuite/pokefirered/sound/songs/midi/mus_evil2.s`)
- Track 6: BEND commands missing from loop body. Loop plays without pitch bending. Original MIDI has bends throughout.
- Users can add bends back via Note Properties dialog.

### MUS_TEST (`porysuite/pokefirered/sound/songs/midi/mus_test.s`)
- Piano-roll-corrupted version from before the fixes. All PAN events stacked after EOT with no timing. Can be reimported from MIDI.
