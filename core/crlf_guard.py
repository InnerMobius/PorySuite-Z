"""crlf_guard.py — keep the decomp's text files LF, always.

The decomp toolchain is LF-only. On Windows, Python's default text-mode write
turns `\\n` into `\\r\\n`, and one tool (mid2agb-adjacent mapjson) is silently
broken by CRLF — it corrupts the file into trailing NULs and dies with
"unexpected trailing (0)". Most other tools tolerate CRLF, so it usually shows
up only as noisy git churn.

Every tool writer is expected to pass `newline='\\n'`. This module is the
belt-and-braces backstop: a fast pre-build sweep that finds any decomp text
file that slipped through with CRLF (a missed writer, a hand edit, a bad merge)
and normalizes it to LF BEFORE the build sees it. Verification as
infrastructure — the class of bug can't reach the compiler even if a new writer
forgets the flag.
"""

from __future__ import annotations

import os

# Only the file types where CRLF is BUILD-FATAL. The JSON processors (mapjson,
# jsonproc) have a text-mode read bug that turns a CRLF file into trailing NUL
# bytes and dies ("unexpected trailing (0)"). Everything else in the decomp
# (.inc / .s / .h) is tolerated by gas/cpp, and the project legitimately ships
# hundreds of CRLF files — mass-rewriting those would be a huge surprise diff
# for zero build benefit. So the guard heals ONLY the JSON the toolchain reads.
_SCAN_DIRS = (
    ("data", (".json",)),
    (os.path.join("src", "data"), (".json",)),
)
_SKIP_DIR_PARTS = (os.sep + "build" + os.sep, os.sep + ".git" + os.sep)


def find_crlf_files(project_root: str) -> list[str]:
    """Return decomp text files under *project_root* that contain CRLF."""
    hits: list[str] = []
    for sub, exts in _SCAN_DIRS:
        base = os.path.join(project_root, sub)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            if any(p in (root + os.sep) for p in _SKIP_DIR_PARTS):
                continue
            for f in files:
                if not f.endswith(exts):
                    continue
                path = os.path.join(root, f)
                try:
                    with open(path, "rb") as fh:
                        # cheap: only need to know if a CR-LF exists
                        if b"\r\n" in fh.read():
                            hits.append(path)
                except OSError:
                    continue
    return hits


def heal_project_crlf(project_root: str) -> list[str]:
    """Normalize every decomp text file with CRLF to LF. Returns the files
    that were fixed (relative paths)."""
    fixed: list[str] = []
    for path in find_crlf_files(project_root):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            with open(path, "wb") as fh:
                fh.write(data.replace(b"\r\n", b"\n"))
            fixed.append(os.path.relpath(path, project_root).replace("\\", "/"))
        except OSError:
            continue
    return fixed
