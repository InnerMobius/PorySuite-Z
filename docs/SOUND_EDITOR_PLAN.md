# PorySuite-Z Sound Editor — Master Roadmap

**Created:** 2026-04-06
**Status:** All phases COMPLETE (1-9). Phase 8: piano roll with full editing, real-time sequencer, GM voicegroup generator, round-trip save, song structure panel. Phase 9: Import song .s file from another project (9.1 export and 9.2 QoL marked NOT NEEDED).
**Goal:** A full-featured music and sound editing suite built into PorySuite-Z, allowing users to browse, preview, edit, and create songs, instruments, and voicegroups using the actual project data — no external tools needed.

---

## How Music Works in pokefirered (Plain English)

Songs are assembly (`.s`) files containing a sequence of commands: play this note, wait, change instrument, set volume, loop back to here, etc. Each song has multiple **tracks** (like layers — one for melody, one for bass, one for drums, etc.).

Every song points to a **voicegroup** — a bank of up to 128 **instruments**. Instruments come in several flavors:
- **DirectSound** — a real audio recording (`.bin` file with a GBA `WaveData` header containing sample rate, loop start, and size, followed by signed 8-bit PCM data). Also has `no_resample` and `alt` variants.
- **Square Wave** — a classic chiptune synth sound (two hardware variants: square_1 with frequency sweep, square_2 without). Each has an `_alt` variant.
- **Programmable Wave** — a custom 16-byte waveform cycle (also has `_alt` variant)
- **Noise** — hiss/static used for percussion (also has `_alt` variant)
- **Keysplit** — routes different note ranges to different instruments (two modes: `keysplit` with a table, `keysplit_all` for blanket routing)

In total there are **15 voice macro types** the parser must handle (5 base types × 2-3 variants each, plus 2 keysplit types).

Loop points are `GOTO` commands inside the track data — the song plays through an intro section, then a `GOTO` sends it back to a label, looping forever. Editing loop points means moving where that `GOTO` target sits.

The song table (`sound/song_table.inc`) registers every song with a constant (`MUS_*` or `SE_*`), and `include/constants/songs.h` defines the numbers that scripts and Porymap use.

**Key files:**
| File | What it does |
|------|-------------|
| `sound/songs/midi/*.s` | Individual song data (bytecode tracks) |
| `sound/songs/midi/midi.cfg` | mid2agb settings per song (voicegroup, reverb, volume) |
| `sound/song_table.inc` | Master song registry (gSongTable) |
| `sound/voice_groups.inc` | All voicegroup definitions (instrument banks) |
| `sound/direct_sound_data.inc` | PCM sample includes |
| `sound/direct_sound_samples/*.bin` | Audio sample files (GBA WaveData header + signed 8-bit PCM) |
| `sound/programmable_wave_data.inc` | Custom waveform includes |
| `sound/programmable_wave_samples/*.pcm` | 16-byte waveform files |
| `sound/keysplit_tables.inc` | Note-range routing tables |
| `sound/MPlayDef.s` | Bytecode command definitions |
| `sound/music_player_table.inc` | Music player channel config |
| `include/constants/songs.h` | MUS_*/SE_* constant definitions |
| `asm/macros/m4a.inc` | Song table macro |
| `asm/macros/music_voice.inc` | Voice/instrument macros |
| `audio_rules.mk` | Build rules for MIDI→assembly→object |
| `tools/mid2agb/mid2agb.exe` | MIDI to GBA assembly converter |

**Verified facts (from audit):**
- **347 songs** total (260 SE + 87 MUS), 100% generated from MIDI via `mid2agb` — no hand-written `.s` files exist
- **77 voicegroups** with sparse numbering: 000–012 (13), then a gap, then 127–190 (64)
- **89 instrument samples** (DirectSoundWaveData labels in `direct_sound_data.inc`) + **481 total `.bin` files** in `direct_sound_samples/` (the rest are cry samples)
- **11 programmable wave samples**, **5 keysplit tables**
- **392 Pokemon cries** — separate system in `cry_tables.inc`, already handled by PorySuite's existing cry preview (`ui/audio_player.py`). **Cries are OUT OF SCOPE for the Sound Editor.**
- **`.bin` files are NOT raw PCM** — they have a GBA `WaveData` struct header (type, status, frequency/sample rate, loop start position, data size) followed by signed 8-bit PCM. The header contains loop point info that some instruments use for sustain looping.
- Songs reference voicegroups **by name** (e.g., `.equ mus_cycling_grp, voicegroup141`), not by number
- All looping uses `GOTO` (0xB2) bytecode. `PATT`/`PEND` are intra-loop pattern reuse optimizations, not loop alternatives.

---

## Architecture Overview

The Sound Editor will be a new top-level toolbar section in PorySuite-Z (like Overworld GFX or Trainers), containing multiple organized sub-tabs. It needs its own audio engine in Python that can parse and play back the GBA M4A bytecode format using the project's actual samples and synth definitions.

### Core Engine (backend)
- **M4A Parser** — reads `.s` song files, extracts tracks, commands, loop points, metadata
- **Voicegroup Parser** — reads `voice_groups.inc`, maps instruments to their type/samples/parameters
- **Sample Loader** — reads `.bin` DirectSound samples and `.pcm` programmable wave data
- **Audio Synthesizer** — Python audio engine that can render tracks using parsed instruments:
  - PCM sample playback with pitch shifting and ADSR envelopes
  - Square wave synthesis (duty cycle variants)
  - Programmable wave synthesis
  - Noise generation
  - Mixing multiple tracks with volume, panning, reverb
- **Song Table Manager** — reads/writes `song_table.inc`, `songs.h`, `midi.cfg`
- **Export/Write Pipeline** — writes modified `.s` files, voicegroup changes, new samples back to disk

### UI Tabs

The Sound Editor section will have these sub-tabs:

```
[Sound Editor]
  ├── Songs          — browse, preview, edit, add/remove songs
  ├── Instruments    — browse, preview, edit individual instruments
  ├── Voicegroups    — edit instrument banks, assign to songs
  └── Samples        — manage audio samples (import/export/preview)
```

---

## Phase 1 — Core Parsing Engine (No UI Yet) — COMPLETE

Parsing engine built and tested against live project data (2026-04-06).

