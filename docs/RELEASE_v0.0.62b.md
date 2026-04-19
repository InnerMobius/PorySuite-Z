# PorySuite-Z v0.0.62b

> pokefirered projects only.

A piano-roll and workflow release. Sound editor replacements now land atomically and survive cold restarts. The piano roll's Copy/Paste carries a note's volume and pan along with it instead of losing them on paste. A new per-track Max Volume action fixes SFX that stayed too quiet even with the track slider maxed. The Git tab's Switch to Branch now handles "local changes would be overwritten" in-app with plain-English Stash / Discard / Cancel buttons instead of forcing the user to a terminal. Oak's Parcel no longer jumps to `CCF0CCF0` because the items-header writer pads dense array gaps again. Stale `.s` files from removed songs no longer break upstream pulls — the build sweeps them automatically before every build.

## What's New in 0.0.62b

### Git Tab — Switch Branch Handles Conflicts In-App

`git checkout <branch>` refuses to run when local files differ from what's committed, printing "Your local changes to the following files would be overwritten by checkout" and aborting. Previously the app surfaced the raw error text in a warning dialog and left the user stuck — the only way out was a terminal.

Switch to Branch now parses the list of conflicting files out of git's error and pops a plain-English recovery dialog listing exactly what git is worried about. Three buttons:

- **📦 Stash & Switch** (safe) — runs `git stash push -u -m "PorySuite auto-stash before switching to <branch>"`, retries the checkout, and leaves the stashed changes recoverable from the Stash section.
- **🗑 Discard & Switch** (destructive, with confirmation) — runs `git checkout -- <files>` for each named file (batched 40 at a time to stay under Windows argv limits), then retries. Falls back to `git checkout -- .` only if git didn't name specific files.
- **Cancel** — no-op, stays on the current branch.

The recovery path works for the common case where PorySuite's own pipeline (items.json regeneration, .gitignore tweaks from project setup, a failed build that wrote partial output) caused the working-tree drift even though the user never edited those files by hand. Mirrored in `eventide/mainwindow.py` so EVENTide users see the same dialog.

### Sound Editor — Replace Song Atomic Write + Cache Hydration

Right-clicking a song and picking Replace With... previously wrote the replacement `.s` in-place with no protection against a partial write, no confirmation that disk contents matched what was written, and no refresh of the in-memory song cache. If the user had the replaced song open in the piano roll at the moment of replacement, the piano roll's dirty state could flush back over the new file and restore the old audio on the next save. In the worst case the replacement "worked" in the editor's preview but the old audio still built into the ROM.

Rewrote `_replace_song` to:

- Force-close any open piano roll window for the replaced song before touching disk.
- Write the new `.s` atomically — staged as `<dest>.tmp`, then `os.replace` to the final path.
- Back-date the companion `.mid` and `midi.cfg` by one hour (up from two seconds) so the Makefile rebuild rule `$(MID_ASM_DIR)/%.s: $(MID_SUBDIR)/%.mid $(MID_CFG_PATH)` cannot see the `.s` as stale and regenerate it from the old `.mid`.
- Read the file back and byte-compare against the intended bytes — surface a warning if the on-disk content does not match.
- Re-parse the new `.s` and install the fresh `Song` object in `_all_songs` so other parts of the app (the piano-roll reopen path, the category tree row count, the "Play" preview) see the replacement immediately.

Every step logs through the sound-editor logger so a failing replace is diagnosable from the log file.

### Piano Roll — Volume Round-Trip No Longer Clamps on Cold Reload

Users who slid a track volume past ~90, saved, built successfully, and then killed the app and reopened the project found the slider back at 90 on cold reload even though an F5 refresh showed it correctly. Every successive edit collapsed back to the old ceiling.

Root cause: the `.s` writer expresses VOL as `raw * _mvl / _mxv` where `_mvl` is the song's master volume and `_mxv` is fixed at 127. `_raw_vol(value, mvl)` reverse-computes `raw = round(value * 127 / mvl)` and clamps to 127 — so sliding to VOL 120 on a song with `_mvl = 90` writes `raw = 127`, which re-parses back to `127 * 90 / 127 = 90`. F5 reloaded the in-memory song (which still held the 120), but a cold start re-parsed the `.s` from disk and got 90 back.

`_on_track_volume` now auto-raises the song's `master_volume` to 127 whenever the user slides a track VOL past the current master. A master of 127 means `_raw_vol(v, 127) == v` exactly for every value — the full 0-127 VOL range round-trips losslessly through save/reload. Same fix applied to `_on_bpm_changed` for the tempo ceiling: when the BPM exceeds `(255 * tbs) // 2` and `tbs < 2`, `tempo_base` is bumped to 2 so the byte-encoded tempo field has the headroom it needs.

### Piano Roll — Track Volume Slider Updates Every VOL Event

`_on_track_volume` was only rewriting the first VOL command in a track. mid2agb routinely emits multiple VOL events (fades, automation), so setting the slider to 127 would bump VOL=127 at tick 0 and then the track would ramp right back down to whatever the next imported VOL event said a few ticks in. The slider looked like it worked, but the body of the track stayed at the original soft level.

The handler now rewrites every VOL event in the track, and inserts one at tick 0 if the track had none. Per-note fades are still achievable via the Note Properties dialog or by editing individual VOL events.

### Piano Roll — Copy/Paste Carries Volume, Pan, Bend, BendR

Notes and track control events (VOL, PAN, BEND, BENDR) are stored separately in the piano-roll model: notes carry pitch/tick/duration/velocity, while VOL/PAN/BEND events live on the track at specific ticks and apply to every note at or after that tick. Copy/paste only captured the note dicts, so pasted notes inherited whatever the track's current VOL/PAN state was at the paste position — usually the default, which made the paste sound quieter and flatter than the original.

