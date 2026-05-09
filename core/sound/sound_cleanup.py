"""
Sound Editor cleanup utilities.

Scans the project for dead audio data:
  1. Orphaned .bin files — in direct_sound_samples/ but not in direct_sound_data.inc
  2. Broken .inc entries — in direct_sound_data.inc but .bin file missing on disk
  3. Orphaned song files — .s/.mid in sound/songs/midi/ with no matching MUS_* constant
  4. Orphaned voicegroups — voicegroupNNN blocks in voice_groups.inc not referenced by any .s file
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_log = logging.getLogger("SoundEditor.Cleanup")


@dataclass
class OrphanEntry:
    category: str          # "bin", "inc_entry", "song", "voicegroup"
    label: str             # human-readable name / label
    file_path: Optional[Path]
    size_bytes: int = 0
    extra_paths: list[Path] = field(default_factory=list)  # paired files


# ── Scanner 1: orphaned .bin files ──────────────────────────────────────────

def scan_orphaned_bins(project_root: str, sample_data) -> list[OrphanEntry]:
    """Find .bin files in direct_sound_samples/ with no matching .inc entry."""
    samples_dir = Path(project_root) / "sound" / "direct_sound_samples"
    inc_path = Path(project_root) / "sound" / "direct_sound_data.inc"

    if not samples_dir.exists():
        return []

    # Build set of .bin filenames referenced in the .inc file
    referenced_bins: set[str] = set()
    if inc_path.exists():
        text = inc_path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'\.incbin\s+"([^"]+)"', text):
            ref = m.group(1)
            referenced_bins.add(os.path.basename(ref).lower())

    # Also cross-check against in-memory sample_data (unsaved state)
    ram_labels: set[str] = set()
    if sample_data and hasattr(sample_data, '_ds_label_to_path'):
        for rel in sample_data._ds_label_to_path.values():
            ram_labels.add(os.path.basename(rel).lower())

    orphans = []
    for bin_file in sorted(samples_dir.glob("*.bin")):
        name_lower = bin_file.name.lower()
        if name_lower not in referenced_bins and name_lower not in ram_labels:
            try:
                size = bin_file.stat().st_size
            except OSError:
                size = 0
            orphans.append(OrphanEntry(
                category="bin",
                label=bin_file.stem,
                file_path=bin_file,
                size_bytes=size,
            ))
    return orphans


# ── Scanner 2: broken .inc entries ─────────────────────────────────────────

def scan_broken_inc_entries(project_root: str) -> list[OrphanEntry]:
    """Find .inc entries pointing to .bin files that don't exist on disk."""
    inc_path = Path(project_root) / "sound" / "direct_sound_data.inc"
    if not inc_path.exists():
        return []

    root = Path(project_root)
    text = inc_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Build label -> incbin path mapping
    label_re = re.compile(r'^(\w+)::')
    incbin_re = re.compile(r'^\s*\.incbin\s+"([^"]+)"')

    orphans = []
    current_label = None
    for line in lines:
        m = label_re.match(line.strip())
        if m:
            current_label = m.group(1)
            continue
        m = incbin_re.match(line)
        if m and current_label:
            rel_path = m.group(1)
            abs_path = root / rel_path
            if not abs_path.exists():
                orphans.append(OrphanEntry(
                    category="inc_entry",
                    label=current_label,
                    file_path=None,
                    size_bytes=0,
                    extra_paths=[],
                ))
            current_label = None
    return orphans


# ── Scanner 3: orphaned song files ─────────────────────────────────────────

