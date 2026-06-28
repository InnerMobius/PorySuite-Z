"""Regenerate a MIDI-sourced song's ``.s`` with the project's own **mid2agb** —
the same reference encoder the build uses — instead of PorySuite's hand-rolled
``.s`` writer.

For a song that has a ``.mid`` source, the ``.s`` is a build artifact: the
Makefile rule is ``%.s: %.mid midi.cfg`` and runs mid2agb. PorySuite's writer
re-derives the ``.s`` text itself, and small encoding differences from mid2agb
(e.g. omitting an explicit note-length on a chord tone) can play back broken
even though the file assembles. Routing saves through mid2agb makes the editor
produce exactly what the build would — so editing volume / priority / etc. can
never desync the ``.s`` from mid2agb's encoding.

Fail-OPEN: if mid2agb can't be found or errors, the caller keeps its own ``.s``.
"""
import os
import re
import shutil
import subprocess

__all__ = ["find_mid2agb", "recompile_song"]

_CFG_LINE = re.compile(r"^(\S+\.mid):\s*(.*)$")


def find_mid2agb(project_root: str):
    """Locate the mid2agb executable: the project's bundled tool first
    (tools/mid2agb/), then PATH. Returns the path or None."""
    for name in ("mid2agb.exe", "mid2agb"):
        cand = os.path.join(project_root, "tools", "mid2agb", name)
        if os.path.isfile(cand):
            return cand
    return shutil.which("mid2agb")


def _flags_for(project_root: str, label: str):
    """Return mid2agb's arg list for *label* from midi.cfg (the SAME flags the
    build passes), or None if the song isn't listed."""
    cfg = os.path.join(project_root, "sound", "songs", "midi", "midi.cfg")
    want = label + ".mid"
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                m = _CFG_LINE.match(line.strip())
                if m and m.group(1) == want:
                    return m.group(2).split()
    except OSError:
        pass
    return None


def recompile_song(project_root: str, label: str):
    """Regenerate ``<midi>/<label>.s`` from ``<label>.mid`` via mid2agb, using
    midi.cfg's flags so the output matches the build exactly.

    Returns (ok, err). On any failure (no mid2agb, no .mid, not in midi.cfg,
    non-zero exit) returns (False, message) and writes nothing — the caller
    keeps whatever .s it already has.
    """
    exe = find_mid2agb(project_root)
    if not exe:
        return (False, "mid2agb not found")
    midi_dir = os.path.join(project_root, "sound", "songs", "midi")
    mid = os.path.join(midi_dir, label + ".mid")
    s = os.path.join(midi_dir, label + ".s")
    if not os.path.isfile(mid):
        return (False, f"no .mid source for {label}")
    flags = _flags_for(project_root, label)
    if flags is None:
        return (False, f"{label}.mid has no midi.cfg entry")
    try:
        proc = subprocess.run(
            [exe, mid, s] + flags,
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, f"mid2agb invocation failed: {exc}")
    if proc.returncode != 0:
        return (False, (proc.stderr or proc.stdout or "mid2agb error").strip())
    return (True, None)
