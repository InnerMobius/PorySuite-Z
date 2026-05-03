"""
Region Map engine codegen.

Owns every piece of region-coupled code in src/region_map.c. Seven marker blocks
are rewritten on every save from a single source of truth (the region table).
The user never edits these blocks by hand.

Marker convention:
    // PORYSUITE-REGIONS-START (<tag>)
    ... auto-generated content ...
    // PORYSUITE-REGIONS-END (<tag>)

Code outside the markers is preserved verbatim. First-time migration on a
vanilla project parses the existing engine state, inserts markers around the
seven blocks, and from then on owns those ranges.

Tags:
    enum                        - region enum body
    includes                    - #include "data/region_map/region_map_layout_X.h" lines
    decompress_dispatch         - LoadRegionMapGfx state cases that LZ77UnCompWram each tilemap
    player_region_lookup_table  - sRegionMapsecLookup[][] (renamed from vanilla's sSeviiMapsecs)
    player_region_detect        - the "which region is the player in" block
    get_section_dispatch        - GetSelectedMapSection switch
    visibility_gates            - story-flag-gated rect fills (vanilla Navel/Birth Island)

All emitters are pure functions of the engine state. Same input -> same output,
byte-for-byte. Idempotent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Marker scaffolding
# ---------------------------------------------------------------------------

MARKER_PREFIX = "// PORYSUITE-REGIONS"
TAGS = (
    "enum",
    "tilemap_incbins",
    "includes",
    "decompress_dispatch",
    "player_region_lookup_table",
    "player_region_detect",
    "get_section_dispatch",
    "visibility_gates",
)

# Renamed lookup symbol — vanilla called this sSeviiMapsecs, which doesn't
# generalise to non-Pokemon hacks. We own this declaration and the only
# reference to it (player_region_detect), so renaming is safe.
LOOKUP_SYMBOL = "sRegionMapsecLookup"


def _start_marker(tag: str) -> str:
    return f"{MARKER_PREFIX}-START ({tag})"


def _end_marker(tag: str) -> str:
    return f"{MARKER_PREFIX}-END ({tag})"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class VisibilityGate:
    """A flag-gated rect fill that hides cells until a flag is set.

    Emits one C statement of the form:
        if (whichMap == <region_const> && !FlagGet(<flag_const>))
            FillBgTilemapBufferRect_Palette0(0, <tile>, <x>, <y>, <w>, <h>);
    """
    region_name: str   # e.g. 'sevii_45' (lowercase folder/code id)
    flag_const: str    # e.g. 'FLAG_WORLD_MAP_NAVEL_ROCK_EXTERIOR'
    tile: int          # tile index (e.g. 0x003)
    x: int
    y: int
    w: int
    h: int


@dataclass
class RegionRecord:
    """A single region in the engine table.

    `name` is the folder/file id (with underscores: 'sevii_123', 'kanto').
    Vanilla collapses underscores in the C-side enum/symbol names but keeps
    them in filenames — the properties below preserve that convention so
    existing files are referenced correctly.
    """
    name: str
    mapsecs: List[str] = field(default_factory=list)
    """Distinct MAPSECs that appear in this region's LAYER_MAP grid.

    Drives `sRegionMapsecLookup`. Slot 0 (the base region) is excluded from
    the lookup — same as vanilla's [REGIONMAP_X - 1] indexing math. Filled
    by the manager from the live grid on every save.
    """

    @property
    def _camel(self) -> str:
        return "".join(p.capitalize() for p in self.name.split("_"))

    @property
    def enum_const(self) -> str:
        # Vanilla collapses underscores: sevii_123 -> REGIONMAP_SEVII123
        return f"REGIONMAP_{self.name.replace('_', '').upper()}"

    @property
    def layout_header(self) -> str:
        return f"region_map_layout_{self.name}.h"

    @property
    def tilemap_symbol(self) -> str:
        return f"s{self._camel}_Tilemap"

    @property
    def section_grid_symbol(self) -> str:
        return f"sRegionMapSections_{self._camel}"

    @property
    def tilemap_basename(self) -> str:
        return f"{self.name}.bin"

    @property
    def tilemap_lz_basename(self) -> str:
        return f"{self.name}.bin.lz"


@dataclass
class EngineState:
    """Single source of truth for the engine codegen."""
    regions: List[RegionRecord] = field(default_factory=list)
    gates: List[VisibilityGate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Block emitters
# ---------------------------------------------------------------------------

def emit_enum(state: EngineState) -> str:
    """Region enum body — no enclosing braces, just the entries.

    Vanilla layout:
        enum {
            REGIONMAP_KANTO,
            ...
            REGIONMAP_COUNT
        };
    The tool owns the `enum {` line through `};` line. Indentation is 4 spaces
    matching vanilla style.
    """
    lines = ["enum {"]
    for r in state.regions:
        lines.append(f"    {r.enum_const},")
    lines.append("    REGIONMAP_COUNT")
    lines.append("};")
    return "\n".join(lines) + "\n"


def emit_tilemap_incbins(state: EngineState) -> str:
    """Per-region tilemap symbol declarations.

    Vanilla:
        static const u32 sKanto_Tilemap[] = INCBIN_U32("graphics/region_map/kanto.bin.lz");
        static const u32 sSevii123_Tilemap[] = INCBIN_U32("graphics/region_map/sevii_123.bin.lz");
        ...
    """
    lines = []
    for r in state.regions:
        lines.append(
            f'static const u32 {r.tilemap_symbol}[] = '
            f'INCBIN_U32("graphics/region_map/{r.tilemap_lz_basename}");'
        )
    return "\n".join(lines) + "\n"


def emit_includes(state: EngineState) -> str:
    lines = []
    for r in state.regions:
        lines.append(f'#include "data/region_map/{r.layout_header}"')
    return "\n".join(lines) + "\n"


def emit_decompress_dispatch(state: EngineState) -> str:
    """Cases 5..(5+N-1) of LoadRegionMapGfx's loadGfxState switch.

    Each case decompresses one region's tilemap into its layouts[] slot. The
    default arm decompresses the shared background tilemap and returns TRUE
    (signalling completion of the load chain).

    Tool owns from `case 5:` through (and including) the `default:` arm. State
    values 0..4 stay outside the marker.
    """
    lines = []
    for i, r in enumerate(state.regions):
        case = 5 + i
        lines.append(f"    case {case}:")
        lines.append(
            f"        LZ77UnCompWram({r.tilemap_symbol}, sRegionMap->layouts[{r.enum_const}]);"
        )
        lines.append("        break;")
    lines.append("    default:")
    lines.append(
        "        LZ77UnCompWram(sBackground_Tilemap, sRegionMap->layouts[REGIONMAP_COUNT]);"
    )
    lines.append("        return TRUE;")
    return "\n".join(lines) + "\n"


def emit_player_region_lookup_table(state: EngineState) -> str:
    """sRegionMapsecLookup[N-1][M] — for each non-base region, the list of
    MAPSECs that identify "the player is in this region."

    Slot 0 (base region) is excluded — same indexing math as vanilla's
    [REGIONMAP_X - 1]. Only emit if there are >= 2 regions; with a single
    region the lookup is unused.

    M is sized to fit the largest region's mapsec list, plus one trailing
    MAPSEC_NONE sentinel that the iteration loop relies on.
    """
    if len(state.regions) <= 1:
        # Emit a stub so the symbol still exists (referenced by detect block
        # via marker structure), but with one empty slot.
        lines = [
            f"static const u8 {LOOKUP_SYMBOL}[1][1] = {{",
            "    {MAPSEC_NONE}",
            "};",
        ]
        return "\n".join(lines) + "\n"

    non_base = state.regions[1:]
    longest = max((len(r.mapsecs) for r in non_base), default=0)
    width = longest + 1  # trailing MAPSEC_NONE sentinel
    if width < 2:
        width = 2  # minimum so the array is well-formed even if all empty

    lines = [
        f"static const u8 {LOOKUP_SYMBOL}[{len(non_base)}][{width}] = {{",
    ]
    for i, r in enumerate(non_base):
        lines.append(f"    [{r.enum_const} - 1] =")
        lines.append("    {")
        for ms in r.mapsecs:
            lines.append(f"        {ms},")
        lines.append("        MAPSEC_NONE")
        # Comma after closing brace except for last
        suffix = "," if i < len(non_base) - 1 else ""
        lines.append("    }" + suffix)
    lines.append("};")
    return "\n".join(lines) + "\n"


def emit_player_region_detect(state: EngineState) -> str:
    """The "which region is the player in?" block.

    Replaces vanilla's SEVII_MAPSEC_START-dependent logic with a generic
    iteration over all non-base regions. Base region is whatever's at
    state.regions[0]. If a single region exists, the detection collapses to
    just `region = <base>;`.

    Emitted code lives inside InitRegionMap() (or wherever the existing block
    is — caller positions the markers correctly). Output assumes locals
    `region`, `i`, `j` already declared in the enclosing function (vanilla
    does, and we haven't moved the block).
    """
    if not state.regions:
        return "    region = 0;\n"

    base_const = state.regions[0].enum_const

    if len(state.regions) == 1:
        lines = [
            f"    region = {base_const};",
            "    sRegionMap->selectedRegion = region;",
            "    sRegionMap->playersRegion = region;",
        ]
        return "\n".join(lines) + "\n"

    lines = [
        f"    region = {base_const};",
        "    if (gMapHeader.regionMapSectionId != MAPSEC_NONE)",
        "    {",
        "        for (j = 0; j < REGIONMAP_COUNT - 1; j++)",
        "        {",
        f"            for (i = 0; {LOOKUP_SYMBOL}[j][i] != MAPSEC_NONE; i++)",
        "            {",
        f"                if (gMapHeader.regionMapSectionId == {LOOKUP_SYMBOL}[j][i])",
        "                {",
        "                    region = j + 1;",
        "                    break;",
        "                }",
        "            }",
        f"            if (region != {base_const})",
        "                break;",
        "        }",
        "    }",
        "    sRegionMap->selectedRegion = region;",
        "    sRegionMap->playersRegion = region;",
    ]
    return "\n".join(lines) + "\n"


def emit_get_section_dispatch(state: EngineState) -> str:
    """The GetSelectedMapSection switch body.

    Vanilla's wrapping function declares `static u8 GetSelectedMapSection(...)`
    and we own from `switch (whichMap)` through the closing brace of the
    switch. The function header and the function-closing brace stay outside.
    """
    lines = ["    switch (whichMap)", "    {"]
    for r in state.regions:
        lines.append(f"    case {r.enum_const}:")
        lines.append(f"        return {r.section_grid_symbol}[layer][y][x];")
    lines.append("    default:")
    lines.append("        return MAPSEC_NONE;")
    lines.append("    }")
    return "\n".join(lines) + "\n"


def emit_visibility_gates(state: EngineState) -> str:
    """Story-flag visibility gates.

    Each gate becomes:
        if (whichMap == <region> && !FlagGet(<flag>))
            FillBgTilemapBufferRect_Palette0(0, 0x<tile>, <x>, <y>, <w>, <h>);

    Gates whose region_name is no longer in the table are silently dropped
    by the caller before this is invoked, so we never emit a dangling const.
    Empty gate list -> empty block (just a comment).
    """
    if not state.gates:
        return "    // no visibility gates\n"
    lines = []
    region_consts = {r.name: r.enum_const for r in state.regions}
    for g in state.gates:
        if g.region_name not in region_consts:
            continue  # dropped — region no longer exists
        const = region_consts[g.region_name]
        lines.append(f"    if (whichMap == {const} && !FlagGet({g.flag_const}))")
        lines.append(
            f"        FillBgTilemapBufferRect_Palette0(0, 0x{g.tile:03X}, "
            f"{g.x}, {g.y}, {g.w}, {g.h});"
        )
    if not lines:
        return "    // no visibility gates\n"
    return "\n".join(lines) + "\n"


EMITTERS = {
    "enum": emit_enum,
    "tilemap_incbins": emit_tilemap_incbins,
    "includes": emit_includes,
    "decompress_dispatch": emit_decompress_dispatch,
    "player_region_lookup_table": emit_player_region_lookup_table,
    "player_region_detect": emit_player_region_detect,
    "get_section_dispatch": emit_get_section_dispatch,
    "visibility_gates": emit_visibility_gates,
}


# ---------------------------------------------------------------------------
# Parsing — first-time migration & re-load
# ---------------------------------------------------------------------------

_RE_ENUM_BLOCK = re.compile(
    r"enum\s*\{\s*(REGIONMAP_[A-Z0-9_]+(?:\s*,\s*REGIONMAP_[A-Z0-9_]+)*)\s*\}\s*;",
    re.MULTILINE,
)

_RE_GATE_LINE = re.compile(
    r"if\s*\(\s*whichMap\s*==\s*(REGIONMAP_[A-Z0-9_]+)\s*&&\s*"
    r"!FlagGet\(\s*([A-Z0-9_]+)\s*\)\s*\)\s*\n?\s*"
    r"FillBgTilemapBufferRect_Palette0\(\s*0\s*,\s*"
    r"(0x[0-9A-Fa-f]+|\d+)\s*,\s*"
    r"(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*;",
    re.MULTILINE,
)


_RE_TILEMAP_INCBIN = re.compile(
    r'static\s+const\s+u32\s+s([A-Za-z0-9_]+?)_Tilemap\[\]\s*=\s*'
    r'INCBIN_U32\(\s*"graphics/region_map/([a-z0-9_]+)\.bin\.lz"\s*\)\s*;',
)


def parse_existing_state(
    content: str,
    folder_names: Optional[List[str]] = None,
) -> Optional[EngineState]:
    """Parse a vanilla (un-markered) region_map.c to seed the region table.

    Used once on first migration. Returns None if the file doesn't look like
    a region_map.c (no enum found).

    Cross-references the enum constants with `folder_names` (the list of
    actual layout-file folder ids on disk, e.g. ['kanto', 'sevii_123', ...])
    to recover the correct folder name for each enum const — vanilla's enum
    drops underscores (REGIONMAP_SEVII123) but filenames keep them
    (region_map_layout_sevii_123.h). Without this mapping we can't tell
    'sevii123' from 'sevii_123'.

    If folder_names is None, falls back to scanning the tilemap INCBIN
    declarations in the file itself (those reference the .bin.lz path which
    contains the folder name with underscores).

    Mapsec lists are NOT parsed from sRegionMapsecLookup — those are derived
    fresh from the live grids on every save. The manager fills them in.
    """
    m = _RE_ENUM_BLOCK.search(content)
    if not m:
        return None

    consts = [c.strip() for c in m.group(1).split(",")]
    consts = [c for c in consts if c and c != "REGIONMAP_COUNT"]

    # Build collapsed_name -> folder_name map.
    # Source: caller-provided folder_names if available; else the INCBIN paths.
    collapsed_to_folder: dict = {}
    if folder_names:
        for fn in folder_names:
            collapsed_to_folder[fn.replace("_", "").lower()] = fn
    else:
        for incm in _RE_TILEMAP_INCBIN.finditer(content):
            folder = incm.group(2)  # e.g. 'sevii_123'
            collapsed_to_folder[folder.replace("_", "").lower()] = folder

    regions: List[RegionRecord] = []
    for c in consts:
        collapsed = c[len("REGIONMAP_"):].lower()  # 'sevii123'
        folder = collapsed_to_folder.get(collapsed, collapsed)
        regions.append(RegionRecord(name=folder))

    gates: List[VisibilityGate] = []
    region_const_to_name = {r.enum_const: r.name for r in regions}
    for gm in _RE_GATE_LINE.finditer(content):
        const = gm.group(1)
        if const not in region_const_to_name:
            continue
        gates.append(VisibilityGate(
            region_name=region_const_to_name[const],
            flag_const=gm.group(2),
            tile=int(gm.group(3), 0),
            x=int(gm.group(4)),
            y=int(gm.group(5)),
            w=int(gm.group(6)),
            h=int(gm.group(7)),
        ))

    return EngineState(regions=regions, gates=gates)


def has_markers(content: str) -> bool:
    """Quick check: is this file already migrated?"""
    return _start_marker("enum") in content


# ---------------------------------------------------------------------------
# First-time migration: locate vanilla blocks for marker insertion
# ---------------------------------------------------------------------------

@dataclass
class _BlockSpan:
    start: int   # byte offset where the block begins (line start)
    end: int     # byte offset just past the block's last line (line end + 1)


def _line_bounds(content: str, char_idx: int) -> Tuple[int, int]:
    """Return (line_start, line_end_exclusive) containing char_idx."""
    line_start = content.rfind("\n", 0, char_idx) + 1
    nl = content.find("\n", char_idx)
    line_end = (nl + 1) if nl != -1 else len(content)
    return line_start, line_end


def _find_enum_span(content: str) -> Optional[_BlockSpan]:
    m = re.search(
        r"enum\s*\{\s*REGIONMAP_[A-Z0-9_]+.*?REGIONMAP_COUNT\s*\}\s*;",
        content, re.DOTALL,
    )
    if not m:
        return None
    ls, _ = _line_bounds(content, m.start())
    _, le = _line_bounds(content, m.end() - 1)
    return _BlockSpan(ls, le)


def _find_tilemap_incbins_span(
    content: str,
    region_folder_names: Optional[List[str]] = None,
) -> Optional[_BlockSpan]:
    """Find the contiguous run of per-region tilemap INCBIN_U32 declarations.

    Vanilla:
        static const u32 sKanto_Tilemap[] = INCBIN_U32("graphics/region_map/kanto.bin.lz");
        static const u32 sSevii123_Tilemap[] = ...;
        static const u32 sSevii45_Tilemap[] = ...;
        static const u32 sSevii67_Tilemap[] = ...;
        static const u32 sMapEdge_Tilemap[] = ...;            <-- NOT per-region
        static const u32 sSwitchMap_*_Tilemap[] = ...;        <-- NOT per-region

    The per-region tilemaps and the unrelated ones (`sMapEdge_Tilemap`,
    `sSwitchMap_*`) sit on consecutive lines in vanilla. If we just take
    the longest contiguous run we'd swallow them all and the codegen
    rewrite would delete everything except the per-region declarations,
    breaking the build.

    The fix: filter the match list to ONLY declarations whose INCBIN path
    matches a known region folder name. Other tilemap declarations are
    skipped — they're outside the marker, codegen never touches them.

    `region_folder_names` MUST be supplied for first-time migration on a
    vanilla file. After migration the marker bracket is the source of
    truth and this function isn't called again.
    """
    if not region_folder_names:
        return None
    region_set = set(region_folder_names)
    matches = [
        m for m in _RE_TILEMAP_INCBIN.finditer(content)
        if m.group(2) in region_set
    ]
    if not matches:
        return None
    # Take the longest run that is line-adjacent (no >1-newline gap between
    # consecutive matches). For vanilla all 4 region tilemaps are
    # consecutive, so this returns lines 410-413, NOT 410-417.
    runs = []
    cur = [matches[0]]
    for prev, m in zip(matches, matches[1:]):
        between = content[prev.end():m.start()]
        if between.count("\n") <= 1:
            cur.append(m)
        else:
            runs.append(cur)
            cur = [m]
    runs.append(cur)
    longest = max(runs, key=len)
    ls, _ = _line_bounds(content, longest[0].start())
    _, le = _line_bounds(content, longest[-1].end() - 1)
    return _BlockSpan(ls, le)


def _find_includes_span(content: str) -> Optional[_BlockSpan]:
    # Find a contiguous run of region_map_layout_*.h includes.
    matches = list(re.finditer(
        r'^#include\s+"data/region_map/region_map_layout_[a-z0-9_]+\.h"\s*$',
        content, re.MULTILINE,
    ))
    if not matches:
        return None
    first = matches[0]
    last = matches[-1]
    ls, _ = _line_bounds(content, first.start())
    _, le = _line_bounds(content, last.end() - 1)
    return _BlockSpan(ls, le)


def _find_decompress_span(content: str) -> Optional[_BlockSpan]:
    """Find cases 5..N + default in LoadRegionMapGfx that LZ77UnCompWram each
    region tilemap. Match starts at `case 5:` line, ends after the
    `return TRUE;` line of the default arm.
    """
    m = re.search(
        r"^\s*case\s+5\s*:\s*\n"
        r"(?:\s*LZ77UnCompWram\([^)]+\);\s*\n\s*break;\s*\n"
        r"\s*case\s+\d+\s*:\s*\n)*"
        r"\s*LZ77UnCompWram\([^)]+\);\s*\n\s*break;\s*\n"
        r"\s*default\s*:\s*\n"
        r"\s*LZ77UnCompWram\(sBackground_Tilemap[^)]*\);\s*\n"
        r"\s*return\s+TRUE\s*;\s*\n",
        content, re.MULTILINE,
    )
    if not m:
        return None
    ls, _ = _line_bounds(content, m.start())
    _, le = _line_bounds(content, m.end() - 1)
    return _BlockSpan(ls, le)


def _find_lookup_table_span(content: str) -> Optional[_BlockSpan]:
    """Vanilla declaration: `static const u8 sSeviiMapsecs[3][30] = { ... };`."""
    m = re.search(
        r"static\s+const\s+u8\s+sSeviiMapsecs\s*\[[^\]]*\]\s*\[[^\]]*\]\s*=\s*\{.*?\}\s*;",
        content, re.DOTALL,
    )
    if not m:
        return None
    ls, _ = _line_bounds(content, m.start())
    _, le = _line_bounds(content, m.end() - 1)
    return _BlockSpan(ls, le)


def _find_player_detect_span(content: str) -> Optional[_BlockSpan]:
    """Vanilla block:
        region = REGIONMAP_KANTO;
        j = REGIONMAP_KANTO;
        if (gMapHeader.regionMapSectionId >= SEVII_MAPSEC_START) { ... }
        sRegionMap->selectedRegion = region;
        sRegionMap->playersRegion = region;
    """
    m = re.search(
        r"^\s*region\s*=\s*REGIONMAP_[A-Z0-9_]+\s*;\s*\n"
        r"\s*j\s*=\s*REGIONMAP_[A-Z0-9_]+\s*;\s*\n"
        r"\s*if\s*\(\s*gMapHeader\.regionMapSectionId\s*>=\s*SEVII_MAPSEC_START\s*\)\s*\n"
        r"\s*\{.*?\n\s*\}\s*\n"
        r"\s*sRegionMap->selectedRegion\s*=\s*region\s*;\s*\n"
        r"\s*sRegionMap->playersRegion\s*=\s*region\s*;\s*\n",
        content, re.MULTILINE | re.DOTALL,
    )
    if not m:
        return None
    ls, _ = _line_bounds(content, m.start())
    _, le = _line_bounds(content, m.end() - 1)
    return _BlockSpan(ls, le)


def _find_get_section_span(content: str) -> Optional[_BlockSpan]:
    """Inside GetSelectedMapSection: from `switch (whichMap)` through the
    closing brace of the switch."""
    m = re.search(
        r"^\s*switch\s*\(\s*whichMap\s*\)\s*\n\s*\{"
        r"(?:.|\n)*?"
        r"^\s*default\s*:\s*\n\s*return\s+MAPSEC_NONE\s*;\s*\n"
        r"\s*\}\s*\n",
        content, re.MULTILINE,
    )
    if not m:
        return None
    ls, _ = _line_bounds(content, m.start())
    _, le = _line_bounds(content, m.end() - 1)
    return _BlockSpan(ls, le)


def _find_gates_span(content: str) -> Optional[_BlockSpan]:
    """Vanilla has two consecutive `if (whichMap == REGIONMAP_SEVII* && !FlagGet...)`
    lines. Find the contiguous run.
    """
    matches = list(_RE_GATE_LINE.finditer(content))
    if not matches:
        return None
    # Take only the first contiguous run (vanilla has them adjacent).
    ls, _ = _line_bounds(content, matches[0].start())
    _, le = _line_bounds(content, matches[-1].end() - 1)
    return _BlockSpan(ls, le)


_SPAN_FINDERS = {
    "enum": _find_enum_span,
    "tilemap_incbins": _find_tilemap_incbins_span,
    "includes": _find_includes_span,
    "decompress_dispatch": _find_decompress_span,
    "player_region_lookup_table": _find_lookup_table_span,
    "player_region_detect": _find_player_detect_span,
    "get_section_dispatch": _find_get_section_span,
    "visibility_gates": _find_gates_span,
}


# ---------------------------------------------------------------------------
# Apply — full file rewrite
# ---------------------------------------------------------------------------

class CodegenError(RuntimeError):
    pass


def _wrap_block(tag: str, body: str) -> str:
    """Wrap an emitted body with start/end markers. Body must end with newline."""
    if not body.endswith("\n"):
        body += "\n"
    return f"{_start_marker(tag)}\n{body}{_end_marker(tag)}\n"


def _replace_marker_block(content: str, tag: str, new_body: str) -> str:
    start = _start_marker(tag)
    end = _end_marker(tag)
    s = content.find(start)
    if s == -1:
        raise CodegenError(f"start marker for '{tag}' not found")
    e = content.find(end, s)
    if e == -1:
        raise CodegenError(f"end marker for '{tag}' not found after start")
    # Find the line containing the end marker and consume through its newline
    line_end = content.find("\n", e)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1
    # Find the start of the line containing the start marker
    line_start = content.rfind("\n", 0, s) + 1
    return content[:line_start] + _wrap_block(tag, new_body) + content[line_end:]


def _migrate_insert_markers(
    content: str,
    region_folder_names: Optional[List[str]] = None,
) -> str:
    """One-shot: locate every vanilla block, replace it in place with a
    marker-wrapped placeholder. Subsequent calls to apply_codegen() rewrite
    the wrapped contents normally.

    Done in reverse order of file position so each replacement doesn't
    invalidate the offsets of the next.

    `region_folder_names` is required by the tilemap_incbins span finder
    so we don't accidentally bracket adjacent non-region tilemap
    declarations (`sMapEdge_Tilemap`, `sSwitchMap_*`) and lose them on
    re-emit.
    """
    spans = []
    for tag in TAGS:
        finder = _SPAN_FINDERS[tag]
        if tag == "tilemap_incbins":
            span = finder(content, region_folder_names=region_folder_names)
        else:
            span = finder(content)
        if span is None:
            raise CodegenError(
                f"could not locate vanilla block for '{tag}' during migration"
            )
        spans.append((tag, span))

    # Sort by start offset descending so later edits don't shift earlier offsets
    spans.sort(key=lambda ts: ts[1].start, reverse=True)

    for tag, span in spans:
        original = content[span.start:span.end]
        # Preserve the original block body inside the markers; it'll be
        # replaced on the next apply_codegen() call. We keep it (rather than
        # emitting a fresh body) so migration alone doesn't change behaviour
        # — only marker comments are added. The next save replaces it.
        wrapped = _start_marker(tag) + "\n" + original
        if not wrapped.endswith("\n"):
            wrapped += "\n"
        wrapped += _end_marker(tag) + "\n"
        content = content[:span.start] + wrapped + content[span.end:]

    return content


def apply_codegen(content: str, state: EngineState) -> str:
    """Rewrite all marker blocks from the engine state.

    If markers are absent, run first-time migration to insert them around
    the existing vanilla blocks, then proceed with the rewrite. The result
    is a complete file ready to be written back to disk.

    Raises CodegenError if migration fails or markers are malformed.
    """
    if not has_markers(content):
        # Pass region folder names so tilemap_incbins span doesn't include
        # adjacent non-region tilemap declarations.
        folders = [r.name for r in state.regions]
        content = _migrate_insert_markers(content, region_folder_names=folders)

    for tag in TAGS:
        body = EMITTERS[tag](state)
        content = _replace_marker_block(content, tag, body)

    _verify_braces(content)
    return content


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

def _verify_braces(content: str) -> None:
    """Crude balance check: equal {/} ignoring strings, char literals, comments.

    Not a full C parser; catches gross codegen errors (truncated emit, missing
    closing brace) without false positives in normal source.
    """
    depth = 0
    i = 0
    n = len(content)
    while i < n:
        c = content[i]
        # Line comment
        if c == "/" and i + 1 < n and content[i + 1] == "/":
            nl = content.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        # Block comment
        if c == "/" and i + 1 < n and content[i + 1] == "*":
            end = content.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        # String literal
        if c == '"':
            i += 1
            while i < n and content[i] != '"':
                if content[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        # Char literal
        if c == "'":
            i += 1
            while i < n and content[i] != "'":
                if content[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                raise CodegenError("brace imbalance: extra '}' detected")
        i += 1
    if depth != 0:
        raise CodegenError(f"brace imbalance: depth={depth} at EOF")


def verify_marker_integrity(content: str) -> None:
    """Confirm every tag has a single, properly-ordered START/END pair."""
    for tag in TAGS:
        starts = content.count(_start_marker(tag))
        ends = content.count(_end_marker(tag))
        if starts != 1 or ends != 1:
            raise CodegenError(
                f"marker integrity failure for '{tag}': "
                f"{starts} start, {ends} end (expected 1 each)"
            )
        s = content.find(_start_marker(tag))
        e = content.find(_end_marker(tag))
        if e <= s:
            raise CodegenError(
                f"marker order failure for '{tag}': end appears before start"
            )
