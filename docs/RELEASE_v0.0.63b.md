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

A third round uncovered the actual root cause — the first two rounds were treating symptoms. Byte-match guards work only when the bytes already match; if PorySuite's writers and upstream's writer disagree on format, the guards correctly detect a real diff and correctly perform a write, and the working tree stays dirty forever. Three format mismatches were identified and corrected:

- **Save path used `ensure_ascii=False`.** Upstream's `items.json` ships ASCII-escaped unicode (`"POK\u00e9 BALL"`); PorySuite's save path wrote the literal UTF-8 character (`"POKé BALL"`). Every Items-tab save flipped `\u00e9` → `é` across the entire file. Fixed by switching to `ensure_ascii=True` in `core/pokemon_data.py`.
- **Extractor path used `indent=4`.** Upstream's `items.json` (and every other `src/data/*.json`) is indented with two spaces. PorySuite's `_write_json` helper wrote four, so every re-extraction produced a whole-file reformat diff. Fixed by switching to `indent=2, ensure_ascii=True` in `core/pokemon_data_extractor.py::_write_json`.
- **`_load_json` had an mtime-based staleness check.** If any source header (`src/data/items.h`, `src/data/graphics/items.h`, etc.) had a newer mtime than the JSON, the JSON was discarded and the header was re-parsed. A `git pull` from upstream bumps every touched file's mtime to "now" — so every pull forced a re-extraction, and the re-extraction emitted content that differed from upstream's (four-space indent, deduplicated `ITEM_NONE` placeholder rows). Fixed by removing the staleness check entirely: if the JSON exists and parses, it is trusted. Re-extraction now only runs when the JSON is missing or corrupt. Users who want to force a re-extraction can delete the JSON manually (via the Git panel's new Discard / Delete buttons, or an OS file delete).

`_write_json` still carries a byte-match guard (serialize, compare, skip write on match, `os.utime()` to bump mtime) as a belt-and-suspenders measure. With the staleness check removed, the re-extraction path is rarely reached in normal use — but if anything does route through it, the guard prevents unnecessary writes.

### Git Panel — Porcelain-Path Parsing Bug

Fixed a parsing bug that mangled the first file's path whenever `git status --porcelain` was used to populate a file list. The `_git_run` helper calls `.strip()` on the full command output, which eats the leading space of the first line's XY status column. `git status --porcelain` outputs ` M src/data/items.json` (leading space is significant — it represents the empty "staged" column). After the outer `.strip()` the line became `M src/data/items.json`, and the fixed-position parser `raw[3:]` sliced off the first character of the path, producing `rc/data/items.json` instead of `src/data/items.json`. Any attempt to act on that file through the panel's Discard button — or to stage it through the Commit dialogs in the main window and the EVENTide commit pipeline, which both used the same parse — routed to a non-existent path and failed with `pathspec 'rc/...' did not match any file(s) known to git`.

All three sites now use `str.split(None, 1)`, which is tolerant of leading whitespace and handles every porcelain format (`" M path"`, `"M  path"`, `"MM path"`, `"?? path"`) consistently.

### Git Panel — In-App Discard & Delete Buttons

The Commit section now has two new buttons so users never need to drop to a terminal to clean up their working tree.

**🗑 Discard Checked Changes** sits directly under the "Modified files" list. Tick any number of tracked files, click the button, confirm, and each one is reverted to its last committed state via `git checkout --`. Batched 40 paths at a time to stay under the Windows command-line length cap. Failed files (permissions, locked by another process) are reported back in a dialog with the full list. This is the button path for wiping a phantom `items.json` or `.gitignore` modification that PorySuite's own auto-maintenance paths may have left behind in older builds.

**🗑 Delete Checked Untracked** sits directly under the "New untracked files" list. Tick any stray build artifacts, stale test exports, or accidental drops, click the button, confirm, and each one is removed from disk with `os.remove` (or `shutil.rmtree` for directories). Again, failures are reported back with the full list.

Both buttons show a confirmation dialog with the full list of affected paths (up to 20 shown inline; the rest summarized as `… and N more`). There is no undo — the confirmation is the last checkpoint.

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
- Updated: `core/pokemon_data.py` — items save path now serializes with `ensure_ascii=True` to match upstream's ASCII-escaped unicode (`"POK\u00e9 BALL"`), and byte-compares against disk to skip no-op writes.
- Updated: `core/pokemon_data_extractor.py` — `_load_json`'s mtime-based staleness check removed; if the JSON exists and parses, it is trusted. `_write_json` now serializes with `indent=2, ensure_ascii=True` to match upstream format, and retains a byte-match guard that skips the rewrite and bumps mtime via `os.utime()` on match.
- Updated: `ui/dialogs/git_panel.py` — scroll-to-top on showEvent so the panel always opens at the Status section, not mid-way through Commit.
- Updated: `ui/dialogs/git_panel.py` — minimum width 620 → 880; default size 660×800 → 960×820. Added `🗑 Discard Checked Changes` button (tracked modifications) and `🗑 Delete Checked Untracked` button (stray files) to the Commit section, each with a confirmation dialog. No terminal needed for working-tree cleanup.
- Updated: `core/app_info.py` — VERSION bump to `0.0.63b` *(pending — bumped on release)*.

## Known Limitations

- **Pre-existing dirty state is not auto-cleared.** Projects that already have `items.json` or `.gitignore` modifications from a prior build still show them as uncommitted. Use Switch to Branch → pick the current branch → **🗑 Discard & Switch** to wipe those two specific files in-app, or commit the changes if you intend to keep them. Future project opens will stay clean.
- **`.git/info/exclude` is per-clone.** If you clone the project onto a second machine, PorySuite will re-seed the bridge entries on first open there. That's intentional — each clone maintains its own local exclude list.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
