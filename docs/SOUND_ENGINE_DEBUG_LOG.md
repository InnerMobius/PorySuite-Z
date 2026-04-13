# Sound Engine Debug Log

Tracks all findings, failed fixes, and confirmed fixes for the PorySuite sound renderer.
Purpose: prevent re-investigating solved problems and avoid regressions from bad fixes.

---

## Confirmed Fixes (working correctly)

### 1. MidiKeyToFreq pitch calculation (replaces naive 2^(semitone/12))
- **Problem**: DirectSound samples pitched incorrectly
- **Root cause**: GBA uses gScaleTable + gFreqTable lookup, not simple exponential
- **Fix**: `_midi_key_to_freq()` in audio_engine.py with exact GBA table data
- **Verified**: 0.00 semitone error across full MIDI range

### 2. BEND subtracts C_V (64)
- **Problem**: Lavender Town main instrument in wrong key (+6 semitones)
- **Root cause**: GBA's ply_bend (m4a_1.s line 998) does `subs r3, C_V` before storing. We stored raw value.
- **Fix**: `state.bend = cmd.value - 64` in track_renderer.py
- **Verified**: Lavender Town now in correct key

### 3. Double key-shift removed
- **Problem**: Notes shifted twice by key_shift
- **Root cause**: `state.key_shift` initialized to `song_key_shift`, then `midi_note = cmd.pitch + state.key_shift + song_key_shift` applied it again
- **Fix**: `midi_note = cmd.pitch + state.key_shift` only

### 4. CGB ADSR scale conversion
- **Problem**: Square waves nearly silent (6% volume)
- **Root cause**: CGB uses 0-7/0-15 ADSR but envelope generator expected 0-255. attack=0 meant slowest (4.3s), sustain=15 meant 6%
- **Fix**: `_cgb_to_ds_adsr()` conversion function

### 5. Double velocity removed
- **Problem**: Volume balance wrong — some instruments too quiet
- **Root cause**: Velocity applied in track_renderer (as effective_vel) AND in audio_engine (vel_scale)
- **Fix**: Pass raw velocity to audio_engine, apply only once

### 6. Fake master_volume multiplication removed
- **Problem**: Overall volume too quiet
- **Root cause**: Song's `mvl` constant already baked into VOL command values (written as `VOL, n*mvl/mxv`). Multiplying by master_volume/127 again was double-applying.
- **Fix**: Removed master_volume from track_renderer volume calculation

### 7. CGB volume reduction factors removed
- **Problem**: Square waves, prog waves, noise too quiet relative to DirectSound
- **Root cause**: Arbitrary multipliers (0.3, 0.25, 0.2) that don't exist on GBA
- **Fix**: Removed — GBA uses same volume pipeline for all types

### 8. Pan: constant-power -> GBA linear crossfade
- **Problem**: Volume relationship between instruments wrong at different pan positions
- **Root cause**: We used cos/sin, GBA uses linear `left=(127-pan)/255, right=(pan+128)/255`
- **Fix**: Updated apply_pan()

### 9. Per-note panning (was per-track)
- **Problem**: Mid-song PAN changes ignored
- **Root cause**: render_track returned mono, render_song applied first PAN only
- **Fix**: render_track now returns stereo with per-note panning

### 10. Running status bytes parsed correctly
- **Problem**: mus_gym missing notes (VOL stayed at 0)
- **Root cause**: Bare integers on `.byte` lines dropped as CONTINUATION instead of repeating last control command
- **Fix**: `last_control_cmd` tracking in song_parser.py

### 11. XCMD continuation lines
- **Problem**: ~85 songs had phantom KEYSH commands shifting notes up 8-16 semitones
- **Root cause**: Second `.byte` line of XCMD (e.g. `.byte xIECL, 8`) fell through to running-status handler. xIECL=9 emitted as last_control_cmd (usually KEYSH).
- **Fix**: Added XCMD sub-command token recognition before running-status handler

