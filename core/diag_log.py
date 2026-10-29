"""Opt-in gate for PorySuite's temporary diagnostic logs. DISABLED BY DEFAULT.

WHY (2026-10-29). Two write-only diagnostic logs were appending on every
species save, with no rotation and no size cap, into the APPLICATION folder:

  * `diag_dirty.log`  — written by `MainWindow.save_species_data`'s `_diag`
                        helper, whose own comment reads "TEMP diagnostic
                        (false-dirty hunt) … Remove once confirmed clean".
                        17 call sites. 103 KB / 1,359 lines and growing.
  * `save_diag.log`   — written by `MainWindow._log_save_diag`. 7 call sites.
                        Already 208 KB.

Nothing in the application reads either file. Both record the project's own
content — species constants and field values, e.g.

    SPECIES_OCTO form=None DIRTY[evolutions] stored=[{'method': 'EVO_LEVEL',
    'param': 16, 'targetSpecies': 'SPECIES_OCTOROK'}] …

so they are a running transcript of the user's game data, written without being
asked, for a diagnostic nobody is currently running.

HOW THIS IS SAFE FOR A PUBLIC RELEASE
-------------------------------------
The switch is an ENVIRONMENT VARIABLE, not a settings file and not a constant.
That matters: an env var cannot be accidentally committed, cannot be shipped
switched on inside a release archive, and leaves no file in the repository that
someone could flip and publish. A released copy is silent unless the person
running it deliberately sets the variable for that session.

    PORYSUITE_DIAG=1    (or true / yes / on, any case)

TO TURN IT ON for a troubleshooting session:

    Windows (cmd)        set PORYSUITE_DIAG=1 && python app.py
    Windows (PowerShell) $env:PORYSUITE_DIAG=1; python app.py
    bash                 PORYSUITE_DIAG=1 python app.py

Anything else — unset, empty, 0, false, no, off — keeps it disabled.

This module deliberately does NOT delete, truncate, move or upload any existing
log file. Files already on disk belong to the user.
"""
from __future__ import annotations

import os

#: The one switch. An env var by design — see the module docstring.
ENV_VAR = "PORYSUITE_DIAG"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Cached so the hot paths (17 + 7 call sites, hit on every species save) do
#: not touch the environment every time. `reset_cache()` clears it.
_cached: bool | None = None


def enabled() -> bool:
    """True only when the diagnostic env var is explicitly set to a truthy value.

    The default is False. There is no settings file, constant or code path that
    turns this on by itself.
    """
    global _cached
    if _cached is None:
        _cached = os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY
    return _cached


def reset_cache() -> None:
    """Forget the cached answer — used by tests, and after a deliberate change
    to the environment within one session."""
    global _cached
    _cached = None


def append(path: str, line: str) -> bool:
    """Append one line to *path*, but ONLY when diagnostics are enabled.

    Returns True if something was written. Never raises: a diagnostic must not
    be able to break a save. When disabled this does nothing at all — it does
    not create the file, so a default install accumulates nothing.
    """
    if not enabled():
        return False
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line if line.endswith("\n") else line + "\n")
        return True
    except Exception:
        return False
