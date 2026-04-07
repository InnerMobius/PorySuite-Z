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

## Answered Questions

- **What does TONEDATA_TYPE_FIX do for CGB vs DirectSound?** — DirectSound: fixed mixer step (0x800000), plays at native rate. CGB: minor frequency rounding in CgbSound() for anti-aliasing only. Noise (_alt): zero effect (CgbSound checks `ch < 4`). See item 13 above.
- **Does CgbSound() check TONEDATA_TYPE_FIX?** — Yes, but only for frequency rounding (`& 0x7fc` or `& 0x7fe`), not for fixing pitch. MidiKeyToCgbFreq does NOT check the type byte at all.

## Open Questions

- Are there volume differences from GBA's integer truncation at each stage that we're not matching?
- voice_directsound_alt (type 0x10, TONEDATA_TYPE_REV) — reverse playback not implemented (only 2 instruments)
