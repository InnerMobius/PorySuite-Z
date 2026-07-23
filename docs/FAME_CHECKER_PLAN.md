# Fame Checker Editor — Master Plan

**Goal:** a full PorySuite-Z tab for editing the Fame Checker, designed so it can be **repurposed as a quest
tracker** (persons → quests, flavor-text entries → objectives, unlock calls → "objective completed" triggers).

**Status:** Phase 0 DONE. **Phase 1 COMPLETE** (2026-07-22, verified by audit round 7) — the page is live as
a toolbar button in the unified window, showing every field plus portrait and informant-icon previews,
and **text editing is LIVE** — every string plus the four custom person names, saved through the shared per-label
writer in `core/text_index`, which replaces one `<label>::` block in place so the 14 foreign labels in that file
survive untouched. Three text budgets, all parsed: msgbox 208px, origin captions 84px, person list 56px. 7 audit rounds, 20
blocking defects, all caught before shipping. **Phase 2 IN PROGRESS** — `core/fame_checker_unlocks.py` parses and classifies all 118 unlock calls and
flags the ones that would silently do nothing (P2-A / P2-B / P2-D / P2-H). Read-only; no UI yet. Remaining:
reachability + map attribution (P2-E / P2-F), then writing as its own change (P2-I / P2-J). **Read §4e first.**

> ⚠ **Navigation lives in `ui/unified_mainwindow.py`, NOT in `MainWindow.ui.mainTabs`.** The shipping app
> never displays `mainTabs` — `setup_pages()` pulls each widget out of it into the unified window's stack and
> builds an icon toolbar. Adding a tab to `mainTabs` compiles, runs, and is invisible. A new page needs: the
> widget added to `self.stack` + `_page_indices`, an entry in `porysuite_pages`, a `res/icons/toolbar/<name>.png`,
> a section->icon map entry, and a View-menu action. To hide a button, toggle the **QWidgetAction** that
> `QToolBar.addWidget()` returns — hiding the button itself does not stick.

**Working method:** a persistent auditing agent reviews this plan AND every implementation change as it is made.
**Audit round 1 (2026-07-21) rewrote large parts of this doc — see §6 for what was corrected.**

> 🌍 **PROJECT-AGNOSTIC RULE (governs every phase).** PorySuite serves **any** pokefirered decomp — stock, this
> Zelda hack, or anyone else's. **Nothing** may be hardcoded from one project's shape: not the person count, not
> entries-per-person, not symbol names, not which padding arrays exist, not the text-label set, not which persons
> are custom-named. Every one of those is **parsed from the project being edited**, with a clear failure when the
> shape isn't recognised. Concrete numbers quoted in this doc (16 persons, 6 entries, 118 unlock calls, 334 text
> labels, 14 foreign labels, specific `unused_*` arrays) are **observations from the live test project**, used as
> fixtures and sanity checks — never as assumptions to code against.

> ⚠ **Never cite the `/*0x…*/` offset comments in `include/global.h` as evidence.** They are stale from
> `:767` onward (see §1.5). Measure with `sizeof` / compile instead.

---

## 1. Engine data model (verified by audit 2026-07-21)

### 1.1 Constants — `include/constants/fame_checker.h`
- `FAMECHECKER_OAK`(0) … `FAMECHECKER_GIOVANNI`(15), `NUM_FAMECHECKER_PERSONS` = 16
- `FCPICKSTATE_NO_DRAW`(0) / `FCPICKSTATE_SILHOUETTE`(1) / `FCPICKSTATE_COLORED`(2)
- **Also**: `FC_NONTRAINER_START` = `0xFE00` and the four pseudo-consts `FAME_CHECKER_PROF_OAK / DAISY_OAK /
  BILL / MR_FUJI` are `#define`d **inside `src/fame_checker.c:140-143`**, not in the header.

### 1.2 The NINE per-person / per-entry tables — `src/fame_checker.c`
| # | Table | Shape | Live? | Meaning |
|---|-------|-------|-------|---------|
| 1 | `sTrainerIdxs` `:145` | `[N]` designated | yes | `TRAINER_*`, or `FC_NONTRAINER_START + slot` for custom-named persons |
| 2 | `sFameCheckerTrainerPicIdxs` `:171` | `[N]` designated | yes | `TRAINER_PIC_*` portrait |
| 3 | `sFameCheckerTrainerGenders_Unused` `:190` | `[N]` designated | **dead** | must still be regenerated or deleted |
| 4 | `sNonTrainerNamePointers` `:164` | `[4]` positional | yes | custom names, indexed by `trainerIdx - FC_NONTRAINER_START` |
| 5 | `sFameCheckerNameAndQuotesPointers` `:209` | `[2*N]` **explicit size** | yes | first N = names, next N = quotes |
| 6 | `sFameCheckerFlavorTextPointers` `:246` | `[N*E]` positional | yes | the flavor-text entries |
| 7 | `sFameCheckerArrayNpcGraphicsIds` `:265` | `[N*E]` positional | yes | `OBJ_EVENT_GFX_*` informant icon per entry |
| 8 | `sFlavorTextOriginLocationTexts` `:380` | `[N*E]` positional | **yes** | "where you heard it" — read at `:1402-1403` |
| 9 | `sFlavorTextOriginObjectNameTexts` `:399` | `[N*E]` positional | **yes** | "who/what told you" — read at `:1404` |

**Tables 8 and 9 are the most dangerous omission.** Adding a person or entry without regenerating them makes
`UpdateIconDescriptionBox` index past the end of a `const u8 *const[]` and hand a garbage pointer to
`GetStringWidth` → hang/crash. Tables **6–9 must always be regenerated together.** For a quest tracker they are
natural "objective location" / "objective source" fields.

### 1.3 Text
- `data/text/fame_checker.inc` — 334 labels, of which **only ~320 are Fame Checker's**. The other **14**
  (`PokemonJournal_Text_SpecialFeature*`) belong to `data/scripts/fame_checker.inc` (included from
  `data/event_scripts.s:1354`). **A full-file rewrite deletes them → link failure.** Edit in place / re-emit
  unknown labels verbatim, and add a regression check.
