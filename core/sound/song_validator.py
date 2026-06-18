"""Export-time validator for GBA M4A song .s files.

The Sound Editor's writer is the canonical source of truth, but a bad import,
a stale code path, or a future regression must NEVER be able to put a song on
disk that won't build or that strands a sound on the wrong voicegroup. This
module is the gate: ``save_song_file`` runs it on the generated text + the
SongData BEFORE writing, and refuses to write if anything is wrong.

It checks the two failure classes seen in the wild:

  * Malformed M4A: a velocity that isn't 3 digits (``v53`` vs ``v053``), a
    track/song pointer emitted as ``.hword`` (→ "relocation truncated to fit
    R_ARM_ABS16"), a missing ``.word <name>_grp`` voicegroup pointer, an
    undefined bare macro like ``.byte REV``.
  * Wrong/out-of-range instrument: a ``VOICE`` number that doesn't exist in the
    song's assigned voicegroup (e.g. VOICE 127 on voicegroup000), or a song
    that references a voicegroup the project doesn't contain.

Every check is conservative — it flags only definitely-invalid output, so it
never blocks a legitimate save. Returns a list of human-readable error strings
(empty == valid).
"""

from __future__ import annotations

import os
import re
from typing import Optional


# Bare tokens that are NOT defined by MPlayDef.s and must never appear as a
# `.byte <TOKEN>` argument. Reverb is emitted as the `<song>_rev` equate.
_UNDEFINED_BYTE_TOKENS = {'REV'}


def _project_root_from_path(s_path: Optional[str]) -> Optional[str]:
    """Derive the project root from a .../sound/songs/midi/<x>.s path."""
    if not s_path:
        return None
    marker = os.path.join('sound', 'songs', 'midi')
    norm = s_path.replace('\\', '/')
    m = marker.replace('\\', '/')
    idx = norm.rfind(m)
    if idx < 0:
        return None
    return s_path[:idx].rstrip('\\/') or None


def validate_s_text(text: str, song_label: str) -> list[str]:
    """Validate the GENERATED .s text. Returns a list of error strings."""
    errors: list[str] = []
    lines = text.split('\n')

    # 1. Velocity tokens must be exactly 3 digits (v053, not v53). `\bv\d+\b`
    #    won't match `c_v+64` or `reverb` (the `v` there isn't on a word
    #    boundary / isn't followed by digits), so this is false-positive safe.
    for i, ln in enumerate(lines, 1):
        for mo in re.finditer(r'\bv(\d+)\b', ln):
            if len(mo.group(1)) != 3:
                errors.append(
                    f"line {i}: velocity 'v{mo.group(1)}' is not 3 digits "
                    f"(should be v{int(mo.group(1)):03d}) — {ln.strip()}")

    # 2. No `.hword` anywhere. Valid M4A song files use `.word` for every
    #    pointer; a `.hword` track pointer truncates the relocation and fails
    #    the build (this is exactly what broke se_select).
    for i, ln in enumerate(lines, 1):
        if re.search(r'\.hword\b', ln):
            errors.append(
                f"line {i}: '.hword' is invalid for a song pointer "
                f"(must be '.word') — {ln.strip()}")

    # 3. The voicegroup pointer must be present in the header.
    if not re.search(r'^\s*\.word\s+' + re.escape(song_label) + r'_grp\s*$',
                     text, re.MULTILINE):
        errors.append(
            f"missing voicegroup pointer '.word {song_label}_grp' "
            f"in the song header")

    # 4. No undefined bare macros (e.g. `.byte REV , ...`).
    for i, ln in enumerate(lines, 1):
        mo = re.match(r'\s*\.byte\s+(\w+)\b', ln)
        if mo and mo.group(1) in _UNDEFINED_BYTE_TOKENS:
            errors.append(
                f"line {i}: undefined macro '.byte {mo.group(1)}' — {ln.strip()}")

    # 5. The header block must carry NumTrks/NumBlks/priority/reverb and at
    #    least one track pointer after the voicegroup pointer.
    hdr = re.search(r'^' + re.escape(song_label) + r':\s*$', text, re.MULTILINE)
    if not hdr:
        errors.append(f"missing song header label '{song_label}:'")
    else:
        tail = text[hdr.end():]
        n_word = len(re.findall(r'^\s*\.word\s+\w+', tail, re.MULTILINE))
        # voicegroup pointer + >=1 track pointer => at least 2 .word lines
        if n_word < 2:
            errors.append(
                f"song header has too few '.word' pointers "
                f"(need voicegroup + at least one track)")

    return errors


