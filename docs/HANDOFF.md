# PorySuite-Z — Working Rules & Handoff

How to work on this project. Read alongside root `CLAUDE.md` (the binding source), `docs/CLAUDECONTEXT.md` (current state), and the recalled memories. This is the distilled rulebook — when it and `CLAUDE.md` disagree, `CLAUDE.md` wins.

## 1. The operating model
- **The user directs; Claude codes.** The user (InnerMobius) owns architecture, decisions, and testing. Execute their instruction literally — don't add "edge-case handling" or "optimizations" they didn't ask for. If you think an edge case needs different handling, ASK first.
- **Plain English, zero jargon.** No struct/function names in explanations unless needed. Summarize after every work turn.
- **Trust the user's evidence.** A screenshot / "it's broken" is ground truth. Investigate runtime behavior, not source assumptions. Don't argue "the code looks correct."
- **When uncertain, stop and ask.** Don't guess at decision points.
- **Read before speculating.** The code is on disk — READ the named file/function before theorizing. Speculation that replaces a source-read is the failure mode.

## 2. Hands off the game files — fix the TOOL
- **Never hand-edit files inside `pokefirered/`** (or any game repo) to make the tool's life easier. If the tool can't handle something, fix the tool. Hand-editing creates invisible state that breaks after context loss.
- **But the tool's PATCHER is free to refactor the engine aggressively.** PorySuite-Z is an engine-refactoring tool; vanilla pokefirered is the starting point, not sacred. Patchers (`innate_patch`, `form_change_patch`, `normalize_forms_patch`, `refactor_service`, …) may add constants, rewrite functions, add source files, change tables — whatever a feature needs. Default to refactor, not bandaid. Don't offer "preserve vanilla vs refactor" as parallel options once the user has opted into the refactor.
- **Auto-generated files** (e.g. `include/constants/map_event_ids.h`) are build OUTPUTS regenerated from `data/maps/*/map.json` — never hand-edit; change the source data and rebuild.

## 3. Don't apply a tool to the user's project yourself
When you build a feature/button for the user to run: **verify the logic in throwaway copies, then let the USER run it via the UI.** Don't apply the operation to their real project — that uses up their test subjects and removes their control. (Learned the hard way 2026-07-20.)

## 4. "It compiles" is NOT "it's verified"
- `py_compile` + reading-audits do NOT catch runtime bugs. Two shipped this session: a function-local `import` in `mainwindow.__init__` that shadowed a module global (crashed ALL project loading), and a patcher writing data the app's reader couldn't parse (froze every species tab).
- A patcher that produces a **building ROM** can still write data the **app can't read**. Verify the APP-side path: drive species-selection / the actual data read, not just the file output.
- On `mainwindow.__init__` edits: never add a function-local `from … import X` for a module-global name; AST-check for shadowing.
- **Build before handoff** — never tell the user to "rebuild just to be safe"; build to completion yourself, then write test steps.

## 5. Releases
- **Version = one line:** `core/app_info.py` `VERSION = "X.Y.Zb"`. Every visible version string reads it dynamically. Bump that line only.
- **Don't over-release.** No version bump + tag + GitHub release for every small change. For non-trivial changes, lay out the plan and get a nod BEFORE implementing; confirm before tagging.
- **Checklist:** bump `VERSION` → write `docs/RELEASE_vX.Y.Zb.md` → update `docs/CHANGELOG.md` + `docs/BUGS.md` → commit → **wait for user to test** → on approval: tag `vX.Y.Zb`, push `main`, push tag, `gh release create`.
- **Public release-notes tone** (they go on GitHub Releases): third-person, neutral, ≤~60 lines, grouped subsections. NO file paths, function names, root-cause essays, DO-NOT-regress bullets, or dates in the body. NEVER include the user's Zelda game content (ocarina/Hyrule/hand-authored game fixes) — tool features only. Same content ban applies to commit messages. End with the AI-disclosure + beta-warning blockquote.
- The user "doesn't do terminals" — you commit/push/tag/release for them via the git tools.

## 6. Anti-regression & docs (HIGHEST PRIORITY)
- **Before fixing any bug**, read `docs/BUGS.md` + `docs/CHANGELOG.md`. If it was fixed before, find what regressed it first.
- **The moment a bug is found/fixed**, update `docs/BUGS.md` (what/where/root cause; then fix + DO-NOT-regress) and `docs/CHANGELOG.md` (date, files, one-line). Immediately, every time — not batched.
- **Trim as you go:** CHANGELOG keep last 3 days, ≤900 lines; BUGS keep OPEN + FIXED-last-3-days, ≤750 lines.
- After compaction/context loss, re-read `BUGS.md` + `CHANGELOG.md` — they ARE your memory.

## 7. No dead code — ever
When a feature is removed/redesigned/replaced, delete ALL its scaffolding the same session: hidden widgets (`removeRow()`, not just `setVisible(False)`), unreferenced methods, dead signal connections, dict keys/extractor fields/codegen branches, stale comments, save/flush entries. Grep for the removed name and clean every hit.

## 8. Sprite rendering (HIGHEST PRIORITY)
Every sprite shown anywhere routes through `core/sprite_render` (`load_sprite_pixmap`) + `core/sprite_palette_bus` (`get_bus`, RAM-first palettes, `palette_changed` signal). NEVER `QPixmap(path)` for a sprite — the authoritative palette is the `.pal` file + unsaved RAM edits. Editor tabs push edits to the bus; viewer tabs subscribe + invalidate. Project close clears the bus.

## 9. UI/UX & workflow specifics
- **Users never use terminals** — direct all test steps to in-app toolbar/menu actions (Make, Make Modern, Play, Git panel, the specific button). The user tests over Chrome Remote Desktop.
- **Dropdowns must never scroll on mouse-wheel when closed** (two-finger page-scroll must scroll the page). Use the no-scroll combo pattern everywhere.
- **No hardcoded game labels** — parse everything from the project (the hack is Zelda/Hyrule, never assume Kanto/vanilla).
- **Text fields need per-line char limits** with a counter + overflow highlight (see `ui/dex_description_edit.py`).
- **Debug logging goes to a FILE** (the module's file logger), never `print()` / "check the console." Tell the user which log to read; read it yourself.
- **Measure, don't guess** runtime behavior — mGBA `.lua` disk-log scripts in `C:\GBA\mgba\scripts\`; read `.map`/headers for offsets; you read the log, don't ask the user to paste it.
- **Tab `load()`/F5 must fully reset** in-memory AND visual dirty state (amber frames, swatches, per-row/per-card dirty markers). Reference: `TrainerGraphicsTab.load()`.

## 10. Where things live
- Version: `core/app_info.py`. Docs: `docs/` (CHANGELOG, BUGS, CLAUDECONTEXT, TROUBLESHOOTING, RELEASE_v*). EVENTide: `eventide/` (+ `eventide/docs/`).
- Repos: app = `C:\GBA\porysuite` (`origin` `InnerMobius/PorySuite-Z`, branch `main`); live game = `C:\GBA\porysuite\pokefirered`; clean diff copy = `C:\GBA\READONLYREFERENCE\pokefirered`.
- Build the game: in-app **Make Modern** (WSL `make firered_modern`).