- **The four non-trainer names are NOT here.** `gFameCheckerOakName/DaisyName/BillName/MrFujiName` live in
  **`src/strings.c:1273-1276`** (declared `include/strings.h:119-122`) — a C file shared with the whole game.
- **Do not touch `gFameCheckerText_Cancel`** (`src/strings.c:128`). Despite the name it is used by ~15 unrelated
  systems (`item_menu.c`, `shop.c`, `learn_move.c`, `player_pc.c`, `zelda_menu.c:887`, …).

### 1.4 Graphics — `graphics/fame_checker/`
- Custom portraits: `prof_oak`, `daisy`, `bill`, `mr_fuji` — shipped as `.png` + **`.gbapal`, with no `.pal`**.
- UI chrome: `bg`, `cursor`, `question_mark`, `spinning_pokeball`, `silhouette.gbapal`, `tilemap1..3.bin`.
- Everyone else uses their `TRAINER_PIC_*`.
- Custom art loads into OBJ palette **6** (`PERSON_PAL_NUM` `:1338`), `gReservedSpritePaletteCount = 7` `:1201`
  — a single slot; per-person custom art must respect it.
- **Sprite-bus gap:** `core/sprite_palette_bus.py` has no category for these, and no `.gbapal` ingest path.
  A new category + loader is required before CLAUDE.md's "all sprites route through the bus" rule can be met.
  `TRAINER_PIC_*` portraits and `OBJ_EVENT_GFX_*` icons can reuse `CAT_TRAINER_PIC` / `CAT_OVERWORLD`.

### 1.5 Save state — `include/global.h`
```c
struct FameCheckerSaveData { u16 pickState:2; u16 flavorTextFlags:12; u16 unk_0_E:2; };  // sizeof == 2
struct FameCheckerSaveData fameChecker[NUM_FAMECHECKER_PERSONS];   // 16 * 2 = 32 bytes
u8 unused_3A94[64];                                                // padding after it
```
- **`sizeof(FameCheckerSaveData) == 2`, verified by compiling.** The `/*0x3A94*/` comment at `global.h:767` and
  every offset comment after it are **stale by 0x20**; `// size: 0x3D68` at `:774` is stale too (real: 0x3D48).

**Growing the save is ALLOWED (USER DECISION 2026-07-21). Existing saves become incompatible — warn, don't block.**

Why it breaks saves (this is the text the warning must convey, not a reason to refuse): `save.c:43-48` derives
each save sector's `size` from `sizeof(SaveBlock1)`; `save.c:638-644` copies those into
`gRamSaveSectorLocations[].size`; `GetSaveValidStatus` recomputes the checksum over exactly `size` bytes. Real
chunking today is `[3968, 3968, 3968, 3784]`. Growing the struct changes the final chunk's size, so a checksum
written over the old length no longer matches → that sector fails → `validSectors != ALL_SECTORS` →
`SAVE_STATUS_ERROR` for **both slots**. Note this is all-or-nothing: it is not "some data is lost", it is "the
save file no longer loads".

**Design consequences (deliberately simple):**
- Just grow `fameChecker[]`. No in-place padding swap, no runtime donor discovery, no negotiating with other
  features over `unused_*` arrays. Those were only ever needed to preserve save compatibility.
- **The editor MUST show one explicit, unmissable warning** before applying any expansion: *existing save files
  for this project will no longer load; players (including you) start fresh.* Require a positive confirmation.
- **New person slots must still be ZEROED on load** — `save.c:190-198` doesn't clear the sector buffer, so bytes
  past the old size are stale flash garbage. Init loop: `fame_checker.c:1143`.
- `src/save.c:81` asserts `sizeof(SaveBlock1) <= SECTOR_DATA_SIZE * 4` (headroom ≈184 bytes ≈ 92 more persons).
  **This one IS a hard stop** — exceeding it fails the build. Past it, extra sectors would be required; out of
  scope. The editor should report remaining headroom and refuse beyond it with a clear build-breakage message.

### 1.6 Unlock triggers
Macro `asm/macros/event.inc:1825-1830`:
```
famechecker person:req, index:req, function=SetFlavorTextFlagFromSpecialVars
```
- **118 calls total** — only **80** under `data/maps/`; the other **38** are in `data/scripts/fame_checker.inc`.
  Glob all of `data/`.
- **The second argument is OVERLOADED.** With the default function it is a flavor-text index (0–5). With
  `function=UpdatePickStateFromSpecialVar8005` it is an `FCPICKSTATE_*` value (0–2). **17** of the 118 are pickState
  calls. Treating those as objective indices produces false "duplicate" and false "orphan" reports (it is why
  raw per-person counts read 7–9 instead of 6).
- Specials validate `gSpecialVar_0x8004 < NUM_FAMECHECKER_PERSONS`, `gSpecialVar_0x8005 < 6` (flavor) / `< 3` (pick).

### 1.7 Hardcoded assumptions — ALL must be patched to expand