def scan_orphaned_songs(project_root: str) -> list[OrphanEntry]:
    """Find .s/.mid files in sound/songs/midi/ with no matching constant in songs.h.

    File naming convention in pokefirered is the constant name lowercased —
    `MUS_BERRY_PICK` → `mus_berry_pick.{s,mid}`, `SE_BALL` → `se_ball.{s,mid}`,
    and so on. The previous implementation only matched `MUS_*` and reported
    every `SE_*` SFX file as orphaned even though they're properly registered
    as `SE_*` constants in the SAME header file. The fix is to take every
    `#define`'d constant in songs.h as a known stem, regardless of prefix,
    and not strip any prefix from the file stem either — match full names.
    """
    songs_dir = Path(project_root) / "sound" / "songs" / "midi"
    songs_h = Path(project_root) / "include" / "constants" / "songs.h"

    if not songs_dir.exists():
        return []

    # Every `#define <NAME>` constant in songs.h, lowercased. Covers MUS_*,
    # SE_*, and anything else a hack might add (FANFARE_*, CRY_*, custom).
    known: set[str] = set()
    if songs_h.exists():
        text = songs_h.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'#define\s+(\w+)', text):
            known.add(m.group(1).lower())

    # Group files by lowercased stem (no prefix stripping — the file stem
    # IS the constant name lowercased, so we use it directly as the join
    # key against `known`).
    stems: dict[str, dict] = {}
    for f in songs_dir.iterdir():
        if f.suffix in ('.s', '.mid'):
            stem = f.stem.lower()
            if stem not in stems:
                stems[stem] = {'stem': stem, 'paths': []}
            stems[stem]['paths'].append(f)

    orphans = []
    for stem_key, info in sorted(stems.items()):
        if stem_key not in known:
            total_size = sum(
                p.stat().st_size for p in info['paths']
                if p.exists()
            )
            primary = next((p for p in info['paths'] if p.suffix == '.s'), info['paths'][0])
            extras = [p for p in info['paths'] if p != primary]
            orphans.append(OrphanEntry(
                category="song",
                label=primary.stem,
                file_path=primary,
                size_bytes=total_size,
                extra_paths=extras,
            ))
    return orphans


# ── Scanner 4: orphaned voicegroups ────────────────────────────────────────

