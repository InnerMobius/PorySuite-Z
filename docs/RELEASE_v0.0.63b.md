# PorySuite-Z v0.0.63b

> pokefirered projects only.

A follow-up to v0.0.62b focused on eliminating the phantom "your working tree is dirty" state users saw after every upstream pull. Two files — `.gitignore` and `src/data/items.json` — were being touched by PorySuite's own auto-maintenance paths even when the user hadn't edited anything, which made `git checkout` refuse to switch branches and made every `git status` look like the user had uncommitted work. Both paths now leave the working tree alone. The Git panel is also wider so section descriptions no longer clip.

## What's New in 0.0.63b

### Git Hygiene — `.gitignore` Is No Longer Auto-Touched

PorySuite's Porymap bridge was appending three entries — `porysuite_bridge.json`, `porysuite_command.json`, `porymap.user.cfg` — to the project's tracked `.gitignore` on every project open. The intent was correct (those IPC scratch files should never be committed) but the mechanism was wrong: upstream's `.gitignore` doesn't have those lines, so every `git pull` from upstream reverted them, PorySuite re-appended them on the next project open, and now `git status` reported `.gitignore` as modified forever. "Switch to Branch" refused to run; "Stash Changes" kept filling up with phantom stashes of identical content; users were forced to look at a dirty tree they never wrote to.

`ensure_bridge_gitignored` now writes to `.git/info/exclude` — git's dedicated file for *local-only* ignore rules. It is never tracked, never committed, never appears in any diff, and is never affected by `git pull` or `git checkout`. If the tracked `.gitignore` still contains the auto-added block from a previous build, the exact sentinel-wrapped block is scrubbed out once on project open so the phantom diff disappears. User-added lines adjacent to the block are left alone — only the three entries under the exact `# PorySuite bridge files (auto-added, do not commit)` sentinel are removed, and only if they match the three known entries.

### Items.json — Byte-Match Guard Prevents No-Op Writes

The items save path rewrote `src/data/items.json` every time its in-memory dict differed from the snapshot taken at load. Internal conversions (list→dict keyed by `ITEM_*`, dict entry ordering as `name → itemId → …` reinsertion, two-space indent) were producing content that didn't byte-match upstream's on-disk formatting even when the actual data was identical. Result: the save pipeline rewrote `items.json` on every full save cycle even when the user hadn't edited a single item, bumping the file's mtime and surfacing as a phantom `modified:` line in `git status` after every build.

The save path now serializes the JSON, reads the current on-disk bytes, and bails before writing when the two are identical. No mtime bump, no write, no diff. If the content genuinely differs (a real edit), the write happens as before and `self.original_data` is refreshed so subsequent no-op saves stay quiet. The guard is surgical — it touches only the `items.json` write, not the `items.h` regeneration (which is a separate pipeline and already had its own gap-padding logic fixed in v0.0.62b).

A second write path had the same symptom from a different angle. On project open, the items extractor checks whether `src/data/items.json` is older than any of its source headers (`src/data/items.h`, `src/data/graphics/items.h`). After a `git pull` from upstream the source header's mtime jumps to "now" — newer than the tracked JSON — so the staleness check fired, the extractor re-parsed the header, and `_write_json` rewrote `items.json` unconditionally. The rewritten content was byte-identical to what was already on disk (same items, same ordering, same indent), but the mtime bump still showed up as a `modified:` entry in `git status` the moment the user opened the project. No tab clicks required, no edits made.

The shared `_write_json` helper in `core/pokemon_data_extractor.py` now performs the same byte-match guard: serialize the payload, read the current file bytes, and if they match, skip the write entirely and just call `os.utime()` to bump the JSON's mtime forward. Git does not track mtime, so the file stays clean in `git status`, and the next load's staleness check is satisfied because the JSON is now newer than the header again. The guard lives in the shared writer, so every extractor that routes through `_write_json` (items, abilities, moves, and others) benefits automatically — not just items.

### Git Panel — Width

Minimum width bumped from 620 to 880 pixels; default size from 660×800 to 960×820. Section descriptions (Push / Commit / Branches / Stash) were clipping their left margin behind the scrollbar, hiding the first few characters of every long line. The new width keeps the full paragraph visible without a horizontal scroll. Users who want the panel narrower can still resize below 880 — it just won't fit every explanation cleanly at that point.

<!--
  ▼ MORE ENTRIES WILL BE ADDED BELOW AS 0.0.63b DEVELOPMENT CONTINUES ▼
  Keep new sections in the same H3 style.  When the release is cut:
    - Update the opening paragraph above to mention new headline changes.
    - Consolidate the full list of file changes into "Files of Note".
    - Move any open items into "Known Limitations".
  Do NOT remove the AI Disclosure / Beta software footers below.
-->

## Files of Note

- Updated: `porymap_bridge/porymap_launcher.py` — `ensure_bridge_gitignored` rewritten to target `.git/info/exclude`; legacy-block scrubbing on the tracked `.gitignore` for users migrating from prior builds.
- Updated: `core/pokemon_data.py` — items save path now byte-compares against disk and skips the write when the content is identical.
- Updated: `core/pokemon_data_extractor.py` — shared `_write_json` helper now byte-compares against disk before writing, skips the rewrite on a match, and bumps mtime via `os.utime()` so the staleness check stays quiet without producing a phantom git diff. Fixes items.json (and any other extractor JSON routed through the helper) appearing as modified on project open after an upstream pull.
- Updated: `ui/dialogs/git_panel.py` — minimum width 620 → 880; default size 660×800 → 960×820.
- Updated: `core/app_info.py` — VERSION bump to `0.0.63b` *(pending — bumped on release)*.

## Known Limitations

- **Pre-existing dirty state is not auto-cleared.** Projects that already have `items.json` or `.gitignore` modifications from a prior build still show them as uncommitted. Use Switch to Branch → pick the current branch → **🗑 Discard & Switch** to wipe those two specific files in-app, or commit the changes if you intend to keep them. Future project opens will stay clean.
- **`.git/info/exclude` is per-clone.** If you clone the project onto a second machine, PorySuite will re-seed the bridge entries on first open there. That's intentional — each clone maintains its own local exclude list.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