> 🌍 **The line numbers below are EVIDENCE FROM THE TEST PROJECT, NOT ADDRESSES.** Every other decomp — including
> stock pokefirered, whose `fame_checker.c` differs from this fork — has different line numbers. **Phase 3 must
> locate every one of these sites by PATTERN MATCH against the project's own source, and if a site cannot be
> found it must REFUSE to patch and report which one is missing.** A line-number patcher would silently corrupt
> somebody else's project.
| Site | Assumption | Consequence |
|---|---|---|
| `:690` `AllocZeroed(17 * sizeof(struct ListMenuItem))` | 16 persons + CANCEL | **heap overflow at 17+ persons** |
| `:975` `sFameCheckerFlavorTextPointers[person * 6 + data[1]]` | 6 entries | wrong entry / OOB |
| `:1108` `sFameCheckerArrayNpcGraphicsIds[person * 6 + i]` | 6 entries | wrong icon / OOB |
| `:1402-1404` origin-text lookups (tables 8, 9) | 6 entries | **garbage pointer → crash** |
| `:1224` `gSpecialVar_0x8005 < 6` | 6 entries | unlock rejected |
| `:1062-1072` `AdjustGiovanniIndexIfBeatenInGym` | literal index **9** + `FAMECHECKER_GIOVANNI` | any insert/remove above index 9 silently shuffles the whole list once Giovanni's gym is beaten; called for **every** person at `:1553` |
| `:1345-1368` `CreatePersonPicSprite` | 4-way custom-art list | must match Destroy exactly |
| `:1385-1389` `DestroyPersonPicSprite` | **same 4-way list again** | mismatch → leaked trainer-pic alloc or wrong free path |
| `:1149` `ResetFameChecker` sets `FAMECHECKER_OAK` COLORED | person 0 always visible | removing/reordering OAK breaks new games (`src/new_game.c:139`) |
| `:47` `u8 spriteIds[6]`, `:937/1029/1093/1103/1158/1250` `i < 6` loops | 6 entries | see §2 |
| `:1110-1111` icon grid `47*(i%3)+0x72, 27*(i/3)+0x2F` | fixed **3×2** layout | see §2 |
| `:41` `numUnlockedPersons:6` | counts persons **+ CANCEL** → max 63 items = **62 persons** | |
| `:1152` `FullyUnlockFameChecker` `j < 6` | no caller in `src/`, still must be patched | |

---

## 2. Capacity — corrected (USER DECISION: expand anyway, but WARN)

| Change | Reality |
|---|---|
| **More persons** | Just grow `fameChecker[]`. Existing saves stop loading → **explicit warning + confirmation** (§1.5). Also requires patching `AllocZeroed(17*…)` and `AdjustGiovanniIndexIfBeatenInGym` regardless. |
| **BINDING CAP: 62 persons** | `numUnlockedPersons:6` (`fame_checker.c:41`) counts persons **+ the CANCEL row** → 63 items → **62 persons**. This is the real ceiling and the number the UI must quote. Widening it also runs into `:1572`, which stores `0xFF` in `unlockedPersons[]` as the CANCEL sentinel → a `u8` index caps at 254 regardless. **Parse `numUnlockedPersons`' bit width; don't assume 6.** |
| **SaveBlock1 assert** | `save.c:81` fails the BUILD at ≈92 more persons than vanilla — **unreachable**, since the 62-person cap bites first. Keep it as a secondary guard (a large `E`, or a widened save struct, could still trip it). |
| **Heap bound** | `load_save.c:99-105` stacks SaveBlock2 + SaveBlock1 + PokemonStorage into `gHeap`; ~53 KB used of `HEAP_SIZE 0x1C000`. ~2× headroom, so growth of this order is safe — but it is a second size-dependent bound. |
| **ROM header** | `rom_header_gf.c:137` publishes `.saveBlock1Size`. It changes; the neighbouring `offsetof` fields don't (`fameChecker[]` sits after them). No practical impact on a hack. |
| **Entries ≤ 12** | Save-safe *only in the flag field*. **NOT free**: the engine is hard-wired to 6 by `spriteIds[6]`, six `i < 6` loops, and a fixed 3×2 icon grid on a 240×160 screen. **Going past 6 is a screen-layout redesign** (paging/scrolling the icon panel), not a table resize. |
| **Entries > 12** | Also needs a wider `flavorTextFlags` → changes the save struct. |

**Rule:** the editor MUST show one explicit, unmissable warning + require confirmation before ANY change that
alters `sizeof(struct SaveBlock1)` — i.e. before adding or removing persons. Wording must be concrete: *"existing
save files for this project will no longer load — you and your players start fresh."* Not a caveat buried in a
tooltip. Growing the save is otherwise fine and is not a reason to block the feature. Newly-added person slots
must still be zeroed on load.

---

## 3. Phases

### Phase 0 — feature detection (DONE)
A project may have removed or gutted the Fame Checker. Then: **no tab, no writes** (same opt-in model as innate
abilities). `core/fame_checker_data.py`:
- `has_fame_checker()` — cheap gate; requires a real table **definition** (`name[...] = {`), not a mention
  (the index maths reference these symbols, so matching a usage falsely reports support for a gutted file).
- `load_fame_checker()` sets `available=False` + `unavailable_reason` for: both files missing, source missing,
  header missing, no `FAMECHECKER_*` constants, tables gutted.
- Verified on throwaway copies for all five cases.
- **Stable contract:** the nine TABLE identifiers and the `FAMECHECKER_*` / `FC_NONTRAINER_START` macro names
  are the one thing a project may NOT rename — every lookup keys on them. A project that renames a table gets
  no tab, silently. Everything else (text symbols, names, portraits, counts) may be renamed freely.

### Phase 1 — the editor tab (IN PROGRESS) — BUILD SPEC
Audit-agreed requirements. Build against these; each piece is reviewed as it lands.

