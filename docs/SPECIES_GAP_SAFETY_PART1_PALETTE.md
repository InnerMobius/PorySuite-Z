# PorySuite bug: appended species render as black silhouettes in battle

## Summary

PorySuite's "append species" feature expands a project past pokefirered's built-in species range,
but it does not patch the one vanilla engine guard that hardcodes the old upper bound. The result:
**every appended species renders in battle as a correct-shaped, entirely black silhouette**, front
and back, player and opponent side. The species' own sprite and palette files on disk are fine, and
PorySuite's own previews render them correctly, which makes this look like a project data problem
when it is an engine problem.

This affects **every project that has ever used append-species**, not just the one it was found in.
It needs a patcher in PorySuite, not a hand edit in the game repo.

## Symptom as reported by a user

> "dont understand why his backsprite is black in game but loads fine in porysuite"

Species #488, the last in their dex. In game the battle back sprite was a solid black shape with
the correct outline. In PorySuite's Species Graphics tab the same sprite showed its real colors and
the Battle Scene Preview rendered it correctly.

## Root cause

`src/pokemon.c`, function `GetMonSpritePalFromSpeciesAndPersonality`. This is **unmodified
vanilla** — byte-identical to `READONLYREFERENCE/pokefirered/src/pokemon.c:5904-5916`:

```c
const u32 *GetMonSpritePalFromSpeciesAndPersonality(u16 species, u32 otId, u32 personality)
{
    u32 shinyValue;

    if (species > SPECIES_EGG)              // <-- SPECIES_EGG is 412
        return gMonPaletteTable[0].data;    // <-- gMonPalette_CircledQuestionMark

    shinyValue = GET_SHINY_VALUE(otId, personality);
    if (shinyValue < SHINY_ODDS)
        return gMonShinyPaletteTable[species].data;
    else
        return gMonPaletteTable[species].data;
}
```

In vanilla, `SPECIES_EGG` (412) genuinely is the last valid id, so this guard means "invalid
species, show the question mark". `gMonPaletteTable[0]` is `gMonPalette_CircledQuestionMark`, which
is near-black.

Append-species produces ids **above** 412. In the project this was found in:

| id range | contents |
|---|---|
| 1 – 411 | the original species block |
| 412 | `SPECIES_EGG` |
| 413 – 439 | an EMPTY HOLE — no names, no palette entries, NULL icon table entries |
| 440 – 518 | the 79 appended species (`NUM_SPECIES + 28` .. `NUM_SPECIES + 106`) |

`NUM_SPECIES` is still `SPECIES_EGG` = 412. PorySuite adds `NUM_TOTAL_SPECIES` (here
`NUM_SPECIES + 107`) and defines each appended species as `(NUM_SPECIES + n)`. Vanilla has no
`NUM_TOTAL_SPECIES` at all — grep the reference, it does not exist — which confirms this entire id
block is tool-generated.

Every appended species therefore takes the `species > SPECIES_EGG` branch. Their **tiles** load
correctly, because the pic tables are indexed without any such guard, so the silhouette is right
and only the palette is wrong.

Why PorySuite previews look fine: they read the `.pal` file from disk through `core/sprite_render`
plus `core/sprite_palette_bus` and never execute this function.

## Why this is PorySuite's job, not the project's

The tool creates the condition (ids past 412) and owns the constant that describes the new bound
(`NUM_TOTAL_SPECIES`). Per the project rule that PorySuite-Z is an engine refactoring tool, the
patcher should refactor the engine to match the data the tool emits. Leaving it to each project
means every user who appends species silently gets black battle sprites with no way to connect the
two facts.

Integration points / prior art to look at:

- `core/append_species.py` — emits `NUM_TOTAL_SPECIES` and the `(NUM_SPECIES + n)` defines.
- `core/deunown_patch.py` — an existing engine patcher, likely the pattern to follow.
- `core/pokemon_data_extractor.py` — also references `NUM_TOTAL_SPECIES`.

## The fix applied in the game repo, for reference

