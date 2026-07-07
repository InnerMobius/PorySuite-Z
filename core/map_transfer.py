"""map_transfer.py — Portable map bundles for moving maps between decomp projects.

PorySuite / porymap only offers "import from AdvanceMap", which is useless for
moving a map you already have in one pokefirered decomp into another decomp.
A single map is not one file — it drags along four coupled things:

  1. The map folder          data/maps/<Name>/            (map.json + *.inc)
     plus a one-line entry in data/maps/map_groups.json
  2. Its layout              data/layouts/<Folder>/       (border.bin, map.bin)
     plus an entry in        data/layouts/layouts.json
  3. Its tileset(s)          data/tilesets/<kind>/<f>/    (tiles, metatiles, attrs)
     plus declarations in    src/data/tilesets/graphics.h
                             src/data/tilesets/metatiles.h
                             src/data/tilesets/headers.h
  4. Palettes                live inside each tileset folder (palettes/*.gbapal)

This module does two jobs:

  * build_bundle(...)  — read a project, resolve every dependency of the chosen
    maps, and write a self-contained, zippable "bundle" (a folder with a
    manifest.json plus copies of every raw file, INCLUDING the exact C
    declaration stanzas for each tileset so nothing has to be regenerated).

  * import_bundle(...) — read a bundle and inject it into ANOTHER project,
    renaming maps/layouts (and their MAP_/LAYOUT_ constants) on the way in,
    resolving name collisions, auto-stubbing a missing region-map section so
    the build stays green, and patching all five index files
    (layouts.json, map_groups.json, and the three tileset headers).

All user-facing narration is returned as an ImportReport / warnings list — this
module never prints. Callers log to their own file logger.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger("PorySuite.MapTransfer")

BUNDLE_VERSION = 1
MANIFEST_NAME = "manifest.json"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def camel_to_screaming(name: str) -> str:
    """CeladonCity -> CELADON_CITY ; PalletTown_PlayersHouse_1F -> PALLET_TOWN_PLAYERS_HOUSE_1F.

    Mirrors porymap's own map-name -> MAP_ constant convention: insert an
    underscore before every capital that follows a lowercase/digit, and before
    a capital that starts a new word inside a run of capitals, then upper-case.
    Existing underscores are preserved and never doubled.
    """
    if not name:
        return ""
    # underscore before a capital that follows a lowercase letter
    # (PalletTown -> Pallet_Town). A capital after a DIGIT is left joined
    # so "1F" stays "1F" (matches decomp: MAP_..._1F, not _1_F).
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name)
    # acronym boundary: SSAnne -> SS_Anne
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.upper().strip("_")


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, data: dict) -> None:
    # newline="\n": the decomp is LF-only and its mapjson tool mangles CRLF
    # files (its read_text_file sizes the buffer to the raw byte count but
    # text-mode-reads fewer bytes, leaving trailing NULs -> "unexpected
    # trailing (0)"). Never let Windows text mode turn our \n into \r\n.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def maps_dir(root: str) -> str:
    return os.path.join(root, "data", "maps")


def layouts_dir(root: str) -> str:
    return os.path.join(root, "data", "layouts")


def tilesets_dir(root: str) -> str:
    return os.path.join(root, "data", "tilesets")


def graphics_h(root: str) -> str:
    return os.path.join(root, "src", "data", "tilesets", "graphics.h")


def metatiles_h(root: str) -> str:
    return os.path.join(root, "src", "data", "tilesets", "metatiles.h")


def headers_h(root: str) -> str:
    return os.path.join(root, "src", "data", "tilesets", "headers.h")


def is_decomp_project(root: str) -> bool:
    """True if root looks like a pokefirered/pokeemerald-style decomp we can read."""
    return (
        os.path.isfile(os.path.join(maps_dir(root), "map_groups.json"))
        and os.path.isfile(os.path.join(layouts_dir(root), "layouts.json"))
        and os.path.isfile(graphics_h(root))
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TilesetDep:
    label: str          # e.g. gTileset_General
    suffix: str         # e.g. General
    kind: str           # "primary" | "secondary"
    folder: str         # e.g. general
    graphics_decl: str  # exact stanza from graphics.h (tiles + palettes)
    metatiles_decl: str  # exact stanza from metatiles.h
    header_decl: str    # exact struct from headers.h
    callback: str = ""  # anim callback symbol, "" / "NULL" if none
    anim_code: str = ""  # self-contained C block from src/tileset_anims.c


@dataclass
class LayoutDep:
    entry: dict         # verbatim entry from layouts.json
    folder: str         # data/layouts/<folder>


@dataclass
class MapDep:
    name: str           # folder name, e.g. CeladonCity
    const: str          # MAP_CELADON_CITY
    group: str          # gMapGroup_TownsAndRoutes
    layout_id: str      # LAYOUT_CELADON_CITY
    region_section: str  # MAPSEC_CELADON_CITY
    json: dict          # parsed map.json
    files: list         # inc/json filenames in the map folder


# ---------------------------------------------------------------------------
# Tileset resolution — label -> folder + exact C stanzas
# ---------------------------------------------------------------------------

def _extract_graphics_decl(text: str, suffix: str) -> tuple[str, str]:
    """Return (tiles+palettes stanza, tiles INCBIN path) for a tileset suffix.

    The tiles line and the palettes array are found INDEPENDENTLY — pokefirered
    is inconsistent about their order and which file they live in (some put the
    palettes array before the tiles line, and some in src/graphics.c). Both are
    captured wherever they are and re-emitted tiles-first.
    """
    tiles_re = re.compile(
        r'^const\s+u\d+\s+gTilesetTiles_' + re.escape(suffix) +
        r'\[\]\s*=\s*INCBIN_\w+\("([^"]+)"\);', re.M)
    m = tiles_re.search(text)
    if not m:
        return "", ""
    tiles_path = m.group(1)
    tiles_line = m.group(0)
    pal_re = re.compile(
        r'const\s+u16\s+gTilesetPalettes_' + re.escape(suffix) +
        r'\[\]\[16\]\s*=\s*\{.*?\};', re.S)
    mp = pal_re.search(text)
    if mp:
        return tiles_line + "\n\n" + mp.group(0), tiles_path
    # uncompressed / atypical tileset — just the tiles line
    return tiles_line, tiles_path


def _extract_metatiles_decl(text: str, suffix: str) -> str:
    lines = []
    for var in ("gMetatiles_", "gMetatileAttributes_"):
        r = re.compile(r'^const\s+u\d+\s+' + var + re.escape(suffix) +
                       r'\[\]\s*=\s*INCBIN_\w+\("[^"]+"\);', re.M)
        m = r.search(text)
        if m:
            lines.append(m.group(0))
    return "\n".join(lines)


def _extract_header_decl(text: str, suffix: str) -> tuple[str, str]:
    """Return (struct text, callback symbol) for gTileset_<suffix>."""
    r = re.compile(
        r'const\s+struct\s+Tileset\s+gTileset_' + re.escape(suffix) +
        r'\s*=\s*\{.*?\};', re.S)
    m = r.search(text)
    if not m:
        return "", ""
    struct = m.group(0)
    cb = re.search(r'\.callback\s*=\s*([A-Za-z0-9_]+)\s*,', struct)
    callback = cb.group(1) if cb else "NULL"
    return struct, callback


def _anim_source_path(root: str) -> str:
    for c in ("tileset_anims.c", "tileset_anim.c"):
        p = os.path.join(root, "src", c)
        if os.path.isfile(p):
            return p
    return ""


def _split_top_level_chunks(text: str) -> list[tuple[int, int, str]]:
    """Split C source into top-level chunks (declarations ending in ';' and
    functions with balanced braces). Returns (start, end, text) triples in
    source order. Good enough for tileset_anims.c, which is flat file-scope
    declarations and small functions — no macros spanning constructs."""
    chunks = []
    i, n = 0, len(text)
    start = 0
    depth = 0
    while i < n:
        c = text[i]
        # skip string/char literals so ';' or '{' inside them don't confuse us
        if c in '"\'':
            q = c
            i += 1
            while i < n and text[i] != q:
                if text[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            while i < n and text[i] != '\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                chunks.append((start, end, text[start:end]))
                # swallow trailing ';' / whitespace
                j = end
                while j < n and text[j] in ' \t;':
                    j += 1
                start = j
                i = j
                continue
        elif c == ';' and depth == 0:
            end = i + 1
            chunks.append((start, end, text[start:end]))
            start = end
        i += 1
    return chunks


def _extract_anim_code(root: str, suffix: str, cache: dict) -> str:
    """Extract the self-contained animation block for gTileset_<suffix> from
    src/tileset_anims.c: every top-level frame array, frame-pointer array,
    QueueAnimTiles_/TilesetAnim_/InitTilesetAnim_ function that names this
    tileset. These only call shared helpers that exist in every target, so the
    block can be appended to another project's tileset_anims.c verbatim."""
    key = "__anim_src__"
    if key not in cache:
        p = _anim_source_path(root)
        cache[key] = _read_file(p) if p else ""
        cache["__anim_chunks__"] = _split_top_level_chunks(cache[key])
    chunks = cache.get("__anim_chunks__", [])
    # a chunk belongs to this tileset if it references a symbol ..._<suffix>
    # bounded so "Celadon" never matches "CeladonCity", "General" matches
    # "sTilesetAnims_General_Flower" (next char '_' is not alnum).
    belongs = re.compile(r'_' + re.escape(suffix) + r'(?![A-Za-z0-9])')
    picked = [txt.strip("\n") for _s, _e, txt in chunks if belongs.search(txt)]
    return "\n\n".join(picked)