1. **Nothing unresolved may ever be written.**
   - Fields whose symbol is in `unresolved_text_symbols` → **read-only, with a visible reason**. Never a blank
     editable box, because Save would overwrite the real text (which lives in a file we didn't read) with "".
   - `name_source == "trainer"` → the list name comes from `gTrainers[]`; render **read-only with a link to the
     Trainers tab**, never as an editable field that silently does nothing.
   - **`blocking_problems` non-empty → Save is DISABLED entirely** and the problems are shown prominently. One
     line, data-driven — never pattern-match message text.
   - Severity contract: `info` → collapsible diagnostics panel; `warn` → grey out that field; `blocking` → no Save.
2. **No `.inc` writer beyond §5's one-writer plan.** Until `write_text_strings` exists with foreign-label
   preservation, either route through it or ship the refuse-to-save interlock (§5.4). A partial writer that
   regenerates the file deletes the foreign strings — the easiest round-1 finding to re-introduce.
3. **`FameCheckerTextEdit`, not `DexDescriptionEdit` — COMPLETE.** Measures real pixels via
   `core/gba_text_metrics.py` (project's own `charmap.txt` + glyph width tables), not characters. Handles
   `{...}` stripping, mid-string `{FONT_*}` switches, `\p` pages, `\n` line slots, `\l` scrolls, and takes both
   the pixel budget and the font from the parsed source. Origin captions use the `0x54` centring width and
   `FONT_SMALL`, and the location one warns about placeholders. Meets the app's three-part text-field rule:
   counter, red highlighting on the overflowing characters, Enter refused at the line budget. See §4 for the
   five ways this goes wrong and the 420/420 + 192/192 verification gate.
6. **Never show entries the engine can't read.** The tables are sized data but the stride is ALSO a literal in
   the engine's indexing maths (`[person * 6 + index]`, `6 * person + which`, `spriteIds[6]`). A project that
   resized the tables without patching those gets rows that silently do nothing and a Save that writes into dead
   slots — so a mismatch is `blocking`. Detecting it needs a balanced-bracket walk, not a regex (the index
   contains its own brackets), and must skip the table's DECLARED SIZE (`[2 * NUM_FAMECHECKER_PERSONS] = {` is a
   different 2 — treating it as a stride flags healthy vanilla as broken).
4. **The CLAUDE.md tab contract in full, from the start** — `load()` stops timers → clears in-memory dirty sets →
   resets *visual* state (amber frame, stale labels) → rebuilds inside `_loading = True/False`. Per-row amber on
   the person list (Pattern A) **and** the amber detail frame (Pattern C), both wired to the same dirty set.
5. **Every sprite through the bus; every dropdown wheel-guarded.** `CAT_FAME_CHECKER_PIC` + shared `.gbapal`
   reader for custom portraits; `CAT_TRAINER_PIC` for `TRAINER_PIC_*`; `CAT_OVERWORLD` for the informant icons.
   No bare `QPixmap(path)`. The portrait toggle is **one per-person boolean** so Phase 3 can't desync
   `CreatePersonPicSprite` / `DestroyPersonPicSprite`.

### Phase 1 — implementation notes
- **Name source must be modelled explicitly.** For the 12 trainer-linked persons the list name comes from
  `gTrainers[].trainerName` (`FC_PopulateListMenu:1556-1563`), **not** from table 5 — table 5's name is only the
  pick-mode quote header (`:964`). So a naive "display name" field would *appear* to rename and change nothing.
  Model per person: **source = trainer** (read-only, link to the Trainers tab) or **custom** (editable, owns a
  `sNonTrainerNamePointers` slot). Adding a custom-named person appends to table 4 and mints a new
  `FAME_CHECKER_*` define; removing one renumbers every later pseudo-const **and** the `sTrainerIdxs` values.
- Per person: portrait (custom art vs `TRAINER_PIC_*` — one toggle driving BOTH `CreatePersonPicSprite` and
  `DestroyPersonPicSprite`), quote, pickState default.
- Per entry: flavor text, informant icon, **origin location** (table 8), **origin object name** (table 9).
- Sprites route through `core/sprite_render` + `sprite_palette_bus` (needs the new category — §1.4).
- No-wheel-scroll dropdowns; staged edits + Save; per-row amber dirty markers; full `load()`/F5 reset.

### Phase 2 — unlock-point management
- Glob **all of `data/`** (118 calls; 38 are outside `data/maps/`).
- **Disambiguate the overloaded 2nd argument** by the `function=` parameter before classifying anything.
- Show map + label + line; add / move / remove; flag orphans and duplicates.
- **Drive EVENTide's existing script-command layer** (`eventide/ui/event_editor_tab.py:685` already parses
  `famechecker`) rather than doing independent line surgery.

### Phase 3 — engine refactor (patcher)
Regenerate tables 1–9 + constants together; patch **every** row of §1.7; grow `fameChecker[]` directly (behind
the save-incompatibility warning, §1.5) and zero the new slots on load; check the `save.c:81` assert headroom and
refuse past it; replace the two 4-way custom-art lists with a generated `sFameCheckerUsesCustomPic[N]`;
delete or regenerate `AdjustGiovanniIndexIfBeatenInGym` (recommended: delete — it is a FireRed-specific
"move Giovanni to the end" hack with no quest-tracker meaning); keep `ResetFameChecker`'s always-visible person
pointing at a real index.

### Phase 4 — quest-tracker vocabulary
Toggle labels between Person/Entry and Quest/Objective.

---

## 4. Measured text limits — DONE, and measured in PIXELS not characters
Built as `core/gba_text_metrics.py` + `ui/fame_checker_text_edit.py`. Character counting was abandoned: the
GBA font is proportional, so no chars-per-line figure can be both safe and usable. The measurer parses the
**project's own** `charmap.txt` and `sFont*LatinGlyphWidths[]` from `src/text.c` and measures exact pixels.

**Budgets (all parsed, none hardcoded):**
- **Flavor text** → `FCWINDOWID_MSGBOX`, 26×4 tiles (`:493-501`) = **208 px × 2 visible lines**. Font is read
  from the `AddTextPrinter*(FCWINDOWID_MSGBOX, …)` call, not assumed.
- **Origin captions** → `FCWINDOWID_ICONDESC`, centred in **84 px** (`(0x54 - GetStringWidth(…))/2`, `:1402-1406`),
  one `FONT_SMALL` line each. Use the `0x54` literal, NOT the template's 88 px — the template would allow 4 px of overflow.

**Four things that make or break the measurement — each one, gotten wrong, flags shipped vanilla text as overflow:**
1. **`letterSpacing` does not apply to Latin text.** Both `RenderText()` and `GetStringWidth()` in `src/text.c`
   gate it on the Japanese flag, so `gFontInfos[FONT_NORMAL].letterSpacing = 1` is inert for English. Adding it
   inflates a 35-char line by 35 px and flags **111 of 420** vanilla lines. Without it the widest vanilla line
   is 199 px and **all 420 fit**. Do not "fix" this back.
2. **`{FONT_MALE}` switches font mid-string and it sticks** — across `\n`, `\l` and `\p`, for the rest of the
   entry. Half of vanilla's entries do this. The measurer threads font state through the whole walk.