**Files created:**
- `core/sound/__init__.py`
- `core/sound/sound_constants.py` — 49 waits, 48 note durations, 128 note names, 13 voice types, all bytecodes
- `core/sound/song_parser.py` — parses .s song files into tracks/commands/loop points
- `core/sound/song_table_manager.py` — reads/writes song_table.inc + songs.h + midi.cfg (347 songs)
- `core/sound/voicegroup_parser.py` — parses voice_groups.inc (77 voicegroups) + keysplit_tables.inc (5 tables)
- `core/sound/sample_loader.py` — loads 89 instrument .bin samples + 11 programmable wave .pcm files

**Key finding during build:** `.bin` sample files use fixed-point frequency (actual Hz = raw >> 10), and status flag 0x4000 indicates looping. Some voicegroups have fewer than 128 slots (sub-voicegroups used for keysplit routing).

### Step 1.1 — Song File Parser
- Parse `.s` song files into structured data: header (track count, priority, reverb, voicegroup ref), and per-track command lists
- Handle all bytecode commands: TEMPO, VOICE, VOL, PAN, MOD, BENDR, LFOS, KEYSH, notes (N01-N96), waits (W01-W96), GOTO, PATT, PEND, REPT, FINE, TIE, EOT
- Extract loop points: identify GOTO targets and the label they jump to
- Parse the header block at the bottom of each `.s` file (track count, voicegroup pointer, track pointers)
- Handle the `.equ` constants at the top (voicegroup, priority, reverb, master volume, key shift, etc.)

### Step 1.2 — Song Table Parser
- Parse `sound/song_table.inc` — extract all `song` macro entries (label, music_player, unknown)
- Parse `include/constants/songs.h` — map MUS_*/SE_* constants to table indices
- Parse `sound/songs/midi/midi.cfg` — extract per-song mid2agb settings (reverb, voicegroup index, volume, priority)
- Build a unified song registry: constant name, table index, label, player type (BGM/SE1/SE2/SE3), voicegroup, reverb, volume

### Step 1.3 — Voicegroup Parser
- Parse `sound/voice_groups.inc` — extract all voicegroup definitions
- For each voicegroup, parse all 128 instrument slots
- Identify instrument type — must handle all 15 voice macro types:
  - `voice_directsound` (0x00), `voice_directsound_no_resample` (0x08), `voice_directsound_alt` (0x10)
  - `voice_square_1` (0x01), `voice_square_1_alt` (0x09)
  - `voice_square_2` (0x02), `voice_square_2_alt` (0x0A)
  - `voice_programmable_wave` (0x03), `voice_programmable_wave_alt` (0x0B)
  - `voice_noise` (0x04), `voice_noise_alt` (0x0C)
  - `voice_keysplit` (0x40), `voice_keysplit_all` (0x80)
- Extract parameters: base key, pan, ADSR envelope, sample reference, duty cycle, sweep, etc.
- Handle sparse voicegroup numbering (000–012, then 127–190, with a gap of 114 in between)
- Resolve keysplit chains (voicegroup → keysplit table → sub-voicegroups)

### Step 1.4 — Sample Data Loader
- Parse `sound/direct_sound_data.inc` — map 89 `DirectSoundWaveData_*` labels to `.bin` file paths
- Load `.bin` sample files — these are NOT raw PCM. Each has a GBA `WaveData` header:
  - Bytes 0-1: type/status flags
  - Bytes 2-3: reserved
  - Bytes 4-7: frequency (sample rate in Hz, typically 8000-22050)
  - Bytes 8-11: loop start position (byte offset into PCM data where sustain loop begins; 0 = no loop)
  - Bytes 12-15: data size (number of PCM bytes)
  - Bytes 16+: signed 8-bit PCM sample data
- The loop start field is important — some instruments loop a sustain portion for held notes
- Exclude cry samples (in `cries/` subdirectory) — those are handled separately
- Parse `sound/programmable_wave_data.inc` — map 11 `ProgrammableWaveData_*` labels to `.pcm` files
- Load `.pcm` waveform files (16 bytes each — one cycle of a custom waveform, 4-bit packed)
- Parse `sound/keysplit_tables.inc` — load 5 key split routing tables

---

## Phase 2 — Audio Playback Engine — COMPLETE

Built and tested (2026-04-06). All songs render to playable audio using the project's actual instruments. Multiple accuracy fixes applied after initial build.

**Files created:**
- `core/sound/audio_engine.py` — PCM sample playback with GBA-accurate MidiKeyToFreq pitch shifting (gScaleTable + gFreqTable), square wave synthesis (4 duty cycles + sweep), programmable wave synthesis, noise generation, ADSR envelopes (with CGB 0-7/0-15 to DirectSound 0-255 conversion), GBA-accurate linear stereo panning, reverb, AudioPlayer class (sounddevice OutputStream)
- `core/sound/track_renderer.py` — Track state machine (walks bytecode commands), tick-to-sample timing, per-note stereo rendering with panning, full song mixer (all tracks with reverb/normalization), instrument preview helper
- `tests/test_audio_engine.py` — 14 unit+integration tests (all pass)
- `tests/test_track_renderer.py` — 4 integration tests including full song renders (all pass)
- `tests/test_playback.py` — End-to-end test that renders songs to .wav files

**Performance:** mus_cycling (8 tracks, 134 BPM, 34.6s) renders in ~1.9s. mus_pallet (6 tracks, 88 BPM, 44s) in ~2.6s.

**New dependencies:** `sounddevice` (0.5.5), `numpy` (2.4.4)

**Accuracy fixes applied (2026-04-06):**
- GBA-accurate pitch: MidiKeyToFreq with gScaleTable/gFreqTable (0.00 semitone error) replaces naive `2^(semitone/12)`
- CGB ADSR scale conversion: square/wave/noise use 0-7/0-15 range, converted to 0-255 before envelope generation
- BEND command subtracts C_V (64) matching GBA's `ply_bend` behavior
- Running status bytes (bare integers repeating last control command) now parsed correctly
- Removed double velocity application and fake master_volume multiplication
- Removed arbitrary CGB volume reduction factors (0.3/0.25/0.2)
- Pan changed from constant-power (cos/sin) to GBA's linear crossfade
- Per-note panning replaces per-track (mid-song PAN changes now work)
- XCMD continuation lines no longer create phantom KEYSH commands (~85 songs affected)
- TONEDATA_TYPE_FIX (_alt voice types): investigated — only affects DirectSound (already handled by no_resample); CGB _alt instruments pitch-shift normally (no fix needed)
- keysplit_all overflow: drum notes that index past voicegroup bounds chain into next contiguous voicegroup