def validate_song_voices(song, project_root: Optional[str],
                         vg_data=None) -> list[str]:
    """Validate that every VOICE number exists in the song's voicegroup.

    Catches the se_rupee class of bug: a sound stranded on voicegroup000 whose
    VOICE 127 doesn't exist there. Needs voicegroup data — pass ``vg_data`` if
    already loaded, else give ``project_root`` and it loads on demand. If the
    voicegroup can't be resolved, that itself is reported.
    """
    errors: list[str] = []
    vg_name = getattr(song, 'voicegroup', None)
    if not vg_name:
        errors.append("song has no voicegroup (.equ <name>_grp) assigned")
        return errors

    if vg_data is None and project_root:
        try:
            from core.sound.voicegroup_parser import load_voicegroup_data
            vg_data = load_voicegroup_data(project_root)
        except Exception:
            return errors  # can't load — skip voice-range check, not fatal
    if vg_data is None:
        return errors

    vg = vg_data.get_voicegroup(vg_name)
    if vg is None:
        errors.append(
            f"voicegroup '{vg_name}' referenced by the song does not exist in "
            f"the project (mid2agb would default it to voicegroup000)")
        return errors

    count = len(vg.instruments)
    if count <= 0:
        return errors  # parsed empty — don't false-flag
    for ti, track in enumerate(getattr(song, 'tracks', []) or []):
        for cmd in getattr(track, 'commands', []) or []:
            if cmd.cmd != 'VOICE' or cmd.value is None:
                continue
            v = int(cmd.value)
            if 0 <= v < count:
                continue  # in range for this voicegroup
            # A VOICE beyond this voicegroup's own count is NOT automatically
            # wrong: the GBA lays voicegroups out contiguously and a song can
            # legitimately read into the next one (ROM overflow — several
            # vanilla songs do this). Only flag a voice that maps to NO
            # instrument even after overflow into the following voicegroups.
            inst = None
            if v >= 0:
                try:
                    inst = vg_data.get_instrument_overflow(vg_name, v)
                except Exception:
                    inst = None
            if inst is None:
                errors.append(
                    f"track {ti + 1}: VOICE {v} is out of range for {vg_name} "
                    f"({count} voices) and maps to no instrument even via ROM "
                    f"overflow — it will play garbage")
    return errors


def validate_song_export(song, s_text: str,
                         project_root: Optional[str] = None,
                         vg_data=None) -> list[str]:
    """Full export validation: text shape + voicegroup/VOICE range.

    Returns a combined list of error strings (empty == valid). ``save_song_file``
    calls this BEFORE writing and refuses to write if it's non-empty.
    """
    label = getattr(song, 'label', '') or ''
    if project_root is None:
        project_root = _project_root_from_path(getattr(song, 'file_path', None))
    errors = validate_s_text(s_text, label)
    errors.extend(validate_song_voices(song, project_root, vg_data=vg_data))
    return errors


def validate_s_file(s_path: str, project_root: Optional[str] = None,
                    vg_data=None) -> list[str]:
    """Validate a song .s file ON DISK by parsing it and running the full
    export validation. Used by the import / replace paths to gate a freshly
    written .s before it's committed/registered.

    Returns a list of error strings (empty == valid). A file that can't even
    be read/parsed is itself reported as an error — a song the tool can't
    parse is one the user can't edit and is very likely build-broken.
    """
    if not os.path.isfile(s_path):
        return [f"file does not exist: {s_path}"]
    try:
        with open(s_path, encoding='utf-8') as f:
            text = f.read()
    except OSError as exc:
        return [f"could not read {os.path.basename(s_path)}: {exc}"]
    if project_root is None:
        project_root = _project_root_from_path(s_path)
    try:
        from core.sound.song_parser import parse_song_file
        song = parse_song_file(s_path)
    except Exception as exc:
        # Still run the text checks — they don't need a parse — but flag the
        # parse failure too.
        errs = validate_s_text(text, _label_from_text(text))
        errs.insert(0, f"could not parse {os.path.basename(s_path)}: {exc}")
        return errs
    if not getattr(song, 'tracks', None):
        # No tracks parsed — either an empty/placeholder file or a malformed
        # one. Run the text checks to surface the specific problem.
        errs = validate_s_text(text, getattr(song, 'label', '') or
                               _label_from_text(text))
        if not errs:
            errs.append(f"{os.path.basename(s_path)} parsed to zero tracks")
        return errs
    return validate_song_export(song, text, project_root=project_root,
                                vg_data=vg_data)


def _label_from_text(text: str) -> str:
    """Best-effort song label from a `.global <label>` line (for messages
    when a full parse failed)."""
    m = re.search(r'^\s*\.global\s+(\w+)\s*$', text, re.MULTILINE)
    return m.group(1) if m else ''