3. **`\l` SCROLLS, it does not add a visible line — but it does NOT give the page a fresh line budget.**
   `RENDER_STATE_CLEAR` (`\p`) resets `currentX` **and** `currentY`; `RENDER_STATE_SCROLL_START` (`\l`) resets
   `currentX` **only**. So the model is a per-page LINE SLOT: `\n` → slot+1, `\l` → slot unchanged, `\p` → slot 0,
   and a line whose slot reaches the window's capacity is drawn below the floor and renders as a clipped sliver.
   Counting `\l` like `\n` flags **21 of 96** entries (confirmed against `gFameCheckerFlavorText_Brock0`, which is
   2 lines then a scroll to a third); but giving each scroll group its own fresh budget is the opposite error and
   passes `A\nB\lC\nD`, which the engine clips. Vanilla has zero `\l`-then-`\n`, so only the slot model catches it.
4. **Control codes cost zero pixels** — `{COLOR DARK_GRAY}{SHADOW LIGHT_GRAY}` is 36 chars and 0 px.
5. **The apostrophe and `=` must survive charmap parsing.** `''' = B4` and `'=' = 35` both break a naive
   `line.split("=")` / strip-the-quotes parser, and the apostrophe is the most common punctuation in English prose
   — mis-charging it at the fallback average inflated the widest vanilla line from 196 px to 199 px. Conversely
   `'\l' = FA` / `'\n' = FE` / `'\p' = FB` must NOT be un-escaped, or the letters l/n/p get mapped to those bytes.

**Verification gate (re-run this after any change to either module):** all **420 flavor lines** and all
**192 origin captions** in vanilla must measure as fitting, on both the live project and
`READONLYREFERENCE/pokefirered`. Currently 420/420 (widest 196 px) and 192/192 (widest 79 px) on both, with 0
below-floor lines and 0 red spans across all 16 persons.

**A parse that succeeds is not a parse that is right.** `charmap.txt` read with `errors="replace"` yields a
non-empty, entirely plausible dict in which every accented glyph has collapsed onto one replacement character —
the textbook silent-wrong-answer. The measurer therefore reads UTF-8 **strictly** (falling back to cp1252) and
sanity-checks the result: minimum entry count, no U+FFFD, the ASCII basics all present, and a **collision count**
(entries naming an already-seen character). A healthy charmap has 0 collisions; a mangled one had 169. Failing
any check drops to average widths with `exact=False`, and the tab raises an `info` problem saying so.

**Two more things the source hides:**
- The **origin location caption is printed raw** while the object caption goes through `StringExpandPlaceholders`
  (`:1402-1406`). `GetStringWidth` returns 0 for an unexpanded `{PLAYER}`, so a placeholder in the location field
  centres as if empty and then prints nothing. That field gets its own explicit warning.
- The **entries-per-person stride is duplicated as a literal** in the engine's indexing maths. See §1.7 / the
  Phase 1 spec — the tab must refuse to show entries the engine can't read.

Still advisory about length: `{PLAYER}`/`{RIVAL}` expand at runtime so counts are a lower bound. The widget
colours, warns, red-highlights the overflowing characters, and refuses Enter past the line budget — it never
truncates.

- ⚠ `ui/dex_description_edit.py` is a raw char counter with no `{...}` stripping and no `\l`/`\p` handling —
  dropped in as-is it flags **every vanilla entry** as overflow. That is why `FameCheckerTextEdit` exists.
  `core/gba_text_metrics.py` is generic, so the rest of the app's text fields could adopt it later.

