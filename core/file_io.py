"""Safe-write helpers that avoid phantom git diffs.

The PorySuite save pipeline runs every C-header / JSON writer on every
``Save All`` regardless of whether anything in that editor was actually
modified. This is intentional — the dirty-flag bookkeeping has historically
been unreliable in the unified window — but it means a writer that produces
output even one byte different from the existing file dirties the file.
After ``git pull`` from upstream, that "one byte different" trap fires
across many files at once because PorySuite's writers don't always match
upstream's exact formatting.

The fix is byte-equality guards: read the existing file, compare bytes,
skip the write if identical. ``write_text_if_changed`` is the single
entry point.
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger("PorySuite.FileIO")


def write_text_if_changed(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> bool:
    """Write *text* to *path* only if the on-disk bytes differ.

    Returns ``True`` if a write happened, ``False`` if the file was
    already byte-identical and the write was skipped.

    Comparison is done in binary against the encoded form of *text* with
    the given *newline* substitution applied — matching what the writer
    would have produced. This means an LF-on-disk file compared against
    LF-encoded text round-trips cleanly without spurious differences.

    On any read error (file missing, permission denied, decode failure),
    the write proceeds unconditionally. On write error, the exception
    propagates to the caller so the save path can log and recover.
    """
    # Apply the same newline normalization Python's text-mode writer would.
    if newline == "\n":
        normalized = text
    elif newline == "\r\n":
        normalized = text.replace("\n", "\r\n")
    else:
        # Be conservative — fall back to leaving \n as-is. Python would
        # do the OS-default translation; we don't want phantom diffs from
        # different machines saving the same content.
        normalized = text

    new_bytes = normalized.encode(encoding)

    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                existing_bytes = f.read()
            if existing_bytes == new_bytes:
                return False
        except Exception as exc:
            _log.debug(
                "write_text_if_changed: read failed for %s, "
                "writing unconditionally: %s", path, exc)

    # Either the file doesn't exist, the read failed, or content differs.
    # Use binary write so we don't double-translate newlines.
    with open(path, "wb") as f:
        f.write(new_bytes)
    return True