### Step 2.1 — Sample Playback (DirectSound)
- Parse WaveData header from `.bin` files to get sample rate, loop start, and data size
- Decode signed 8-bit PCM -> float32 audio buffer
- Pitch-shift samples based on note vs. base_midi_key (resample with linear interpolation)
- Handle sample looping: sustain portion loops between loop_start and end for held notes
- Apply ADSR envelope (attack, decay, sustain, release)
- Velocity scaling

### Step 2.2 — Synthesizer Voices
- Square wave generator with 4 duty cycle modes (12.5%, 25%, 50%, 75%)
- Programmable wave generator (16-byte wavetable lookup)
- Noise generator (periodic and aperiodic modes via LFSR approximation)
- ADSR envelopes for all synth types
- Frequency sweep for square_1

### Step 2.3 — Track Renderer
- State machine walks bytecode commands (VOICE, VOL, PAN, MOD, BEND, KEYSH, TEMPO, NOTE)
- Tick-to-sample conversion using GBA timing (59.7275 fps, tempo-scaled ticks)
- Per-note rendering through voicegroup instruments with keysplit routing
- Volume chain: velocity (applied once by audio engine) * track volume (applied per-note by renderer). Song mvl is already baked into VOL command values.

### Step 2.4 — Song Mixer
- Renders all tracks to stereo with per-note panning (GBA linear crossfade)
- Song-level reverb (delay-based feedback matching GBA behavior)
- Peak normalization to prevent clipping
- AudioPlayer class: sounddevice OutputStream with callback, play/stop/pause/resume/seek
- Transport controls: play, pause, stop, seek (jump to measure/tick)
- Loop-aware seeking: show "intro" vs "loop" sections on a timeline

---

## Phase 3 — Songs Tab (UI) — COMPLETE

### Step 3.1 — Song Browser
- Left panel: scrollable list of all songs, divided into sections:
  - BGM (background music) — MUS_* entries
  - Sound Effects — SE_* entries
  - Fanfares/Jingles — MUS_HEAL, MUS_LEVEL_UP, etc.
- Search bar with filtering by name
- Category filter dropdown (BGM / SE / Fanfare / All)
- Each entry shows: constant name (friendly), voicegroup, track count, player type
- Song count badge per category

### Step 3.2 — Song Preview & Playback
- Right panel: selected song details
  - Friendly name + constant (e.g., "Cycling — MUS_CYCLING")
  - Voicegroup assignment (clickable → jumps to Voicegroups tab)
  - Track count, reverb, volume, priority
  - Tempo (BPM)
- Transport bar: Play / Pause / Stop / Loop toggle
- Timeline scrubber showing song position with intro/loop markers
- Per-track mute/solo toggles (like a mini mixer)
- Volume knob for preview

### Step 3.3 — Loop Point Editor
- Visual timeline showing the song structure:
  - Green section = intro (before first GOTO target)
  - Blue section = loop body (between GOTO target and GOTO command)
  - Red marker = GOTO command position
  - Yellow marker = GOTO target (loop start)
- Draggable loop point markers to reposition the loop start
- "Play from loop start" button for quick preview
- When moved, automatically updates all tracks' GOTO targets to the new measure
- Warning if tracks have mismatched loop points (they should normally all loop together)

### Step 3.4 — Song Properties Editor
- Edit song metadata: reverb, master volume, priority, voicegroup assignment
- Voicegroup dropdown (shows all available voicegroups with friendly labels)
- Music player assignment (BGM / SE1 / SE2 / SE3)
- "Apply" saves changes to the `.s` file header and `midi.cfg`

### Step 3.5 — Add / Remove Songs
- **Add New Song:**
  - Option A: Import from MIDI file (runs mid2agb conversion with user-chosen settings)
  - Option B: Create blank song (minimal template with configurable track count)
  - Auto-generates: `.s` file, entry in `song_table.inc`, constant in `songs.h`, entry in `midi.cfg`
  - Constant name picker with validation (must be unique, MUS_* or SE_* prefix)
- **Remove Song:**
  - Confirmation dialog with cross-reference check (warns if used in any scripts)
  - Removes: `.s` file, song table entry, songs.h constant, midi.cfg entry
  - Renumbers remaining constants if needed (or leaves gap with comment)
- **Duplicate Song:**
  - Copies an existing song's `.s` file with a new name
  - Registers in all the right places

---

## Phase 4 — Instruments Tab (UI) — COMPLETE

### Step 4.1 — Instrument Browser — COMPLETE
Built `ui/instruments_tab.py` with a full instrument browser. Left panel lists all 7,841 instruments across 77 voicegroups. Columns: instrument name, type, voicegroup number, slot index. Three filters: voicegroup dropdown, type dropdown (Sample/Square/Prog. Wave/Noise/Keysplit), and text search. Count label updates to show visible/total. Sound Editor now has a QTabWidget with "Songs" and "Instruments" sub-tabs.

### Step 4.2 — Instrument Preview — COMPLETE
Right panel shows selected instrument details: friendly name, voice macro + type byte, base MIDI key, pan, voicegroup + slot. Type-specific info: sample name/rate/loop for DirectSound, duty cycle/sweep for square waves, waveform label for prog. wave, period for noise, target voicegroup for keysplits. ADSR envelope visualization (graphical curve widget, auto-detects CGB vs DirectSound scale). "Used By" cross-reference shows other voicegroups with the same sample/synth. 3-octave clickable piano keyboard (C3–B5) for instant preview at any pitch. "Play Note" button for middle C. Background thread rendering.