### 12. keysplit_all overflow into next voicegroup
- **Problem**: mus_gym drum track silent
- **Root cause**: GBA stores voicegroups contiguously. keysplit_all note 40 in 29-slot voicegroup001 overflows to voicegroup002[11]. Parser returned None.
- **Fix**: `get_instrument_overflow()` chains into subsequent voicegroups by numeric order

---

## Failed/Reverted Fixes

### 13. TONEDATA_TYPE_FIX (_alt) as blanket fixed-pitch — REGRESSION, REVERTED
- **Date**: 2026-04-06
- **Problem attempted to fix**: 406 _alt instruments (drums/percussion) assumed to need fixed pitch
- **What I did**: In render_instrument(), checked `type_byte & 0x08` and overrode midi_note to base_midi_key for ALL matching instruments
- **What went wrong**: Created a flat monotone "dun dun dun" sound in mus_gym and mus_encounter_gym_leader. CGB instruments that should pitch-shift were locked to one note.
- **Root cause of failure**: TONEDATA_TYPE_FIX does DIFFERENT things for different channel types:
  - **DirectSound**: Mixer uses fixed step (0x800000 = 1.0), ignoring computed frequency. THIS is true fixed pitch. Already handled by `no_resample` flag.
  - **CGB (square/wave/noise)**: Only applies minor frequency rounding in CgbSound() for anti-aliasing at certain DAC PWM rates (`& 0x7fc` or `& 0x7fe`). Pitch STILL varies normally with MIDI key. NOT fixed pitch.
  - **Noise (channel 4)**: CgbSound's FIX check uses `ch < 4`, so noise_alt has ZERO effect.
- **GBA source evidence**:
  - m4a_1.s SoundMainRAM line ~498: `tst r0, TONEDATA_TYPE_FIX` / `movne r8, 0x800000` — DirectSound only
  - m4a.c CgbSound line ~1187: `if (ch < 4 && (channels->type & TONEDATA_TYPE_FIX))` — frequency rounding only
  - MidiKeyToCgbFreq: does NOT receive or check type byte — always computes pitch from key
- **Fix**: Reverted. Removed the blanket `type_byte & 0x08` check entirely. CGB _alt instruments now pitch-shift normally (correct). DirectSound no_resample was already correct.

---

### 13. Piano roll BEND state reset on loop (2026-04-08)
- **Problem**: Pitch bend effects stopped working after the piano roll looped
- **Root cause**: `realtime_sequencer.py` explicitly reset `ts.bend = 0.0` for all tracks on every loop wrap
- **Fix**: Removed the reset. The real GBA M4A engine carries BEND state through GOTO loops (the Songs tab track_renderer.py already did this correctly with its single persistent TrackState object)
- **Verified**: BEND effects persist through loop boundaries, matching GBA behavior

### 14. Piano roll save PATT corruption (2026-04-08)
- **Problem**: Songs with PATT/PEND subroutines were destroyed on save — notes missing, wrong instruments, dead code after FINE
- **Root cause**: Save tried to mix flattened notes (expanded from PATT calls) back into original PATT/PEND structure. Impossible — produced broken subroutine boundaries.
- **Fix**: `notes_to_track_commands` in song_writer.py now strips PATT/PEND entirely and writes fully linear tracks. PATT cannot survive the flatten→edit→save round-trip.
- **Verified**: MUS_EVIL round-trips correctly — 205 notes, correct instruments, correct volumes

### 15. Fractional BEND pitch interpolation (2026-04-07)
- **Problem**: All pitch bends quantized to whole semitones — audible stairstepping on smooth bend sweeps
- **Root cause**: `_midi_key_to_freq()` computed `val1` and `val2` from GBA scale table but only used `val1`, discarding the `fineAdjust` interpolation. Both renderers then truncated/rounded midi_note to int before passing to the engine.
- **GBA behavior**: MidiKeyToFreq interpolates: `val = val1 + (val2 - val1) * fineAdjust / 256`. The BEND command produces fractional semitone offsets.
- **Fix**: (1) `_midi_key_to_freq()` now accepts float key and interpolates between val1/val2 matching GBA. (2) All render functions accept float midi_note. (3) track_renderer passes fractional pitch (not int-truncated). (4) realtime_sequencer passes float bend (not rounded).
- **Also fixed**: Realtime sequencer pan formula changed from simple `pan/127` to GBA's linear crossfade `(127-signed_pan)/255, (signed_pan+128)/255` matching track_renderer's `apply_pan()`.
- **Verified**: Continuous pitch interpolation between semitones, matching GBA hardware behavior.