def scan_orphaned_voicegroups(project_root: str) -> list[OrphanEntry]:
    """Find voicegroupNNN blocks in voice_groups.inc not transitively reachable.

    Reachability sources:
      1. Direct reference from a song .s file via `.equ <song>_grp, voicegroupNNN`.
      2. Direct reference from C/H source via the bare `voicegroupNNN` token
         (e.g. `m4a_tables.c` references voicegroup000 for the cry table).
      3. Transitive `voice_keysplit` / `voice_keysplit_all` reference from
         a voicegroup that is itself reachable. The GBA M4A engine uses
         keysplit voicegroups to dispatch to child voicegroups (Pokemon cries
         are the canonical example: voicegroup000 keysplits into 001-007 by
         species index). The previous implementation didn't follow these
         child references and therefore reported every cry-child voicegroup
         as "orphaned" — clicking Delete would have wiped Pokemon cries.
    """
    project = Path(project_root)
    vg_inc = project / "sound" / "voice_groups.inc"
    songs_dir = project / "sound" / "songs" / "midi"

    if not vg_inc.exists():
        return []

    text = vg_inc.read_text(encoding="utf-8", errors="replace")

    # Defined voicegroups (declared with `voicegroupNNN::` at the start of
    # a line). Tracked in declaration order so the orphan list stays stable.
    defined: list[str] = re.findall(
        r'^(\w*voicegroup\w*):', text,
        re.MULTILINE | re.IGNORECASE)
    defined_set = set(defined)

    # Build the keysplit graph: parent -> set of children. Walk the file
    # line by line; track the current parent block; collect every
    # `voice_keysplit <child>, ...` and `voice_keysplit_all <child>` ref.
    children_of: dict[str, set[str]] = {name: set() for name in defined}
    current = None
    head_re = re.compile(r'^(\w*voicegroup\w*):', re.IGNORECASE)
    keysplit_re = re.compile(
        r'^\s*voice_keysplit(?:_all)?\s+(\w+)',
        re.IGNORECASE)
    for line in text.splitlines():
        m = head_re.match(line)
        if m:
            current = m.group(1)
            continue
        if current is None:
            continue
        m = keysplit_re.match(line)
        if m:
            child = m.group(1)
            if child in defined_set:
                children_of[current].add(child)

    # Roots: directly referenced from .s files via `.equ ..._grp, voicegroupNNN`.
    referenced: set[str] = set()
    if songs_dir.exists():
        for s_file in songs_dir.glob("*.s"):
            try:
                s_text = s_file.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'\.equ\s+\w+_grp\s*,\s*(\w+)', s_text):
                    referenced.add(m.group(1))
            except Exception:
                pass

    # Roots: bare `voicegroupNNN` token mentioned in any C / H source. The
    # M4A engine in C code holds a typed reference to voicegroup000 (cry
    # table), so even if no song uses it the C side keeps it alive.
    src_dirs = [project / "src", project / "include"]
    c_token_re = re.compile(r'\bvoicegroup\d+\b')
    for src_dir in src_dirs:
        if not src_dir.exists():
            continue
        for ext in ("*.c", "*.h"):
            for src_file in src_dir.rglob(ext):
                try:
                    s_text = src_file.read_text(
                        encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for tok in c_token_re.findall(s_text):
                    if tok in defined_set:
                        referenced.add(tok)

    # Transitive reachability: BFS from referenced through children.
    live: set[str] = set()
    frontier = list(referenced & defined_set)
    while frontier:
        nxt = frontier.pop()
        if nxt in live:
            continue
        live.add(nxt)
        for child in children_of.get(nxt, ()):
            if child not in live:
                frontier.append(child)

    # Orphans = defined - live (preserve declaration order for the UI).
    orphans = []
    for name in defined:
        if name not in live:
            orphans.append(OrphanEntry(
                category="voicegroup",
                label=name,
                file_path=vg_inc,
                size_bytes=1536,  # estimate — 128 slots × 12 bytes typical
            ))
    return orphans


# ── Deleter ─────────────────────────────────────────────────────────────────

def delete_entries(entries: list[OrphanEntry], project_root: str) -> list[str]:
    """Delete the given orphan entries. Returns list of error messages."""
    from pathlib import Path

    inc_path = str(Path(project_root) / "sound" / "direct_sound_data.inc")
    vg_inc_path = Path(project_root) / "sound" / "voice_groups.inc"
    build_root = Path(project_root) / "build"

    errors = []

    for entry in entries:
        try:
            if entry.category == "bin":
                if entry.file_path and entry.file_path.exists():
                    _log.info("Deleting orphaned .bin: %s (%d bytes)",
                              entry.file_path, entry.size_bytes)
                    entry.file_path.unlink()

            elif entry.category == "inc_entry":
                from core.sound.sample_loader import _remove_inc_entry
                _log.info("Removing broken .inc entry: %s", entry.label)
                _remove_inc_entry(inc_path, entry.label)

            elif entry.category == "song":
                # Delete .s file
                if entry.file_path and entry.file_path.exists():
                    _log.info("Deleting orphaned song .s: %s", entry.file_path)
                    entry.file_path.unlink()
                # Delete paired files (.mid, etc.)
                for extra in entry.extra_paths:
                    if extra.exists():
                        _log.info("Deleting paired song file: %s", extra)
                        extra.unlink()
                # Delete compiled .o objects
                stem = entry.file_path.stem if entry.file_path else entry.label
                if build_root.exists():
                    for o_file in build_root.rglob(f"sound/songs/midi/{stem}.o"):
                        _log.info("Deleting compiled .o: %s", o_file)
                        o_file.unlink()

            elif entry.category == "voicegroup":
                if vg_inc_path.exists():
                    _log.info("Removing voicegroup block: %s", entry.label)
                    _remove_voicegroup_block(str(vg_inc_path), entry.label)

        except Exception as e:
            msg = f"Failed to delete {entry.label}: {e}"
            _log.error(msg)
            errors.append(msg)

    return errors


def _remove_voicegroup_block(inc_path: str, vg_name: str) -> None:
    """Remove a voicegroupNNN block from voice_groups.inc."""
    with open(inc_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    in_block = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect block start: "voicegroupNNN:"
        if not in_block and re.match(rf'^{re.escape(vg_name)}\s*:', stripped):
            in_block = True
            i += 1
            continue

        if in_block:
            # Detect block end: ".end" or next label definition or blank line after end
            if 'voicegroup_end' in stripped or stripped == '.end':
                in_block = False
                i += 1
                # Skip trailing blank line after block end
                if i < len(lines) and lines[i].strip() == '':
                    i += 1
                continue
            i += 1
            continue

        new_lines.append(line)
        i += 1

    with open(inc_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