def _tileset_sources(root: str, cache: dict) -> tuple[str, str, str]:
    """Combined source text to search for tileset declarations.

    In vanilla pokefirered most tilesets live in src/data/tilesets/*.h, but
    some (e.g. GenericBuilding1) declare their tiles/palettes in src/graphics.c
    instead. Search both so every tileset resolves regardless of where it lives.
    """
    if "__gtext__" not in cache:
        gc = os.path.join(root, "src", "graphics.c")
        g_files = [graphics_h(root), gc]
        m_files = [metatiles_h(root), gc]
        cache["__gtext__"] = "\n".join(
            _read_file(f) for f in g_files if os.path.isfile(f))
        cache["__mtext__"] = "\n".join(
            _read_file(f) for f in m_files if os.path.isfile(f))
        cache["__htext__"] = (_read_file(headers_h(root))
                              if os.path.isfile(headers_h(root)) else "")
    return cache["__gtext__"], cache["__mtext__"], cache["__htext__"]


def resolve_tileset(root: str, label: str,
                    _cache: dict) -> Optional[TilesetDep]:
    """Resolve a gTileset_X label to its folder + exact C stanzas."""
    if label in _cache:
        return _cache[label]
    if not label.startswith("gTileset_"):
        return None
    suffix = label[len("gTileset_"):]

    gtext, mtext, htext = _tileset_sources(root, _cache)

    graphics_decl, tiles_path = _extract_graphics_decl(gtext, suffix)
    if not tiles_path:
        _log.warning("Tileset %s: no tiles declaration found in graphics.h", label)
        return None
    # tiles_path -> data/tilesets/<kind>/<folder>/tiles.*
    parts = _norm(tiles_path).split("/")
    # data, tilesets, kind, folder, file
    kind = parts[2] if len(parts) > 3 else "secondary"
    folder = parts[3] if len(parts) > 4 else parts[-2]

    metatiles_decl = _extract_metatiles_decl(mtext, suffix)
    header_decl, callback = _extract_header_decl(htext, suffix)

    anim_code = ""
    if callback and callback != "NULL":
        anim_code = _extract_anim_code(root, suffix, _cache)

    dep = TilesetDep(
        label=label, suffix=suffix, kind=kind, folder=folder,
        graphics_decl=graphics_decl, metatiles_decl=metatiles_decl,
        header_decl=header_decl, callback=callback, anim_code=anim_code,
    )
    _cache[label] = dep
    return dep


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Project scan
# ---------------------------------------------------------------------------

