# PorySuite: gap-safe species bounds checks — part 2 (icon + remaining sites)

Follow-up to the black-silhouette report. That one covered a single site
(`GetMonSpritePalFromSpeciesAndPersonality`). This covers the rest of the same class, now that the
ID hole has been confirmed permanent.

## Why this is a real work item and not theory

Your own answer settled it: IDs 413–439 are vanilla's Unown letter-form block, they stay empty
forever because renumbering would break save files and every ID-based reference, and therefore
**the engine must be gap-safe rather than range-safe**. That makes every remaining range-based
species guard a latent null dereference, not a style issue.

The principle, stated once:

> Ask "does this species have a table entry?" — never "is this number inside a range?"

A range test admits every ID in the hole. Whether that crashes depends on which table gets indexed
next, which is why the sites below need to be judged individually rather than sed-replaced.

## Site 1 — `src/pokemon_icon.c`, seven guards, all wrong twice over

Lines 1129, 1164, 1174, 1205, 1230, 1263, 1270. Every one reads:

```c
if (species > NUM_TOTAL_SPECIES)
```

**Defect A — off by one.** Valid indices are `0 .. NUM_TOTAL_SPECIES - 1`. `gMonIconTable[]` and
`gMonIconPaletteIndices[]` are both sized `NUM_TOTAL_SPECIES`. ID `NUM_TOTAL_SPECIES` itself passes
the guard and reads one element past the end of both. Should be `>=` at minimum.

**Defect B — range-based, so it admits the hole.** IDs 413–439 sail through. What happens next
differs per table, and this distinction matters when writing the patch:

- `gMonIconPaletteIndices[hole]` is implicitly `0` — a *valid* palette index. No crash; the icon
  just draws with palette 0. Wrong colors, no worse.
- `gMonIconTable[hole]` is implicitly `NULL`. That is the crash. It flows
  `GetMonIconTiles` → `GetMonIconPtr` → sprite `images` → `UpdateMonIconFrame` (line 1300) →
  `RequestSpriteCopy(NULL, ...)`.

So the palette-index guards want the off-by-one fix; the **icon table** path wants a genuine
existence test.

**Defect C — `GetMonIconTiles` (line 1179) has no guard at all.**

```c
const u8 *GetMonIconTiles(u16 species, bool32 extra)
{
    const u8 *iconSprite = gMonIconTable[species];
    return iconSprite;
}
```

This is the function that actually returns the NULL. Guarding its seven callers individually is the
fragile way to fix it; guarding here is the one that cannot be bypassed.

**Defect D — ordering bug at line 1122–1130.** The guard runs *after* the out-of-range read it is
meant to prevent:

```c
    struct MonIconSpriteTemplate iconTemplate =
        {
            ...
            .paletteTag = POKE_ICON_BASE_PAL_TAG + gMonIconPaletteIndices[species],   // <-- reads here
        };

    if (species > NUM_TOTAL_SPECIES)                                                  // <-- checks here
        iconTemplate.paletteTag = POKE_ICON_BASE_PAL_TAG;
```

The initializer has already indexed the array by the time the check runs. Fixing the comparison
alone does not fix this one — the species has to be clamped *before* the initializer.

Note there are two near-identical constructors in this file (`CreateMonIcon` and
`CreateMonIcon_HandleDeoxys`); check both.

## Site 2 — `src/pokemon.c`, `GetMonSpritePalStructFromOtIdPersonality`

Directly below the function fixed in part 1. No bounds check of any kind:

```c
const struct CompressedSpritePalette *GetMonSpritePalStructFromOtIdPersonality(u16 species, u32 otId, u32 personality)
{
    u32 shinyValue;

    shinyValue = GET_SHINY_VALUE(otId, personality);
    if (shinyValue < SHINY_ODDS)
        return &gMonShinyPaletteTable[species];
    else
        return &gMonPaletteTable[species];
}
```

Returns a pointer to the struct rather than the `.data` field, so it cannot use the part-1 fix
verbatim — the caller dereferences `.data` later. Decide whether it returns
`&gMonPaletteTable[0]` on a miss (matching part 1's fallback) or whether callers should be made to
handle NULL. Worth checking what its callers actually do before choosing.

## Site 3 — `CreateMonIconSprite` (`src/pokemon_icon.c:1316`) has no `MAX_SPRITES` guard

Unrelated to the ID hole, same file, cheap to fold in:

```c
    spriteId = CreateSprite(&spriteTemplate, x, y, subpriority);
    gSprites[spriteId].animPaused = TRUE;          // <-- gSprites[MAX_SPRITES] if creation failed
```

`CreateSprite` returns `MAX_SPRITES` on failure and this writes three fields through it before
returning. Callers that check `id != MAX_SPRITES` (e.g. `src/main_menu.c:909`) check too late — the
out-of-bounds write already happened. Note this project raised `MAX_SPRITES` from 64 to 128, so the
address being clobbered is not the same as vanilla's.

## Site 4 — `src/mystery_gift.c:290`

```c
if (stamp[STAMP_SPECIES] >= NUM_SPECIES)
```

Same class of hardcoded bound. Not investigated — flagging only. Mystery Gift stamps may legitimately
be restricted to the base block, in which case this is correct as written and should be left alone.

## Site 5 — debug menu species list (informational)

`src/debug_menu.c` used a raw species ID as its scroll index, so its add-pokemon and add-egg rows
stopped at the last base-block species plus one garbage row, and **every appended species was
unreachable**. Already fixed in the project by mapping a gap-free option index onto the real ID
blocks, skipping `SPECIES_EGG` and the hole.

Listed because if PorySuite ships, generates, or patches a debug menu, it has the same bug — and
because it is a good illustration that the fix pattern is not always "add a bounds check": here the
correct fix was an index→ID mapping, since a UI list must not show empty rows.

## Deliberately NOT changed in the project

None of sites 1–4 were touched. The project is left in vanilla state at those spots so the patcher
has predictable text to match. Site 5 is already fixed there.

One exception to be aware of: `GetMonSpritePalFromSpeciesAndPersonality` (part 1) **is** already
patched in that project, so a patcher will meet modified text there and vanilla text at every other
site.

## How to verify

1. A project with appended species, plus a debug way to force an ID inside the hole (413–439) and
   the exact ID `NUM_TOTAL_SPECIES`.
2. Party menu, PC boxes, trade screen, Pokédex list — anywhere mon icons are drawn. Neither ID may
   crash or draw from a NULL pointer.
3. Confirm an ordinary appended species still shows its correct icon and palette — the fix must not
   push valid species onto the fallback path.
4. Confirm eggs still display normally; they sit at the boundary these guards are written around.
5. Exhaust the sprite pool and create one more mon icon, to cover site 3.
