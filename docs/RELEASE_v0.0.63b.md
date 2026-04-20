# PorySuite-Z v0.0.63b

> pokefirered projects only.

A follow-up to v0.0.62b focused on eliminating the phantom "your working tree is dirty" state and a cluster of related cleanup bugs. Two files — .gitignore and src/data/items.json — were being touched by PorySuite's own auto-maintenance even when nothing was edited, which blocked git operations. Both paths now leave the working tree alone. The Git panel also gained in-app Discard and Delete buttons, a fix for a path parsing bug that mangled filenames, and a wider layout to prevent text clipping. The build pipeline now purges stale compiled song objects whose voicegroups were removed upstream, and a bug causing build-log messages to mark Pokémon as edited has been eliminated.

## What's New in 0.0.63b

### Git Hygiene — .gitignore Is No Longer Auto-Touched

PorySuite's bridge now writes to .git/info/exclude — Git's dedicated file for local-only ignore rules. This file is never tracked or committed, so local scratch files no longer cause dirty trees or "Switch to Branch" failures. If the tracked .gitignore contains the old auto-added block, it is scrubbed out once on project open to clear the phantom diff.

### Items.json — Format Parity With Upstream

Fixed three format mismatches in src/data/items.json that produced phantom diffs on every save or refresh:
- Save path now uses ASCII-escaped unicode (e.g., \u00e9) to match upstream.
- Extractor path now uses 2-space indentation instead of 4.
- Removed the mtime-based staleness check; the JSON is now trusted if it exists, preventing git pulls from forcing a re-extraction.
- Internal data normalization now ensures that save-path diff checks compare like-for-like, preventing unnecessary rewrites during a refresh.

### Git Panel — Improvements and Fixes

- Fixed a parsing bug where the Git status output was stripping the first character of the first file's path (e.g., "rc/data" instead of "src/data").
- Added 🗑 Discard Checked Changes button to revert tracked files to their last committed state.
- Added 🗑 Delete Checked Untracked button to remove build artifacts or stray files from disk.
- Increased minimum and default panel width to 880+ pixels to prevent section descriptions from clipping.

### Build Pipeline — Stale Song Object Cleanup

The build pipeline now performs a second pass to delete compiled .o files if their associated .s file was pruned and the voicegroup is missing from voice_groups.inc. This prevents "undefined reference" linker errors after upstream pulls.

### Dirty Marking Fix

The build log output no longer triggers the "edited" status for the currently selected Pokémon. The log window has been excluded from the auto-wiring loop that monitors for user edits.

## Known Limitations

- **Pre-existing dirty state is not auto-cleared.** Projects that already have `items.json` or `.gitignore` modifications from a prior build still show them as uncommitted. Use Switch to Branch → pick the current branch → **🗑 Discard & Switch** to wipe those two specific files in-app, or commit the changes if you intend to keep them. Future project opens will stay clean.
- **`.git/info/exclude` is per-clone.** If you clone the project onto a second machine, PorySuite will re-seed the bridge entries on first open there. That's intentional — each clone maintains its own local exclude list.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