### 16. VOL/TEMPO double-evaluation on save (2026-04-08)
- **Problem**: Volume halved every save cycle (127→89→63→44...). TEMPO similarly degraded.
- **Root cause**: Parser evaluates `127*mvl/mxv` → 89 (byte value). Writer wrote `89*mvl/mxv` — applying the multiplier again.
- **Fix**: Added `_raw_vol()` and `_raw_tempo()` in song_writer.py that reverse the evaluation. All 5 VOL and 2 TEMPO write sites use them.
- **Verified**: `127*mvl/mxv` round-trips perfectly. Minor ±1 rounding on non-127 values due to integer truncation.

### 17. Piano roll loop uses wrong loop_end (max across all tracks) (2026-04-08)
- **Problem**: Piano roll cursor continued way past where notes end before looping back. mus_evil loop_end was 1343 (67+ seconds at 50 BPM) but audible notes ended around tick 1151.
- **Root cause**: `load_song_data` computed loop_end as `max()` across all tracks' `get_flattened_loop_info()` results. Different tracks had GOTOs at different tick positions after PATT flattening. Track 0's GOTO was at 1151 but another track's was at 1343.
- **Fix**: Canvas loop region overridden with track 0's `get_flattened_loop_info()` values after loading. Track 0 is the structural authority (matches Song Structure panel display).
- **Verified**: Loop wraps at correct tick, cursor jumps back promptly.

### 18. Piano roll loop save loses edited GOTO position (2026-04-08)
- **Problem**: User set loop back to tick 960 in Song Structure, saved, reopened — loop was back to 1151.
- **Root cause**: `notes_to_track_commands` appended the loop GOTO *after* processing all timeline events. If notes existed past the loop end tick, `current_tick` was already past the desired position, so GOTO landed at ~1151 instead of 960.
- **Fix**: Loop GOTO inserted into timeline at the correct tick (priority 3). Emit loop now `break`s on GOTO/FINE, excluding post-loop notes from the saved .s file.
- **Verified**: Loop back position persists through save/reload cycle.

### 19. Keysplit routing broken by float midi_note (2026-04-08)
- **Problem**: Songs with keysplit instruments produced silence after BEND pitch was changed to float.
- **Root cause**: Keysplit table lookups and drum kit slot lookups need integer indices. Passing float midi_note caused silent failures (no matching slot found → no audio rendered).
- **Fix**: Added `int()` conversion specifically for keysplit slot lookups and drum kit indexing while keeping float for audio rendering interpolation.
- **Verified**: mus_evil plays correctly with both keysplit and BEND instruments.

---

## Answered Questions

- **What does TONEDATA_TYPE_FIX do for CGB vs DirectSound?** — DirectSound: fixed mixer step (0x800000), plays at native rate. CGB: minor frequency rounding in CgbSound() for anti-aliasing only. Noise (_alt): zero effect (CgbSound checks `ch < 4`). See item 13 above.
- **Does CgbSound() check TONEDATA_TYPE_FIX?** — Yes, but only for frequency rounding (`& 0x7fc` or `& 0x7fe`), not for fixing pitch. MidiKeyToCgbFreq does NOT check the type byte at all.

## Open Questions

- Are there volume differences from GBA's integer truncation at each stage that we're not matching?
- voice_directsound_alt (type 0x10, TONEDATA_TYPE_REV) — reverse playback not implemented (only 2 instruments)
