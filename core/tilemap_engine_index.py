"""
core/tilemap_engine_index.py
What the engine source says about each tilemap `.bin`.

A `.bin` tilemap is headerless. Nothing in it records how wide it is, how many
bytes a cell takes, which tile sheet it indexes into, or which palettes its cells
select between. The Tilemap Editor used to guess all four from the filename and
the file size, and measurably got them wrong: `item_menu/list.bin` was shown 27
cells wide where `item_menu.c` declares 18, the party-menu slot layouts were read
as 16-bit when the engine reads them as bytes, and `party_menu/slot_main.bin` was
paired with `pokeball.png` while the `bg.png` it belongs to sat in the same
folder.

All four facts are written down — in C, in the `INCBIN` declaration itself:

    const u32 gPartyMenuBg_Gfx[]     = INCBIN_U32("graphics/party_menu/bg.4bpp.lz");
    const u32 gPartyMenuBg_Pal[]     = INCBIN_U32("graphics/party_menu/bg.gbapal.lz");
    const u32 gPartyMenuBg_Tilemap[] = INCBIN_U32("graphics/party_menu/bg.bin.lz");

The sheet and the palette are the `_Gfx` and `_Pal` sitting in the same
blank-line-separated block as the `_Tilemap`. The cell size is the `INCBIN_` width
for an uncompressed file. The declared array bound gives the cell count outright
when it is symbolic (`sItemListTilemap[LIST_TILES_WIDTH * LIST_TILES_HEIGHT]`).

This module reads those facts so the editor does not have to guess them. It is
READ-ONLY with respect to the game tree — it parses `src/` and never writes there.

Coverage, measured 2026-10-28 on this project: 285 of 297 `.bin` files on disk are
declared somewhere in `src/`, and 221 of those sit in a block that also names a
sheet and/or a palette. The remaining handful fall back to the editor's old
filename heuristics, which is why those are kept rather than deleted.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger("PorySuite.TilemapEngineIndex")

# `const u32 gFoo_Tilemap[] = INCBIN_U32("graphics/x/y.bin.lz");`
# The declaration half is optional so a bare INCBIN inside a struct still parses.
_INCBIN_RE = re.compile(
    r"(?:(?:static\s+)?const\s+(?:u8|u16|u32)\s+"
    r"(?P<sym>\w+)\s*\[(?P<bound>[^\]]*)\]\s*=\s*)?"
    r"INCBIN_(?P<width>U8|U16|U32)\(\"(?P<path>[^\"]+)\"\)"
)

# `#define LIST_TILES_WIDTH  18`
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(?P<name>\w+)\s+(?P<value>[^/\r\n]+)")

_GFX_SUFFIXES = (".4bpp", ".8bpp")
_PAL_SUFFIXES = (".gbapal", ".pal")


@dataclass
class BinFacts:
    """Everything the engine source states about one tilemap `.bin`."""
    bin_rel: str                                  # "graphics/item_menu/list.bin"
    symbol: str = ""                              # "sItemListTilemap"
    incbin_width: str = ""                        # "U8" | "U16" | "U32"
    compressed: bool = False                      # declared as the .lz
    declared_cells: Optional[int] = None          # from the array bound
    declared_dims: Optional[Tuple[int, int]] = None   # (w, h), when the bound
                                                      # is a product of two
                                                      # resolvable factors
    sheet_rel: str = ""                           # "graphics/party_menu/bg.png"
    palette_rels: List[str] = field(default_factory=list)
    declared_at: str = ""                         # "src/graphics.c:1070"
    slot_palettes: Dict[int, str] = field(default_factory=dict)
    """VRAM palette slot -> the palette file loaded into it.

    A cell's palette bits name a slot in BG palette RAM, and which palette is in
    which slot is decided by `LoadPalette` calls at runtime, NOT by the `INCBIN`
    block. Where the engine says it plainly —

        LoadPalette(gMoveRelearner_Pal, BG_PLTT_ID(0), PLTT_SIZE_4BPP);

    — this records slot 0 -> `graphics/interface/learn_move.gbapal`.

    Measured coverage is modest: of the 119 tilemaps that reference a slot the
    editor could not fill, 12 gain one this way and 10 have every slot they use
    accounted for. The rest reference slots filled through helper functions
    (`LoadUserWindowGfx`, `ListMenuLoadStdPalAt`), or through a getter rather
    than a symbol (`GetTextWindowPalette(2)`), or genuinely are not filled by
    that screen at all — `interface/learn_move.bin` has slot-4 cells and nothing
    in the move relearner loads slot 4, so in game they take whatever the
    previous screen left there. That last group cannot be resolved by reading
    further, which is why `unresolved_slots` exists to report them instead.
    """

    @property
    def cell_bytes(self) -> int:
        """Bytes per cell: 1 for a byte tilemap, 2 for a normal BG tilemap.

        Only an UNCOMPRESSED file's `INCBIN_` width tells us this. `INCBIN_U32`
        on a `.bin.lz` describes the compressed blob, not the cells, and an
        uncompressed `.bin` declared `U32` is a word-addressed 16-bit tilemap —
        so both mean "ordinary 16-bit cells".

        `INCBIN_U8` on an uncompressed `.bin` genuinely means one byte per cell:
        the party-menu slot layouts, the trade stripe maps, and the GBA's affine
        backgrounds, which have no flip or palette bits to carry.
        """
        if not self.compressed and self.incbin_width == "U8":
            return 1
        return 2


def _rel_to_png(gfx_rel: str) -> str:
    """`graphics/party_menu/bg.4bpp.lz` -> `graphics/party_menu/bg.png`.

    The editor works from the PNG the artist edits; the engine names the
    compiled form beside it.
    """
    p = gfx_rel[:-3] if gfx_rel.endswith(".lz") else gfx_rel
    for suf in _GFX_SUFFIXES:
        if p.endswith(suf):
            return p[: -len(suf)] + ".png"
    return ""


def _rel_to_palette(pal_rel: str) -> str:
    """Strip `.lz`. The `.pal` beside a `.gbapal` is the authored source of
    truth (the `.gbapal` is a build artifact), so prefer it when it exists —
    resolved against disk by the caller, which knows the project directory."""
    return pal_rel[:-3] if pal_rel.endswith(".lz") else pal_rel


def _collect_defines(src_dir: str) -> Dict[str, int]:
    """Integer `#define`s, for resolving a symbolic array bound.

    Only plain integers and products of already-known names are resolved — this
    is not a C preprocessor and does not try to be. An expression it cannot
    resolve simply leaves `declared_cells` as None.
    """
    raw: Dict[str, str] = {}
    for root, _dirs, files in os.walk(src_dir):
        for f in files:
            if not f.endswith((".c", ".h")):
                continue
            try:
                with open(os.path.join(root, f), encoding="utf-8",
                          errors="replace") as fh:
                    for line in fh:
                        m = _DEFINE_RE.match(line)
                        if m:
                            raw.setdefault(m.group("name"),
                                           m.group("value").strip())
            except OSError:
                continue
    out: Dict[str, int] = {}
    for name, value in raw.items():
        try:
            out[name] = int(value, 0)
        except ValueError:
            continue
    return out


def _eval_bound(bound: str, defines: Dict[str, int]) -> Optional[int]:
    """Resolve an array bound like `LIST_TILES_WIDTH * LIST_TILES_HEIGHT`.

    Accepts integers, known `#define` names, and `*`/`+` between them. Anything
    else returns None rather than guessing.
    """
    bound = bound.strip()
    if not bound:
        return None
    total = 0
    for addend in bound.split("+"):
        product = 1
        for factor in addend.split("*"):
            tok = factor.strip()
            if not tok:
                return None
            if tok.isdigit():
                product *= int(tok)
            elif tok in defines:
                product *= defines[tok]
            else:
                return None
        total += product
    return total or None


def _eval_dims(bound: str, defines: Dict[str, int]
               ) -> Optional[Tuple[int, int]]:
    """Pull (width, height) out of a two-factor bound.

    `sItemListTilemap[LIST_TILES_WIDTH * LIST_TILES_HEIGHT]` is the engine saying
    the map is 18 wide and 12 tall, which is the fact the editor's size guess got
    wrong (it showed 27 x 8). Only an `A * B` bound can say this; anything else
    returns None.

    Which factor is the width: a factor whose name mentions WIDTH or COLS wins,
    otherwise the first, because these are written width-first by convention.
    """
    factors = [f.strip() for f in bound.split("*")]
    if len(factors) != 2 or "+" in bound:
        return None
    vals: List[int] = []
    for tok in factors:
        if tok.isdigit():
            vals.append(int(tok))
        elif tok in defines:
            vals.append(defines[tok])
        else:
            return None
    names = [f.upper() for f in factors]
    if any(k in names[1] for k in ("WIDTH", "COLS", "COLUMNS")):
        return (vals[1], vals[0])
    return (vals[0], vals[1])


# A function definition at column 0, followed by its opening brace on the next
# line — the house style throughout `src/`.
_FUNC_RE = re.compile(r"^[A-Za-z_][\w \t\*]*\b(?P<name>\w+)\s*\([^;]*\)\s*$")
_LOADPAL_RE = re.compile(
    r"Load(?:Palette|Compressed(?:Palette|PaletteFast))\s*\("
    r"\s*(?P<src>\w+)\s*,\s*(?P<off>[^,]+),")
# A palette destination can be written three ways across forks and vintages of
# this engine, so all three are read rather than only the one this project
# happens to use:
#   BG_PLTT_ID(4)   the current macro — the argument IS the slot
#   PLTT_ID(4)      the same, without the BG offset
#   0x40 / 64       a raw palette-RAM index in colours; slot = index / 16
_SLOT_MACRO_RE = re.compile(r"(?:BG_)?PLTT_ID\s*\(\s*(\d+)\s*\)")
_SLOT_RAW_RE = re.compile(r"^\s*(0x[0-9a-fA-F]+|\d+)\s*$")


def _slot_from_offset(expr: str) -> Optional[int]:
    """The palette slot a `LoadPalette` destination expression names, or None.

    Returns None for anything computed at runtime (a variable, an array lookup),
    because guessing a slot is worse than reporting it as unresolved.
    """
    m = _SLOT_MACRO_RE.search(expr)
    if m:
        slot = int(m.group(1))
        return slot if 0 <= slot < 16 else None
    m = _SLOT_RAW_RE.match(expr)
    if m:
        value = int(m.group(1), 0)
        if value % 16 == 0 and 0 <= value // 16 < 16:
            return value // 16
    return None


def _iter_function_bodies(lines: List[str]):
    """Yield each top-level function body as one string, by brace depth.

    Deliberately simple: it only has to be good enough to tell which
    `LoadPalette` calls sit in the same function as a tilemap's symbol.
    """
    i = 0
    n = len(lines)
    while i < n:
        m = _FUNC_RE.match(lines[i].rstrip())
        if m and i + 1 < n and lines[i + 1].strip().startswith("{"):
            depth = 0
            j = i + 1
            body: List[str] = []
            while j < n:
                depth += lines[j].count("{") - lines[j].count("}")
                body.append(lines[j])
                if depth <= 0 and j > i + 1:
                    break
                j += 1
            yield "\n".join(body)
            i = j + 1
            continue
        i += 1


def _resolve_sheets_by_symbol(index: Dict[str, BinFacts],
                              sym_to_path: Dict[str, str],
                              game_dir: str) -> None:
    """Pair each tilemap with its tile sheet by what the two symbols are CALLED.

    The engine names a screen's assets after the screen:

        sKantoTrainerCardFront_Tilemap  <->  gKantoTrainerCard_Gfx
        sHoennTrainerCardFront_Tilemap  <->  gHoennTrainerCard_Gfx
        gPartyMenuBg_Tilemap            <->  gPartyMenuBg_Gfx

    Stripping the storage prefix and the role suffix leaves a stem, and the sheet
    whose stem shares the longest prefix with the tilemap's is its sheet. This
    works ACROSS FILES, which matters: the trainer card's tilemaps are declared
    in `trainer_card.c` and its sheets in `graphics.c`, so nothing local to one
    file can connect them. It also keeps the Kanto and Hoenn cards apart, where
    both sit in the same run of declarations.

    The winner is the most specific sheet whose name is an ANCESTOR of the
    tilemap's — every word of the sheet's name being a leading word of the
    tilemap's. `KantoTrainerCard` is an ancestor of `KantoTrainerCardBack`, so it
    wins; `KantoTrainerCardBadges` is a SIBLING of it, not an ancestor, so it is
    not a candidate at all. That is the relationship that matters: a screen's
    sheet is named after the screen, while its other assets are named after
    themselves.

    A sheet already resolved from a single-asset block is left alone. A tie, or
    agreement on fewer than `_MIN_STEM_WORDS` leading words, resolves to nothing
    rather than to a guess, and `discover_assets` falls back to its heuristics.
    """
    sheets: List[Tuple[Tuple[str, ...], str]] = []
    palettes: List[Tuple[Tuple[str, ...], str]] = []
    for sym, path in sym_to_path.items():
        words = _stem_words(_symbol_stem(sym))
        png = _rel_to_png(path)
        if png:
            sheets.append((words, png))
        elif any(s in path for s in _PAL_SUFFIXES):
            palettes.append((words, _rel_to_palette(path)))

    def best_ancestor(words, pool):
        """The most specific name in `pool` that is an ancestor of `words`."""
        best_len = 0
        best: set = set()
        for cand_words, rel in pool:
            if (len(cand_words) < _MIN_STEM_WORDS
                    or len(cand_words) > len(words)
                    or words[:len(cand_words)] != cand_words):
                continue                     # a sibling or unrelated, not an ancestor
            if len(cand_words) > best_len:
                best_len, best = len(cand_words), {rel}
            elif len(cand_words) == best_len:
                best.add(rel)
        if best_len < _MIN_STEM_WORDS or len(best) != 1:
            return ""
        rel = next(iter(best))
        return rel if os.path.isfile(
            os.path.join(game_dir, *rel.split("/"))) else ""

    # Symbol affinity is AUTHORITATIVE and overrides anything the block hinted.
    # A block holding exactly one sheet looks trustworthy and is not: a screen
    # can declare one of its SIBLING sheets (a badge strip) in the same run as
    # its tilemaps while the sheet those tilemaps use sits in another file. The
    # block is only consulted where affinity finds no ancestor at all.
    for facts in index.values():
        if not facts.symbol:
            continue
        words = _stem_words(_symbol_stem(facts.symbol))
        if len(words) < _MIN_STEM_WORDS:
            continue
        by_symbol = best_ancestor(words, sheets)
        if by_symbol:
            facts.sheet_rel = by_symbol
        if not facts.palette_rels:
            # One palette, by the same relationship. The trainer card has
            # eighteen SIBLING palettes (bronze / silver / gold / ...) and no
            # ancestor among them, which is correct: the engine picks one of
            # them at runtime by badge count, so the editor must not pretend to
            # know which. They stay reachable in the palette dropdown.
            pal = best_ancestor(words, palettes)
            if pal:
                facts.palette_rels = [pal]


def _attach_slot_palettes(src_dir: str, index: Dict[str, BinFacts],
                          sym_to_path: Dict[str, str]) -> None:
    """Fill in `slot_palettes` by reading the functions that use each tilemap.

    For every function body that mentions a tilemap's symbol, any
    `LoadPalette(<symbol>, BG_PLTT_ID(n), ...)` in that same body is taken as
    "slot n holds that palette while this tilemap is on screen".
    """
    tm_syms = {f.symbol: rel for rel, f in index.items() if f.symbol}
    if not tm_syms:
        return
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".c"):
                continue
            try:
                with open(os.path.join(root, fname), encoding="utf-8",
                          errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            for body in _iter_function_bodies(lines):
                present = [rel for sym, rel in tm_syms.items() if sym in body]
                if not present:
                    continue
                slots: Dict[int, str] = {}
                for m in _LOADPAL_RE.finditer(body):
                    slot = _slot_from_offset(m.group("off"))
                    if slot is None:
                        continue
                    pal = sym_to_path.get(m.group("src"))
                    if pal:
                        slots[slot] = _rel_to_palette(pal)
                if not slots:
                    continue
                for rel in present:
                    # An earlier, more specific reading wins; this only ever
                    # adds slots nobody has claimed.
                    for slot, pal in slots.items():
                        index[rel].slot_palettes.setdefault(slot, pal)


_ROLE_SUFFIXES = ("_Tilemap", "_TileMap", "_Map", "_Gfx", "_Tiles", "_Pal",
                  "_Palette", "_Pals", "_Palettes")


def _symbol_stem(symbol: str) -> str:
    """`sKantoTrainerCardFront_Tilemap` -> `KantoTrainerCardFront`.

    Drops the house-style `s`/`g` storage prefix and the role suffix, leaving the
    part that names WHICH asset family this is. Two declarations belonging to the
    same screen share a long stem; two that merely sit near each other do not.
    """
    stem = symbol
    if len(stem) > 1 and stem[0] in "sg" and stem[1].isupper():
        stem = stem[1:]
    for suf in _ROLE_SUFFIXES:
        if stem.endswith(suf):
            return stem[: -len(suf)]
    # Trailing digits on a variant (`sTrainerCardStickerPal1`) are noise.
    return stem.rstrip("0123456789")


_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


# Words that say what KIND of asset a declaration is rather than which asset,
# dropped wherever they appear. The role is not always a suffix:
# `gBattleAnimBgTilemap_Attract` and `gBattleAnimBgPalette_Attract` are the same
# asset family with the role in the middle, and only agree once it is removed.
_ROLE_WORDS = frozenset({
    "tilemap", "tilemaps", "map", "maps", "tile", "tiles", "gfx", "graphics",
    "pal", "pals", "palette", "palettes", "bin",
})


def _stem_words(stem: str) -> Tuple[str, ...]:
    """`KantoTrainerCardBack` -> ('kanto', 'trainer', 'card', 'back').

    Compared as WORDS, never as characters. Character prefixes reward accidental
    agreement inside a word: `KantoTrainerCardBack` and `KantoTrainerCardBadges`
    share eighteen characters because "Back" and "Badges" both start "Ba", which
    was enough to hand the card's back face the badges sheet.
    """
    return tuple(w for w in (w.lower() for w in _WORD_RE.findall(stem))
                 if w not in _ROLE_WORDS)


# Two names must agree on at least this many leading words to be called
# relatives, so a one-word stem cannot match half the project.
_MIN_STEM_WORDS = 2


def _blocks_for(lines: List[str]) -> List[int]:
    """Block id per line, where a blank line starts a new block. Mirrors how
    these declarations are grouped in `graphics.c` and the data headers."""
    ids: List[int] = []
    bid = 0
    for line in lines:
        if not line.strip():
            bid += 1
        ids.append(bid)
    return ids


def _build(game_dir: str) -> Dict[str, BinFacts]:
    src_dir = os.path.join(game_dir, "src")
    if not os.path.isdir(src_dir):
        _log.debug("no src/ under %s; engine index empty", game_dir)
        return {}

    defines = _collect_defines(src_dir)
    index: Dict[str, BinFacts] = {}
    # Every INCBIN'd symbol, so a `LoadPalette(gFoo_Pal, ...)` can be resolved
    # back to the file it names.
    sym_to_path: Dict[str, str] = {}

    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith((".c", ".h")):
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue

            block_of = _blocks_for(lines)
            # Everything INCBIN'd in this file, with its block.
            found: List[Tuple[int, str, str, str, str]] = []
            for i, line in enumerate(lines):
                for m in _INCBIN_RE.finditer(line):
                    found.append((block_of[i], m.group("path"),
                                  m.group("width"), m.group("sym") or "",
                                  m.group("bound") or ""))
                    if m.group("sym"):
                        sym_to_path.setdefault(m.group("sym"), m.group("path"))
            if not found:
                continue

            rel_src = os.path.relpath(path, game_dir).replace("\\", "/")
            for bid, inc_path, width, sym, bound in found:
                if ".bin" not in inc_path:
                    continue
                compressed = inc_path.endswith(".lz")
                bin_rel = (inc_path[:-3] if compressed else inc_path)
                if not bin_rel.endswith(".bin"):
                    continue
                # Block adjacency is only a HINT, and a weak one. `trainer_card.c`
                # declares the stickers sheet, all eight card tilemaps and
                # eighteen palettes as one unbroken run with no blank line, so
                # taking the block's first sheet handed every card the 16-tile
                # stickers sheet, and taking the block's palettes handed each one
                # all eighteen — loaded into slots 0..33, none of them where the
                # engine actually puts them. The sheets those cards really use
                # are declared in a DIFFERENT FILE (`graphics.c`), which block
                # adjacency cannot reach at all.
                #
                # So the block only supplies a palette when it holds exactly one
                # (the party_menu / learn_move shape, where a block IS one asset
                # family). The sheet is resolved afterwards by symbol affinity
                # across the whole project — see `_resolve_sheets_by_symbol`.
                siblings = [p for b, p, _w, _s, _b in found if b == bid]
                block_sheets = [s for s in (_rel_to_png(p) for p in siblings) if s]
                block_pals = []
                for sp in siblings:
                    if any(s in sp for s in _PAL_SUFFIXES):
                        pal = _rel_to_palette(sp)
                        if pal not in block_pals:
                            block_pals.append(pal)
                sheet = block_sheets[0] if len(block_sheets) == 1 else ""
                pals = block_pals if len(block_pals) == 1 else []
                existing = index.get(bin_rel)
                if existing is not None and not (sheet or pals):
                    continue
                if existing is not None and not (existing.sheet_rel
                                                 or existing.palette_rels):
                    pass          # replace a bare earlier hit with this richer one
                elif existing is not None:
                    continue
                index[bin_rel] = BinFacts(
                    bin_rel=bin_rel,
                    symbol=sym,
                    incbin_width=width,
                    compressed=compressed,
                    declared_cells=_eval_bound(bound, defines),
                    declared_dims=_eval_dims(bound, defines),
                    sheet_rel=sheet,
                    palette_rels=pals,
                    declared_at=f"{rel_src}:{bid}",
                )

    _resolve_sheets_by_symbol(index, sym_to_path, game_dir)
    _attach_slot_palettes(src_dir, index, sym_to_path)
    _log.debug("engine index: %d tilemaps from %s (%d with slot palettes)",
               len(index), src_dir,
               sum(1 for f in index.values() if f.slot_palettes))
    return index


# One parse per game tree per session. `clear_engine_index()` is called wherever
# project state is dropped, so a newly opened project never sees the previous
# project's engine facts, and from the editor tab's `load()` so F5 re-reads the
# source — which is also how a tilemap added DURING a session becomes visible.
_CACHE: Dict[str, Dict[str, BinFacts]] = {}


def get_engine_index(game_dir: str) -> Dict[str, BinFacts]:
    """The index for one game tree, keyed by game-relative bin path.

    `game_dir` is the root of the game checkout — the directory holding `src`
    and `graphics`. That is what the rest of the app calls `project_dir` (see
    the tilemap tab, which joins it with "graphics"), and nothing here depends
    on what that directory is NAMED.
    """
    if not game_dir:
        return {}
    key = os.path.abspath(game_dir)
    if key not in _CACHE:
        _CACHE[key] = _build(key)
    return _CACHE[key]


def clear_engine_index() -> None:
    """Drop every parsed game tree. Call this wherever project state is dropped,
    and on an explicit refresh, so newly added tilemaps are picked up."""
    _CACHE.clear()


def _bin_key(game_dir: str, bin_path: str) -> str:
    """Absolute `.bin` path -> the game-relative key the index uses."""
    try:
        rel = os.path.relpath(bin_path, game_dir)
    except ValueError:        # different drives on Windows
        return ""
    rel = rel.replace("\\", "/")
    return "" if rel.startswith("..") else rel


def facts_for(game_dir: str, bin_path: str) -> Optional[BinFacts]:
    """What the engine says about this `.bin`, or None if it says nothing.

    None is the normal answer for a tilemap a project has added and not yet
    referenced from C, and every caller falls back to its own heuristics.
    """
    key = _bin_key(game_dir, bin_path)
    if not key:
        return None
    return get_engine_index(game_dir).get(key)


def infer_game_dir(bin_path: str) -> str:
    """Find the game tree from a `.bin` path, with no caller plumbing.

    Walks up from the file to the first ancestor holding a `src` directory with
    C sources in it, and returns THAT directory — the game tree root.

    Nothing here is keyed on the directory's name. An earlier version of this
    returned the parent and had its callers re-append "pokefirered", which meant
    the whole index silently returned nothing for any project whose checkout is
    named anything else — and this editor is not for one project.

    Returns "" when there is no such ancestor; callers treat that as "the engine
    has nothing to say" and fall back to the filename heuristics.
    """
    cur = os.path.dirname(os.path.abspath(bin_path))
    while True:
        src = os.path.join(cur, "src")
        if os.path.isdir(src):
            try:
                for entry in os.listdir(src):
                    if entry.endswith((".c", ".h")):
                        return cur
            except OSError:
                pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent


def facts_for_path(bin_path: str) -> Optional[BinFacts]:
    """`facts_for` without needing the game directory passed in."""
    game_dir = infer_game_dir(bin_path)
    if not game_dir:
        return None
    return facts_for(game_dir, bin_path)