### Step 4.3 — Instrument Editor — COMPLETE
All instrument properties are now editable in the detail panel. ADSR envelope has 4 sliders with live visual curve updates (auto-detects CGB 0-7/0-15 vs DirectSound 0-255 scale). Base key spinner, pan spinner, duty cycle dropdown (square waves), sweep spinner (square_1), period dropdown (noise). Edits propagate to every copy of the same instrument across all voicegroups automatically (they're the same sound, just shared). Modified signal emitted for dirty tracking. Keysplit instruments are read-only (routing only). File writing deferred to Phase 6 save pipeline.

### Step 4.4 — Sample Management — COMPLETE
All four DirectSound sample operations implemented:
- **Export to WAV**: Converts GBA signed 8-bit PCM to standard unsigned 8-bit WAV with correct sample rate from the WaveData header.
- **Import from WAV**: User picks a .wav, names it, wav2agb converts it to .bin, auto-appended to direct_sound_data.inc, loaded into memory.
- **Replace sample**: Overwrites existing .bin via wav2agb, creates backup during conversion, restores on failure.
- **Delete sample**: Reference-checks all voicegroup slots first — blocks deletion if any instrument still uses the sample. Removes .bin file and .inc entry.
- Reference checker utility: `get_sample_references()` finds every voicegroup slot referencing a given sample label.
- Programmable wave visual editor deferred to a later phase.
- Pure Python WAV conversion (no wav2agb dependency) — handles any bit depth, stereo→mono, any sample rate.
- **Import size/rate dialog**: Shows WAV metadata (rate, duration, estimated GBA size) before importing. If WAV rate > 13379 Hz, offers downsample options (e.g. "Downsample to 13379 Hz — 26.1 KB"). Uses `peek_wav_info()` for metadata and `_resample_linear()` for rate conversion during import.

### Step 4.5 — Piano Octave Controls & Cross-Tab Navigation — COMPLETE
- **Piano octave shift**: ◀/▶ buttons shift the 3-octave keyboard up or down. Range label shows current span (e.g. "C5 – B7"). Clamped to valid MIDI range (0–127).
- **Right-click "Go to Instrument"**: Songs tab track list supports right-click → "Go to Instrument" which switches to the Instruments tab and selects the matching instrument via identity-key deduplication.
- **`select_instrument()` API**: Public method for programmatic instrument selection by voicegroup name + slot index.
- **Comprehensive tooltips**: Every interactive control has a tooltip explaining what it does in plain English.

---

## Phase 5 — Voicegroups Tab (UI) — COMPLETE

### Step 5.1 — Voicegroup Browser — COMPLETE
Built `ui/voicegroups_tab.py` with left panel listing all 77 voicegroups. Shows VG number, non-filler slot count (e.g. "24/128"), and song usage count. Search filter. Tooltips list songs using each voicegroup (up to 20). Sorted by number.

### Step 5.2 — Voicegroup Editor — COMPLETE
Right panel has full 128-slot instrument list with columns: slot index, type badge (DS/SQ1/SQ2/PW/NS/KS), friendly name, detail summary. Filter dropdown (All/Non-Filler/Samples/Square/Prog. Wave/Noise/Keysplits). Click a slot to edit below. Voice Type dropdown changes instrument type (all 13 editable voice types). Type-specific editors: Sample picker (DirectSound), Wave picker (Prog. Wave), Keysplit target VG + table picker. Common params: base key, pan, duty cycle, sweep, period, ADSR sliders with auto-ranging (CGB vs DirectSound scale). Right-click context menu: "Copy Instrument From Another VG" and "Go to Instrument" (jumps to Instruments tab).

### Step 5.3 — Voicegroup Management — COMPLETE
- **Add New**: Creates voicegroupNNN with 128 filler slots. User picks number (next available suggested).
- **Clone**: Deep-copies all 128 slots from the selected VG to a new number.
- **Delete**: Reference-checks all songs first — blocks deletion if any song uses the VG. Confirmation dialog.
- Keysplit visual piano-range editor deferred to a later enhancement phase.

### Step 5.4 — Cross-Reference Views — COMPLETE
"Songs Using This Voicegroup" section shows all songs referencing the selected VG. Filler slots greyed out in the slot list. Unused (filler) count shown in the VG info summary line.

### Step 5.5 — Save to Disk — COMPLETE
`save_to_disk()` writes the full `voice_groups.inc` file. Correct assembly syntax for all 15 voice macro types. Round-trip tested: output matches original file format exactly. Dirty tracking per-voicegroup. Wired into the unified save pipeline (File → Save).

---

## Phase 6 — Integration with PorySuite-Z — COMPLETE

### Step 6.1 — Toolbar & Navigation — COMPLETE
- Sound Editor icon in the PorySuite-Z toolbar (RPG Maker XP-style)
- Sub-tabs: Songs | Instruments | Voicegroups
- F8 keyboard shortcut to jump to Sound Editor from anywhere

### Step 6.2 — Cross-Editor Integration — COMPLETE

- **EVENTide integration — COMPLETE:**
  - `playbgm`, `playse`, and `playfanfare` command widgets have ▶ Preview and ■ Stop buttons
  - Preview renders and plays the selected song in the background WITHOUT switching tabs (stays on Event Editor)
  - ■ Stop button halts audio playback without leaving the Event Editor
  - 🔊 "Open in Sound Editor" button switches to Sound Editor page and selects the song
  - Song picker dropdowns already show friendly names via ConstantsManager
  - Module-level callback pattern (`_preview_song_cb`, `_open_in_sound_editor_cb`, `_stop_preview_cb`) wired by unified_mainwindow
  - Songs render with configurable loop iterations (default 2, via `sound/loop_count` setting)
- **EVENTide bug fixes (2026-04-06):**
  - `savebgm` widget: was `_header_only = True` returning `('savebgm',)` — now has ConstantPicker for MUS_* and returns `('savebgm', song_constant)`
  - `healplayerteam` widget: was emitting nonexistent macro — now returns `('special', 'HealPlayerParty')`
  - `playmoncry` widget: was discarding mode parameter — now preserves it
  - Shared sub-label save: events sharing `goto` targets could overwrite each other — save loop now processes selected event last
  - Empty `local_id`: `_collect_current()` injected `"local_id": ""` into objects that never had one — now only writes if non-empty
- **Constants sync — COMPLETE:** Sound Editor page added to the set of pages that trigger a ConstantsManager.refresh() when switching to EVENTide pages. Any song additions/removals that wrote to songs.h are picked up automatically when the user opens the Event Editor.
- **Porymap integration — COMPLETE:** Porymap reads `include/constants/songs.h` directly from the project folder and watches it for changes. The save pipeline already writes songs.h, so Porymap will detect the change and prompt the user to reload. No bridge code needed.

### Step 6.3 — Save Pipeline Integration — COMPLETE
- Sound Editor changes mark the unified window dirty via `modified` signal
- File → Save pipeline covers:
  - `voice_groups.inc` — via VoicegroupsTab.save_to_disk() (preserves .include lines between voicegroup blocks)
  - `song_table.inc` — via write_song_table() when SongTableData._dirty is set
  - `songs.h` — via write_songs_h() when SongTableData._dirty is set
  - `midi.cfg` — via write_midi_cfg() when SongTableData._dirty is set
  - `direct_sound_data.inc` — updated on import/delete (immediate, not deferred to save)
  - Sample `.bin` files — written on import/replace (immediate)
  - Song `.s` files — will be handled in Phase 8 (Piano Roll)

### Step 6.4 — Settings — COMPLETE

- Sound Editor settings page in the Settings dialog:
  - Default preview volume (0-100%)
  - Loop count for song preview (1-10 times, default 2)
  - Auto-downsample rate for sample imports (No limit / 44100 / 22050 / 13379 Hz)
  - Audio output mode selector (Stereo / Mono). Stereo preserves left/right panning like the GBA. Mono mixes both channels together. Setting is respected by both the Songs tab playback and the Instruments tab preview.

---

## Phase 7 — MIDI Import & Conversion Studio — COMPLETE

This is a core feature, not polish. Most people adding custom music will start with a MIDI file they found or composed elsewhere, and they need a proper workflow to bring it into the GBA format with the right instruments and loop points.

**New dependency:** `mido>=1.3` (MIDI file reading/writing) — added to `requirements.txt`.

### Step 7.1 — MIDI Import Dialog — COMPLETE
Built `ui/dialogs/midi_import_dialog.py` — a multi-step wizard dialog:
- **Page 1 — File Selection:** File picker for `.mid` files. Immediately shows MIDI metadata (type, BPM, duration, track count, total notes). Track list table with columns: channel number, name, GM instrument name, note count, and note range. Song constant name field with live validation (checks format, uniqueness against songs.h). Auto-generates `MUS_*` name from filename.
- **Page 2 — Settings:** Voicegroup selector dropdown (all project voicegroups with instrument counts). Master volume, reverb, and priority spinners. Player type selector (BGM/SE1/SE2/SE3). Advanced options: exact gate time (-E), high resolution timing (-X).
- **Page 3 — Progress:** Progress bar and result summary. Shows all files that were created/modified.
- Backend module: `core/sound/midi_importer.py` with full GM instrument name table (128 entries), MIDI file parser via `mido`, and `Mid2AgbSettings` dataclass.

### Step 7.2 — Instrument Mapping — COMPLETE
Built as Page 2 of the MIDI Import wizard:
- **Per-track mapper:** Scrollable grid showing each MIDI track's channel, GM instrument name (e.g. "#21: Trumpet"), an arrow, a VG slot spinner (0-127), and a live label showing what voicegroup instrument is in that slot (or "filler / empty" in red if the slot is unused).
- **Default mapping:** Each track starts mapped to the same slot number as its MIDI program number (e.g. GM #21 → VG slot 21). This matches what mid2agb does by default.
- **Auto-Match by Name:** Button that tries to find the closest voicegroup instrument for each track by comparing GM instrument names to voicegroup instrument names (word overlap + substring matching).
- **Post-processing:** After mid2agb produces the `.s` file, `_postprocess_voice_remap()` rewrites any `VOICE` commands where the user chose a different slot than the MIDI default. Drums tracks are left at slot 0 (mid2agb maps drum hits by note number, not by VOICE).
- Voicegroup data is passed from the Sound Editor so the mapper can show real instrument names and detect filler slots.

### Step 7.3 — Song Structure & Loop Points — COMPLETE
Rebuilt from 4 simple radio buttons into a full section sequencer (Page 3 of the MIDI Import wizard):
- **Section definition:** Users define named sections by specifying start and end measure numbers. The page reads `total_measures`, `time_sig_num`, and `time_sig_den` from `MidiFileInfo` (computed from the MIDI) so measure ranges are accurate.
- **Custom play order:** Sections can be arranged in any order via drag/reorder. This lets users repeat sections, rearrange the song, or build complex structures.
- **Loop start position:** Users set which position in the play order starts the loop. Everything before that position is the intro (plays once). Everything from that position onward repeats.
- **Quick presets:** Automatic (preserves mid2agb's native GOTO/PATT/PEND structure), Loop All (GOTO back to start), No Loop (FINE only). These cover simple cases without needing to define sections manually.
- **Post-processor:** Translates the custom section structure into GOTO/PATT/PEND assembly commands in the .s file after mid2agb conversion.

### Step 7.4 — Convert & Register — COMPLETE
Built into `core/sound/midi_importer.py`:
- `run_mid2agb()` — locates and runs the project's `tools/mid2agb/mid2agb.exe` with the user's settings. Copies the .mid file into `sound/songs/midi/` so `make` can rebuild from source.
- `register_song()` — adds the new song to `song_table.inc`, `songs.h`, and `midi.cfg` with correct formatting.
- `import_midi()` — full pipeline: convert + register in one call.
- `validate_constant_name()` — checks name format (MUS_/SE_ prefix, uppercase, unique).
- Runs on a background thread via `_ImportWorker` to keep the UI responsive.
- After import, the Songs tab reloads its song list and selects the new song.
- The "Import MIDI..." button is in the Songs tab left panel, next to the song count label.

---

## Phase 8 — Piano Roll Composer

A full visual song editor for creating new songs from scratch or editing existing ones note-by-note. This is what turns the Sound Editor from a management tool into a composition tool.

### Step 8.1 — Piano Roll View (Read-Only First) — COMPLETE

Implemented 2026-04-06. "Piano Roll" button in Songs tab transport controls opens a standalone window for the currently selected song.

**What was built:**
- **`ui/piano_roll_widget.py`** — Custom QPainter-based piano roll canvas inside a QScrollArea. Mini piano keyboard on left sidebar (all 128 MIDI notes), measure ruler at top with measure numbers, grid lines at measure/beat/sub-beat levels (bright/medium/dim), black key rows darker than white key rows, colored note bars per track (velocity affects opacity), loop region highlight (green tint between loop start/end), playback cursor (red vertical line), zoom via Ctrl+Wheel (horizontal) and Ctrl+Shift+Wheel (vertical), hover shows note name in status bar.
- **`ui/piano_roll_window.py`** — Standalone QMainWindow hosting the piano roll. Track tab bar ("All Tracks" plus one tab per track showing channel and instrument), horizontal and vertical zoom sliders in toolbar, grid snap selector combo box (scroll-guarded per project rules), song info label (label, BPM, notes, tracks, loop info), status bar with hovered note name, keyboard shortcuts (Ctrl+0 reset zoom, Ctrl+/- zoom in/out), Reset Zoom button. Playback transport: Play/Stop/Pause buttons, Space bar toggle, moving cursor that follows playback, volume control, time display.
- **`ui/sound_editor_tab.py`** — Added "Piano Roll" button next to Play/Stop. Enables/disables based on whether a parsed song is selected.

### Step 8.2 — Piano Roll Editing — COMPLETE

Implemented 2026-04-07 in `ui/piano_roll_widget.py`.

**What was built:**
- **Click to place a note** — click empty space, a new note appears snapped to the current grid setting, drag right after placing to extend duration
- **Drag to move** — click and drag an existing note body to move it in time and pitch
- **Drag to resize** — drag the right edge of a note bar to change its duration
- **Right-click to delete** a note
- **Selection** — Shift+drag draws a selection box that selects all notes inside; Ctrl+click toggles selection on individual notes; Ctrl+A selects all; Escape deselects
- **Delete selected** — Delete or Backspace removes all selected notes
- **Copy/paste** — Ctrl+C copies selected notes, Ctrl+V pastes at the current playback position
- **Visual feedback** — selected notes get a yellow highlight outline; velocity shown as a thin bar at the bottom of each note
- **Snap grid** — toolbar dropdown with 1/4 beat, 1/8, 1/16, 1/32, and Free options
- **Context-sensitive cursors** — crosshair on empty space, open hand on note body, resize arrow on right edge

### Step 8.3 — Track Management — COMPLETE

Implemented 2026-04-07. New file: `ui/piano_roll_tracks.py`.

**What was built:**
- **Track sidebar panel** — 220px wide panel on the left side of the piano roll
- **Per-track rows** — each row shows: color swatch, track name, instrument number + name from voicegroup, channel, note count
- **Volume slider** per track (0-127, reads initial VOL command from track)
- **Pan slider** per track (0-127, 64=center, reads initial PAN command)
- **Mute (M) and Solo (S) toggles** — toggle buttons per track with colored highlight when active
- **Click to select** — clicking a track row selects it and switches the piano roll to that track
- **Add track (+)** — creates a new empty track with default VOICE command
- **Remove track (-)** — confirmation dialog if the track has notes
- **Duplicate track (Dup)** — deep copies all commands and notes
- **Instrument names** — looked up from voicegroup data when available

### Step 8.4 — Real-Time Sequencer for Piano Roll — COMPLETE

Implemented 2026-04-07. Piano roll now uses a real-time sequencer that synthesizes notes on-the-fly as the cursor moves across the timeline. No pre-rendering of the full song.

**What was built:**
- **Real-time sequencer** (`core/sound/realtime_sequencer.py`) — notes are rendered individually when the cursor reaches them and mixed into live audio output. No pre-render step at all.
- **Instant play/pause/resume** — pause anywhere, edit notes, resume from the same position. No waiting for renders.
- **Click-to-seek** — click the ruler bar to jump the cursor to any tick position
- **Live note updates** — edits to notes are pushed to the sequencer immediately and take effect on the next cursor pass
- **32-voice polyphony** — up to 32 simultaneous voices, oldest dropped if exceeded
- **Per-track mute/solo/volume/pan** — track sidebar controls feed directly into the sequencer
- **Loop region support** — cursor wraps at loop boundaries
- **Space bar** toggles play/pause

**Bug fixes (2026-04-07):**
- Voicegroup swap during playback: now calls `sequencer.update_voicegroup()` instead of destroying and failing to recreate
- Instrument swap during playback: now calls `sequencer.set_track_instrument()` instead of destroying sequencer
- Track volume/pan sliders: wired to `sequencer.set_track_volume()`/`set_track_pan()` (were disconnected)
- Pause/resume with no sequencer: now starts playback instead of silently returning
- Cleanup: `sequencer.stop()` called before any `sequencer = None` assignment

**Performance fixes (2026-04-07):**
- Note rendering moved to background worker thread — audio callback only does lightweight mixing, preventing buffer underruns that caused tempo lagging/speeding up
- Vectorized looping sample resample in `render_directsound` — Python for-loop (65k iterations) replaced with numpy array operations (~10-50x faster)

**Song structure fix (2026-04-07):**
- `load_song_data` in piano roll now calls `flatten_track_commands(loop_count=1)` — songs with PATT/PEND subroutine calls and GOTO loops (like Game Corner) now show their full intro-then-loop structure

**Files created/changed:**
- `core/sound/realtime_sequencer.py` — NEW: real-time note-by-note sequencer with background render thread; includes `update_voicegroup()`, `set_track_volume()`, `set_track_pan()`, `set_track_instrument()` for live updates
- `core/sound/audio_engine.py` — vectorized looping sample interpolation in `render_directsound`
- `ui/piano_roll_window.py` — rewritten to use RealtimeSequencer, no pre-rendering; all playback bugs fixed
- `ui/piano_roll_widget.py` — added `ruler_clicked` signal for click-to-seek; `load_song_data` flattens PATT/PEND/GOTO

### Step 8.5 — GM Voicegroup Generator — COMPLETE

Implemented 2026-04-06. New module: `core/sound/gm_voicegroup.py`.

**What was built:**
- **Sample scanning** — scans all 77 voicegroups and catalogs all 89 unique DirectSound samples
- **GM mapping** — maps samples to GM program numbers (0-127) by matching sample names against SC-88 Pro/SD-90 instrument names
- **Voicegroup creation** — builds a 128-slot voicegroup with real instruments for matched GM slots and a square wave fallback for unmatched slots
- **Coverage report** — function that shows how many GM slots have real instruments vs fallback
- **Voicegroups Tab UI** (`ui/voicegroups_tab.py`) — "Generate GM" button added to the toolbar, creates the GM voicegroup and adds it to the project
- **MIDI Import Dialog UI** (`ui/dialogs/midi_import_dialog.py`) — "Generate GM" button added to the voicegroup selection page; MIDI import auto-detects an existing GM voicegroup and pre-selects it

**Files created/changed:**
- `core/sound/gm_voicegroup.py` — NEW: GM voicegroup generator (sample scanning, GM mapping, coverage report)
- `ui/voicegroups_tab.py` — added "Generate GM" toolbar button
- `ui/dialogs/midi_import_dialog.py` — added "Generate GM" button + auto-select existing GM voicegroup

### Step 8.6 — Round-Trip Editing — COMPLETE

Implemented 2026-04-07. Piano roll edits can now be saved back to .s assembly files.

**What was built:**
- **Song writer** (`core/sound/song_writer.py`) — NEW: Converts SongData back to valid GBA M4A .s assembly format. Handles NOTE/WAIT pairs with running-status optimization (omits pitch/velocity/duration when unchanged), all control commands, GOTO/PATT/PEND structure, .equ header, track bodies, song footer.
- **Save button** in piano roll toolbar + File → Save (Ctrl+S)
- **Dirty tracking** — title bar shows [modified], Save button enables only when changes exist
- **Close confirmation** — Save/Discard/Cancel dialog when closing with unsaved changes
- **Notes-to-commands converter** — `notes_to_track_commands()` turns piano roll note dicts into track command lists with proper KEYSH/VOICE/VOL/PAN setup. Supports loop_start_tick/loop_end_tick (emits LABEL + GOTO), preserves mid-song control changes (VOL/PAN/MOD/BEND/TEMPO/etc.) from original track at their original tick positions, and merges everything into a sorted timeline.
- **Loop preservation** — if the piano roll has a loop region, saves as LABEL at loop start + GOTO at loop end using the track's existing loop label name (or auto-generates one)
- **Control change preservation** — mid-song volume fades, pan sweeps, tempo changes, modulation etc. from the original .s file are kept at their exact tick positions even after note editing
- **Structure preservation** — songs with PATT/GOTO/PEND structural commands keep those commands in the regenerated output alongside the edited notes
- **Enhanced ruler/timeline** — 32px ruler with gradient background, beat subdivision ticks, 7px red triangle playback handle (visible at all times including after stop), drag-to-scrub for positioning
- **UI overhaul (2026-04-07)** — RulerWidget extracted as a separate fixed widget above the scroll area (never scrolls away vertically), syncs horizontally with the piano roll. Toolbar compacted: track selector changed from QTabBar to QComboBox (saves ~300px), song info label moved from toolbar to status bar. PianoRollWidget restructured from QScrollArea subclass to QWidget composite (ruler row + scroll area in QVBoxLayout).

**Files created/changed:**
- `core/sound/song_writer.py` — NEW: song-to-assembly writer
- `ui/piano_roll_window.py` — Save pipeline, dirty tracking, close confirmation, compact toolbar (combo box track selector, status bar info)
- `ui/piano_roll_widget.py` — RulerWidget (fixed above scroll area), canvas ruler code removed, PianoRollWidget restructured

### Step 8.7 — Song Structure Panel & UX Polish — COMPLETE

Implemented 2026-04-07. Major usability improvements to the piano roll.

**What was built:**
- **Song Structure Panel** (`ui/piano_roll_structure.py`) — NEW: Right-side panel (220px) showing structural commands in plain English. LABEL → "Section" (green), GOTO → "Loop Back" (blue), PATT → "Play Pattern" (orange), PEND → "End Pattern" (faded orange), FINE → "End Song" (red). Click to seek to tick, double-click to edit. Add buttons for each type with tooltips. Remove with confirmation. Structure edits update loop region in real-time (syncs to sequencer).
- **Save button** on toolbar + Ctrl+S shortcut — calls `save_to_disk()` directly from piano roll, no need to go back to main window.
- **Instrument dropdown** — replaced QSpinBox (tiny arrows, unusable) with QComboBox showing all 128 slots with names (e.g. "0: Sc88Pro Square Wave", "38: Sc88Pro Organ2"). Repopulates when voicegroup changes.
- **Voicegroup friendly labels** (`core/sound/voicegroup_labels.py`) — NEW: UI-only label system. "Auto-Label" button in Voicegroups tab generates names from song table usage. Pencil button (✎) in piano roll for individual rename. Labels stored in `porysuite/cache/{project_hash}/voicegroup_labels.json`. VG dropdown shows "Friendly Name (voicegroupNNN)". Dropdown refreshes from live voicegroup data on open (picks up newly created GM voicegroups without restarting). Labels are a shared dict reference between voicegroups tab, sound editor tab, and piano roll — edits in one place are immediately visible in others.

**Files created/changed:**
- `ui/piano_roll_structure.py` — NEW: Song Structure panel
- `core/sound/voicegroup_labels.py` — NEW: VG label management
- `ui/piano_roll_window.py` — Save button, Ctrl+S, structure panel wiring, VG labels passthrough
- `ui/piano_roll_tracks.py` — Instrument dropdown, VG rename button, live VG refresh on open
- `ui/voicegroups_tab.py` — Auto-Label button, labels in VG tree
- `ui/sound_editor_tab.py` — VG labels loaded on project open, passed through

---

## Phase 9 — Song Import — COMPLETE

### ~~Step 9.1 — Export Features~~ — NOT NEEDED
~~Export song to MIDI, export to WAV, export voicegroup preset.~~

### ~~Step 9.2 — Quality of Life~~ — NOT NEEDED
~~Undo/redo, bulk operations, song usage report, duplicate detection.~~

### Step 9.3 — Import Song .s File from Another Project — COMPLETE

Implemented 2026-04-07. New file: `ui/dialogs/s_file_import_dialog.py`.

**What was built:**
- **"Import .s..." button** on the Songs tab alongside the existing "Import MIDI..." button
- **3-page wizard dialog** (`SFileImportDialog`):
  - **Page 0 — File Selection & Preview:** File picker for `.s` files. Parses the song with `song_parser.parse_song_file()` and displays: label, voicegroup, track count, tempo, reverb, volume. Track list shows per-track note count and loop status. Constant name input with live validation (same `validate_constant_name()` as MIDI import). Player type selector (BGM/SE1/SE2/SE3). Auto-suggests constant name from the song label.
  - **Page 1 — Voicegroup Mapping:** Shows source voicegroup from the .s file. Checks if it exists in the target project — green message if yes, amber warning if not. Dropdown of all project voicegroups for remapping. Editable reverb, volume, and priority spinners pre-filled from the parsed .s file.
  - **Page 2 — Progress & Result:** Progress bar during copy + registration. Success shows constant name, label, file path, voicegroup remap note, and list of files modified.
- **Worker thread** (`_SImportWorker`): Copies .s file to `sound/songs/midi/`, rewrites internal labels to match the new constant name, rewrites voicegroup `.equ` reference if remapped, calls `register_song()` to update `songs.h`, `song_table.inc`, and `midi.cfg`. Cleans up copied file on registration failure.
- **Reload on success:** Song list repopulates and auto-selects the imported song.

**Files created/changed:**
- `ui/dialogs/s_file_import_dialog.py` — NEW: import wizard dialog
- `ui/sound_editor_tab.py` — added "Import .s..." button + `_open_s_import()` + `_on_s_imported()`

**Post-release fixes (2026-04-07):**
- .s import dialog changed from constant input to display name input with MUS_/SE_ prefix selector and auto-derived constant preview.
- Song rename/delete context menu added to Songs list. Rename updates all 3 config files + .s file. Delete checks cross-references and warns.
- Piano roll save/reload corruption fixed (loop_count=1→0). Multi-select drag fixed. Ctrl+A respects track filter.
- GM voicegroup generator deduplicated: empty slots get silent filler instead of identical square waves. Non-DirectSound instruments from all voicegroups included.
- Voicegroup rename button + double-click rename in Voicegroups tab.
- Additional files changed: `core/sound/song_table_manager.py` (rename_song, delete_song, find_song_references), `core/sound/gm_voicegroup.py` (_catalog_all_instruments, filler for empty slots, trimmed VG size), `ui/piano_roll_widget.py` (loop_count fix, multi-drag, track select_all), `ui/voicegroups_tab.py` (rename button, Generate GM dict key fix).
- Song context menu expanded: "Export .s File..." (save a copy) and "Replace with .s File..." (overwrite music data, rewrite labels, keep registration) added alongside Rename and Delete.
- GM voicegroup no longer pads to 128 slots — stops at the highest real instrument index.

---

## Technical Decisions (Resolved from Audit)

1. **Audio library:** `sounddevice` + `numpy` for the synthesis engine. This is a **new dependency** — the existing codebase only uses `QMediaPlayer` (for cry `.wav` playback), which can't do real-time synthesis. `sounddevice` wraps PortAudio, supports callback-based streaming, and works well with numpy for sample generation. `QMediaPlayer` stays for simple file playback; the synthesizer engine is separate.
2. **Sample format: RESOLVED.** The `.bin` files have a GBA `WaveData` struct header (16 bytes: type, status, frequency, loop_start, size) followed by signed 8-bit PCM data. The frequency field gives us the sample rate. The loop_start field tells us where sustain looping begins. No further research needed.
3. **Reverb implementation:** GBA reverb is a simple feedback delay buffer. A close approximation is fine — exact cycle-accurate emulation isn't needed for a preview tool.
4. **Thread model:** `sounddevice` callback-based streaming — the callback runs in a separate thread and pulls samples from a ring buffer that the engine fills. UI stays responsive.
5. **Parse vs. regex:** Song `.s` files are regular enough for line-by-line parsing. Voicegroups can be parsed with macro-aware regex. No need for a full assembler.
6. **Piano roll data model:** Internally, notes stored as a list of (tick, pitch, duration, velocity, instrument) tuples per track. The `.s` bytecode is generated from this on save. On load, bytecode is decompiled back into this format. Output should match `mid2agb` style since all existing songs were generated by it.
7. **MIDI library:** `mido` (Python MIDI library) for reading/writing MIDI files. Lightweight, well-maintained, handles all MIDI message types. **New dependency.**
8. **Code organization:** The existing `core/` directory is flat (no subdirectories). Given the Sound Editor has 10+ backend modules, we'll create `core/sound/` as a package — this is the first subdirectory in `core/`, but the alternative (10 files named `core/sound_*.py`) would be messy. Similarly, `ui/sound/` for the UI modules.

---

## New Dependencies

Add to `requirements.txt`:
- `sounddevice` — PortAudio wrapper for real-time audio streaming (synthesis engine)
- `numpy` — numerical arrays for audio sample generation and mixing
- `mido` — MIDI file reading/writing (for import/export)

Existing (no changes needed):
- `PyQt6` (includes QtMultimedia for QMediaPlayer — keeps handling cry playback)

## File Plan

New files to create (first subdirectories in `core/` and `ui/`):
```
porysuite/
  core/
    sound/
      __init__.py
      song_parser.py          — Parse .s song files into structured note/command data
      song_table_manager.py   — Read/write song_table.inc + songs.h + midi.cfg
      voicegroup_parser.py    — Parse voice_groups.inc into instrument definitions
      sample_loader.py        — Load .bin and .pcm samples, decode to PCM audio
      audio_engine.py         — Synthesizer + mixer + real-time playback
      sound_constants.py      — Shared constants (bytecode values, voice types, note names)
      song_writer.py          — Convert note data back to .s bytecode files
      voicegroup_writer.py    — Write modified voice_groups.inc
      midi_importer.py        — MIDI import: read MIDI, instrument mapping, mid2agb wrapper
      midi_exporter.py        — Export song data to MIDI format
      wav_exporter.py         — Render song to WAV file via audio engine
  ui/
    sound/
      __init__.py
      sound_tab.py            — Main Sound Editor container (sub-tab host)
      songs_tab.py            — Songs browser + editor + playback + loop editor
      instruments_tab.py      — Instrument browser + editor + preview
      voicegroups_tab.py      — Voicegroup browser + editor
      samples_tab.py          — Sample management (import/export/preview)
      piano_roll_widget.py    — QPainter-based piano roll canvas (keyboard, ruler, grid, notes, zoom)
      piano_roll_window.py    — Standalone piano roll window (track tabs, zoom sliders, grid snap, song info)
      track_controls.py       — Per-track sidebar (instrument, volume, pan, mute/solo)
      midi_import_dialog.py   — MIDI import wizard (file pick, instrument map, loop setup)
      audio_controls.py       — Shared transport bar, timeline scrubber, volume
      waveform_widget.py      — Waveform display for samples
      envelope_widget.py      — ADSR envelope visualization + editor
      loop_editor_widget.py   — Visual loop point markers on timeline
      piano_widget.py         — Clickable mini piano keyboard for instrument preview
```
