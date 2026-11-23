"""Putting a voicegroup's instruments in alphabetical order.

A voicegroup's slots are in whatever order they were added, so related
instruments end up scattered -- one project's voicegroup013 has trumpets at
17, 25, 28, 42, 43, 56 and 59. Sorting them by name makes the list readable.

Sorting is not a cosmetic change. **The slot number IS the instrument**, so
moving a slot moves what every song plays:

* Every song on the voicegroup picks its instrument by number (``VOICE , 24``).
  One voicegroup in this project is used by 45 songs with 186 of those
  commands between them.
* Those songs are rebuilt from their ``.mid``, where the number is a MIDI
  program change. Rewriting only the ``.s`` would last until the next build.
* A keysplit instrument reaches into another voicegroup by slot number, and
  the numbers live in ``keysplit_tables.inc`` as raw bytes.

So a sort is really a renumbering, and it is only safe when every reference
moves with it. This module plans the whole thing first, refuses outright if
anything in the plan cannot be carried through, and only then writes --
there is no half-sorted state.

The MIDI is treated as the source: its program changes are remapped and the
``.s`` is re-rendered from it with the project's own mid2agb, which is what a
build would produce anyway.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

MIDI_SUBDIR = os.path.join('sound', 'songs', 'midi')

_GRP_RE = re.compile(r'_grp\s*,\s*(voicegroup\d+)\b')


class VoicegroupSortRefused(Exception):
    """Raised before anything is written, when the sort cannot be carried
    through safely. Nothing on disk has been touched."""


@dataclass
class SortPlan:
    voicegroup: str
    order: list                      # new position -> old slot index
    remap: dict                      # old slot index -> new slot index
    songs: list = field(default_factory=list)      # labels to renumber
    moved: int = 0                   # how many slots actually move

    @property
    def is_noop(self) -> bool:
        return self.moved == 0


# ─────────────────────────────────────────────────────────────────────────────
# Planning
# ─────────────────────────────────────────────────────────────────────────────

def sort_key(inst, index: int):
    """Where a slot sorts. Named instruments A-Z first, placeholders last.

    Filler slots are all the same empty square wave, so they carry no name
    worth ordering and would otherwise scatter through the list. Ties keep
    their original order, so two slots with the same name stay as they were.
    """
    if inst is None:
        return (2, '', index)
    try:
        filler = bool(inst.is_filler)
    except Exception:
        filler = False
    name = (getattr(inst, 'friendly_name', '') or '').strip()
    if filler or not name:
        return (1, name.lower(), index)
    return (0, name.lower(), index)


def plan_sort(vg) -> SortPlan:
    """Work out the new slot order for *vg*. Reads nothing from disk."""
    instruments = list(vg.instruments)
    order = sorted(range(len(instruments)),
                   key=lambda i: sort_key(instruments[i], i))
    remap = {old: new for new, old in enumerate(order)}
    moved = sum(1 for old, new in remap.items() if old != new)
    return SortPlan(voicegroup=vg.name, order=order, remap=remap, moved=moved)


def songs_using(project_root: str, vg_name: str) -> list:
    """Labels of the songs whose ``.s`` names *vg_name* as its voicegroup."""
    d = os.path.join(project_root, MIDI_SUBDIR)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.s'):
            continue
        try:
            with open(os.path.join(d, fn), encoding='utf-8', errors='replace') as fh:
                head = fh.read(4000)
        except OSError:
            continue
        m = _GRP_RE.search(head)
        if m and m.group(1) == vg_name:
            out.append(fn[:-2])
    return out


def keysplits_into(project_root: str, vg_name: str) -> list:
    """Voicegroups holding a keysplit instrument that reaches into *vg_name*.

    A keysplit maps note ranges to slot numbers in its target voicegroup,
    with the numbers stored as raw bytes in ``keysplit_tables.inc``.
    Renumbering the target without rewriting those tables would silently
    repoint a drum kit, so a sort stops here instead.
    """
    path = os.path.join(project_root, 'sound', 'voice_groups.inc')
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    out = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith('::') and stripped.startswith('voicegroup'):
            current = stripped.rstrip(':')
            continue
        if 'voice_keysplit' in stripped and vg_name in stripped:
            # the target is the voicegroup named in the macro's arguments
            if re.search(r'\b' + re.escape(vg_name) + r'\b', stripped):
                out.append(current or '?')
    return sorted(set(out))


def prepare(project_root: str, vg, *, require_midi: bool = True) -> SortPlan:
    """A plan that can be carried through, or an exception saying why not.

    Nothing is written. Every reason to stop is found HERE, so that `apply`
    either does the whole job or is never reached.
    """
    plan = plan_sort(vg)
    if plan.is_noop:
        return plan

    blockers = keysplits_into(project_root, vg.name)
    if blockers:
        raise VoicegroupSortRefused(
            f"{vg.name} is reached by a keysplit instrument in "
            f"{', '.join(blockers)}. A keysplit picks slots by number from raw "
            f"bytes in keysplit_tables.inc, which this cannot rewrite, so "
            f"sorting would repoint those notes at the wrong instruments. "
            f"Nothing was changed.")

    plan.songs = songs_using(project_root, vg.name)
    if not require_midi:
        return plan

    from core.sound.song_compiler import find_mid2agb
    if plan.songs and not find_mid2agb(project_root):
        raise VoicegroupSortRefused(
            "mid2agb could not be found (tools/mid2agb), so the songs on this "
            "voicegroup cannot be rebuilt against the new slot numbers. "
            "Nothing was changed.")

    missing = []
    d = os.path.join(project_root, MIDI_SUBDIR)
    for label in plan.songs:
        if not os.path.isfile(os.path.join(d, label + '.mid')):
            missing.append(label)
    if missing:
        raise VoicegroupSortRefused(
            "these songs on this voicegroup have no .mid to renumber, so the "
            "new slot numbers would be lost the next time they are built: "
            + ", ".join(missing[:6])
            + (" and others" if len(missing) > 6 else "")
            + ". Nothing was changed.")
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Carrying it out
# ─────────────────────────────────────────────────────────────────────────────

def remap_midi(mid_bytes: bytes, remap: dict) -> bytes:
    """The same MIDI with every program change pointed at its new slot."""
    import io
    import mido
    mid = mido.MidiFile(file=io.BytesIO(mid_bytes))
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'program_change':
                new = remap.get(msg.program)
                if new is not None and new != msg.program:
                    msg.program = new
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def apply_sort(project_root: str, vg, plan: SortPlan) -> dict:
    """Renumber the songs, then reorder *vg* in memory.

    The caller saves the voicegroup itself (the Voicegroups tab's save writes
    voice_groups.inc, and backs it up first). Returns a small report.

    Each song's previous ``.mid`` and ``.s`` are copied into PorySuite's cache
    before being replaced, through the same helper the rest of the sound code
    uses.
    """
    from core.sound.safe_song_save import render_s_with_mid2agb, mid2agb_flags
    from core.sound.song_sync import backup_song_file
    from core.sound import song_guard

    d = os.path.join(project_root, MIDI_SUBDIR)
    done = []
    for label in plan.songs:
        mid_path = os.path.join(d, label + '.mid')
        s_path = os.path.join(d, label + '.s')
        with open(mid_path, 'rb') as fh:
            original = fh.read()
        new_mid = remap_midi(original, plan.remap)
        flags = mid2agb_flags(project_root, label)
        new_s = render_s_with_mid2agb(project_root, label, new_mid, flags)

        backup_song_file(project_root, mid_path, why='voicegroup_sort')
        if os.path.isfile(s_path):
            backup_song_file(project_root, s_path, why='voicegroup_sort')

        with open(mid_path, 'wb') as fh:
            fh.write(new_mid)
        s_bytes = new_s.encode('latin-1')
        # The .s was just rendered FROM this .mid, so where Song Build
        # Protection is installed its record line says exactly that: the two
        # are in step, and a build has no reason to regenerate the .s or to
        # warn that it was edited behind the MIDI's back.
        if song_guard.is_installed(project_root):
            s_bytes = song_guard.with_stamp(
                s_bytes,
                song_guard._sha1(new_mid),
                song_guard.fingerprint(s_bytes),
                song_guard.canon_flags(flags or ()))
        with open(s_path, 'wb') as fh:
            fh.write(s_bytes)
        done.append(label)

    vg.instruments = [vg.instruments[old] for old in plan.order]
    for new_index, inst in enumerate(vg.instruments):
        if inst is not None and hasattr(inst, 'slot_index'):
            inst.slot_index = new_index
    return {'voicegroup': vg.name, 'songs': done, 'moved': plan.moved}
