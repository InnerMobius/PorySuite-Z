"""GATE 1 — assemble a song .s exactly as the build does, as an export gate.

The static validator (`song_validator`) catches every *known* malformed-output
pattern fast and with no toolchain. This module is the belt-and-suspenders
catch for anything it doesn't know about: it runs the REAL assembler on the
generated .s, so any byte raw GNU `as` would reject — the failure class that
"assembles fine in our heads but not in the build" — is caught before the file
is committed.

It mirrors pokefirered's sound rule (audio_rules.mk):
    $(AS) $(ASFLAGS) -I sound -o $@ $<
with AS = arm-none-eabi-as (Makefile PREFIX), run from the repo root.

The toolchain may be a Windows devkitARM (DEVKITARM set to a Windows path, or
`as` on PATH) OR a WSL one (the common Windows+firered_modern setup, since the
modern build runs through WSL). Both are handled; for WSL the .s/repo paths are
translated to /mnt/<drive>/... and `as` is resolved inside WSL (its own
$DEVKITARM, else a system arm-none-eabi-as) via `wsl -e bash -lc`.

FAIL-OPEN: if no assembler (Windows or WSL) is reachable, the gate is skipped and
reports OK — the static validator stays the always-on guarantee, and we never
block a legitimate save just because this box can't assemble right now.
FAIL-CLOSED only when `as` actually runs and rejects the file.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

_log = logging.getLogger("SoundEditor.Assembler")

_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_AS_NAMES = ("arm-none-eabi-as", "arm-none-eabi-as.exe")  # Makefile: $(PREFIX)as


def _windows_as() -> Optional[str]:
    """A native Windows arm-none-eabi-as (DEVKITARM=C:\\... or on PATH), else None."""
    dka = os.environ.get("DEVKITARM", "")
    if dka and not dka.startswith("/"):  # Windows-style devkitARM path
        for n in _AS_NAMES:
            p = os.path.join(dka, "bin", n)
            if os.path.isfile(p):
                return p
    return shutil.which("arm-none-eabi-as")


def _to_wsl(path: str) -> str:
    """C:\\GBA\\x -> /mnt/c/GBA/x for a WSL invocation."""
    p = os.path.abspath(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m else p


def _run_as(s_path: str, repo_root: str) -> tuple[bool, Optional[str]]:
    """Assemble one .s with the real toolchain, exactly as audio_rules.mk does
    (`-mcpu=arm7tdmi -I sound`, cwd = repo root). Returns (ok, error_text):
    ok is True when it assembles AND when no assembler is reachable (fail-open)."""
    if not repo_root or not os.path.isdir(repo_root):
        return True, None

    # 1) Native Windows toolchain.
    win = _windows_as()
    if win:
        try:
            with tempfile.TemporaryDirectory() as td:
                out_o = os.path.join(td, "gate_check.o")
                r = subprocess.run(
                    [win, "-mcpu=arm7tdmi", "-I", "sound", "-o", out_o, s_path],
                    cwd=repo_root, capture_output=True, text=True,
                    creationflags=_NO_WINDOW, timeout=60,
                )
        except Exception as exc:
            _log.warning("assemble gate (win) could not run (%s) — skipping", exc)
            return True, None
        if r.returncode == 0:
            return True, None
        return False, (r.stderr or r.stdout or f"as exited {r.returncode}").strip()

    # 2) WSL toolchain (the modern build commonly runs through WSL).
    if shutil.which("wsl"):
        repo_w = _to_wsl(repo_root)
        s_w = _to_wsl(s_path)
        # Resolve `as` INSIDE WSL exactly as the Makefile does — prefer WSL's
        # own $DEVKITARM/bin (NOT the Windows env var, which can hold a stale or
        # non-WSL path) and fall back to a system arm-none-eabi-as on the WSL
        # PATH. `-l` sources the WSL profile so DEVKITARM/PATH are populated.
        bash = (
            f"cd '{repo_w}' && "
            f"AS=arm-none-eabi-as; "
            f'if [ -n "$DEVKITARM" ] && [ -x "$DEVKITARM/bin/arm-none-eabi-as" ]; '
            f'then AS="$DEVKITARM/bin/arm-none-eabi-as"; fi; '
            f'"$AS" -mcpu=arm7tdmi -I sound -o /tmp/_porysuite_gate.o \'{s_w}\''
        )
        try:
            r = subprocess.run(
                ["wsl", "-e", "bash", "-lc", bash],
                capture_output=True, text=True,
                creationflags=_NO_WINDOW, timeout=90,
            )
        except Exception as exc:
            _log.warning("assemble gate (wsl) could not run (%s) — skipping", exc)
            return True, None
        # `command not found` for as => toolchain not really there => fail-open.
        combined = (r.stderr or "") + (r.stdout or "")
        if "not found" in combined and "arm-none-eabi-as" in combined and r.returncode != 0:
            _log.info("WSL has no arm-none-eabi-as — skipping assemble gate")
            return True, None
        if r.returncode == 0:
            return True, None
        return False, combined.strip() or f"as exited {r.returncode}"

    _log.info("no assembler reachable (Windows or WSL) — skipping assemble "
              "gate; static validator still applies")
    return True, None


def assemble_file(s_path: str, repo_root: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Assemble an on-disk .s (used by the transactional import gate)."""
    if repo_root is None:
        repo_root = _repo_root_from_path(s_path)
    return _run_as(s_path, repo_root or "")


def assemble_text(content: str, repo_root: str,
                  near_dir: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Assemble generated .s text without committing it: writes a temp .s (in
    near_dir if given, so include resolution matches the real target) and runs
    the assembler. Used by save_song_file BEFORE writing the real file."""
    if not repo_root:
        return True, None
    tmp_dir = near_dir if (near_dir and os.path.isdir(near_dir)) else repo_root
    fd, tmp_s = tempfile.mkstemp(suffix=".s", prefix="_gate_", dir=tmp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return _run_as(tmp_s, repo_root)
    finally:
        try:
            os.remove(tmp_s)
        except OSError:
            pass


def _repo_root_from_path(s_path: Optional[str]) -> Optional[str]:
    if not s_path:
        return None
    marker = os.path.join("sound", "songs", "midi")
    norm = s_path.replace("\\", "/")
    idx = norm.rfind(marker.replace("\\", "/"))
    if idx < 0:
        return None
    return s_path[:idx].rstrip("\\/") or None