## 4b. Phase-2 regression baseline (measured on the live test project)
The 118 `famechecker` calls split **101 flavor / 17 pickState**. The 101 flavor calls cover **exactly 96 distinct
(person, index) pairs** — **0 orphans, 5 duplicates**. Use that as the fixture for the orphan/duplicate detector.
*(Numbers are that project's; the detector must compute them, not assume them.)*

## 4c. Portrait / sprite pre-flight (audit round 2, 2026-07-22) — READ BEFORE WRITING PHASE 1's SPRITE PIECE
Engine-verified hazards. Each one silently produces wrong behaviour if missed.

- **H1 — the custom-vs-trainer decision exists TWICE, on different indices.** `CreatePersonPicSprite` (`:1342-1377`)
  branches on a hardcoded 4-way `if/else if`; `DestroyPersonPicSprite` (`:1379-1393`) repeats the same literal chain
  against `who_copy`, derived with a `- 1` fudge when `who == numUnlockedPersons - 1` — **not** the index Create got.
  Disagreement leaks tiles+palette for the session, or frees tiles the shared sheet still owns. Generate a single
  `sFameCheckerUsesCustomPic[N]` **and delete the `who_copy` fudge** — one boolean read through two different
  indices is still two decisions.
- **H2 — custom portrait sheets load unconditionally at init: this is the real ceiling.** `sUISpriteSheets`
  (`:418-428`) loads all four person sheets at 0x800 bytes each the moment the UI opens, used or not — 8 KB of
  32 KB OBJ VRAM before a single sprite exists. Per-person custom art costs **2 KB VRAM per person up front**, not
  per displayed person. Compute and SHOW that budget before letting anyone add the fifth.
- **H3 — `FreeNonTrainerPicTiles` frees four hardcoded tags** (`:1306-1312`). An Nth portrait leaks its tiles on
  every exit until reset. Regenerate from the same list, same length, as the sheet table.
- **H4 — custom portrait palettes bypass the palette allocator entirely.** Neither is in `sUISpritePalettes`; both
  are raw `LoadPalette(..., OBJ_PLTT_ID(PERSON_PAL_NUM), ...)` into **fixed slot 6**, with `oam.paletteNum` set by
  hand, and the trainer path passes `TAG_NONE` for the same reason. So `IndexOfSpritePaletteTag` is blind to them,
  and **slot 6 is time-multiplexed** — `sSilhouettePalette` overwrites it whenever `pickState == SILHOUETTE`
  (`:1374-1375`). A preview must state WHICH state it is showing or it disagrees with the game half the time.
- **H5 — `SPRITETAG_DAISY` is 1006 and the source itself warns it is shared** with other NPCs (`:27`). Minting new
  tags by "next free number" will collide; scan for existing literals and surface the known collision as `info`.
- **H6 — informant icons key on the graphics info's palette tag, NOT the gfx id.** `CreateFameCheckerObject`
  (`event_object_movement.c:2076-2101`) forces `paletteTag = TAG_NONE`, then loads via
  `gObjectEventGraphicsInfo[gfxId].paletteTag`. So `CAT_OVERWORLD` keyed by that tag — never by `OBJ_EVENT_GFX_*`.
  There is **no `ensure_overworld_palette` on the bus yet**; add one following `ensure_trainer_palette_from_png`.
  Subsprite tables are applied, so a naive 16x16 preview crops tall/wide graphics.
- **H7 — six icons render at once** (`:1099-1123`), each pulling its own overworld palette, on top of slot 6, the
  cursor and the spinning pokeball, against 16 OBJ slots. Distinct palettes on all six can exhaust them. Compute
  the warning; don't guess it.
- **H8 — a locked entry renders a question mark at a different Y** (`:1117-1121`), not the chosen graphic. The
  preview must show locked-vs-unlocked or the user sets an icon they will never see in game. Ties to Phase 2.
- **H10 — `.gbapal` handling, decided (audit round 5).** Extend `core/overworld_palette_io.py` — three readers
  already exist and a fourth is exactly the drift the sprite rule prevents; it is also the only one that survives
  JASC text written to a `.gbapal` path. The bus gets `ensure_fame_checker_pic_palette(...)` which CALLS
  `read_palette_pair` — no decoder in the bus. **`bg.gbapal` is 64 bytes = TWO palettes**
  (`gFameCheckerBgPals[][16]`, loaded `2 * PLTT_SIZE_4BPP`) and `decode_gbapal` truncates to 16 while
  `encode_gbapal` always writes 32 — so check the on-disk size and refuse anything but 32 bytes rather than let it
  be quietly reshaped. **No `.4bpp` decoder is needed**: every portrait ships as a PNG, so the preview is
  `ensure_*` + `load_sprite_pixmap` like every other sprite site. `write_palette_pair` would CREATE `.pal`
  siblings that don't exist in that folder today — fine long-term, but decide it when the writer lands, not by
  discovering it in `git status`.
- **H9 — non-negotiables.** Every preview through `core/sprite_render.load_sprite_pixmap` + the bus (a `.gbapal`
  reader is needed; `sOakSpriteGfx` + `sOakSpritePalette` is the test case). Both dropdowns through
  `_field_lock(..., needs_symbol=False)` via the existing `_combo` — do not add a second gate. Project close must
  `get_bus().clear()` including the new category. `load()` must reset the new visual state per the tab contract.
  **A missing asset must LOCK, not default** — never offer to write a file the project never had.

## 4d. Severity rule (audit round 5, 2026-07-22) — learned the hard way
**Severity follows whether the MODEL is wrong, not how bad the underlying defect is.**

`blocking` disables Save, locks every field AND hides the entries. It is for one thing only: *the parsed model may
be wrong, so writing it back would persist misaligned or invented data.* The entries-per-person stride mismatch
earns it — it misaligns every entry.

A portrait create/clean-up disagreement does **not**. It is a real defect in the user's project (the running game
leaks tiles and a palette on every close) but every table, person, entry, name, quote and caption still parses
correctly. So it is a `warn` that locks the **portrait fields only**.

Two reasons this is not merely tidier:
1. The detector for it can never be complete — the shapes a project can write that dispatch in are open-ended
   (`if` chain, `switch`, ternary, helper function, and combinations). A check that cannot be perfect must not be
   able to brick the tab. Scoped, a false positive is an annoyance; global, it is a dead editor.
2. It stops a check from being load-bearing for data it has nothing to do with.

**Corollary — an empty result set is not an answer.** If the anchor exists but no branch was recognised, say
"this project uses a form the editor cannot read", never "these people are on one side only". The second is an
accusation built on a failed parse, and it turns every unreadable shape into a false alarm.

## 4e. Phase 2 pre-flight — unlock points (audit round 7, 2026-07-22) — READ BEFORE WRITING PHASE 2
Measured on the live project: **118 calls — 38 in `data/scripts/fame_checker.inc`, 80 under `data/maps/`;
101 flavor (2-arg), 17 pickState (3-arg).**

- **P2-A — ZERO calls use `function=`.** All 17 three-arg calls are POSITIONAL
  (`famechecker FAMECHECKER_AGATHA, FCPICKSTATE_COLORED, UpdatePickStateFromSpecialVar8005`). GAS accepts both
  forms, so the parser must handle both. **§Phase 2's "disambiguate by the `function=` parameter" is wrong** —
  building against a form the project never uses classifies all 17 pickState calls as flavor unlocks with index
  `FCPICKSTATE_COLORED` (= 1).
- **P2-B — both handlers SILENTLY NO-OP out of bounds.** `if (person < NUM_FAMECHECKER_PERSONS && index < 6)`,
  no assert, no error. A project that renumbers persons or adds a seventh entry gets scripts that run, print their
  text, and quietly never unlock. **Highest-value thing Phase 2 can surface, and a pure read.**
- **P2-C — `index < 6` is the hardcoded stride, third site.** `_hardcoded_strides` scans index arithmetic; this
  bound is a COMPARISON in the same file and gates the script path. Extend the stride check to bounds comparisons.
- **P2-D — "duplicate" is NOT a defect.** All five duplicate (person, index) pairs are one map call PLUS one call
  in the shared `data/scripts/fame_checker.inc`. Two NPCs telling you the same fact is legitimate design.
  **ORPHAN is the defect** (an entry no call can ever unlock = dead text). Never offer to "fix" a duplicate by
  deleting one.