def _map_group_of(map_groups: dict, map_name: str) -> str:
    for group in map_groups.get("group_order", []):
        if map_name in map_groups.get(group, []):
            return group
    # fall back: search any list value
    for k, v in map_groups.items():
        if k != "group_order" and isinstance(v, list) and map_name in v:
            return k
    return ""


def scan_maps(root: str) -> list[MapDep]:
    """List every map in the project with its group/layout/section resolved."""
    mg = _read_json(os.path.join(maps_dir(root), "map_groups.json"))
    out: list[MapDep] = []
    base = maps_dir(root)
    for name in sorted(os.listdir(base)):
        mdir = os.path.join(base, name)
        mjson = os.path.join(mdir, "map.json")
        if not os.path.isfile(mjson):
            continue
        try:
            j = _read_json(mjson)
        except Exception as e:
            _log.warning("Skipping map %s: bad map.json (%s)", name, e)
            continue
        files = [f for f in os.listdir(mdir)
                 if f.endswith(".inc") or f == "map.json"]
        out.append(MapDep(
            name=name,
            const=j.get("id", ""),
            group=_map_group_of(mg, name),
            layout_id=j.get("layout", ""),
            region_section=j.get("region_map_section", ""),
            json=j,
            files=files,
        ))
    return out


def _layout_entry(root: str, layout_id: str) -> Optional[dict]:
    lj = _read_json(os.path.join(layouts_dir(root), "layouts.json"))
    for e in lj.get("layouts", []):
        if e.get("id") == layout_id:
            return e
    return None