```c
    if (species >= NUM_TOTAL_SPECIES || gMonPaletteTable[species].data == NULL)
        return gMonPaletteTable[0].data;

    shinyValue = GET_SHINY_VALUE(otId, personality);
    // The shiny table has no SPECIES_EGG entry, so a shiny egg would dereference NULL.
    if (shinyValue < SHINY_ODDS && gMonShinyPaletteTable[species].data != NULL)
        return gMonShinyPaletteTable[species].data;
    else
        return gMonPaletteTable[species].data;
```

Design points that matter. Do not simplify these away:

1. **Test the table entry, not an id range.** A patch of the form `if (species >= NUM_TOTAL_SPECIES)`
   alone is WRONG. It lets the 413–439 hole through and dereferences NULL `.data` pointers.
   Appended id blocks are not guaranteed contiguous with the base block, so a range test cannot be
   written correctly in general. `gMonPaletteTable[species].data == NULL` is self-maintaining and
   also covers holes the tool did not create.
2. **`SPECIES_EGG` must still reach its own palette.** Vanilla's `>` (not `>=`) deliberately lets
   412 through, and `gMonPaletteTable` has a real `SPECIES_PAL(EGG, gMonPalette_Egg)` entry.
   Emitting `>=` here silently breaks egg display in every project. The NULL test preserves vanilla
   behaviour because the egg has an entry.
3. **The shiny NULL check is a separate latent vanilla bug.** `gMonShinyPaletteTable` has no
   `SPECIES_EGG` entry, so a shiny egg dereferences NULL. Include it or not, but know it is
   independent of the append-species issue.
4. **The bounds test assumes `gMonPaletteTable` is sized `NUM_TOTAL_SPECIES`.** It is built with
   designated initializers, so its length is highest-id + 1. If append-species can ever emit an id
   >= `NUM_TOTAL_SPECIES`, this test is wrong. Verify that invariant in the generator.

## Note on matching the source when patching

The target text differs between an unpatched project (vanilla's four lines) and one already
carrying this fix. Whatever matching strategy the patcher uses has to cope with both, since
projects get re-patched. Stating this as a fact about the input, not prescribing an approach.

## Related landmines found in the same audit

Each is a separate decision. Listed so they are not rediscovered one at a time. Only the palette
guard was fixed; these were left alone.

1. **`GetMonSpritePalStructFromOtIdPersonality`** (`src/pokemon.c`, immediately below the patched
   function) indexes `gMonShinyPaletteTable[species]` and `gMonPaletteTable[species]` with **no
   bounds check at all**. Harmless for real species; an out-of-range id reads past the array end.
2. **`src/pokemon_icon.c`** — six bounds checks written `if (species > NUM_TOTAL_SPECIES)`. Off by
   one: valid indices are 0 .. NUM_TOTAL_SPECIES-1, so the highest id + 1 passes the guard and
   reads one element past the end of both `gMonIconTable[]` and `gMonIconPaletteIndices[]`. Should
   be `>=`. Not currently reachable; becomes a live crash the moment one more species is appended.
3. **The 413–439 id hole itself.** Append-species left 27 ids with no name, no palette entry and
   NULL icon table entries. Worth deciding whether the generator should pack appended ids
   immediately after `SPECIES_EGG`, or whether the gap is intentional.
4. **Debug menu species list** (`src/debug_menu.c`) — was using a raw species id as a scroll index,
   so it stopped at id 411 plus one garbage row and every appended species was unreachable. Fixed
   in the project by mapping a gap-free option index onto the real id blocks. If PorySuite ships or
   patches a debug menu, it has the same bug.
5. **`src/mystery_gift.c`** has `if (stamp[STAMP_SPECIES] >= NUM_SPECIES)` — same class of
   hardcoded bound. Not investigated.

## How to verify a fix

1. Take any project that has used append-species. Put an appended species (id above `SPECIES_EGG`)
   into the party.
2. Start a battle. Its **back** sprite must show real colors, not a black silhouette.
3. Fight one so the **front** sprite renders too. Both used the same lookup.
4. Check a base-block species (id < 412) is unchanged.
5. Check an **egg** in the party still displays normally. It goes through the same function and is
   the easiest thing to break with a careless `>=`.
6. If the project has an id hole, confirm nothing crashes when the engine touches those ids.
