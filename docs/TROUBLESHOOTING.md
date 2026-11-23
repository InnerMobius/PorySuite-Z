# Troubleshooting PorySuite-Z

This guide covers common setup issues and how to reset the application if something goes wrong. Written in plain English.

## Pokemon graphics

### "Import Normal + Shiny Set" gives a smeared/wrong palette
This happens when one sprite in the set was exported as **RGBA** (with an alpha channel) while its siblings are RGB — the two convert to indexed colours differently and the shared palette comes out wrong. The importer now flattens any alpha channel to plain RGB first, so it's handled automatically. If you still see it, re-export the odd sprite as RGB (or indexed) so the whole set matches.

### Unown / Deoxys / Castform can't be edited as one mon
They ship as multi-form bundles (letter/weather/version forms). Select the species on the **Info tab** and use the **Normalize this species…** button to collapse it to a single normal, editable sprite. Rebuild afterward (Make) to see it in-game. Once done, the button shows "✓ Already a single sprite".

## Region Map editor

### I cloned a region and now the new region's tilemap shows up in-game when the player is in the source region's territory
The clone shipped at first as a true duplicate, copying both the artwork AND the MAPSEC grid. The engine's player-region lookup matches the first region in enum order that owns the player's MAPSEC — so the clone (added later) silently shadowed the source for every shared MAPSEC.

Fixed: Clone now copies the **artwork only**. The new region's MAPSEC grid starts blank — you paint which MAPSECs the new region should own. To recover an existing broken state, **delete the clone** (Delete Region → Save), then re-clone after the fix is in. The new clone will have the source's artwork as a starting point and a blank grid.

If you wanted a truly empty region from the start, use **Create New Region**, not Clone.

### A tilemap I edited looks fine in the Tilemap Editor but renders with wrong colors in-game
Region-map style sheets are 8bpp PNGs that bake multiple sub-palettes side-by-side, but the GBA renders them as 4bpp tiles with per-entry attr-palette selection. Older builds of PorySuite-Z saved tilemap entries with `palette=0` regardless of which sub-palette the artwork was drawn for, so the GBA rendered with the wrong colors.

Fixed in two parts:
1. The canvas now renders in GBA-accurate mode, so what you see in the editor matches the GBA.
2. Picking a tile from the picker auto-detects the right sub-palette and sets the spinner.

For tilemaps already saved with wrong palette bits: open the affected `.bin` in the Tilemap Editor and click **Auto-Fix Palettes** in the toolbar. It scans every tile and rewrites the stored palette bits to match the dominant 16-color range in the artwork. One undo step.

### Switching the Tilemap Editor's palette source from "Auto .pal" to "PNG colors" shows the old colors until I toggle twice
Fixed in 2026-05-11. The picker's per-paint recolor was leaking the temporary color table back to the source `QImage` (a PyQt6 detach edge case in `_recolor_tile`). The fix switches the recolor functions from `QImage(tile)` to `tile.copy()`, which produces an independent deep copy. The Toggle-twice workaround is no longer needed.

### A `.bin` file is bigger than it should be after I scrubbed the W/H spinners
Fixed in 2026-05-11. The live rewrap path (`_on_dimensions_changed`) used to pad `self.entries` to fill `new_w * new_h` after a ceiling-division — scrubbing the spinner up by one and back grew the entry list each round trip, and `Tilemap.save()` then wrote those surplus entries. At runtime the engine's `LZDecompressVram` would overflow the BG tilemap buffer and crash on the first BG redraw.

Two fixes together: the rewrap no longer touches `entries` at all (only the dimensions change), and `Tilemap.save()` now writes EXACTLY `width × height × 2` bytes — padding short, truncating long — so a stale entry count can't drive the file size. To repair a `.bin` already inflated by the old behavior: open it in the Tilemap Editor and hit Save. The new save logic truncates automatically.

### My textbox1.pal / textbox2.pal got the same flat color list written to both files (or my .gbapal got picked over the .pal split)
Fixed in 2026-05-11. `discover_assets` used to pick whichever same-stem pal file came first alphabetically AND had > 16 colors, which meant combined `<stem>.gbapal` build artifacts won over the canonical `<stem>1.pal` + `<stem>2.pal` source split. Saves then either wrote back to the wrong file or wrote the full 32-color flat list to both source files.

`discover_assets` now detects the numbered-pal-set pattern (two or more `<stem><digit>.pal`) and prefers it over any same-stem combined file. `_flush_palette_edits` writes file N's 16-color slice to file N, not the full list to every file. Recovery: either restore the two `.pal` files from git, or open the tilemap in PorySuite, edit one swatch, and save — the per-file slice logic writes the right content from then on.

### Region rename / delete left my custom code referencing the old enum constant
Both Rename Region and Delete Region now scan the project for external references to the affected `REGIONMAP_<NAME>` constant before applying. If references are found in `src/`, `include/`, or `data/`, the dialog lists them with `path:line` snippets and warns the build will fail until you update them manually. Cancel the op, fix the references, then retry.

In vanilla pokefirered no code outside `region_map.c` references the region constants by name, so deletion is build-safe out of the box. Custom hacks vary.

---

## Overworld Editor — Frame Cycle

### A Frame Cycle sprite freezes (or races through frames) after the player talks to it
Fixed in two parts:

1. **Movement type (fixed 2026-05-18).** The Frame Cycle entity MUST be placed in Porymap with `MOVEMENT_TYPE_FRAME_CYCLE`, not `MOVEMENT_TYPE_NONE`. The first time you press the Frame Cycle button, PorySuite installs the new movement type into the engine automatically and the dialog tells you which one to pick in Porymap. If a sprite was placed with `MOVEMENT_TYPE_NONE` before this fix, just switch its movement type in Porymap and save — no rebuild trick needed.

2. **Random preset only — duration overflow (fixed 2026-05-19).** The engine's per-frame hold value is a 6-bit field (maximum 63). The Random Frame Cycle dialog used to allow holds up to 120 ticks, and any value above 63 silently wrapped on its way into the ROM (64 became 0, 100 became 36, 120 became 56). A wrapped-to-zero hold flashed a frame past instantly, so the cycle looked frozen or sped up. The dialog's "fastest" and "slowest" spinboxes are now capped at 63 ticks and the codegen clamps every duration to the same range. If you have a Random table generated before this fix and it still misbehaves, open the Frame Cycle dialog on that sprite and click OK to regenerate the table within the new range.

### "Frame Cycle…" button says my sprite has 0 frames
Fixed 2026-05-18. The frame-count helper used to require Pillow (PIL), which isn't shipped in `requirements.txt`. Without it, every sprite came back as 0 frames. Frame counting now uses PyQt6's `QImage`, which is already a hard dependency. Update PorySuite and try the button again.

---

## Sound Editor

### A sound I imported or edited won't build (or the game has no/garbled audio for it)
As of 2026-06-21 this shouldn't reach a build at all. Every time the Sound Editor writes a song — saving in the Piano Roll, importing a `.s`, or replacing one — it runs two checks before the file is allowed to touch disk:

1. A fast structural check for the known ways a sound breaks the build (bad note velocities, wrong pointer types, a missing or wrong voicegroup link, double-colon labels).
2. **GATE 1** — the *real* GBA assembler (the same one your build uses) actually assembles the file. If it wouldn't build, the save is refused and your existing working file is left untouched; an import is rolled back to exactly how it was.