def resolve_map_dependencies(root: str, map_name: str) -> dict:
    """Everything one map needs: the MapDep, its LayoutDep, and TilesetDeps.

    Returns {"map": MapDep, "layout": LayoutDep|None,
             "tilesets": [TilesetDep], "warnings": [str]}.
    """
    warnings: list[str] = []
    ts_cache: dict = {}
    maps = {m.name: m for m in scan_maps(root)}
    md = maps.get(map_name)
    if md is None:
        raise ValueError(f"Map {map_name!r} not found in {root}")

    layout = None
    tilesets: list[TilesetDep] = []
    if md.layout_id:
        entry = _layout_entry(root, md.layout_id)
        if entry is None:
            warnings.append(f"Layout {md.layout_id} referenced by {map_name} "
                            f"was not found in layouts.json.")
        else:
            folder = os.path.basename(
                os.path.dirname(_norm(entry.get("blockdata_filepath", ""))))
            layout = LayoutDep(entry=entry, folder=folder)
            for key in ("primary_tileset", "secondary_tileset"):
                label = entry.get(key)
                if not label:
                    continue
                dep = resolve_tileset(root, label, ts_cache)
                if dep is None:
                    warnings.append(f"Tileset {label} could not be resolved.")
                else:
                    tilesets.append(dep)
    else:
        warnings.append(f"{map_name} has no layout - geometry/tileset skipped.")

    return {"map": md, "layout": layout, "tilesets": tilesets,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# Bundle building (export)
# ---------------------------------------------------------------------------

def build_bundle(root: str, map_names: list[str], dest_dir: str,
                 project_name: str = "", make_zip: bool = True,
                 progress=None) -> dict:
    """Gather every dependency of `map_names` into a portable bundle folder.

    dest_dir is created (must not already exist as a non-empty bundle unless
    the caller cleared it). Returns {"bundle_dir", "zip_path", "manifest",
    "warnings"}.
    """
    os.makedirs(dest_dir, exist_ok=True)
    b_maps = os.path.join(dest_dir, "maps")
    b_layouts = os.path.join(dest_dir, "layouts")
    b_tilesets = os.path.join(dest_dir, "tilesets")
    for d in (b_maps, b_layouts, b_tilesets):
        os.makedirs(d, exist_ok=True)

    warnings: list[str] = []
    manifest = {
        "porysuite_map_bundle_version": BUNDLE_VERSION,
        "source_project": project_name or os.path.basename(root.rstrip("/\\")),
        "exported_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "maps": [],
        "layouts": [],
        "tilesets": [],
    }
    seen_layouts: set[str] = set()
    seen_tilesets: set[str] = set()

    total = len(map_names)
    for i, mname in enumerate(map_names):
        if progress:
            progress(i, total, f"Packing {mname}")
        info = resolve_map_dependencies(root, mname)
        warnings.extend(info["warnings"])
        md: MapDep = info["map"]

        # --- copy map folder ---
        src_mdir = os.path.join(maps_dir(root), md.name)
        dst_mdir = os.path.join(b_maps, md.name)
        _copytree(src_mdir, dst_mdir)
        manifest["maps"].append({
            "name": md.name,
            "const": md.const,
            "group": md.group,
            "layout_id": md.layout_id,
            "region_section": md.region_section,
            "files": md.files,
        })

        # --- layout ---
        layout: Optional[LayoutDep] = info["layout"]
        if layout and layout.entry.get("id") not in seen_layouts:
            seen_layouts.add(layout.entry.get("id"))
            src_ldir = os.path.join(layouts_dir(root), layout.folder)
            if os.path.isdir(src_ldir):
                _copytree(src_ldir, os.path.join(b_layouts, layout.folder))
            manifest["layouts"].append({
                "entry": layout.entry,
                "folder": layout.folder,
            })

        # --- tilesets ---
        for ts in info["tilesets"]:
            if ts.label in seen_tilesets:
                continue
            seen_tilesets.add(ts.label)
            src_tdir = os.path.join(tilesets_dir(root), ts.kind, ts.folder)
            if os.path.isdir(src_tdir):
                _copytree(src_tdir, os.path.join(b_tilesets, ts.kind, ts.folder))
            manifest["tilesets"].append({
                "label": ts.label,
                "suffix": ts.suffix,
                "kind": ts.kind,
                "folder": ts.folder,
                "callback": ts.callback,
                "animated": bool(ts.anim_code),
                "anim_code": ts.anim_code,
                "graphics_decl": ts.graphics_decl,
                "metatiles_decl": ts.metatiles_decl,
                "header_decl": ts.header_decl,
                "inventory": _tileset_inventory(src_tdir),
                "used_by": [],   # filled in below
            })

    # record which of the bundle's maps use each tileset (shared-tileset view)
    _tag_tileset_usage(root, manifest)

    _write_json(os.path.join(dest_dir, MANIFEST_NAME), manifest)
    if progress:
        progress(total, total, "Writing manifest")

    zip_path = ""
    if make_zip:
        zip_path = dest_dir.rstrip("/\\") + ".zip"
        _zip_dir(dest_dir, zip_path)

    return {"bundle_dir": dest_dir, "zip_path": zip_path,
            "manifest": manifest, "warnings": warnings}


def _copytree(src: str, dst: str) -> None:
    if not os.path.isdir(src):
        _log.warning("copytree: source missing %s", src)
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _tileset_inventory(tdir: str) -> dict:
    """Human-readable count of what a tileset folder contains, so the export
    preview and import report can prove nothing was left behind."""
    inv = {"tiles": False, "metatiles": False, "attributes": False,
           "palettes": 0, "anim_frames": 0}
    if not os.path.isdir(tdir):
        return inv
    inv["tiles"] = os.path.isfile(os.path.join(tdir, "tiles.png"))
    inv["metatiles"] = os.path.isfile(os.path.join(tdir, "metatiles.bin"))
    inv["attributes"] = os.path.isfile(
        os.path.join(tdir, "metatile_attributes.bin"))
    pdir = os.path.join(tdir, "palettes")
    if os.path.isdir(pdir):
        inv["palettes"] = len([f for f in os.listdir(pdir)
                               if f.endswith(".gbapal")])
    adir = os.path.join(tdir, "anim")
    if os.path.isdir(adir):
        for base, _d, files in os.walk(adir):
            inv["anim_frames"] += len([f for f in files if f.endswith(".4bpp")])
    return inv


def _tag_tileset_usage(root: str, manifest: dict) -> None:
    """Fill each manifest tileset's used_by with the bundle maps that need it."""
    # layout id -> (primary label, secondary label)
    lay_ts = {}
    for l in manifest.get("layouts", []):
        e = l["entry"]
        lay_ts[e["id"]] = (e.get("primary_tileset"), e.get("secondary_tileset"))
    ts_by_label = {t["label"]: t for t in manifest.get("tilesets", [])}
    for mp in manifest.get("maps", []):
        prim, sec = lay_ts.get(mp.get("layout_id"), (None, None))
        for lbl in (prim, sec):
            if lbl in ts_by_label and mp["name"] not in ts_by_label[lbl]["used_by"]:
                ts_by_label[lbl]["used_by"].append(mp["name"])


def _zip_dir(src_dir: str, zip_path: str) -> None:
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(src_dir):
            for f in files:
                full = os.path.join(base, f)
                rel = os.path.relpath(full, src_dir)
                zf.write(full, rel)


# ---------------------------------------------------------------------------
# Bundle reading
# ---------------------------------------------------------------------------

def load_bundle(path: str) -> dict:
    """Load a bundle's manifest from either a folder or a .zip. Returns
    {"manifest", "root", "is_zip"} where root is a filesystem dir to read
    payload files from (a zip is extracted to a temp sibling dir)."""
    if os.path.isdir(path):
        manifest = _read_json(os.path.join(path, MANIFEST_NAME))
        return {"manifest": manifest, "root": path, "is_zip": False}
    if zipfile.is_zipfile(path):
        extract_to = path + ".extracted"
        if os.path.isdir(extract_to):
            shutil.rmtree(extract_to)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(extract_to)
        # bundle may be zipped at top level or inside a single folder
        root = extract_to
        if not os.path.isfile(os.path.join(root, MANIFEST_NAME)):
            subs = [os.path.join(root, d) for d in os.listdir(root)]
            for s in subs:
                if os.path.isfile(os.path.join(s, MANIFEST_NAME)):
                    root = s
                    break
        manifest = _read_json(os.path.join(root, MANIFEST_NAME))
        return {"manifest": manifest, "root": root, "is_zip": True}
    raise ValueError(f"Not a bundle folder or zip: {path}")


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------

def detect_collisions(root: str, manifest: dict) -> dict:
    """Report which incoming maps/layouts/tilesets already exist in target.

    Returns {"maps": {name: bool}, "layouts": {id: bool},
             "tilesets": {label: bool}}.
    """
    mg = _read_json(os.path.join(maps_dir(root), "map_groups.json"))
    existing_maps = {k for k in os.listdir(maps_dir(root))
                     if os.path.isdir(os.path.join(maps_dir(root), k))}
    lj = _read_json(os.path.join(layouts_dir(root), "layouts.json"))
    existing_layouts = {e.get("id") for e in lj.get("layouts", [])}
    htext = _read_file(headers_h(root))
    existing_ts = set(re.findall(r'gTileset_([A-Za-z0-9_]+)\s*=', htext))

    return {
        "maps": {m["name"]: (m["name"] in existing_maps)
                 for m in manifest.get("maps", [])},
        "layouts": {l["entry"]["id"]: (l["entry"]["id"] in existing_layouts)
                    for l in manifest.get("layouts", [])},
        "tilesets": {t["label"]: (t["suffix"] in existing_ts)
                     for t in manifest.get("tilesets", [])},
        "_mapsecs": _existing_mapsecs(root),
        "_anim_syms": _existing_anim_symbols(root),
    }


def _existing_mapsecs(root: str) -> set:
    p = os.path.join(root, "include", "constants", "region_map_sections.h")
    if not os.path.isfile(p):
        return set()
    return set(re.findall(r'\b(MAPSEC_[A-Z0-9_]+)\b', _read_file(p)))


def _existing_anim_symbols(root: str) -> set:
    """Function symbols the target defines for tileset anim callbacks."""
    syms: set = set()
    candidates = [
        os.path.join(root, "src", "tileset_anims.c"),
        os.path.join(root, "src", "tileset_anim.c"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            txt = _read_file(c)
            syms |= set(re.findall(r'\b(InitTilesetAnim_[A-Za-z0-9_]+)\b', txt))
    return syms


# ---------------------------------------------------------------------------
# Script symbol cross-check (so you can fix mismatches after an import)
# ---------------------------------------------------------------------------

# Constant families most likely to differ between two decomp projects. These
# all live in include/constants — a reference the target doesn't define is a
# concrete build break the user needs to know about. MAP_/LAYOUT_/MAPSEC_ are
# deliberately excluded (they are generated / handled by the importer itself).
_CHECK_PREFIXES = (
    "FLAG_", "VAR_", "ITEM_", "SPECIES_", "MOVE_", "MOVEMENT_TYPE_",
    "OBJ_EVENT_GFX_", "MUS_", "SE_", "TRAINER_", "WEATHER_", "ABILITY_",
    "METATILE_", "TYPE_", "TM_", "HM_",
)


def _target_defined_symbols(root: str) -> set:
    """Every constant-shaped token defined anywhere under include/ (a symbol
    referenced by a map but absent here is a real cross-project mismatch)."""
    toks: set = set()
    inc = os.path.join(root, "include")
    if not os.path.isdir(inc):
        return toks
    for base, _dirs, files in os.walk(inc):
        for f in files:
            if f.endswith((".h", ".inc")):
                try:
                    txt = _read_file(os.path.join(base, f))
                except Exception:
                    continue
                toks |= set(re.findall(r'\b[A-Z][A-Z0-9_]{2,}\b', txt))
    return toks


def scan_map_symbols(map_dir: str) -> set:
    """Constant references (of the checked families) inside a map's files."""
    toks: set = set()
    if not os.path.isdir(map_dir):
        return toks
    for f in os.listdir(map_dir):
        if f.endswith((".inc", ".json")):
            try:
                txt = _read_file(os.path.join(map_dir, f))
            except Exception:
                continue
            toks |= set(re.findall(r'\b[A-Z][A-Z0-9_]{2,}\b', txt))
    return {t for t in toks if t.startswith(_CHECK_PREFIXES)}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def tileset_usage_count(root: str, label: str) -> int:
    """How many layouts in the target reference this tileset label. Used to
    warn before overwriting a shared (primary) tileset — that edit hits every
    map built on it."""
    try:
        lj = _read_json(os.path.join(layouts_dir(root), "layouts.json"))
    except Exception:
        return 0
    n = 0
    for e in lj.get("layouts", []):
        if e.get("primary_tileset") == label or e.get("secondary_tileset") == label:
            n += 1
    return n


@dataclass
class ImportReport:
    added_maps: list = field(default_factory=list)
    added_layouts: list = field(default_factory=list)
    added_tilesets: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def import_bundle(root: str, bundle_path: str, plan: dict,
                  progress=None, strip_connections: bool = True) -> ImportReport:
    """Inject a bundle into `root` according to `plan`.

    plan = {
      "target_group": "gMapGroup_TownsAndRoutes",
      "tilesets": { label: {"action": "create|overwrite|skip|rename",
                             "new_suffix": str, "new_folder": str} },
      "layouts":  { old_id: {"action": "create|overwrite|skip|rename",
                             "new_id": str, "new_name": str,
                             "new_folder": str} },
      "maps":     { old_name: {"action": "create|overwrite|skip|rename",
                               "new_name": str, "new_const": str} },
    }
    Any entry missing from plan defaults to action "create" with no rename.
    """
    rep = ImportReport()
    loaded = load_bundle(bundle_path)
    manifest = loaded["manifest"]
    bdir = loaded["root"]

    coll = detect_collisions(root, manifest)
    existing_mapsecs = coll["_mapsecs"]
    existing_anims = coll["_anim_syms"]
    target_defined = _target_defined_symbols(root)

    # ---- resolve label/id remaps up front so cross-refs can be rewritten ----
    ts_plan = plan.get("tilesets", {})
    lay_plan = plan.get("layouts", {})
    map_plan = plan.get("maps", {})

    # label -> final gTileset_ label used in target
    ts_label_map: dict[str, str] = {}
    for t in manifest.get("tilesets", []):
        p = ts_plan.get(t["label"], {})
        new_suffix = p.get("new_suffix") or t["suffix"]
        ts_label_map[t["label"]] = "gTileset_" + new_suffix

    # old layout id -> final layout id
    lay_id_map: dict[str, str] = {}
    for l in manifest.get("layouts", []):
        oid = l["entry"]["id"]
        p = lay_plan.get(oid, {})
        lay_id_map[oid] = p.get("new_id") or oid

    steps = (len(manifest.get("tilesets", [])) +
             len(manifest.get("layouts", [])) +
             len(manifest.get("maps", [])))
    done = 0

    # ============ 1. TILESETS ============
    for t in manifest.get("tilesets", []):
        done += 1
        if progress:
            progress(done, steps, f"Tileset {t['label']}")
        p = ts_plan.get(t["label"], {})
        action = p.get("action", "create")
        new_suffix = p.get("new_suffix") or t["suffix"]
        new_folder = p.get("new_folder") or t["folder"]
        try:
            if action == "skip":
                rep.skipped.append(f"tileset {t['label']} (reused existing)")
                continue
            _import_one_tileset(root, bdir, t, new_suffix, new_folder,
                                action, existing_anims, rep)
        except Exception as e:
            rep.errors.append(f"tileset {t['label']}: {e}")
            _log.exception("tileset import failed: %s", t["label"])

    # ============ 2. LAYOUTS ============
    lj_path = os.path.join(layouts_dir(root), "layouts.json")
    lj = _read_json(lj_path)
    lj_by_id = {e.get("id"): e for e in lj.get("layouts", [])}
    for l in manifest.get("layouts", []):
        done += 1
        if progress:
            progress(done, steps, f"Layout {l['entry']['id']}")
        oid = l["entry"]["id"]
        p = lay_plan.get(oid, {})
        action = p.get("action", "create")
        try:
            if action == "skip":
                rep.skipped.append(f"layout {oid} (reused existing)")
                continue
            new_id = p.get("new_id") or oid
            new_name = p.get("new_name") or l["entry"].get("name")
            new_folder = p.get("new_folder") or l["folder"]
            entry = dict(l["entry"])
            entry["id"] = new_id
            entry["name"] = new_name
            entry["border_filepath"] = f"data/layouts/{new_folder}/border.bin"
            entry["blockdata_filepath"] = f"data/layouts/{new_folder}/map.bin"
            for key in ("primary_tileset", "secondary_tileset"):
                if entry.get(key) in ts_label_map:
                    entry[key] = ts_label_map[entry[key]]
            # copy folder
            src = os.path.join(bdir, "layouts", l["folder"])
            dst = os.path.join(layouts_dir(root), new_folder)
            if os.path.isdir(dst) and action != "overwrite":
                if action == "rename":
                    pass  # new_folder already unique per plan
                else:
                    rep.warnings.append(
                        f"layout folder {new_folder} exists; files left as-is")
            _copytree(src, dst)
            if new_id in lj_by_id:
                lj_by_id[new_id].update(entry)
                rep.warnings.append(f"layout {new_id} overwritten in layouts.json")
            else:
                lj["layouts"].append(entry)
                lj_by_id[new_id] = entry
            rep.added_layouts.append(new_id)
        except Exception as e:
            rep.errors.append(f"layout {oid}: {e}")
            _log.exception("layout import failed: %s", oid)
    _write_json(lj_path, lj)

    # ============ 3. MAPS ============
    mg_path = os.path.join(maps_dir(root), "map_groups.json")
    mg = _read_json(mg_path)
    # Fallback group only for maps whose bundle didn't record one. Each map
    # normally keeps its OWN original group (or a per-map override from the UI).
    fallback_group = plan.get("target_group") or _first_group(mg)

    def _ensure_group(grp: str) -> str:
        if not grp:
            grp = fallback_group
        if grp not in mg:
            mg[grp] = []
            if grp not in mg.get("group_order", []):
                mg.setdefault("group_order", []).append(grp)
            rep.warnings.append(
                f"created map group {grp} (didn't exist in this project).")
        return grp

    # Map constants that WILL exist after this import — every map already in
    # the target plus every map coming in on this bundle. Used to prune
    # connections that would otherwise point at a map that isn't here.
    known_map_consts = {
        "MAP_" + camel_to_screaming(d)
        for d in os.listdir(maps_dir(root))
        if os.path.isdir(os.path.join(maps_dir(root), d))
    }
    for mm in manifest.get("maps", []):
        pp = map_plan.get(mm["name"], {})
        nn = pp.get("new_name") or mm["name"]
        known_map_consts.add(
            pp.get("new_const") or ("MAP_" + camel_to_screaming(nn)))

    for m in manifest.get("maps", []):
        done += 1
        if progress:
            progress(done, steps, f"Map {m['name']}")
        p = map_plan.get(m["name"], {})
        action = p.get("action", "create")
        try:
            if action == "skip":
                rep.skipped.append(f"map {m['name']} (kept existing)")
                continue
            new_name = p.get("new_name") or m["name"]
            new_const = p.get("new_const") or ("MAP_" + camel_to_screaming(new_name))
            src = os.path.join(bdir, "maps", m["name"])
            dst = os.path.join(maps_dir(root), new_name)
            _copytree(src, dst)
            # rewrite map.json
            mj_path = os.path.join(dst, "map.json")
            mj = _read_json(mj_path)
            mj["id"] = new_const
            mj["name"] = new_name
            old_layout = mj.get("layout", "")
            if old_layout in lay_id_map:
                mj["layout"] = lay_id_map[old_layout]
            # Prune connections to maps that won't exist here (dangling links
            # would break the build). Links to co-imported maps are kept.
            if strip_connections and mj.get("connections"):
                kept, dropped = [], []
                for conn in mj["connections"]:
                    if conn.get("map") in known_map_consts:
                        kept.append(conn)
                    else:
                        dropped.append(conn.get("map"))
                if dropped:
                    mj["connections"] = kept or None
                    rep.warnings.append(
                        f"{new_name}: removed {len(dropped)} connection(s) to "
                        f"maps not in this project: "
                        f"{', '.join(sorted(set(dropped)))}")
            # MAPSEC auto-stub: remap to MAPSEC_NONE if missing in target
            sec = mj.get("region_map_section", "")
            if sec and existing_mapsecs and sec not in existing_mapsecs:
                mj["region_map_section"] = "MAPSEC_NONE"
                rep.warnings.append(
                    f"{new_name}: region section {sec} not in target - set to "
                    f"MAPSEC_NONE (assign one in the Region Map editor).")
            _write_json(mj_path, mj)
            # register under the map's OWN group (or a per-map override),
            # NOT one group for everything. Create the group if it's new.
            grp = _ensure_group(p.get("group") or m.get("group"))
            if new_name not in mg[grp]:
                mg[grp].append(new_name)
            # register the map's scripts.inc so <Map>_MapScripts links. Map
            # HEADERS are auto-generated, but event_scripts.s is hand-listed —
            # without this line the map fails to link ("undefined reference to
            # <Map>_MapScripts").
            _register_map_scripts_include(root, new_name, rep)
            # sync the scripts.inc label to the map's final name (a map renamed
            # in its source project can carry a stale *_MapScripts label).
            _sync_map_scripts_label(
                os.path.join(dst, "scripts.inc"), new_name, rep)
            rep.added_maps.append(new_name)
            # cross-check script/event symbols against the target
            missing = sorted(scan_map_symbols(dst) - target_defined)
            if missing:
                shown = ", ".join(missing[:25])
                if len(missing) > 25:
                    shown += f", … (+{len(missing) - 25} more)"
                rep.warnings.append(
                    f"{new_name}: references {len(missing)} constant(s) not "
                    f"defined in this project - the build will fail until you "
                    f"add or remap them: {shown}")
        except Exception as e:
            rep.errors.append(f"map {m['name']}: {e}")
            _log.exception("map import failed: %s", m["name"])
    _write_json(mg_path, mg)

    if loaded["is_zip"]:
        # clean up extracted temp dir
        try:
            shutil.rmtree(bdir)
        except Exception:
            pass
    return rep


def _first_group(mg: dict) -> str:
    order = mg.get("group_order", [])
    return order[0] if order else "gMapGroup_TownsAndRoutes"


def _find_event_scripts_file(root: str) -> str:
    """The hand-maintained .s that .include's every map's scripts.inc."""
    cand = os.path.join(root, "data", "event_scripts.s")
    if os.path.isfile(cand):
        return cand
    # fall back: any data/*.s carrying the map-scripts include list
    data = os.path.join(root, "data")
    if os.path.isdir(data):
        for f in sorted(os.listdir(data)):
            if f.endswith(".s"):
                p = os.path.join(data, f)
                try:
                    if 'data/maps/' in _read_file(p) and 'scripts.inc' in _read_file(p):
                        return p
                except Exception:
                    continue
    return ""


def _sync_map_scripts_label(scripts_inc: str, new_name: str,
                            rep: "ImportReport") -> None:
    """The map header (generated from map.json) references `<name>_MapScripts`.
    A map that was renamed in its source project can carry a scripts.inc whose
    `*_MapScripts::` label still has the OLD name (porymap doesn't rewrite the
    hand-authored label), which fails to link. Rename the label to match."""
    if not os.path.isfile(scripts_inc):
        return
    text = _read_file(scripts_inc)
    m = re.search(r'^([A-Za-z0-9_]+)_MapScripts::', text, re.M)
    if not m:
        return  # no map-scripts table (unusual, but nothing to sync)
    old_prefix = m.group(1)
    if old_prefix == new_name:
        return
    new_text = re.sub(r'\b' + re.escape(old_prefix) + r'_MapScripts\b',
                      new_name + "_MapScripts", text)
    with open(scripts_inc, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new_text)
    rep.warnings.append(
        f"{new_name}: scripts label {old_prefix}_MapScripts renamed to "
        f"{new_name}_MapScripts to match the map name.")


def _register_one_include(path: str, kind: str, map_name: str,
                          rep: "ImportReport") -> None:
    """Add `.include "data/maps/<map_name>/<kind>"` after the last include of
    that same kind. `kind` is 'scripts.inc' or 'text.inc'."""
    inc_key = f'data/maps/{map_name}/{kind}'
    inc = f'\t.include "{inc_key}"'
    text = _read_file(path)
    if inc_key in text:
        return
    lines = text.split("\n")
    pat = re.compile(r'\.include\s+"data/maps/.+?/' + re.escape(kind) + r'"')
    last = -1
    for i, ln in enumerate(lines):
        if pat.search(ln):
            last = i
    if last >= 0:
        lines.insert(last + 1, inc)
    else:
        lines.append(inc)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    rep.warnings.append(
        f"{map_name}: registered {kind} in {os.path.basename(path)}.")


def _register_map_scripts_include(root: str, map_name: str,
                                  rep: "ImportReport") -> None:
    """Register BOTH the map's scripts.inc AND text.inc in event_scripts.s.

    Map HEADERS are auto-generated, but scripts.inc and text.inc are each
    hand-listed in event_scripts.s (separate include blocks). Miss the
    scripts.inc line and *_MapScripts won't link; miss the text.inc line and
    every text label the map defines is undefined (e.g. Faron_Text_1)."""
    path = _find_event_scripts_file(root)
    if not path:
        rep.warnings.append(
            f"{map_name}: could not find event_scripts.s — add its scripts.inc "
            f"and text.inc includes yourself or the map won't link.")
        return
    _register_one_include(path, 'scripts.inc', map_name, rep)
    # only register text.inc if the map actually has one
    if os.path.isfile(os.path.join(maps_dir(root), map_name, 'text.inc')):
        _register_one_include(path, 'text.inc', map_name, rep)


# Every tileset-related symbol prefix that carries the tileset's suffix. Used
# to rename a whole tileset (headers + anim code) when it is imported renamed.
_TS_SYMBOL_PREFIXES = (
    "gTilesetTiles_", "gTilesetPalettes_", "gMetatiles_",
    "gMetatileAttributes_", "gTileset_",
    "InitTilesetAnim_", "TilesetAnim_", "QueueAnimTiles_", "sTilesetAnims_",
)


def _retarget_text(text: str, old_suffix: str, new_suffix: str,
                   old_path: str, new_path: str) -> str:
    """Rename a tileset's folder path and every ..._<old_suffix> symbol to
    ..._<new_suffix>. The suffix is matched as a name SEGMENT — bounded by a
    non-identifier char in front and a non-alphanumeric behind — so
    "sTilesetAnims_General_Flower" renames correctly (the trailing "_Flower"
    stays) and "General" never matches inside "Generalized"."""
    text = text.replace(old_path, new_path)
    if new_suffix == old_suffix:
        return text
    for var in _TS_SYMBOL_PREFIXES:
        pat = (r'(?<![A-Za-z0-9_])' + re.escape(var) +
               re.escape(old_suffix) + r'(?![A-Za-z0-9])')
        text = re.sub(pat, var + new_suffix, text)
    return text


def _import_one_tileset(root, bdir, t, new_suffix, new_folder, action,
                        existing_anims, rep: ImportReport) -> None:
    """Copy a tileset folder, patch the three C headers, and (for animated
    tilesets) inject the animation code into src/tileset_anims.c so the tileset
    animates in the target exactly as it did in the source project."""
    src = os.path.join(bdir, "tilesets", t["kind"], t["folder"])
    dst = os.path.join(tilesets_dir(root), t["kind"], new_folder)
    _copytree(src, dst)

    old_suffix = t["suffix"]
    old_path = f"data/tilesets/{t['kind']}/{t['folder']}"
    new_path = f"data/tilesets/{t['kind']}/{new_folder}"

    def retarget(text: str) -> str:
        return _retarget_text(text, old_suffix, new_suffix, old_path, new_path)

    graphics_decl = retarget(t["graphics_decl"])
    metatiles_decl = retarget(t["metatiles_decl"])
    header_decl = retarget(t["header_decl"])
    anim_code = retarget(t.get("anim_code", "") or "")

    new_label = "gTileset_" + new_suffix
    new_cb = "InitTilesetAnim_" + new_suffix

    # If this tileset is already declared in the target, only its files were
    # (optionally) overwritten — never re-declare or re-inject code.
    htext = _read_file(headers_h(root))
    if re.search(r'gTileset_' + re.escape(new_suffix) + r'\s*=', htext):
        if action == "overwrite":
            rep.warnings.append(
                f"tileset {new_label} already declared - folder overwritten, "
                f"headers left as-is.")
        else:
            rep.warnings.append(
                f"tileset {new_label} already declared - reused existing headers.")
        rep.added_tilesets.append(new_label + " (files only)")
        return

    # Animation handling for a genuinely NEW tileset:
    #   • have the code + target doesn't already define it  -> inject, keep cb
    #   • animated but no code captured / already defined    -> NULL the cb
    cb = t.get("callback", "NULL")
    inject_anim = bool(anim_code) and new_cb not in existing_anims
    anim_path = _anim_source_path(root)
    if inject_anim and anim_path:
        _append_to_file(anim_path,
                        f"\n// ---- imported tileset animation: {new_label} ----\n"
                        + anim_code + "\n")
        existing_anims.add(new_cb)
        rep.warnings.append(
            f"tileset {new_label}: animation code injected into "
            f"{os.path.basename(anim_path)} (animates as in the source).")
    elif cb and cb != "NULL":
        # can't animate here — disable so the build links
        header_decl = re.sub(r'\.callback\s*=\s*[A-Za-z0-9_]+\s*,',
                             '.callback = NULL,', header_decl)
        why = ("no animation code was captured in the bundle"
               if not anim_code else "the target already defines it")
        rep.warnings.append(
            f"tileset {new_label}: animation disabled ({why}); callback set "
            f"to NULL.")

    _append_to_file(graphics_h(root), "\n" + graphics_decl + "\n")
    _append_to_file(metatiles_h(root), "\n" + metatiles_decl + "\n")
    _append_to_file(headers_h(root), "\n" + header_decl + "\n")
    rep.added_tilesets.append(
        new_label + (" (+anim)" if inject_anim else ""))


def _append_to_file(path: str, text: str) -> None:
    # LF only — keep appended C in step with the decomp's line endings.
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
