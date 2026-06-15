"""core/forme_detect.py — discover a species' VANILLA alternate formes.

pokefirered encodes a multi-forme mon (the canonical case is Deoxys) entirely in
its graphics declarations in ``src/data/graphics/pokemon.h`` — there is no forme
table. The forme is picked at COMPILE TIME by a ``#ifdef`` on the ROM version,
and each ``#ifdef`` branch points the SAME symbol (``gMonFrontPic_Deoxys`` …) at
a different sprite sheet + icon set:

    #ifdef FIRERED
    gMonFrontPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/front.4bpp.lz");
    gMonIcon_Deoxys[]     = INCBIN_U8 ("…/icon.4bpp", "…/icon_attack.4bpp");
    #endif
    #ifdef LEAFGREEN
    gMonFrontPic_Deoxys[] = INCBIN_U32("graphics/pokemon/deoxys/front_def.4bpp.lz");
    gMonIcon_Deoxys[]     = INCBIN_U8 ("…/icon.4bpp", "…/icon_defense.4bpp");
    #endif

On top of that, each front sheet is 64×128 — TWO stacked 64×64 frames — and the
displayed coords are 64×64. ``DuplicateDeoxysTiles`` copies the BOTTOM frame over
the top, so the build shows the bottom pose. So Deoxys's three formes are:

    Normal  = front.png      top frame    + icon.png
    Attack  = front.png      bottom frame + icon_attack.png   (FireRed branch)
    Defense = front_def.png  bottom frame + icon_defense.png  (LeafGreen branch)

This module reads that encoding back out — ``#ifdef`` variants, the multi-arg
icon ``INCBIN`` (base icon + a per-forme icon), and the front-sheet frame count —
and returns an ordered forme list. It is GENERIC: nothing about Deoxys is
hard-coded. A forme is only emitted when there is real corroborating evidence
(a per-forme icon, a ``#ifdef`` variant, or a suffixed sheet like ``front_def``),
so an ordinary mon with a plain 1-frame sheet yields no formes (``[]``) and a
2-frame *animation*-only sheet is not mistaken for a forme.

The PNG dimensions are read straight from the IHDR header (no Qt dependency), so
this works headless and during early load before any QApplication exists.
"""

import os
import re
import struct

_KIND_RE_CACHE: dict[str, re.Pattern] = {}