If you see a "blocked" message, it's telling you the sound as written wouldn't build — fix what it names (or pick a different voicegroup/instrument) and try again. If you have an older project with a sound that was corrupted before this safety net, re-import the original `.s` to recover it. (If your machine can't reach the assembler, the structural check still runs.)

### The track volume slider snaps back to a lower number, or a track won't get as loud as I set it
That's the slider being honest. A track's volume can never exceed the song's **master volume** — the game clamps it. Older builds let the slider go to 127 even on a song whose master was, say, 90, so anything above 90 silently played (and reloaded) as 90. Now each track's slider stops at the song's master volume, so what you see is what plays. To make a track louder than that, raise the **master volume** (in the song header) or use the **🔊 Max Volume** button, which raises master to 127 and re-opens every track's slider to the full range.

### I changed a sound's volume and now it's broken / distorted / wrong in-game (but it's fine in the editor)
As of 2026-06-28, volume edits go through the game's own sound converter (mid2agb), so this shouldn't happen. Older builds wrote the assembly with PorySuite's own converter, which encoded some overlapping notes differently from the game and applied the master volume twice (making the sound quieter or distorted on each save). Now editing the master Volume field or the per-track slider regenerates the sound through mid2agb — exactly what the build produces — so the in-game result matches the editor. If an older project still has a sound corrupted this way, open it in the Sound Editor and re-save (or nudge its volume) to regenerate it cleanly.

### A sound effect plays the wrong instrument or is silent in-game, even though it looks right in the editor
The instrument a sound uses lives in its `.mid` (the source the build compiles), not just the `.s`. If a sound's `.mid` held a stale instrument number, every build regenerated the `.s` with that wrong instrument — so hand-editing the `.s` kept reverting. Fixed by writing the chosen instrument into the `.mid`. To repair such a sound: open it in the Sound Editor, set the instrument you want, and save — that writes the `.mid`, and the build then renders it correctly. (Example: the bomb-pickup chime was silent because its `.mid` pointed at a silent voice; it now points at the audible one.)

### The Piano Roll says "'song' was NOT saved — nothing on disk was changed"
The save builds the song with the game's own converter first and refuses if the result would not play what the editor shows, or would change something you did not edit. Nothing is written when that happens, so your files are safe. The message says why. The common cases:
- **"PorySuite was updated while it was running"**: restart PorySuite, reopen the song and make the edit again.
- **"….mid was changed after ….s was built"**: another program changed the song's MIDI. Use **Refresh from MIDI** in the Sound Editor (or build the project), reopen the song and make the edit again.
- **A track move was refused**: the song's MIDI keeps two of those tracks inside one MIDI track, or the move would put the first MIDI track (which holds the tempo and loop markers) behind another. Move a different track, or rearrange the tracks in a MIDI editor.

### I edited a song in another program (such as PoryDAW) and PorySuite still shows the old version
Some editors write only the song's `.mid`; the `.s` PorySuite shows is rebuilt from it by the next game build. Select the song in the Sound Editor (or switch back to PorySuite): when its `.mid` no longer matches, a note appears with a **Refresh from MIDI** button. It rebuilds just that song, the way the build would, and never changes the `.mid` or `midi.cfg`. The old `.s` is kept as a backup in PorySuite's cache folder. If the note says the `.s` was changed instead, refreshing replaces those `.s` changes, so it asks first. Don't edit the same song in two programs at the same time.

### "Song changed on disk" appears while the Piano Roll is open
Another program changed the song's files. Choose **Yes** to reload (refreshing from the MIDI when that is what changed); any unsaved edits in the window are discarded, and the box says whether you have any. Choose **No** to keep working: saving is refused until the song is reloaded, so nothing on disk can be overwritten.

### A save failed with "Access is denied"
Another program (often a virus scanner or a file-sync tool) held the song's file open. PorySuite retries for a moment before giving up; if it still fails, every file is put back as it was, and your edits stay in the window. Close the other program, or wait a moment, and save again.

### An empty track I added is gone after saving
The game's converter writes no track that has no notes, so a new track is only saved once it has at least one note. For the same reason, deleting every note of an existing track makes the save refuse (the song would lose a track); remove the track with the track list's **-** button instead.

### After a git pull, building from a terminal rebuilt every song
In a stock pret project (FireRed, Emerald, pokeemerald-expansion), every song depends on the one shared `sound/songs/midi/midi.cfg`, so any change to it — including a pull that only rewrote its line endings — makes `make` rebuild every song. Building with PorySuite's **Make** button avoids this: before the build it checks which songs a `midi.cfg` change really affects and lets only those rebuild. Real setting changes and edited MIDI files are always rebuilt. A terminal `make` run while PorySuite is closed cannot be checked first; it behaves as the stock project always has. PorySuite never changes your Makefile; if your project's song rules are customised in a way PorySuite does not recognise, it leaves the build to decide and only protects songs written in PorySuite.


### Song Build Protection: keeping terminal builds, `make clean` and git from losing music
PorySuite's **Make** button checks every song before it builds. A build started from a **terminal** does not
go through PorySuite. In a stock pret FireRed project it follows file dates alone, so it can overwrite an edit
made directly in a `.s` after a pull re-dates the `.mid` or `midi.cfg`, or ship an old `.s` when one commit
changes the `.mid` and touches the `.s`. `make clean` deletes every song `.s`, including one that is a song's
only real copy.

**Tools → Song Build Protection...** adds an optional guard to the project's own build, so it works from a
terminal, on other computers and with PorySuite closed. The window shows every change as a diff before writing
anything, and asks you to confirm. It:
- adds two files to the project folder, `song_guard.sh` and `song_guard.mk`;
- changes three lines: the song rule in `audio_rules.mk` runs the guard instead of the converter, an `include`
  line is added above it, and `make clean` in the `Makefile` calls the guard instead of deleting every `.s`;
- adds one comment line, a **protection record**, to the top of each song `.s`, recording which `.mid` and
  settings it was built from (the music does not change). Git lists those songs as changed: commit them with
  the build changes so protection also works on other computers;
- optionally (**Include generated song files in Git**) changes one line of `.gitignore` so song `.s` files are
  kept in Git history. That makes them easy to review and recover, but on its own it does not stop a build from
  overwriting a song, and switching it off later does not untrack files Git already has.

Protection cannot bring back music that was never saved or committed.

The original build files are backed up first. If your build files are not a shape the setup recognises, it
says why and changes nothing. Only stock-style pret FireRed projects have been tested.

With protection installed, every build (`make`, `make modern`, a pull, a branch switch):
- rebuilds a song whose `.mid` changed, whatever the file dates say;
- keeps a `.s` that was edited after it was built, as long as its `.mid` and settings did not change;
- keeps a song written in PorySuite whose `.mid` is still an empty stand-in, and warns if git ignores its `.s`
  (the music would exist on that computer only);
- **stops with a message** when a song's `.s` and `.mid` both changed. Nothing is changed. The message names
  the song and how to choose:
  - `sh song_guard.sh keep sound/songs/midi <song>` keeps the `.s`;
  - `sh song_guard.sh rebuild sound/songs/midi <song>` rebuilds from the `.mid` (the `.s` is copied to
    `song_guard_backups/` first);
  - or press Make in PorySuite, which asks the same question.
- `make clean` deletes only `.s` files that their `.mid` rebuilds exactly, and lists the ones it kept.

When git stores song `.s` files with Unix line endings (pret's `.gitattributes` does), the guard writes rebuilt
songs that way too, so rebuilding a song no longer makes git list it as changed when only its line endings
differ.

Nothing in the guard judges by file dates. A broken `.mid`, an unknown setting or a missing converter stops
the build without touching the `.s`. `sh song_guard.sh status sound/songs/midi` lists what each song's record
says.

**If you don't install it,** existing projects keep working as before. PorySuite's editors and Make button
stay safe; only builds started from a command prompt and `make clean` keep stock behaviour (judging songs by
file dates). The Make button's log names any songs whose `.s` holds work that a terminal
build could erase.
The Sound Editor's Songs page also shows a line at the top: a quiet note when the project is protected or
when every song can be rebuilt from its `.mid`, and an amber warning naming the songs whose music is only in
their `.s`, with a button to the setup. PorySuite cannot stop a terminal command itself; the warning only tells
you which songs a terminal build could erase.

**To remove it,** open the same window and choose **Remove protection**. Your original build rules come back
exactly and the two files are deleted. The protection records already added to song files **remain**, but
they are no longer enforced during terminal builds, so removal does not restore every song file to how it was
before installing. They are harmless comments and keep their meaning if you install protection again.

---

## Git — Pull / Push

### I pulled and my uncommitted work disappeared
As of 2026-06-28, Pull saves your uncommitted work to a **stash** before it syncs to the remote, so it isn't lost. Older builds ran a hard reset that permanently wiped anything you hadn't committed. After a pull, open the Git panel's **Stash** section (or run `git stash pop`) to bring your pre-pull work back. Note: this saves what's on disk — edits still open in an editor tab that you haven't saved are NOT captured, so Save first.

### I pushed to "master" (or another branch) but my work didn't go up
As of 2026-06-28, choosing a branch in the Push dialog pushes **your current branch's work** to that branch (e.g. promoting your feature branch to master) and fast-forwards it. Older builds pushed the stale local branch of that name instead — so "push to master" sent the old master, not your work. If the target already has commits your branch doesn't, PorySuite asks before force-pushing. The dialog label now reads "Push '<your branch>' → branch: <target>" so it's clear where your work goes.

### Push said it worked but nothing changed on the remote
If your branch already matched the remote, the push had nothing to send — the dialog now says "already up to date — nothing pushed" instead of a misleading success. If you *expected* to push work and see this, you're probably on the wrong branch: the Git panel's status line warns "⚠ Unpushed work on other branches: <name>" when your commits sit on a branch you're not currently on. Switch to that branch and push.

### My work seems to have vanished after switching branches
It's almost certainly safe on another branch. The Git panel's status line lists any branches holding commits not yet on the remote ("⚠ Unpushed work on other branches: …"). Committed work lives in git's history even when the branch you're viewing doesn't show it — switch to the named branch to find it. Committed work is essentially never lost; only *uncommitted* edits can be, and the pull-stash above now protects those.

---

## Shop Editor

### I deleted a shop and now the game crashes when I talk to a certain NPC
Deleting a shop removes its item list, but on purpose it does **not** touch the `pokemart` command in the NPC's script that opened it — so that command now points at a list that no longer exists, which crashes the game at that NPC. The delete dialog warns you about this when the shop is still wired to an NPC, and offers to jump you to EVENTide to fix it. Open that map in EVENTide, find the NPC, and remove (or re-point) its `pokemart` command, then save.

### I created a new shop but it doesn't open in-game
A new shop starts as just an item list, flagged "(not wired)" in the list. A shop only opens in-game when an NPC's script runs a `pokemart` command pointing at it. Add some items, then click **Open in EVENTide** to jump to that map, add a `pokemart <your list label>` command to an NPC (e.g. after `lock` / `faceplayer`), save in EVENTide, and rebuild. The shop will then open when the player talks to that NPC.

### The shop list shows item codes like ITEM_POKE_BALL instead of names
Update PorySuite — as of 2026-06-21 the Shop Editor shows the friendly item names from your project (the same names the Items editor uses). Hover a row to see its underlying constant. If a row still shows a raw code, that item isn't in your project's item data file; it still saves correctly.

---

## Tilemap Editor — palette export

### Exporting palettes saved them as palette_0.pal / palette_1.pal instead of the real name
Fixed 2026-06-21. "Export palettes" (the toolbar button and the palette panel's right-click Export options) used to name every exported file `palette_00.pal`, `palette_01.pal`, … regardless of the source. It now names them after the palette's real source file. For a background like `graphics/item_menu/bg.bin` whose palette is one combined file (`bg.gbapal`, holding 3 sub-palettes), the separate-16-color export produces `bg_0.pal`, `bg_1.pal`, `bg_2.pal`. For an asset that uses several separate `.pal` files, each exported file keeps its own name.

Note: if your goal is just to save palette edits back into the project, use **Save (Ctrl+S)** — it writes your edits straight back to the original palette file the build reads (e.g. `bg.gbapal`). The separate-file export is for pulling the palettes out as individual `.pal` files (the build won't read those).

---

## v0.0.63b — recently fixed

### `src/data/items.json` keeps showing as "modified" in Git after a pull (even without editing anything)
Fixed in v0.0.63b (2026-04-20). The fourth and final root cause was an internal diff bug: on F5 / cache rebuild, the "original" copy of the items data was left in a different shape than the working copy, so the save path always thought something had changed and rewrote the file. Both copies are now normalized through the same helper. If you still see `items.json` drift on a clean tree, report it with a screenshot of the Git panel.

### Build fails with `undefined reference to voicegroup013` (or similar) after upstream pull
Fixed in v0.0.63b. The previous sweep (v0.0.62b) removed stale `.s` song files but left the compiled `.o` objects in `build/…/sound/songs/midi/`, and Make skipped rebuilding because the `.o` was newer than the `.mid`. The sweep now runs in two passes: stale `.s` AND any `.o` that references a voicegroup no longer in `voice_groups.inc` are deleted before every build. Nothing to do — it runs automatically.

### Bulbasaur (or whatever species is selected first) lights up amber dirty on every build
Fixed in v0.0.63b. The build log was auto-wired into the species tab's dirty detector, so every log line marked the first species row edited. Excluded the log widget from the wire-up.

---

## Unified Editor (Phase 1+2) Known Issues

### The toolbar icons are colored squares
That's expected — they're placeholders. Replace them with your own 32x32 PNG files in `res/icons/toolbar/`. The filenames are: save.png, make.png, make_modern.png, pokemon.png, pokedex.png, moves.png, items.png, trainers.png, starters.png, events.png, maps.png, layouts.png, regionmap.png, ui.png, config.png, play.png.

### Dynamic Palettes says "Active" but sprites have broken colors
This happens when you replaced the pokefirered folder (e.g. with a fresh copy) after the Dynamic OW Palettes patch was applied. The old marker file (`.porysuite_dowp_enabled`) stayed behind from the previous folder, so PorySuite thought the patch was still applied. **Fixed in 2026-04-06**: PorySuite now checks the actual C source code for DOWP signatures instead of relying on the marker file. If the source is unpatched, the stale marker is automatically removed and the "Enable Dynamic Palettes" button re-enables. Just restart PorySuite after replacing the folder and the button will be available again.

### The trainer Dialogue tab says "No battle dialogue found"
This happens when a trainer has no dialogue in disk `.inc` files, no pending in-RAM dialogue, and no live edits from the Event Editor. Normally you won't see this:
- **New trainers** automatically get default Intro/Defeat/Post-battle text seeded into RAM as a `(Pending — not yet placed on a map)` entry the moment you add them. Default text comes from Settings → Trainer Defaults.
- **Trainers placed on a map via the Event Editor** show up as `(live — unsaved edits)` here as soon as you add the trainerbattle command — no save needed.
- **Trainers placed on a map and saved** read from the map's `text.inc` using labels that contain the trainer's camel-case name (e.g. `PalletTown_Text_HikerFatManIntro`) or match the labels listed on the `trainerbattle` command in scripts.inc.

If you still see "No battle dialogue found," check that the trainer's constant is actually referenced somewhere — use the search (Ctrl+Shift+F) from the Event Editor tab to find `trainerbattle TRAINER_YOUR_CONSTANT`.

### My trainer class name/money/sprite edit disappeared
Fixed (2026-04-05). Previously, switching to another tab and back to the Trainers tab silently re-loaded trainer class data from disk and discarded any pending edits. Now the class editor preserves its dirty state across tab switches. If you still see this happen, save first before switching away, or report it.

### Name Decapitalizer changed species names in the UI but the ROM still showed ALL CAPS

Fixed (2026-04-05). The decapitalizer was updating the in-memory `speciesName` field and the PorySuite tree widget, so the app looked correct — but the compiled ROM still showed ALL CAPS names (BULBASAUR, CHARMANDER, etc.). The root cause was that the game reads species display names from `src/data/text/species_names.h` (`gSpeciesNames[]`), and that file was never written by the decapitalizer. Only the individual Rename tool (`core/refactor_service.py`) ever touched it. Fix: added `_write_species_names_header()` to `ui/name_decapitalizer.py` — on Apply, it patches `species_names.h` directly. Species names now persist to the ROM without needing a separate save step.

### Saving a map in EVENTide changed scripts I never touched (or broke the build)

Fixed (2026-10-24). Saving a map used to rewrite more than the map, in five different ways. If a project was saved
through EVENTide before that date, re-check it against git — the damage is already on disk and a newer build of
PorySuite-Z will not undo it.

- **The build stopped working with an error on a `delay` line.** Movement timing steps (`delay_4`, `delay_8`,
  `delay_16`) were split in two and written back as `delay _16`, which is not a command.
- **Some dialogue started behaving differently.** The `message` command was rewritten as `msgbox`. They look alike
  but `msgbox` waits for a button press and closes the window, so anything that needed the window left open —
  a multichoice list, for instance — broke.
- **A warp sent the player to the wrong tile.** Warps written with four values lost the last one.
- **Scripts gained `lock`, `release` or `end` lines nobody added.** The worst case is a `lock` added to a map's
  on-entry script, which freezes the player as soon as they walk in.
- **Shared script files lost their comments.** Opening a map whose NPCs call a shared script and pressing Save
  rewrote that shared file too, even though nothing in it was edited.

All five are now covered by an automatic check that re-saves every map in the test project and fails if a single
script line changes.

**How much of your project could be affected is bounded.** A save only rewrites the map's own event scripts, its
`*_MapScripts::` table, scene scripts the editor generates, and any move route you edited in the Move Route dialog.
Every other block in the file is copied through untouched. So anything living in a block a save never writes —
movement routes in particular — was never at risk, whatever the list above says.

**How to check your own project.** The reliable marker that a map was saved through an affected build is a stray
`end` line on its map-script table, after the `.byte 0` that terminates it. A clean copy of the game has none, so
any you find are from this bug. Skip blank lines when you look — there can be several between the two. Those same
maps will also have lost their tab indentation.

For the damage that actually matters — `message` turned into `msgbox` — the tell is a bare `waitmessage` line
directly under an `msgbox`. `msgbox` already waits internally, so that `waitmessage` is a leftover from a `message`
command and the text box is now closing earlier than the script intended. Compare against a clean copy of the game
to confirm before changing anything.

**Repairing it.** Use **Tools → Repair Map Script Damage…**. It checks every map in the project and shows you what
it found — counts, which maps, which lines, and what each one is — before changing anything. Back up or commit your
game project first, then press Repair. Running it twice is safe; the second run finds nothing.

Restoring the lost indentation is a separate checkbox. It's cosmetic — the game builds and plays the same either
way — but it's what makes those files readable in git again. Only maps with real damage are touched; maps that were
simply written without tabs upstream are left alone.

Two things it deliberately will not do. It won't touch an `end` that follows a `goto`, because that's normal and a
clean copy of the game has 432 of them. And it never adds back a command that's missing compared to a clean copy —
anything absent is treated as your own edit, not damage, so stripping a vanilla system out of your hack stays
stripped. It also lists any `lock` or `release` lines that look like they were added for you, but never changes
them: those can't be told apart from your own edits, so they're yours to look at.

### Saving in EVENTide wiped a change something else had made

Fixed (2026-10-24). EVENTide loads a whole map into memory and writes all of it back when you save. If anything
else had changed those files in between — Porymap saving, a git pull, a text editor, Tools → Repair Map Script
Damage, or a second copy of the app — saving in EVENTide wrote its older copy straight over the top, with no
warning at all.

EVENTide now remembers exactly what each file looked like when it read it, and checks again before writing. If
something changed underneath it you get a dialog naming the files, with three choices: **Reload from disk** (take
the other change and lose your unsaved edits here), **Save anyway** (keep yours and lose theirs), or **Cancel**.
Cancel is the default, and nothing is written until you pick.

This matters most when you have Porymap open at the same time, or just after running the script repair. If you see
that dialog, it is telling you something real — read which files it lists before choosing.

### External changes from Porymap wiped my unsaved work
Fixed (2026-04-05). Previously, if Porymap saved a map (or any external tool modified `map.json`/`scripts.inc`) while you had unsaved Event Editor edits, the file watcher silently reloaded from disk and your edits were gone. Now you get a Save / Discard / Cancel dialog when an external change is detected during unsaved edits.

### My external edits to src/credits.c got wiped by PorySuite
Fixed (2026-04-05). The Credits editor now takes a snapshot of `src/credits.c` and `src/strings.c` modification times when you load the tab. If either file is edited outside PorySuite-Z before you hit Save, you get a Save / Discard / Cancel dialog instead of a silent overwrite.

### Ctrl+E in Porymap doesn't bring PorySuite to the foreground
Fixed (2026-04-05). Pressing Ctrl+E (or clicking an event in Porymap) now raises the PorySuite-Z window and logs which event was picked (or "no event within 2 tiles" if the cursor wasn't on an event).

### PorySuite-Z complains about Porymap command files or log errors every half-second
This means the Porymap install is missing the patches PorySuite-Z expects. The installer normally drops a `.psinstalled` marker file next to `porymap.exe`. If that marker is missing, PorySuite-Z treats Porymap as "stock" and avoids commands it can't handle. To fix, re-run Tools → Install Porymap to rebuild with patches.

### An item (or flag/var/trainer) I renamed in PorySuite doesn't show up in the Event Editor dropdowns
Switch to an EVENTide page (EVENTide / Maps / Region Map) from any PorySuite page — the constants cache refreshes on that switch. If the rename hasn't been saved to the header files yet, you still need to Save-All first. In-memory-only renames that haven't touched disk require a dedicated bridge that isn't implemented yet.

### Dialogue text shows {PLAY_BGM} and other commands in blue
That's intentional — those are text commands that are part of the game's text system, not script leakage. `{PLAY_BGM}{MUS_ENCOUNTER_GYM_LEADER}` sets the battle music. `{PLAYER}` inserts the player's name. They're highlighted blue to distinguish them from regular text. Right-click in a text box to insert commands from a menu.

### Character counter shows amber/red on dialogue text
The GBA text box can display 36 characters per line. The counter turns amber when you're past 85% (31+ chars) and red when you exceed 36. Characters inside `{COMMANDS}` don't count toward the limit — they compile to binary opcodes, not visible text.

### Play button says "No .gba file found"
You need to build the ROM first. Click Make or Make Modern, or press Ctrl+M / Ctrl+Shift+M. Once the build finishes, the Play button will work. You can change which .gba file it launches in Settings > Build & Play.

### Changes in PorySuite don't show in EVENTide dropdowns
Save first (Ctrl+S). The shared data layer sends a signal to refresh EVENTide's constants after saving. If it still doesn't show, use File > Refresh (F5) to reload everything from disk.

### New overworld sprite doesn't appear in the Event Editor's graphic dropdown
New sprites added via the Overworld Graphics tab push their `OBJ_EVENT_GFX_*` constant directly into the Event Editor's dropdown through cross-tab sync — no save or refresh needed. If the sprite still doesn't appear, switch to any EVENTide page and back to trigger a constants refresh, or use File > Refresh (F5).

### Deleting a song still causes build failures
Fixed (2026-04-08). Two rounds of fixes:

**Round 1 (2026-04-07):** `delete_song()` wasn't deleting `.mid` files or `.o` artifacts. Also `write_song_table()` was missing the `dummy_song_header` footer. Both fixed.

**Round 2 (2026-04-08):** Orphaned registrations — songs deleted from the UI (or with failed imports) could leave entries in `song_table.inc`, `songs.h`, and `midi.cfg` with no `.s` file on disk. Build failed with "undefined reference" errors. Root cause: failed MIDI imports cleaned up the wrong filename (original name instead of label-based name), leaving stray `.mid` files. Also, no mechanism existed to detect registrations without matching `.s` files. Fix: (1) `import_midi()` now cleans up `label + ".mid"` on failure. (2) New `cleanup_orphaned_songs()` function scans for MUS_* entries with missing `.s` files and removes them from all config files. (3) Sound Editor runs this cleanup automatically on every project load.

### Piano roll notes longer than 4 beats get cut short
Fixed (2026-04-07). Notes longer than 96 ticks (4 beats at 24 ticks/beat) were silently truncated to N96 — the maximum single-note duration in GBA M4A. Now the song writer generates TIE + EOT commands for long notes, which is exactly what mid2agb does. Notes sustain for their full drawn duration.

### Piano roll Mute/Solo buttons hidden behind scrollbar
Fixed (2026-04-07). The track sidebar was 220px wide, and when the vertical scrollbar appeared (6+ tracks), it covered the M and S buttons on each track row. Sidebar widened to 240px.

### Piano roll save corrupts songs with PATT subroutines (broken structure, missing notes, wrong instruments)
Fixed (2026-04-08). Songs using PATT/PEND subroutine calls (mid2agb's way of compressing repeated sections) were destroyed by the piano roll save — notes went missing, instruments changed, dead code appeared after FINE. Root cause: the save tried to insert flattened notes back into the PATT structure, which is impossible. Fix: PATT/PEND are stripped on save and the track is written as a clean linear sequence. The music content is identical, just represented differently (explicit repetitions instead of subroutine calls).

### Piano roll save halves volume every time you save
Fixed (2026-04-08). Volume dropped from 90 to 63 to 44 on successive saves. The parser evaluated `127*mvl/mxv` to get the byte value (89), but the writer wrote `89*mvl/mxv` — applying the multiplier again. Same issue with TEMPO. Fix: reverse-evaluation helpers recover the original raw value before writing.

### Piano roll BEND effects stop working after the song loops
Fixed (2026-04-08). The piano roll sequencer was resetting pitch bend state to zero every time the playback looped. The real GBA engine doesn't do this — BEND state carries through GOTO loops. Now the piano roll matches the GBA behavior.

### Piano roll loop playback ignores the loop region — cursor keeps going
Fixed (2026-04-08). The loop region was computed as the maximum GOTO tick across ALL tracks. Different tracks can have GOTOs at different tick positions (especially with PATT expansions), so the loop end was much later than where audible notes actually ended. Now the loop region comes from track 0's flattened GOTO position — the structural authority that matches what Song Structure shows.

### Editing loop back position in Song Structure doesn't persist after save/reload
Fixed (2026-04-08). When you set the loop back to tick 960 and saved, the GOTO was being appended after ALL notes in the timeline. If notes existed past tick 960, the GOTO landed at the wrong tick (~1151). Now the GOTO is inserted into the timeline at the correct position, and the writer stops emitting events after a GOTO or FINE.

### "Unsaved Changes" dialog appears after saving from the Sound Editor
Fixed (2026-04-08). The Sound Editor marks the window dirty when you do things like import songs or rename. When you hit Save, if no sub-component individually reports changes (because the work was already written to disk), the dirty flag never got cleared. Now the dirty flag always clears after a save attempt.

### "Unsaved Changes" dialog appears on close/refresh even after saving or with no edits
Fixed (2026-04-09). Two separate causes:
1. **On startup with no edits:** PorySuite's items editor loads on a deferred timer. By the time it fires, the unified window's dirty-flag override is installed, so the items loading marks everything dirty. The dirty clear at end of load ran *before* the deferred load.
2. **After saving:** The save pipeline refreshes EVENTide's graphics dropdown (`gfx_combo`). Clearing and repopulating the dropdown fires `currentIndexChanged` → marks EVENTide dirty. The save code only cleared EVENTide's dirty flag if a map was loaded — if you never opened a map, the flag stayed stuck.

Fix: (1) Deferred dirty clear 200ms after load/refresh. (2) `gfx_combo` blocks signals during repopulation. (3) EVENTide's dirty flag always clears after save regardless of map state.

### Git pull doesn't refresh Sound Editor data
Fixed (2026-04-08). Git pull and F5 refresh now explicitly reload the Sound Editor and Credits Editor alongside PorySuite and EVENTide data.

### Accidentally placing notes by clicking in the piano roll
Fixed (2026-04-08). Single-clicking empty space used to create a note. Now single-click just deselects. Double-click to place a note. Double-click an existing note to open its properties.

### Clicking a track name in piano roll sidebar hides other tracks
Fixed (2026-04-08). Clicking a track in the sidebar was changing the Track filter dropdown, hiding all other tracks. Now it only sets the active track for placing new notes — all tracks stay visible.

### Can't reimport a .s file that already exists
Fixed (2026-04-08). The .s import dialog now allows overwriting when a file already exists, showing an amber confirmation instead of a blocking error.

### Piano roll save explodes control events (PAN goes from 18 to 1829)
Fixed (2026-04-08). PATT flattening duplicated control events per subroutine call. A song with 18 PAN commands could balloon to 1,829 after save. Fixed with deduplication at both load time and save time using (tick, cmd, value) sets.

### Song Structure dropdown shows internal PATT subroutine labels
Fixed (2026-04-08). The "Loop Back" dropdown was listing labels like `mus_evil_3_007` that are mid2agb's internal subroutine targets, not user-visible sections. The Song Structure panel now filters out PATT target labels.

### Loop position drifts by 2 ticks on every save/reload (192→190)
Fixed (2026-04-08). The LABEL command was emitted in the .s file before the preceding WAIT gap was calculated. The parser then read the wrong tick position. Fix: WAIT is now calculated and emitted before the LABEL line.

### User's loop label name reverts to auto-generated `_B1` on save
Fixed (2026-04-08). The save pipeline checked `track.loop_label` (empty for fresh imports) and fell back to auto-generated `_B1`. Now it reads the Song Structure panel's label first.

### Scroll wheel goes vertical in the piano roll instead of horizontal
Fixed (2026-04-08). Qt's QScrollArea intercepts wheel events before the parent widget sees them. Fixed with an eventFilter on the scroll area + viewport. Plain scroll = horizontal, Shift = vertical, Ctrl = zoom.

### MIDI import leaves PATT/PEND labels even when "No Loop" is selected
Fixed (2026-04-08). The default import mode was "Automatic" which keeps all mid2agb internal structure. Even if the user didn't click a preset, the import kept PATT/PEND labels. Default changed to "No Loop (clean)" — all imports now strip labels unless you explicitly click "Automatic."

### MIDI import fails with "failed to read event text" on some files
Fixed (2026-04-08). mid2agb can't handle most MIDI meta events (track names, text, lyrics, markers, etc.). The importer now strips all meta events except tempo, time signature, and end-of-track before passing to mid2agb. Also converts Type 0 MIDIs (all channels in one track) to Type 1 (per-channel tracks).

### Generate GM voicegroup is missing drums (no kick, snare, triangle, etc.)
Fixed (2026-04-08). The GM mapping table had no drum kit samples. Added kick, snare, hi-hat, triangle, cymbals, toms, hand clap, cowbell, and more. Also fixed a bug where DirectSound samples that lost their slot in the mapping were silently dropped — they now get placed in any remaining empty slots.

### Building after editing songs wipes music (mid2agb overwrites .s files)
Fixed (2026-04-08). pokefirered's Makefile has a `%.s: %.mid midi.cfg` rule — if midi.cfg or the .mid file is newer than the .s, mid2agb regenerates the .s from the .mid. For tool-edited songs, the .mid is a tiny placeholder, so mid2agb produces empty output that destroys the real music. This was the root cause of mus_evil losing all its data.

That fix worked by moving file dates so the `.s` always looked newer. File dates turned out to be the
wrong thing to rely on: git sets them in the order it writes files, not in the order anybody wrote the music,
so the same trick later hid MIDI edits and let terminal builds overwrite `.s` edits. It has been replaced.
PorySuite now decides by content — what each file actually plays — and never by which file is newer:

- The **Make** button checks every song before building, rebuilds songs whose `.mid` holds newer music, keeps
  a `.s` that holds work its `.mid` does not, and stops to ask when both changed.
- Saves write the `.mid` and the `.s` together and check them with the game's own converter.
- For terminal builds and `make clean`, see **Song Build Protection** below.

Do not write new code that moves song file dates to steer the build.

### Instrument loop checkbox shows unchecked even though the sample has looping
Fixed (2026-04-08). Sample data was lazy-loaded — only loaded when you first tried playing audio. If you selected an instrument before playing anything, `_sample_data` was None and the loop controls stayed empty. Now samples load automatically when you switch to the Instruments or Voicegroups tab, and the display refreshes when sample data arrives.

### Imported WAV sample doesn't show up as an instrument
Fixed (2026-04-08). Import WAV used to create a .bin file but never assign it to any voicegroup slot, so it was invisible. Now the import asks which voicegroup to add it to and replaces a filler slot. If the sample label already exists from a previous import, it replaces the old audio with your new WAV instead of silently reusing stale data. Import buttons are now in the left panel (always visible), not hidden inside another instrument's details.

### Instrument edits don't save / "unsaved changes" after saving
Fixed (2026-04-08). Two issues: (1) The Instruments tab edited instrument objects in memory but never told the Voicegroups tab they were dirty, so `voice_groups.inc` was never written on save. Now the Instruments tab marks affected voicegroups dirty via a cross-link. (2) PorySuite's `isWindowModified` flag was never cleared after save, so the app always thought there were unsaved changes. Now properly cleared.

### WAV import only shows Yes/No with no rate options
Fixed (2026-04-08). The rate/size picker dialog was only shown for WAVs above 13379 Hz. Lower-rate WAVs got a simple yes/no confirmation even when the file was 30+ KB. Now the rate picker always shows, with downsample options including 8000 Hz. Same fix applied to the Replace button.

### Build error: "symbol 'intro' is already defined" after piano roll save
Fixed (2026-04-08). When you named a loop section (e.g. "intro"), the same bare label was written into every track in the .s file. Assembly labels are file-global, so this caused duplicate symbol errors. The save now generates unique per-track labels (e.g. `mus_myroute_1_intro`, `mus_myroute_2_intro`). The Song Structure panel strips these prefixes when displaying, so you still see your friendly name.

### Settings dialog looks different
The settings dialog was rebuilt with a sidebar. All your old settings (diagnostics, notification preferences) are still there under the same INI keys. Autosave and Porymap path settings were removed (they were dead code that never did anything).

---

## Standard FireRed Project Layout

PorySuite expects a FireRed game project to follow the layout used by the official [pokefirered](https://github.com/pret/pokefirered) repository. The most important folders are at the **repository root**:

- `src/`
- `include/`

Before launching PorySuite, ensure your project has generated the real item headers (`src/data/items.h` in current pokefirered, or `src/data/graphics/items.h` in older forks). Run
your build system (e.g. `make`) if neither header exists; the Items tab will log a warning and stay empty until one of them is present.
After editing items, always run `pytest` so the FireRed write-back tests confirm the header and JSON stayed in sync.

If a required canonical source file is missing or unreadable when you try to perform an action (save items/species, write Pokédex text, etc.), PorySuite shows a blocking message and aborts the operation without changing any files. Restore the missing file(s) in your repo first, then retry. This protects downstream `make` builds that expect specific files and formats.

A valid `project.json` or `config.json` file is also required. This file must contain at least `plugin_identifier` and `plugin_version` so PorySuite knows which plugin to load.

## User Data Directory

PorySuite saves its project list and downloaded plugins inside the user data directory. On Windows this is `%LOCALAPPDATA%\PorySuite`, so the project list lives at `%LOCALAPPDATA%\PorySuite\projects.json`. Removing the entire `PorySuite` folder clears the saved project list and any locally installed plugins.

**Steps to clear saved data:**

1. Close PorySuite if it is running.
2. Delete the `PorySuite` folder in your user data directory (for Windows that is `%LOCALAPPDATA%\PorySuite`).
3. Restart PorySuite.

## Rerunning the Setup Wizard

If you want to run the setup wizard again with the FireRed plugin, remove `project.json` or `config.json` from your game project. Launch PorySuite and select the project again. The wizard will appear and you can choose the FireRed plugin.

If you see a dialog stating **"Invalid Project Root"** after opening a project, the selected folder is missing the required `src/` or `include/` directories. Use the **Run Setup Wizard** button in that dialog to pick the correct folder or recreate the project layout.

## Full Reset Checklist

1. Close PorySuite if it is running.
2. Delete the `%LOCALAPPDATA%\PorySuite` directory to clear saved projects and downloaded plugins.
3. In your game project folder delete `project.json` or `config.json`.
4. Delete `src\\data\\*.json` in your game project before reopening to ensure headers are re-parsed.
5. Restart PorySuite and open the project again.
6. The FireRed plugin will recreate any missing `src/data/*.json` files.
7. If the Items tab is still empty, confirm your project actually contains `src/data/items.h` or the legacy `src/data/graphics/items.h`. PorySuite will never synthesize a replacement header; it will log a warning and leave the table empty until one of the real files exists.
`constants.json` now includes type and evolution method data parsed from `pokemon.h`. Delete this file--or choose **Tools > Rebuild Caches**--if method names fail to load. The editor automatically rebuilds the file when its `types` or `evolution_types` sections are missing.
`PokemonConstants` load before evolutions, so type and method lists should
appear as soon as the project opens.

## General Troubleshooting Tips

- Double‑check that you opened the correct folder as the project root.
- Make sure `src/`, `include/` and your `project.json`/`config.json` file all exist.
- Watch the console window for messages about missing files or paths.
- Decline the "Apply C Header Changes" prompt if you want to skip saving; choosing No now leaves headers and JSON untouched so you can keep working without writing to disk.
- If the app reports a missing canonical source file (via a blocking dialog), restore the file in your repo and rerun the action. The editor does not synthesize substitutes and will not proceed with partial data.

Formatting Preservation Guarantees (FireRed)
-------------------------------------------
- Items: Saving only changes values on existing `.field = value` lines in `items.h`/`graphics/items.h`. Whitespace, comments, and blank lines are preserved. New fields/blocks replicate local indentation/commas.
- Learnsets: Saving modifies only the species’ entries in `level_up_learnsets.h`, `level_up_learnset_pointers.h`, `tmhm_learnsets.h`, `tutor_learnsets.h`, and `egg_moves.h`. Comments/spacing remain intact; additions/removals copy local formatting.
- Abilities: `include/constants/abilities.h` is not modified by the editor; `ABILITIES_COUNT` and `#endif` remain as-is.
- If the preview lists a header you do not recognize, verify the file actually exists before saving. The editor now scans alternate FireRed paths and only writes the real file, but missing headers still need to be restored manually.
- The Output panel now mirrors the launcher console, so review it for missing-file warnings or repo root mismatches during saves.

## Header files not found / blank panes

The FireRed plugin expects header files such as
`src/data/pokemon/species_info.h`, `moves.h` and `pokedex_entries.h`.
It uses `util.repo_root()` to locate the project root. If this function
returns the PorySuite folder instead of your game project—something that can
happen after a fresh install—every `open()` call fails quietly. As a result the
Species, Moves and Dex panes appear empty.

1. Open a project and confirm the Species/Moves/Dex panes are empty.
2. Run the provided Python snippet in a console to print `repo_root`:

   ```bash
   python - <<PY
   import plugins.pokefirered.pokemon_data_extractor as pde
   from pathlib import Path
   print("repo_root =", pde.util.repo_root())
   PY
   ```

   If the output path ends with `PorySuitePyQT6` instead of the project folder, the issue is present.
3. After selecting a project the console prints `repo_root confirmed:` or `repo_root mismatch:`.
   Ensure the printed path matches the directory you opened.
4. Choose **Tools > Rebuild Caches** to regenerate the plugin-managed cache files (species, moves, trainers, etc.) without touching authoring files such as `items.json`.
5. Relaunch PorySuite and reopen the project. The panes should populate when the root path is correct.

For the Moves pane specifically, ensure the selected species has entries under
`"species_moves"` in `src/data/moves.json`.
For the Moves pane specifically, FireRed projects now rebuild `species_moves` automatically from the headers when the cache is missing. If the table is still empty after reload, confirm the resolved `repo_root` points at your project and that the header files exist.

If the move dropdowns are empty or TM/HM lists are missing, run **Tools > Rebuild Caches** so moves.json and the header-derived learnsets are regenerated before reopening the project. Automated coverage in `tests/test_cache_tools.py` exercises this path, so a failure after rebuilding usually indicates missing header files or an incorrect project root.
> **Side-effects to watch for**
> 
> - Older extractor code with hard‑coded `"src"`/`"data"` prefixes may create doubled paths after fixing the root.
> - Stale JSON caches can mask whether headers are re-parsed—always clear them before retesting.
> - Extractor errors appear in the same console window after selecting a project, not on initial launch.

- **Plugin version mismatch**: If the UI remains blank after correcting the root path, ensure that `plugin_version` in `project.json` matches the FireRed plugin located under `%LOCALAPPDATA%\PorySuite\plugins`.

## Repo-root Self-Healing

If you accidentally open a nested folder the FireRed plugin climbs up the
directory tree looking for a valid `project.json` or `config.json`. When this
happens the console prints something like:

```
repo_root mismatch: selected C:/games/pokefirered/src resolved C:/games/pokefirered
```

The plugin will continue loading from the resolved path. If headers still do
not appear, delete `src/data/*.json` in your project and reopen it so the caches
are rebuilt from the correct root.

### Sprites fail to load

Check that `src/data/species_graphics.json` exists and that the referenced PNG
files are present in `graphics/pokemon/`. The FireRed plugin derives graphic
constants from the species name when none are stored. Numeric placeholders in
`species.json` are ignored so blank values fall back to this automatic
behaviour. Missing files or a misnamed folder will still result in blank
images. When a PNG cannot be found the console prints something like
``Image file for SPECIES_CONSTANT not found: /full/path/pic.png`` so you know
which file to restore.
If the PNG exists but can't be read the console prints something like
``Failed to load image file /full/path/pic.png for SPECIES_CONSTANT`` so
you know the file is corrupt and needs replacing. When the image loads
successfully, the chosen file URL is printed at debug level so you can
verify which asset is displayed. Image paths are normalized first so
backslashes appear as forward slashes on Windows systems.

All Pokémon data tabs refresh automatically when you pick a different
species in the tree. If the Graphics pane still looks outdated after
editing stats or abilities, switch to another sub-tab and back again to
force a repaint.

### Pokédex description formatting

If saved descriptions in `pokedex_text_fr.h` look misaligned:
- The editor now enforces the per‑line character limit detected from the
  header (usually 42 characters before the `\n` token) and matches the visual
  width to that limit.
- Edits are written using vanilla multi‑line formatting:
  
  `const u8 gXxxPokedexText[] = _(`
  `    "Line 1\n"`
  `    "Line 2\n"`
  `    "Last line");`
- If you still see mismatches, use **Tools > Rebuild Caches** and retry the
  save, then inspect `crashlogs/*.jsonl` for any write errors.

### Regional/National Dex flags are greyed out

The editor shows Dex flags based on `include/constants/pokedex.h` and disables
toggling because the engine uses the NATIONAL_DEX enum for ordering and
`KANTO_DEX_COUNT` for the Regional cut‑off. To change them in‑game, edit
`include/constants/pokedex.h` (reorder NATIONAL_DEX or adjust `KANTO_DEX_COUNT`)
and then use **Tools > Rebuild Caches**.

### HOENN_DEX_* undeclared or Make failures after editing Pokédex

Older editor behavior could overwrite `include/constants/pokedex.h`, removing content
that other files depend on and causing errors like ``'HOENN_DEX_OLD_UNOWN_B' undeclared``
or non‑constant array initializers in `src/pokemon.c`. This has been fixed:

- The editor now patches `pokedex.h` in place, updating only the `NATIONAL_DEX` enum body
  while preserving every other byte (includes, macros such as `KANTO_DEX_COUNT`, comments,
  blank lines). No fabricated `hoenn_dex.h` is created or required.
- If the enum block cannot be uniquely located or the layout is ambiguous, the save aborts
  with a blocking message and leaves all files untouched.

If you still encounter these errors after using an older version of the editor:
- Restore a clean `include/constants/pokedex.h` from your upstream FireRed repository.
- Reopen the project and save again; the in‑place patcher will preserve the file structure.

### Missing graphics for newly added species/items

If Make fails after adding a new species or item due to missing graphics:
- Save your project. The editor detects newly added entries and prompts you to clone assets from an existing template (e.g., copy Squirtle for Piplup, copy Potion for a new item).
- After confirming, the editor copies only image files under `graphics/**` to the correct new paths and shows a summary of created assets so you can edit them manually.
- No headers are created by this step. If you skipped the prompt or canceled it, run Save again and accept the cloning step, or copy assets manually into the expected `graphics/pokemon/<slug>` or `graphics/items/` paths.

### Evolutions not saving

Evolution data is cached in `src/data/evolutions.json`. Changes are written to
this file when it exists, otherwise they fall back to `src/data/species.json`.
Saving now updates `src/data/pokemon/species_info.h` and
`src/data/pokemon/evolution.h` automatically so edits appear in the original
headers without exporting.
If new rows
disappear after reopening the project, confirm the relevant file is writable and
use **Tools > Rebuild Caches** to force regeneration. Changing species in
the editor reloads the list so you can verify the saved data. Method names
    are pulled from `pokemon.h`; delete `constants.json` or choose **Tools > Rebuild Caches** if they do not appear. The editor now recreates the file automatically when its `types` or `evolution_types` sections are missing.
Editing a row no longer replaces the stored method constant so subsequent selections show the correct entry.

If `evolutions.json` is missing it will be regenerated by parsing
`src/data/pokemon/evolution.h` on startup so evolutions remain editable.

If Snorlax still shows `CHESTO_BERRY` as a held item, clear caches and rebuild
them so `species.json` picks up the new `ITEM_LEFTOVERS` values.

If single‑type Pokémon like Pikachu display the wrong second type, remove
`src/data/species.json` and choose **Tools > Rebuild Caches**. The FireRed
plugin will regenerate the file from `species_info.h` so the second slot uses
`TYPE_NONE`.

Upstream FireRed ships with `src/data/items.json`, and the extractor reads that
file first. It only parses `src/data/graphics/items.h` when the JSON is missing
or invalid. PorySuite now backs up `src/data/graphics/items.h` and regenerates
it from the JSON when saving. The bundled `pokefirered` reference may omit this
JSON, but vanilla `pokefirered` does not. If the JSON is absent or non-JSON it
is rebuilt from `src/data/graphics/items.h` so held-item dropdowns repopulate
automatically. If the header is missing a warning is logged and the Items tab
remains empty until `src/data/graphics/items.h` is restored.

Deleting `src/data/moves.json` works the same way for the Moves tab, forcing the
plugin to rebuild move data and species learnsets from the C sources.
If `items.json` contains an `"items"` array or is a list, it is converted to a
dictionary automatically on load to avoid crashes.

### Wrong types or items

If Pokémon appear with incorrect types or held items—for example Pikachu shows
as Water-type—`species.json` is stale. Run **Tools > Rebuild Caches** to
regenerate the file and restore the correct data. The species extractor now
compares cached `types` against `species_info.h` and overwrites mismatched or
`TYPE_NONE` entries. Set `PORYSUITE_REBUILD_ON_TYPE_MISMATCH=1` to rebuild
species caches automatically when these mismatches are detected.
If editing a species' type or ability seems to change another species when you
switch selections, make sure you're on the latest version. The FireRed species
extractor now copies default `types` and `abilities` lists so each Pokémon keeps
its own values. Run `pytest` and verify
`test_species_default_lists_are_independent` passes if issues persist.

Gender ratios can become outdated in a similar way. Rebuilding caches or simply
reopening the project updates `species.json` from `species_info.h` so each
Pokémon shows the correct ratio.

### Pokédex description not updating in-game

- The game reads localized Pokédex text from `src/data/pokemon/pokedex_text_fr.h`
  and uses description symbols referenced in
  `src/data/pokemon/pokedex_entries.h`.
- When you edit a species’ category or description in the Info tab and Save,
  PorySuite rewrites `.categoryName` in `pokedex_entries.h` and replaces the
  string for that species’ description symbol in `pokedex_text_fr.h`.
- If changes don’t appear:
  - Confirm both files are writable and part of your project (not the bundled
    read‑only `pokefirered/` reference).
  - Check the log for lines like `Updated pokedex_text_fr.h: gFooPokedexText`.
  - Ensure you didn’t introduce or rely on a non‑existent `pory_text.h`—the
    FireRed base uses `pokedex_text_fr.h`.

## Capturing Console Output

PorySuite records all console output (stdout and stderr) and Qt messages per
session inside the `crashlogs/` folder next to the app. Every run creates:
- `porysuite_YYYYMMDD_HHMMSS.log` (formatted text log)
- `porysuite_YYYYMMDD_HHMMSS.jsonl` (one JSON object per line)

When reporting issues you can share the `.jsonl` file for exact message text
and timestamps—even for non‑fatal warnings that don’t crash the app.

### Trainer edits not appearing

Trainer data is cached in `src/data/trainers.json`. Saving the project
rewrites this file and `src/data/trainers.h`. If changes do not show up,
confirm both files are writable and rebuild caches.

### Trainer AI Flags column shows the same flag for every trainer

`AI_SCRIPT_CHECK_BAD_MOVE` (the first AI flag) is set on every FireRed trainer by default — this is correct game data, not a display bug. To view or edit a trainer's AI flags, **double-click the AI Flags cell**. A checklist dialog appears with all 9 `AI_SCRIPT_*` flags that have real code in pokefirered, each with a plain-English description. Hover over the cell to see a tooltip listing what each active flag does. Flags without engine code (Smart Switching, Roaming, Safari, First Battle) have been removed — they either don't exist in pokefirered or are set by the engine automatically, not through trainer data.

### Renaming a trainer causes build errors (`TRAINER_* undeclared`)

If a trainer constant is renamed inside `trainers.json` without updating `opponents.h` and `trainer_parties.h`, the compiler will report undeclared identifiers. To rename a trainer correctly:

1. Open the **Trainers** tab.
2. **Double-click the Constant column** (leftmost column) of the trainer row.
3. Enter the new constant name and click OK.
4. Choose **File > Save**.

The rename writes to all required files:
- `include/constants/opponents.h` — `#define TRAINER_*`
- `src/data/trainer_parties.h` — `sParty_*` symbol declarations and usages
- `src/data/trainers.h` — struct key and `.party` field
- `src/data/trainers.json` — JSON key

If you have already manually edited `trainers.json` and the build is now broken, restore the mismatched constants in `opponents.h` and `trainer_parties.h` from git, then use the double-click rename flow to perform the rename correctly.

### Building the ROM — MSYS2 environment

Use **Project > Make (Build ROM)** (Ctrl+M) or **Project > Make Modern** (Ctrl+Shift+M) to open an MSYS2 terminal and build the ROM. If the build fails immediately, check:

1. **MSYS2 not installed at `C:\msys64`** — Install from https://www.msys2.org/ (the standalone release; NOT the one inside `C:\devkitPro\msys2`).
2. **Host tools missing** — Run `make tools` inside the MSYS2 MINGW64 shell to compile `preproc`, `gbagfx`, `scaninc`, etc. These must be Windows `.exe` files. If Linux ELF tool binaries exist (no extension) next to the `.exe` files from a prior WSL build, delete the extension-less ones: `rm tools/gbagfx/gbagfx tools/preproc/preproc ...` etc.
3. **devkitARM shims from WSL interop** — If `C:\devkitPro\devkitARM\bin\arm-none-eabi-gcc.exe` is a tiny shell script calling `/mnt/c/...` paths, it was created by WSL interop and will fail from MSYS2. Fix it to call the actual versioned binary: `exec "/c/devkitPro/devkitARM/bin/arm-none-eabi-gcc-15.1.0.exe" "$@"`. Same fix needed for `arm-none-eabi-as.exe` → `arm-none-eabi/bin/as.exe` and `arm-none-eabi-ar.exe` → `arm-none-eabi/bin/ar.exe`.
4. **libpng missing in MSYS2** — Install with `pacman -S mingw-w64-x86_64-libpng` in the MSYS2 shell.

> **WSL vs MSYS2 distinction**: WSL produces Linux ELF binaries that cannot call Windows executables (devkitARM, gbafix). MSYS2 produces Windows PE binaries that work with devkitARM. Always build pokefirered from MSYS2.

### `src/data/battle_moves.h` — missing array declaration

If you see build errors like `expected identifier or '(' before '[' token` at lines in `battle_moves.h`, the outer array declaration is missing. The file must start with:

```c
const struct BattleMove gBattleMoves[MOVES_COUNT] =
{
```

PorySuite's moves writer can regenerate this file without that header. If the file was overwritten, restore it with `git checkout HEAD -- src/data/battle_moves.h` in your pokefirered directory, then resave your move edits. Also ensure `src/pokemon.c` contains `#include "data/battle_moves.h"` near the top — PorySuite's pre-save validation now checks for its absence rather than its presence.

### `src/data/graphics/items.h` — INCBIN data only

`src/data/graphics/items.h` must contain **only** INCBIN graphics lines (one per item). The item struct definitions (`[ITEM_NONE] = { .name = ...}`) belong in `src/data/items.h` which is included by `src/item.c`. If PorySuite appended item struct data to `graphics/items.h`, the file will cause build errors at file scope. Strip everything from the first `[ITEM_` line onward; keep only lines like:

```c
static const u8 sItemIcon_Potion[] = INCBIN_U8("graphics/items/potion/icon.4bpp");
```

Restore from git if needed: `git checkout HEAD -- src/data/graphics/items.h`.

### GCC 15.1.0 `-Wattribute-alias` error in `pokemon.o`

Newer devkitARM toolchains (GCC 15.1.0) treat `-Wattribute-alias` as an error by default. pokefirered's `GetBoxMonData2` alias has a pre-existing type mismatch that triggers this. The pokefirered Makefile now includes a per-file override:

```makefile
$(C_BUILDDIR)/pokemon.o: CFLAGS += -Wno-attribute-alias
```

If this line is missing from your Makefile, add it after the `$(C_BUILDDIR)/%.o` rule block.

### Rename Species — what gets updated

When you rename a species via **Tools > Rename Species…**, File > Save applies
changes to the following locations:

**Name / Pokédex files (shown in the preview dialog):**
- `src/data/text/species_names.h` — display name and constant
- `src/data/pokemon/pokedex_entries.h` — species block header and `.description` symbol
- `src/data/pokemon/pokedex_text_fr.h` — description symbol definition

**JSON cache files:**
- `src/data/species.json` — top-level species key, `speciesName`, and form names
- `src/data/pokedex.json` — `species` and `dex_constant` entries
- `src/data/evolutions.json` — top-level species key and any `targetSpecies` references
- `src/data/moves.json` — species key inside `species_moves`
- `data/species_graphics.json` — graphic symbol keys (e.g. `gMonFrontPic_*`) and path strings
- `data/starters.json` — starter `species` field if the renamed Pokémon is a starter
- `data/pokedex.json` — `NATIONAL_DEX_*` entry in the national dex list

**Source token sweep (across all `.c`/`.h` in `src/` and `include/`):**
- `SPECIES_*`, `NATIONAL_DEX_*`, `gMonFrontPic_*`, `gMonBackPic_*`, `gMonIcon_*`,
  `gMonFootprint_*`, `gMon*PokedexText`, `CRY_*`, `graphics/pokemon/<slug>`,
  CamelCase and UPPER variants of the name

The confirmation dialog shows the count of name/Pokédex/cache changes; the
source sweep is additional and logged to the crash log after Save completes.

**NOT swept — must be updated manually:**

The rename sweep does **not** touch `data/maps/**/*.inc` or `data/scripts/**/*.inc` map-event script files. After renaming a starter species, search those files for and update:

| Pattern | Example file |
|---|---|
| `SPECIES_<OLD>` | `data/maps/PalletTown_ProfessorOaksLab/scripts.inc` |
| `FLAG_HIDE_<OLD>_BALL` | `data/maps/PalletTown_ProfessorOaksLab/events.inc` |
| `TRAINER_RIVAL_*_<OLD>` | `data/maps/Route22/scripts.inc`, `data/maps/SSAnne_2F_Corridor/scripts.inc`, etc. |
| `TRAINER_CHAMPION_FIRST_<OLD>` | `data/maps/PokemonLeague_ChampionsRoom/scripts.inc` |
| `TRAINER_CHAMPION_REMATCH_<OLD>` | `data/maps/PokemonLeague_ChampionsRoom/scripts.inc` |
| `cleartrainerflag TRAINER_*_<OLD>` | `data/scripts/hall_of_fame.inc` |
| `sParty_*<Old>` / `sParty_*<OLD>` | `src/data/trainer_parties.h` (already swept), `src/data/trainers.h` (already swept) |

Missed references in `.inc` files will cause linker errors (`undefined reference to TRAINER_CHAMPION_FIRST_BULBASAUR` etc.) when you try to build.


### Scroll wheel changing values without clicking

All combo boxes and spin boxes across the app are protected by a scroll guard.
You must click a dropdown or number field before the scroll wheel will change
its value. If you still see accidental changes from scrolling, the widget may
have been created dynamically after the guard was installed — report the
specific tab and field.

### Move rename — changing only capitalization

Renaming "POUND" to "Pound" changes the display name but keeps the same
constant (MOVE_POUND). This is supported — the rename dialog detects that
the display name changed and updates it in memory and JSON even when the
constant stays the same. If the list doesn't update after a display-name-only
rename, save and refresh (F5).

### Move effect field shows an unknown constant

The Effect field in the Moves tab is a standard dropdown listing all known
effect constants. If a move in your data uses an effect that is not in the
built-in list, the dropdown adds it automatically so nothing is lost. That
extra entry may not have engine code backing it — check your source files
if you see an unfamiliar constant.

### New move doesn't appear in-game after adding

When you add a move via Add Move or Duplicate Move, PorySuite must write five
separate files on Save for the move to exist in the ROM. If any file is missing
or unwritable, the save will warn you. Check that all five files exist in your
project: `include/constants/moves.h`, `src/data/battle_moves.h`,
`src/data/text/move_names.h`, `src/move_descriptions.c`, and
`data/battle_anim_scripts.s`. If the animation file is missing, the move will
compile but crash when used in battle (no animation entry).

### Effects vs animations — what's the difference?

The **Effect** field (EFFECT_HIT, EFFECT_BURN_HIT, etc.) controls what the move
does mechanically in battle — damage, status conditions, stat changes. The
**Animation** field controls what plays visually on screen when the move is used.
They are completely independent systems. You can have a fire-type move that uses
EFFECT_BURN_HIT (chance to burn) but plays the Pound animation, or vice versa.
When duplicating a move, both the effect and animation are copied from the source.

### Move display name is too long

FireRed's MOVE_NAME_LENGTH is 12 characters. If you enter a longer name in the
Add Move or Duplicate Move dialog, it will be truncated to 12 characters when
written to `move_names.h`. The dialog enforces this limit.

## Porymap Integration

### Install Porymap appears to hang during compile

The first install (or any install after the source is reset) does a full compile of ~200 C++ files, which takes several minutes. The progress dialog now shows which file is being compiled and a running count. If it still appears stuck for more than 10 minutes, check `aqtinstall.log` in the porysuite folder for download errors, or `crashlogs/` for build failures.

### Porymap opens but shows the wrong map (Battle Colosseum 2P)

This was fixed. The root cause was a Windows backslash vs Qt forward-slash mismatch — CLI args use `C:\GBA\...` but Qt normalizes paths to `C:/GBA/...`, causing `ParseUtil::pathWithRoot()` to double-prepend the project path. All map.json files became unfindable, so Porymap fell back to the first alphabetical map.

If this happens again after a reinstall:
1. Go to Tools > Install Porymap to rebuild (the patch is now correct)
2. Close and restart both PorySuite and Porymap
3. Click "Open in Porymap" — it should open to the map you're editing

### "Open in Porymap" does nothing

Check that Porymap is installed (Tools > Install Porymap). The button is greyed out with a tooltip if Porymap isn't found. If the button is active but nothing happens, check the Output panel for error messages from the launcher.

### Porymap opens a second window instead of switching maps

The launcher detects running Porymap windows by title. If the window title doesn't contain "porymap" (case-insensitive), detection fails. This can happen if Porymap is minimized to the system tray. Bring Porymap to the foreground manually and try again, or close the extra window.

### Map switching feels slow or bounces back to a previous map

This was caused by two issues (both fixed):
1. **Lag**: The auto-sync feature was checking if Porymap was running by spawning a `tasklist.exe` subprocess on every map switch, which blocked the UI for ~4 seconds. Now it just writes a small command file (near-instant).
2. **Echo loop**: PorySuite sends "switch to map X" → Porymap switches → Porymap tells PorySuite "I'm now on map X" → PorySuite sends "switch to map X" again. Fixed with a dual dedup system (flag + last-map tracking) that breaks the loop.

If you still see bouncing, check that you're running the latest code — older versions don't have the anti-echo logic.

### Porymap loses patches after reinstall

This is expected. The Install Porymap flow runs `git reset --hard` to ensure a clean source tree, then re-applies all patches via `apply_patches.py`. If a patch fails to apply (check the Output panel for errors), the binary will be missing features like `openMap` or `readCommandFile`. Report the specific error — it usually means an upstream Porymap change moved the anchor string the patcher looks for.

### "Check for Porymap Updates" says "can't reach GitHub"

Fixed (2026-04-13). The old code required a `.psinstalled` marker with a git commit hash, which didn't exist for pre-marker installs or stock Porymap. If the marker was missing, the function returned early without ever contacting the network, then showed a misleading "can't reach GitHub" error. Now uses the GitHub Releases API (no git needed) and reads the installed version from Porymap's own CHANGELOG.md.

### Porymap updated itself and broke PorySuite bridge

If you use Porymap's built-in "Check for Updates" to update, it downloads a stock release binary that replaces our patched build. All bridge features (map sync, event callbacks, etc.) stop working. PorySuite now detects this: on project load, it compares the SHA-256 hash of porymap.exe against what it stored during the last build. If they don't match, a warning dialog appears. Fix: go to Tools → Update Porymap (or "⚠ Re-patch Porymap...") to rebuild with patches. **Always update Porymap through PorySuite**, not through Porymap's own updater.

### EVENTide: Script changes not saving (shared sub-label overwrite)
Fixed (2026-04-06). When multiple events share a script sub-label via `goto` (for example, three trigger events that all jump to the same `RivalBattle` label), editing one event's copy of that shared label and saving could lose the edit. The save loop processed events in order, and the last event's stale copy of the shared label overwrote the user's edit. Fixed: the currently-selected event is now processed last during save, so its edits always win.

### EVENTide: "Value for 'local_id' cannot be empty" build error
Fixed (2026-04-06). Switching between events in the editor could inject an empty `"local_id": ""` field into objects that originally had no `local_id` (like the three unnamed lab assistants in Oak's Lab). The build tool rejects empty values for this field. Fixed: the editor only writes `local_id` when the field has actual text. If your map.json already has bogus empty `local_id` fields, remove them manually or re-save the map in PorySuite-Z.

### EVENTide: "Missing value for required parameter 'song' of macro 'savebgm'" build error
Fixed (2026-04-06). The `savebgm` command widget was outputting just `savebgm` with no song argument. The GBA macro requires `savebgm MUS_SOMETHING`. Fixed: the widget now has a song picker dropdown and includes the selected constant in its output.

### EVENTide: "healplayerteam" assembler error
Fixed (2026-04-06). The Heal Player Team command widget was emitting `healplayerteam` which isn't a real GBA assembly macro. The correct output is `special HealPlayerParty`. Fixed in the widget's output.

### Voice groups save causes "undefined reference to gCryTable" linker error
Fixed (2026-04-06). The Voicegroups Tab's `save_to_disk()` was writing the full `voice_groups.inc` file from scratch, dropping any `.include` lines that existed between voicegroup blocks (like `.include "sound/cry_tables.inc"`). Without that include, the linker couldn't find the cry table symbols. Fixed: the save function now reads the existing file first, records which `.include` lines appear after which voicegroup, and re-inserts them at the correct positions when writing.

---

## Updating PorySuite-Z

### "This installation is a Git checkout" when updating
The built-in updater refuses to run when the PorySuite-Z install folder is itself a Git working copy (it has a `.git` folder at its root). Replacing files in a checkout could silently destroy uncommitted work, so even a clean checkout is refused. Update with `git pull` instead. Normal installs (downloaded as a zip or release package) are unaffected and keep one-click updates. A Pokémon project repo *inside* the install folder does not trigger this — only a `.git` at the install root counts.

### An update failed — what state am I in?
As of 2026-11-23 updates are transactional. Before any file is changed, everything the update will touch is copied into `_update_backup` and verified. If any step fails, the backup is restored automatically and files the update added are removed — the install comes back exactly as it was. On the next launch PorySuite shows a "Previous Update Failed" message with the reason (also saved in `_update_error.log` in the install folder). You are on your old version and can simply try the update again.

### The update was interrupted (crash, power loss, forced shutdown)
On the next launch PorySuite detects the half-applied update and offers to restore the previous version, then restarts.

If PorySuite itself won't start after an interrupted update, you don't need it to recover: open the install folder and **double-click `_recover_update.bat`**. It is a plain Windows batch script that restores the backup and removes the partial update without needing Python or the app at all.

### "Update Recovery Failed" dialog
This means the rollback itself could not finish (for example, the backup folder was deleted mid-restore) and the install may be a mix of old and new files. PorySuite keeps all recovery materials and refuses further updates until this is resolved. The dialog offers to retry the recovery; manual instructions are appended to `_update_error.log`. If all else fails, reinstalling the release over the folder is safe — your settings, projects, and data are in folders the updater never touches.

### Files the update didn't replace or remove
- **A file you modified** (compared to the shipped release) that the new release replaces: your edited copy is saved to `_update_preserved` in the install folder before the new file goes in. `_update_preserved` is never deleted by the updater.
- **A release file you modified** that the new release wants to *remove*: it is kept in place instead, and a note is written to `_update_notices.log`.
- **A file you created** that a new release now ships under the same name: the update stops before changing anything and tells you, rather than taking the file over. Move or rename your file, then update again.
- Obsolete files from *older* releases are only removed when the updater can prove (by checksum against its manifest, `_installed_manifest.json`) that they are untouched release files. Installs that predate the manifest have nothing removed; the first manifest is written for future updates.

### Leftover `_update_*` files in the folder
Files named `_update_staging`, `_update_backup`, `_apply_update.bat`, and similar are the updater's working materials. After a successful update they are cleaned up automatically. If they are left over from a *failed* update, leave them alone until the next launch — PorySuite uses them to recover. If they were copied in from a *different* install (the updater records which folder they belong to), PorySuite refuses to act on them and says so; it is safe to delete them in that case.

---

## Sound Editor

### Songs sound wrong — wrong pitch, missing notes, volume too loud/quiet
Multiple audio accuracy bugs were fixed on 2026-04-06. If you're hearing:
- **Wrong key / pitches off**: The BEND command and MidiKeyToFreq pitch calculation are now GBA-accurate. If a specific song still sounds off, report which song and which instrument sounds wrong.
- **Missing notes or silent sections**: Running status bytes (bare integers that repeat the last control command like VOL) are now parsed correctly. Previously they were silently dropped, causing volume to stay at 0.
- **Some instruments way too loud or quiet compared to in-game**: The volume pipeline was fixed — velocity is now applied once (not twice), the song's master volume constant is no longer double-applied (it's already baked into VOL command values), and CGB instruments (square waves, programmable waves, noise) no longer have arbitrary volume reduction factors.
- **Sounds disappear in mono playback**: Per-note stereo panning now uses GBA's linear crossfade instead of constant-power. No phase cancellation occurs when channels are summed.

### Song playback has a piercing high-pitched sound
Some songs (e.g., mus_victory_trainer) have square wave notes at very high MIDI pitches (96-98). These are present in the original GBA data but may sound harsher in our renderer than on actual hardware due to differences in the output filtering. This is a known limitation.

### Songs sound shifted up by several semitones / shrill high pitch
Fixed (2026-04-06). XCMD instructions in GBA assembly span multiple `.byte` lines. The parser only handled the first line — the continuation line (e.g., `.byte xIECL, 8`) was misinterpreted as a running-status command, creating phantom KEYSH commands that shifted all subsequent notes up by 8-16 semitones. This affected roughly 85 songs. Fixed by recognizing XCMD sub-command tokens (xIECV, xIECL) and skipping them.

### Drum tracks are silent
Fixed (2026-04-06). The GBA stores voicegroups back-to-back in memory. Drum kits using `keysplit_all` index directly by MIDI note number, which can overflow past the target voicegroup's bounds into the next one (e.g., note 40 in a 29-instrument voicegroup reads slot 11 of the next voicegroup). The parser was returning "not found" for overflow indices. Fixed by chaining into subsequent voicegroups.

---

## EVENTide (Event Editor)

EVENTide has its own, longer troubleshooting file at `eventide/docs/TROUBLESHOOTING.md`. The entries added on
2026-10-24 cover the three most-asked questions after that day's changes:

- **An item appears with no "You got it!" message.** *Add Item* (`additem`) is the silent one; *Give Item*
  (`giveitem`) is the one with the jingle and the message. Both are in the palette now.
- **A sprite in the Browse grid has no picture.** The grid lists every `OBJ_EVENT_GFX_*` the project defines,
  including ones with no sheet registered. Still a valid choice.
- **A map is missing from Open Map.** A folder with no `map.json` is not a map. A map no group claims appears at
  the bottom under *Not in any group*.

---

## Potential Improvements

Future versions of PorySuite may offer clearer error messages and an option in the UI to reset the configuration.