- **P2-E — a third of the calls have no single owning map.** The 38 in `data/scripts/fame_checker.inc` sit in
  labels referenced from several maps via `data/event_scripts.s`. Showing the file path as if it were the map
  would be wrong for a third of the data; it needs a label->referrer index across all of `data/`.
- **P2-F — some of those shared scripts are DEAD** (`EventScript_PokemonJournalUnused1/2` contain live-looking
  calls). A call inside an unreferenced label must not satisfy "this entry is reachable" — that is how an orphan
  hides.
- **P2-G — a flavor unlock has a SIDE EFFECT on script state.** `SetFlavorTextFlagFromSpecialVars` sets
  `gSpecialVar_0x8005 = FCPICKSTATE_SILHOUETTE` and calls the pickState updater. So `famechecker` is not pure: a
  following line that reads `VAR_0x8005` changes meaning, and removing a flavor call also removes the
  silhouette-reveal that made the person appear at all. Neither is visible from the macro invocation.
- **P2-H — the person argument need not be a constant.** `person:req` accepts a const, a bare number or a `VAR_*`.
  Every call here is a constant — exactly the condition under which a constant-only parser looks correct and
  silently drops the rest. Classify by resolution success and REPORT unresolvable arguments.
- **P2-I — EVENTide already stores these args UNSPLIT** (`eventide/backend/eventide_utils.py`, `args` as one
  opaque string; labelled "Fame Checker" in the event editor). Changing how they are stored is a round-trip risk
  for EVENTide's own writer — same class as the `.inc` one-writer problem. Settle ownership before either side
  edits the line.
- **P2-J — WRITING is the real hazard and is not scoped.** These are macro invocations inside files Porymap and
  EVENTide also write. **Make Phase 2 read-only first** — locate, classify, flag orphans, flag out-of-bounds, show
  reachability — and treat add/move/remove as its own change with its own writer discipline.

**Order (cheapest to most valuable, each independently verifiable against the 118-call fixture):**
parse + classify (A, H) -> bounds check (B, C) -> orphan/duplicate with the right semantics (D) ->
reachability and map attribution (E, F) -> then, separately, writing (I, J).

## 5. Cross-editor collisions (dual-widget clobber risk)
- `ui/text_editor_tab.py:104` already exposes a **`fame_checker` text category** (limits `(36,20)` at
  `core/text_index.py:71`) writing the **same `.inc`**. One shared model is required.
- `eventide/ui/event_editor_tab.py:685` already parses/renders the `famechecker` command — Phase 2 must build on
  it, not around it.
- `UseFameChecker` is reachable only via the Fame Checker key item (`src/item_use.c:841,852-863`). A quest
  tracker is useless if that item is unobtainable — the tab should say so.

**Agreed resolution — one writer owns the `.inc`:**
1. `core/fame_checker_data.py` gains `write_text_strings(project_dir, {symbol: text})` that rewrites **only** the
   labels passed in and preserves byte-for-byte every other label (foreign *and* untouched owned ones), block
   order, and the `@ 0x…` comments on untouched labels. Changed strings are re-split after `\n` / `\l` / `\p` so
   the file stays diff-readable.
2. `ui/text_editor_tab.py` routes its `fame_checker` category through that same writer instead of its generic
   `.inc` path. Its `(36, 20)` char limit at `core/text_index.py:71` is a **character** cap and §4 established
   that character caps are unsound for a proportional font — 36 chars of `W` is 252 px in a 208 px window,
   while 36 chars of narrow text fits fine. That limit should move to `core/gba_text_metrics.py` too.
3. **Cross-invalidate:** whichever tab saves emits a signal the other reloads on (same discipline as the sprite
   bus). Without this the stale tab's in-memory copy overwrites the fresh file on its next save — that is the
   actual clobber.
4. Interim if (2) slips past Phase 1: each tab refuses to save while the other has unsaved `fame_checker` edits,
   with an explicit message. Ugly but honest; silent clobber is not acceptable.

**Sprite-bus resolution (§1.4):** add `CAT_FAME_CHECKER_PIC` keyed by person const, with
`ensure_fame_checker_palette(png, gbapal)` reading 16 × little-endian BGR555 and falling back to the PNG's own
table. Lift the existing `.gbapal` reader (`core/battle_anim_data.py`, `core/dynamic_ow_pal_patch.py` both have
one) into a shared helper rather than writing a third. Trainer-pic portraits stay on `CAT_TRAINER_PIC`,
informant icons on `CAT_OVERWORLD`.

## 6. What audit round 1 corrected (2026-07-21)
1. Six tables → **nine**; tables 8 & 9 are live and were entirely missing (crash risk).
2. `AllocZeroed(17*…)` heap overflow — was not in the plan at all.
3. `data/text/fame_checker.inc` holds 14 foreign strings — naive regeneration deletes them.
4. `FameCheckerSaveData` is **2 bytes, not 4**; `global.h` offset comments are stale by 0x20.
5. `unused_3A94[64]` is already claimed by the start-menu plan → append at the SaveBlock1 tail instead.
6. Second index special-case `AdjustGiovanniIndexIfBeatenInGym` (literal 9) — missed.
7. Custom-art special-case is 4-way and duplicated across two functions — missed.
8. `ResetFameChecker` hardcodes OAK as the always-visible person — missed.
9. Non-trainer names live in `src/strings.c`, not the `.inc`.
10. Entries > 6 is a screen-layout redesign, not a table resize.
11. The trainer-linked list name comes from `gTrainers[]`, not table 5 — Phase 1 design error.
12. Phase 2 scope: 118 calls across all of `data/`, with an overloaded second argument.
13. Text limits measured; `DexDescriptionEdit` is unsuitable as-is.
14. `text_editor_tab` / EVENTide already touch this data.
15. Parser hazards — see §7.

## 6b. What audit round 2 corrected (2026-07-21)
1. **BLOCKER — the "append at the SaveBlock1 tail" advice was wrong and would destroy every existing save.**
   Checksums are computed over a size derived from `sizeof(SaveBlock1)`, so growing the struct at all fails
   validation on both slots. Replaced with the in-place padding-swap rule (§1.5).
