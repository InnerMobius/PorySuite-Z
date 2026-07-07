"""voicegroup_writer.py — serialize VoicegroupData back to sound/voice_groups.inc.

This logic used to live only inside ui/voicegroups_tab.py, which meant the MIDI
importer had no way to PERSIST a voicegroup it generated. That gap is exactly
what broke "Generate GM": the new voicegroup was added in memory and the song
was wired to it (-G / _grp), but voice_groups.inc was never rewritten, so the
build failed with "voicegroupNNN does not exist".

Extracting the writer here lets both the editor tab and the importer write the
file the same way, so a generated voicegroup is on disk before anything
references it.
"""

from __future__ import annotations

import os


def instrument_to_asm(inst) -> str:
    """Convert one Instrument back to its assembly macro line."""
    vt = inst.voice_type

    if vt in ('voice_directsound', 'voice_directsound_no_resample',
              'voice_directsound_alt'):
        return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                f"{inst.sample_label}, "
                f"{inst.attack}, {inst.decay}, "
                f"{inst.sustain}, {inst.release}")

    # PSG voices (square/noise/wave): clamp ADSR into hardware ranges so a
    # leaked DirectSound 0-255 value can never be written (that produced the
    # silent "attack 255" noise bug).
    from core.sound.sound_constants import clamp_psg_envelope
    a, d, s, r = clamp_psg_envelope(
        inst.attack, inst.decay, inst.sustain, inst.release)

    if vt in ('voice_square_1', 'voice_square_1_alt'):
        return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                f"{inst.sweep}, {inst.duty_cycle}, "
                f"{a}, {d}, {s}, {r}")

    if vt in ('voice_square_2', 'voice_square_2_alt'):
        return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                f"{inst.duty_cycle}, "
                f"{a}, {d}, {s}, {r}")

    if vt in ('voice_programmable_wave', 'voice_programmable_wave_alt'):
        return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                f"{inst.wave_label}, "
                f"{a}, {d}, {s}, {r}")

    if vt in ('voice_noise', 'voice_noise_alt'):
        return (f"{vt} {inst.base_midi_key}, {inst.pan}, "
                f"{inst.period}, "
                f"{a}, {d}, {s}, {r}")

    if vt == 'voice_keysplit':
        return f"{vt} {inst.target_voicegroup}, {inst.keysplit_table}"

    if vt == 'voice_keysplit_all':
        return f"{vt} {inst.target_voicegroup}"

    return f"@ unknown voice type: {vt}"


def append_voicegroup_to_inc(vg, project_root: str) -> str:
    """Add ONE voicegroup block to sound/voice_groups.inc without touching the
    rest of the file.

    A full rewrite would strip inline comments and reformat every other
    voicegroup — unacceptable for just adding one. Voicegroup labels are
    referenced by name, so appending out of numeric order is fine for the
    assembler. If a block with this name already exists it is replaced in
    place (same surgical, comment-preserving spirit). Returns the path.
    """
    import re
    out_path = os.path.join(project_root, 'sound', 'voice_groups.inc')
    block_lines = ["\t.align 2", f"{vg.name}::"]
    for inst in vg.instruments:
        block_lines.append(f"\t{instrument_to_asm(inst)}")
    block = "\n".join(block_lines)

    existing = ""
    if os.path.isfile(out_path):
        with open(out_path, 'r', encoding='utf-8', newline='') as f:
            existing = f.read().replace('\r\n', '\n')

    # Replace an existing block for this exact voicegroup, if present.
    label_re = re.compile(
        r'(?:^\t*\.align\s+\d+\n)?^' + re.escape(vg.name) + r'::\n'
        r'(?:^(?!\S).*\n?)*', re.M)
    if re.search(r'^' + re.escape(vg.name) + r'::', existing, re.M):
        new_text = label_re.sub(block + "\n", existing, count=1)
    else:
        sep = "" if existing.endswith("\n") or not existing else "\n"
        new_text = existing + sep + "\n" + block + "\n"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_text)
    return out_path


def _voicegroup_sort_key(name: str):
    """Sort voicegroupNNN by numeric index so the file stays ordered."""
    import re
    m = re.search(r'(\d+)', name)
    return (0, int(m.group(1))) if m else (1, name)


def write_voice_groups_inc(voicegroup_data, project_root: str) -> str:
    """Write the full sound/voice_groups.inc for *voicegroup_data*.

    Preserves any ``.include`` lines from the existing file (re-anchored to the
    voicegroup they followed). Returns the path written.
    """
    out_path = os.path.join(project_root, 'sound', 'voice_groups.inc')

    # Preserve .include lines and remember which VG each followed.
    include_after: dict[str, list[str]] = {}
    includes_before_first: list[str] = []
    if os.path.isfile(out_path):
        try:
            with open(out_path, 'r', encoding='utf-8') as f:
                last_vg = None
                for raw in f:
                    stripped = raw.strip()
                    if stripped.endswith('::'):
                        last_vg = stripped.rstrip(':')
                    elif stripped.startswith('.include'):
                        if last_vg is None:
                            includes_before_first.append(stripped)
                        else:
                            include_after.setdefault(last_vg, []).append(stripped)
        except OSError:
            pass

    lines: list[str] = []
    if includes_before_first:
        lines.extend(includes_before_first)
        lines.append("")

    for vg_name in sorted(voicegroup_data.voicegroups.keys(),
                          key=_voicegroup_sort_key):
        vg = voicegroup_data.voicegroups[vg_name]
        lines.append("\t.align 2")
        lines.append(f"{vg_name}::")
        for inst in vg.instruments:
            lines.append(f"\t{instrument_to_asm(inst)}")
        lines.append("")
        if vg_name in include_after:
            for inc_line in include_after[vg_name]:
                lines.append(inc_line)
            lines.append("")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))
        f.write('\n')
    return out_path