`copy_selected` now also clips every control event whose tick falls inside the selection's `[min_tick, max_tick]` range on a track the selection touches, rebasing each event's tick relative to `min_tick`. `paste` re-inserts those events at the paste position on the active track. The status bar now reports both counts, e.g. "Copied 3 notes (+5 volume/pan events)".

### Piano Roll — 🔊 Max Volume (Per-Track)

Three multipliers stack in the GBA M4A output level:

> output = (master_volume / 127) × (track_volume / 127) × (note_velocity / 127) × envelope

The track-volume slider only controls the middle term. Imported MIDIs typically carry per-note velocities in the 60-100 range, so a note tops out at 55-80% of full output even with master + track VOL at 127 — which is why an SFX can stay quieter than music with multiple tracks summing in the mixer.

New 🔊 Max Volume button in the piano-roll toolbar boosts only the ACTIVE track:

- Every VOL event on the track → 127 (inserts one at tick 0 if missing).
- Every note's velocity on the track → 127 (and every note-command velocity in the track data).
- Master volume → 127 so the track's VOL=127 survives save/reload.

Other tracks are left alone. Canvas undo (Ctrl+Z) rolls back the note-velocity change. The user picks which track in the sidebar first, then clicks the button — the prompt names the track number so it's clear what's being boosted.

### Items Header — Gap-Slot Padding Restored, Oak's Parcel Crash Fixed

A fresh save from this build produced `src/data/items.h` containing only the items actually defined in `items.json` — 309 entries packed densely. `gItems[]` is indexed by `ITEM_*` constants, and the project's `include/constants/items.h` defines those constants up to index 375. Any code path that indexed past 308 (common: Oak's Parcel script at `ITEM_OAKS_PARCEL` = 349) read garbage past the end of the array, which the modern build's zero-fill EWRAM surfaces as the fill pattern `CCF0CCF0`. Result: a crash the moment Oak's Parcel logic touched the item entry.

The writer now reads `include/constants/items.h` to enumerate every `ITEM_*` constant, builds `index_by_const`, and iterates `0..max_index`. Each slot is emitted as either the real item block from `items.json` or an `[ITEM_NONE] = {}`-style placeholder. `gItems[]` is always dense in memory and every index that any constant can resolve to points at a valid entry. Regenerated `items.h` on the packaged copy at write time (376 entries, 308 real + 68 placeholders).

### Build Pipeline — Automatic Stale Song Sweep

Fresh upstream pulls (or projects built after a voicegroup rename) would fail to link with "undefined reference to voicegroup013" pointing at leftover `.s` files from songs removed in the source tree. The `.s` files had outlived their `.mid` sources because audio_rules.mk only regenerates them when the `.mid` or `midi.cfg` changes, not when the `.s` references a voicegroup the project no longer defines.

`_run_make` now runs a pre-build sweep before every build:

1. Parse `sound/voice_groups.inc` for valid `voicegroupNNN::` labels.
2. Walk `sound/songs/midi/*.s` and scan each for `.equ <name>_grp, voicegroupNNN`.
3. Delete any `.s` that references a voicegroup not in the valid set, logging each removal.

No terminal invocation, no `make clean-assets` required. Upstream pulls build clean and users never see the linker failure. Failures inside the sweep are non-fatal — logged and the build continues.

## Files of Note

- Updated: `ui/sound_editor_tab.py` — `_replace_song` rewritten for atomic write, cache hydration, piano-roll pre-close, byte-verify, deeper log trail.
- Updated: `ui/piano_roll_window.py` — `_on_track_volume` bumps every VOL event and auto-raises master; `_on_bpm_changed` auto-raises tempo_base; new `_on_boost_all` per-track max; preview-volume slider relabel.
- Updated: `ui/piano_roll_widget.py` — `copy_selected` / `paste` now carry VOL/PAN/BEND/BENDR events; `_clipboard_ctrl` attribute added.
- Updated: `ui/mainwindow.py` — `_git_checkout_branch` detects "would be overwritten" and opens `_show_switch_branch_conflict_dialog` with Stash / Discard / Cancel; `_prune_stale_song_s_files` added and invoked from `_run_make`; items-header writer pads `gItems[]` with `ITEM_NONE` placeholders across every constant's index.
- Updated: `eventide/mainwindow.py` — same branch-conflict recovery dialog mirrored for EVENTide.
- Updated: `ui/dialogs/git_panel.py` — Switch-to-Branch tooltip updated to mention the in-app Stash/Discard dialog.
- Updated: `core/app_info.py` — VERSION bump to `0.0.62b`.
- Regenerated: `pokefirered/src/data/items.h` — 376 dense entries (308 real + 68 `ITEM_NONE`).
- Deleted: `pokefirered/sound/songs/midi/*.s` referencing `voicegroup013` — eight stale files swept.

## Known Limitations

- **Max Volume is one-way inside the piano-roll session.** The undo stack on the canvas rolls back note-velocity changes, but the master-volume and per-track VOL bumps are not captured by the canvas undo. If you need to back out a Max Volume action completely, close the piano roll without saving.
- **Stash & Switch does not auto-restore.** After a branch switch, the stash entry stays in the Stash section for the user to decide. Restore Latest Stash can be used on the new branch if the differences are still relevant; otherwise drop the stash from git's command line or leave it to age out.
- **Items-header placeholder padding assumes `include/constants/items.h` is authoritative.** If the project edits `items.h` by hand without updating the `#define`s, the writer will re-pad according to the constants file and the hand edit is lost on the next save. Edit constants, not the generated header.
- **The stale-song sweep only looks inside `sound/songs/midi/*.s`.** Other orphaned audio assets elsewhere in the tree are not swept. If the user adds songs to additional directories, they'll need to be included in the sweep helper.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
