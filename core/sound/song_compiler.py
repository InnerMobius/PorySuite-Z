"""Find the project's own **mid2agb** and the flags midi.cfg gives a song.

For a song that has a ``.mid`` source, the ``.s`` is what mid2agb makes from it
(the Makefile rule is ``%.s: %.mid midi.cfg``). PorySuite never writes such a
``.s`` itself: ``safe_song_save`` runs this mid2agb, with these flags, in a
temporary folder, checks the result, and only then writes it — so the editor
always produces exactly what the build would.
"""
import os
import re
import shutil

__all__ = ["find_mid2agb"]

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