# [STAT_*] index → the species_info stat key the UI uses.
_STAT_MAP = {
    "STAT_HP": "baseHP",
    "STAT_ATK": "baseAttack",
    "STAT_DEF": "baseDefense",
    "STAT_SPEED": "baseSpeed",
    "STAT_SPATK": "baseSpAttack",
    "STAT_SPDEF": "baseSpDefense",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _png_size(path: str) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR, or None. Header-only — no decode."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return (int(w), int(h))
    except Exception:
        return None


def _frames(gfx_root: str, rel_png: str) -> int:
    """How many stacked square frames a front sheet holds (height // width)."""
    sz = _png_size(os.path.join(gfx_root, rel_png.replace("/", os.sep)))
    if not sz or sz[0] <= 0:
        return 1
    return max(1, sz[1] // sz[0])


def _to_png(incbin_path: str) -> str:
    """A compiled-asset INCBIN path → its source PNG.

    ``graphics/pokemon/deoxys/front.4bpp.lz`` → ``…/front.png``;
    ``graphics/pokemon/deoxys/icon.4bpp``     → ``…/icon.png``.
    """
    for ext in (".4bpp.lz", ".8bpp.lz", ".4bpp", ".8bpp", ".gbapal.lz", ".gbapal", ".lz"):
        if incbin_path.endswith(ext):
            return incbin_path[: -len(ext)] + ".png"
    return incbin_path


def _has_forme_suffix(incbin_path: str) -> bool:
    """True for a variant sheet like ``front_def`` / ``front_attack`` — i.e. a
    front/back file whose stem carries a suffix beyond the bare ``front``/``back``."""
    stem = os.path.basename(incbin_path).split(".")[0]
    return bool(re.match(r"^(front|back)_\w+", stem))


def _forme_name(alt_icon: str | None, front_path: str, flag: str | None) -> str:
    """Human name for an alternate forme, derived from the data (never hard-coded).

    Preference: the per-forme icon's suffix (``icon_attack`` → ``Attack``), then
    the sheet's suffix (``front_def`` → ``Def``), then the ``#ifdef`` flag.
    """
    for src in (alt_icon, front_path):
        if not src:
            continue
        stem = os.path.basename(src).split(".")[0]
        for pre in ("icon_", "front_", "back_"):
            if stem.startswith(pre) and len(stem) > len(pre):
                return stem[len(pre):].replace("_", " ").title()
    if flag:
        return flag.replace("_", " ").title()
    return "Form"


def _forme(name, front_png, back_png, icon_png, frame) -> dict:
    return {
        "name": name,
        "front_png": front_png,
        "back_png": back_png or front_png,
        "icon_png": icon_png or front_png,
        "palette_png": None,   # vanilla formes share the species palette
        "frame": int(frame),
        "flag": None,          # the build flag this forme came from (FIRERED/…)
        "stats": None,         # per-forme base stats {baseHP:…} or None (= base)
    }


def _parse_forme_stats(gfx_root: str, camel: str) -> dict:
    """Parse the per-forme base-stat arrays a vanilla mon carries in
    ``src/pokemon.c`` — Deoxys keeps ``s<Camel>BaseStats`` under a
    ``#if defined(FIRERED)`` / ``#elif defined(LEAFGREEN)`` split (Attack vs
    Defense stats; ``GetDeoxysStat``/``SetDeoxysStats`` apply them in battle).

    Returns ``{flag: {baseHP, baseAttack, …}}`` keyed by the build flag, so the
    caller can attach each array to the forme that shares that flag. ``{}`` if
    the species has no such arrays (every ordinary mon).
    """
    pokemon_c = os.path.join(gfx_root, "src", "pokemon.c")
    if not os.path.isfile(pokemon_c):
        return {}
    try:
        text = _read(pokemon_c)
    except Exception:
        return {}
    sym = "s" + camel + "BaseStats"
    if sym not in text:
        return {}
    sym_re = re.compile(r"\b" + re.escape(sym) + r"\b\s*\[\s*\]\s*=")

    out: dict = {}
    flag_stack: list = [None]
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        s = lines[i].strip()
        if s.startswith("#if"):
            mm = re.match(r"#if(?:def)?\s+(?:defined\s*\(\s*)?(\w+)", s)
            flag_stack.append(mm.group(1) if mm else None)
        elif s.startswith("#elif"):
            mm = re.match(r"#elif\s+(?:defined\s*\(\s*)?(\w+)", s)
            if flag_stack:
                flag_stack[-1] = mm.group(1) if mm else None
        elif s.startswith("#endif"):
            if len(flag_stack) > 1:
                flag_stack.pop()
        elif sym_re.search(s):
            flag = flag_stack[-1]
            body = s
            j = i
            while "}" not in body and j + 1 < n:
                j += 1
                body += "\n" + lines[j]
            stats = {}
            for mm in re.finditer(r"\[\s*(STAT_\w+)\s*\]\s*=\s*(\d+)", body):
                key = _STAT_MAP.get(mm.group(1))
                if key:
                    stats[key] = int(mm.group(2))
            if stats and flag:
                out[flag] = stats
            i = j
        i += 1
    return out


def detect_formes(gfx_root: str, camel: str) -> list[dict]:
    """Return the ordered forme list for the species whose graphics symbols are
    named ``gMon*_<camel>`` (e.g. ``camel='Deoxys'``).

    ``gfx_root`` is the directory under which ``graphics/…`` and
    ``src/data/graphics/pokemon.h`` live (the project's source root).

    Each forme dict: ``{name, front_png, back_png, icon_png, palette_png, frame}``
    with PNG paths RELATIVE to ``gfx_root``. Returns ``[]`` for an ordinary mon
    (one sheet, no per-forme icons / variants) so callers can show a plain base.
    """
    pokemon_h = os.path.join(gfx_root, "src", "data", "graphics", "pokemon.h")
    if not os.path.isfile(pokemon_h):
        return []

    kind_re = _KIND_RE_CACHE.get(camel)
    if kind_re is None:
        kind_re = re.compile(r"gMon(FrontPic|BackPic|Icon)_" + re.escape(camel) + r"\b")
        _KIND_RE_CACHE[camel] = kind_re

    # Walk the file tracking #ifdef nesting; collect this species' graphics lines
    # grouped by the enclosing build flag (None = unconditional).
    variants: dict = {}   # flag -> {"front":path, "back":path, "icon":[paths]}
    order: list = []      # preserves first-seen flag order (FIRERED before LEAFGREEN)
    flag_stack: list = []

    try:
        text = _read(pokemon_h)
    except Exception:
        return []

    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("#if"):
            mm = (re.match(r"#ifdef\s+(\w+)", s)
                  or re.match(r"#if\s+defined\s*\(\s*(\w+)", s)
                  or re.match(r"#if\s+(\w+)", s))
            flag_stack.append(mm.group(1) if mm else None)
            continue
        if s.startswith("#endif"):
            if flag_stack:
                flag_stack.pop()
            continue
        if s.startswith("#else"):
            # An #else arm is a DISTINCT variant from the #if arm — give it its own
            # synthetic flag so its graphics don't overwrite the #if arm's. Without
            # this, an #ifdef/#else-style forme encoding (vs two separate #ifdef
            # blocks) would collapse both arms into one flag and drop a forme.
            if flag_stack:
                top = flag_stack[-1]
                flag_stack[-1] = ("!" + top) if top else "!else"
            continue
        if s.startswith("#elif"):
            mm = (re.match(r"#elif\s+defined\s*\(\s*(\w+)", s)
                  or re.match(r"#elif\s+(\w+)", s))
            if flag_stack:
                top = flag_stack[-1]
                flag_stack[-1] = (mm.group(1) if mm
                                  else (("!" + top) if top else "!elif"))
            continue
        m = kind_re.search(s)
        if not m:
            continue
        kind = m.group(1)
        quoted = re.findall(r'"([^"]+)"', s)
        if not quoted:
            continue
        flag = flag_stack[-1] if flag_stack else None
        if flag not in variants:
            variants[flag] = {"front": None, "back": None, "icon": []}
            order.append(flag)
        v = variants[flag]
        if kind == "FrontPic":
            v["front"] = quoted[0]
        elif kind == "BackPic":
            v["back"] = quoted[0]
        elif kind == "Icon":
            v["icon"] = quoted

    if not order:
        return []

    formes: list[dict] = []
    seen: set = set()
    multi_variant = len(order) > 1

    for vi, flag in enumerate(order):
        v = variants[flag]
        if not v.get("front"):
            continue
        front_png = _to_png(v["front"])
        back_png = _to_png(v["back"]) if v.get("back") else None
        icons = [_to_png(p) for p in (v.get("icon") or [])]
        base_icon = icons[0] if icons else None
        alt_icon = icons[1] if len(icons) > 1 else None
        nf = _frames(gfx_root, front_png)

        # Normal / base forme — frame 0 of the FIRST variant only. It uses the
        # species' own base stats (flag stays None → stats None below).
        if vi == 0 and (front_png, 0) not in seen:
            formes.append(_forme("Normal", front_png, back_png, base_icon, 0))
            seen.add((front_png, 0))

        # An alternate forme exists only with real evidence: a per-forme icon,
        # a #ifdef variant, or a suffixed sheet. (A bare 2-frame anim sheet on a
        # single unconditional definition is NOT treated as a forme.)
        make_alt = bool(alt_icon) or multi_variant or _has_forme_suffix(v["front"])
        if make_alt:
            frame = nf - 1 if nf > 1 else 0
            key = (front_png, frame)
            if key not in seen:
                name = _forme_name(alt_icon, v["front"], flag)
                fm = _forme(name, front_png, back_png, alt_icon or base_icon, frame)
                fm["flag"] = flag          # so per-forme stats can be matched
                formes.append(fm)
                seen.add(key)

    # One entry == just the base pose: an ordinary mon, report no formes.
    if len(formes) <= 1:
        return []

    # Attach per-forme base stats (Deoxys Attack/Defense). Matched by build flag;
    # the Normal forme keeps stats=None so the UI uses the species' base stats.
    stats_by_flag = _parse_forme_stats(gfx_root, camel)
    if stats_by_flag:
        for fm in formes:
            fl = fm.get("flag")
            if fl and fl in stats_by_flag:
                fm["stats"] = stats_by_flag[fl]
    return formes