2. Restored the dropped requirement that **new person slots be zeroed on load** (`fame_checker.c:1143`).
3. Donor padding is now **discovered per project**, not hardcoded — PorySuite serves every decomp, and the
   arrays observed here are one project's shape (see the project-agnostic rule at the top).
4. Parser: symbol ownership was prefix-based and reported **all 334 labels foreign** on a renamed (quest-tracker)
   project → now derived from what tables 4/5/6/8/9 reference.
5. Parser: pseudo-consts were searched only in the `.c` → moving them to the header silently made every custom
   person trainer-named, with no warning. Now searches both, and asserts the invariant.
6. Parser: `(0xFE00 + 2)` define form silently missed → general expression evaluator + a reported failure.
7. Parser: table 5 had **no length check** → a short table silently misaligned *every* quote and would have
   written it back on Save. Now refuses to slice and reports.
8. Parser: missing-key checks extended from one designated table to all three.
9. `gFameCheckerText_Cancel` (+5 other shared UI strings) no longer leak into the editable model.
10. Measured: **17** pickState calls, not "~16". `:975` indexes `+ data[1]`, not `+ i`. Real `sizeof(SaveBlock1)`
    is 0x3D48 and the assert headroom (184 B) was never the binding constraint.
11. Verified against six hostile project shapes (vanilla / renamed symbols / defines-in-header / paren-literal /
    short table / missing designated key) — all now resolve correctly or report loudly.

## 6c. What audit round 5 corrected (2026-07-21)
1. **Person constant VALUES were discarded** — `enumerate()` position was used as the engine index, but the engine
   indexes the positional tables by the constant's value. A duplicate or a gap misaligned every entry **while the
   designated tables still resolved by name**, so each person showed the right portrait with the wrong objectives.
   Now the values are parsed and verified to be a contiguous 0-based run; if not, the tab refuses to load with an
   explanation rather than guessing (a gap also means the engine's own loops are already wrong).
2. The "table 4 is dead data" verdict leaked through the bare-numeric branch, asserting a false diagnosis. Now
   suppressed whenever a slot merely could not be proven.
3. Custom names are also looked up in `src/fame_checker.c`, not just `src/strings.c`.
4. **Severity model added** (`FameCheckerProblem` + `blocking_problems`) so the UI's disable-Save rule is
   data-driven instead of matching message text.
5. Verified: duplicate/gap → refuses with reason; no-base+literals → 1 warn (was 5 problems incl. a false one);
   short name/quote → `blocking=1`; a synthetic **E=4** project derives E=4 correctly.

### Audit round 9 (2026-07-22) — the text-metrics round
Two BLOCKING and ten WARN findings, all real, all fixed and verified. The ones worth remembering:
- **`charmap.txt` parsing has two landmines.** `'=' = 35` breaks `split("=")`; the escaped-quote entry needs
  un-escaping to an apostrophe (3 px, the commonest punctuation in English) while the `\l` / `\n` / `\p` entries
  must NOT be un-escaped, or the letters l/n/p get mapped onto the line-break bytes.
- **A successful parse is not a correct parse.** `errors="replace"` produced a plausible 140-entry charmap with 169
  glyphs collapsed onto one character, and still reported `exact=True`. Fixed by strict decoding plus sanity checks
  including a collision count.
- **`\l` does not give the page a fresh line budget** — only `\p` resets the row. Corrected to a slot model.
- **A font switch that ends a line was being dropped**, because runs only carried a font alongside literal text.
- **`FONT_NORMAL_COPY_1` never matched `sFontNormalCopy1LatinGlyphWidths`** — underscores. Both sides now normalise.
- **Anchoring a regex on a function NAME finds the forward declaration first.** The icondesc budget is anchored on
  the `AddTextPrinter*(FCWINDOWID_ICONDESC` call instead, scanning backwards.
- **A `[^)]*` parameter scan cannot read these printer helpers** — they take a `void (*callback)(...)` argument
  whose own parens end the scan, making the definition unfindable. Balanced matching only.
- **Line height comes from the PRINTER CALL, not `gFontInfos[]`.** The renderer steps by
  `printerTemplate.lineSpacing`: `AddTextPrinterParameterized2` hardcodes `y=1, lineSpacing=1` in its body, while
  the icon-description calls pass `y=0/10, lineSpacing=2` as arguments. Reading `gFontInfos` under-counts the step
  and over-reports the budget — telling the user they have a line the engine will clip.
- **A blocking check must be verified against a KNOWN-GOOD project before shipping.** Two of my own checks flagged
  healthy vanilla: the stride detector read a declared table size as a stride, and it also matched strides written
  inside comments and string literals. `blocking` disables Save AND locks every field AND hides the entries, so a
  false positive is strictly worse than a false negative here.
- **An import guarded by `except: X = None` must still raise a problem.** With the measurer module unimportable,
  every counter went silently blank — no numbers, no red, no "estimated" — and the diagnostics panel said nothing
  was wrong. A quiet nothing is the one outcome that must never happen.
- **My own first stride detector flagged healthy vanilla** by reading a table's declared size as an index stride.
  Verify a new blocking check against a KNOWN-GOOD project before trusting it.

## 7. Parser hazards (`core/fame_checker_data.py`)
- `errors="ignore"` **silently deletes** characters on a non-UTF-8 project (`é` vanishes and is lost on write) —
  use `surrogateescape` or fail loudly.
- `_designated_values` returns `""` for a missing person instead of reporting it; a regenerator would then
  write `0`.
- Table 5 is declared with an **explicit size** `[2 * NUM_FAMECHECKER_PERSONS]` — re-emit the expression, not a
  literal.
- The `// OAK` / `// DAISY` section comments in tables 7–9 are the only thing making 96-line tables readable —
  regeneration must re-emit them.
- `parse_text_strings` concatenates consecutive `.string` lines and loses the split points — regeneration must
  re-split after `\n` / `\l` / `\p`.
- The `@ 0x81AD106` ROM-address comments will be dropped: a deliberate decision, not an accident.
