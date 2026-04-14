import importlib.util
import os
import sys
import shutil
import subprocess
import threading
import webbrowser

from PyQt6.QtCore import pyqtSignal, Qt, QProcess, QProcessEnvironment, QThread, QObject
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QWidget, QStackedWidget, QSizePolicy,
    QTextEdit, QSizePolicy as QSP,
)
from PyQt6.QtGui import QFont, QDesktopServices, QColor, QTextCursor
from PyQt6.QtCore import QUrl

from app_info import get_data_dir


# ──────────────────────────────────────────────────────────────
#  Suppress Windows system-error dialogs for bad/incompatible binaries
# ──────────────────────────────────────────────────────────────

def _suppress_win_error_dialogs():
    """Suppress Windows 'Unsupported 16-Bit Application' and similar popups.

    Calls SetErrorMode with SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX |
    SEM_NOOPENFILEERRORBOX so that running an incompatible .exe via subprocess
    returns an error code instead of popping a system modal dialog.
    Returns the previous error mode so it can be restored.
    """
    if sys.platform != "win32":
        return 0
    import ctypes
    SEM_FAILCRITICALERRORS = 0x0001
    SEM_NOGPFAULTERRORBOX = 0x0002
    SEM_NOOPENFILEERRORBOX = 0x8000
    mode = SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
    return ctypes.windll.kernel32.SetErrorMode(mode)


def _restore_win_error_mode(prev):
    """Restore the previous Windows error mode."""
    if sys.platform != "win32":
        return
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(prev)


# ──────────────────────────────────────────────────────────────
#  Sentinel path (exported — used by app.py)
# ──────────────────────────────────────────────────────────────

def get_setup_complete_path() -> str:
    return os.path.join(get_data_dir(), "toolchain", "setup_complete")


# ──────────────────────────────────────────────────────────────
#  In-app build/install output dialog
# ──────────────────────────────────────────────────────────────

class _InAppBuildDialog(QDialog):
    """Runs a bash or pip command inside the app and streams output to a log view.

    Parameters
    ----------
    title   : Window / heading title.
    program : Executable path (bash.exe, python.exe, …).
    args    : Argument list passed to QProcess.
    env_extra : dict of extra environment variables to merge in.
    parent  : Parent widget.
    """

    def __init__(self, title: str, program: str, args: list[str],
                 env_extra: dict | None = None, parent=None):
        super().__init__(parent)
        # Window flag gives this dialog its own taskbar entry so the user
        # can switch to it without alt-tabbing.  WindowStaysOnTopHint keeps
        # it visible even when the user clicks the main window behind it.
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle(title)
        self.setMinimumSize(700, 420)
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)

        heading = QLabel(title)
        heading.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        layout.addWidget(heading)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        self._log.setStyleSheet("background:#1e1e1e; color:#d4d4d4;")
        layout.addWidget(self._log, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._close_btn = QPushButton("Close")
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        # ── QProcess ──────────────────────────────────────────
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)

        if env_extra:
            env = QProcessEnvironment.systemEnvironment()
            for k, v in env_extra.items():
                env.insert(k, v)
            self._proc.setProcessEnvironment(env)

        self._proc.setProgram(program)
        self._proc.setArguments(args)
        self._proc.start()

    def _on_output(self):
        raw = bytes(self._proc.readAllStandardOutput())
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = str(raw)
        self._output_text = getattr(self, "_output_text", "") + text
        self._log.moveCursor(QTextCursor.MoveOperation.End)
        self._log.insertPlainText(text)
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def _on_finished(self, exit_code: int, _exit_status):
        if exit_code == 0:
            self._append_colored("\n=== Completed successfully ===\n", "#4ec9b0")
        else:
            self._append_colored(
                f"\n=== Failed (exit code {exit_code}) ===\n", "#f44747"
            )
        self._close_btn.setEnabled(True)

    def _append_colored(self, text: str, color: str):
        self._log.moveCursor(QTextCursor.MoveOperation.End)
        self._log.setTextColor(QColor(color))
        self._log.insertPlainText(text)
        self._log.setTextColor(QColor("#d4d4d4"))
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def closeEvent(self, event):
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "Process Running",
                "A build is still running. Cancel it and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._proc.kill()
            self._proc.waitForFinished(2000)
        super().closeEvent(event)


def _win_path_to_msys(p: str) -> str:
    """Convert a Windows absolute path to an MSYS2-style /drive/... path."""
    p = p.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


def _wsl_devkitpro_path() -> str:
    """Return the devkitPro path to use inside WSL.
    Prefers the natively-installed WSL version (/opt/devkitpro) which has
    native ELF binaries and correct lib paths.  Falls back to the Windows
    install mounted at /mnt/c/devkitPro only if the WSL version is absent."""
    exe = _wsl_exe()
    if os.path.isfile(exe):
        try:
            r = subprocess.run(
                [exe, "test", "-f",
                 "/opt/devkitpro/devkitARM/bin/arm-none-eabi-ld"],
                capture_output=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                return "/opt/devkitpro"
        except Exception:
            pass
    # Fall back: Windows install via /mnt/c/
    win = _find_devkitpro()
    if win:
        return "/mnt/" + win[0].lower() + win[2:].replace("\\", "/")
    return "/opt/devkitpro"  # best-guess default


def _wsl_devkitpro_installed() -> bool:
    """True if devkitPro's ARM linker exists as a native ELF inside WSL."""
    exe = _wsl_exe()
    if not os.path.isfile(exe):
        return False
    try:
        r = subprocess.run(
            [exe, "test", "-f",
             "/opt/devkitpro/devkitARM/bin/arm-none-eabi-ld"],
            capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return r.returncode == 0
    except Exception:
        return False


def _install_devkitpro_wsl(parent=None) -> None:
    """Open a WSL terminal to install devkitPro natively inside WSL Ubuntu."""
    exe = _wsl_exe()
    if not os.path.isfile(exe):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, "WSL Required",
                            "WSL must be installed before devkitPro can be set up.")
        return
    script = (
        "echo '=== Installing devkitPro in WSL ==='; "
        "wget -q https://apt.devkitpro.org/install-devkitpro-pacman -O /tmp/install-dkp.sh && "
        "chmod +x /tmp/install-dkp.sh && "
        "sudo /tmp/install-dkp.sh && "
        "sudo dkp-pacman -Sy --noconfirm gba-dev && "
        "echo '=== devkitPro installed. Close this window and click Re-check. ===';"
        "read -p 'Press Enter to close...'"
    )
    subprocess.Popen(
        [exe, "bash", "-c", script],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def _find_devkitpro() -> str:
    """Return the Windows path to devkitPro, or '' if not found.

    Validates by checking that arm-none-eabi-gcc.exe actually exists inside
    the candidate path — this avoids being fooled by DEVKITPRO env vars set
    by Git for Windows' bundled MSYS2 (/opt/devkitpro) which is not the real
    devkitPro ARM toolchain.
    """
    def _has_arm_gcc(win_path: str) -> bool:
        gcc = os.path.join(win_path, "devkitARM", "bin", "arm-none-eabi-gcc.exe")
        return os.path.isfile(gcc)

    # Standard Windows install location (devkitPro installer default)
    if _has_arm_gcc(r"C:\devkitPro"):
        return r"C:\devkitPro"

    # DEVKITPRO env var — only trust it if it resolves to a real Windows path
    # with devkitARM present.  Reject MSYS2/Git-style Unix paths like
    # /opt/devkitpro that don't map to actual Windows directories.
    env_val = os.environ.get("DEVKITPRO", "")
    if env_val:
        # Convert /c/foo  →  C:\foo
        if env_val.startswith("/c/") or env_val.startswith("/C/"):
            win = "C:\\" + env_val[3:].replace("/", "\\")
            if _has_arm_gcc(win):
                return win
        elif not env_val.startswith("/"):
            # Already a Windows path
            win = env_val.replace("/", "\\")
            if _has_arm_gcc(win):
                return win
        # /opt/... or other Unix-only paths — skip

    return ""


def _devkitpro_env_exports() -> str:
    """Return bash export lines for DEVKITPRO / DEVKITARM based on what's installed."""
    dkp = _find_devkitpro()
    if not dkp:
        return ""
    msys_dkp = _win_path_to_msys(dkp)
    return (
        f"export DEVKITPRO={msys_dkp}; "
        f"export DEVKITARM={msys_dkp}/devkitARM; "
        f"export PATH={msys_dkp}/devkitARM/bin:{msys_dkp}/tools/bin:$PATH; "
    )


def _find_bash() -> str:
    """Return path to bash.exe, checking devkitPro's MSYS2 first, then standalone MSYS2."""
    candidates = [
        r"C:\devkitPro\msys2\usr\bin\bash.exe",
        r"C:\msys64\usr\bin\bash.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    found = shutil.which("bash")
    return found or ""


def _find_bash_usr_bin() -> str:
    """Return the MSYS2-style /usr/bin path for whichever MSYS2 we're using."""
    bash = _find_bash()
    if bash:
        return _win_path_to_msys(os.path.join(os.path.dirname(bash)))
    return "/usr/bin"


def _extra_path_dirs() -> str:
    """Build a PATH prefix that makes git and other Windows tools visible inside bash."""
    dirs = []
    usr_bin = _find_bash_usr_bin()
    if usr_bin:
        dirs.append(usr_bin)
    # Add directory of each tool that may only be on the Windows PATH
    for tool in ("git", "gcc", "make"):
        exe = shutil.which(tool)
        if exe:
            d = _win_path_to_msys(os.path.dirname(exe))
            if d not in dirs:
                dirs.append(d)
    return ":".join(dirs)


def _bash_dialog(title: str, script: str, parent=None,
                 env_extra: dict | None = None):
    """Open an in-app dialog that runs *script* inside a MSYS2/devkitPro bash."""
    bash = _find_bash()
    if not bash:
        webbrowser.open("https://www.msys2.org/")
        return

    extra_path = _extra_path_dirs()
    path_header = f"export PATH={extra_path}:/mingw64/bin:$PATH; " if extra_path else ""
    full_script = path_header + script

    extra = {"MSYSTEM": "MINGW64", "CHERE_INVOKING": "1"}
    if env_extra:
        extra.update(env_extra)
    dlg = _InAppBuildDialog(title, bash, ["--login", "-c", full_script],
                            env_extra=extra, parent=parent)
    dlg.exec()


def _pip_dialog(title: str, package: str, parent=None):
    """Open an in-app dialog that pip-installs *package*."""
    dlg = _InAppBuildDialog(
        title, sys.executable, ["-m", "pip", "install", "--upgrade", package],
        parent=parent,
    )
    dlg.exec()


# ──────────────────────────────────────────────────────────────
#  Install helpers
# ──────────────────────────────────────────────────────────────

def _open_url(url: str):
    QDesktopServices.openUrl(QUrl(url))


def _run_winget(pkg_id: str):
    """Launch winget in a new console window. Falls back to browser on failure."""
    try:
        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        subprocess.Popen(
            ["winget", "install", "--id", pkg_id, "-e", "--source", "winget"],
            creationflags=flags,
        )
    except Exception:
        pass


def _install_git():
    try:
        _run_winget("Git.Git")
    except Exception:
        _open_url("https://git-scm.com/download/win")


def _install_msys2():
    try:
        _run_winget("MSYS2.MSYS2")
    except Exception:
        _open_url("https://www.msys2.org/")


def _msys2_pacman(packages: str, parent=None):
    """Run pacman inside MSYS2 to install one or more packages."""
    script = (
        "export PATH=/usr/bin:/mingw64/bin:$PATH; "
        "pacman -Sy --noconfirm && "
        f"pacman -S --noconfirm {packages} && "
        "echo '--- Done ---' || echo '--- Installation failed ---'"
    )
    _bash_dialog(f"Install {packages}", script, parent=parent)


def _install_make_via_pacman():
    _msys2_pacman("make")


def _install_mingw_gcc():
    _msys2_pacman("mingw-w64-x86_64-gcc")


def _install_gba_build_libs():
    # mingw-w64-x86_64-* packages install into /mingw64/lib and /mingw64/include.
    # With /mingw64/bin prepended to PATH in _run_make, cc resolves to MINGW64's
    # GCC which links against these MINGW64 libraries correctly.
    _msys2_pacman("mingw-w64-x86_64-libpng mingw-w64-x86_64-zlib")


def _agbcc_system_dir() -> str:
    """Windows path to the root of the stored agbcc toolchain.

    Layout mirrors what pokefirered expects under tools/agbcc/:
      bin/agbcc.exe  bin/agbcc_arm.exe  bin/old_agbcc.exe
      lib/libgcc.a   lib/libc.a
      include/       (GBA system headers)
    """
    return os.path.join(get_data_dir(), "toolchain", "agbcc")


def _agbcc_system_bin_dir() -> str:
    """Windows path to the bin sub-directory of the stored agbcc toolchain."""
    return os.path.join(_agbcc_system_dir(), "bin")


def _agbcc_binary_path() -> str:
    """Return the Windows path to the agbcc binary (prefer .exe), or ''."""
    d = _agbcc_system_bin_dir()
    for name in ("agbcc.exe", "agbcc"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return ""


def _agbcc_compiled() -> bool | str:
    """Return True if agbcc exists in the toolchain store, or a string
    like ``"found_project"`` when it was only found inside a project's
    ``tools/agbcc/`` folder.  Any truthy return means the dependency is
    satisfied.

    Checks, in order:
    1. The porysuite toolchain store  (data/toolchain/agbcc/bin/)
    2. Any known project's tools/agbcc/bin/  (from projects.json)
    Accepts either agbcc.exe (Windows PE, built with MinGW64) or a plain
    ELF 'agbcc' (usable via WSL/MSYS2).
    """
    # 1. Porysuite toolchain store
    d = _agbcc_system_bin_dir()
    for name in ("agbcc.exe", "agbcc"):
        if os.path.isfile(os.path.join(d, name)):
            return True

    # 2. Any registered project's tools/agbcc/bin/
    try:
        import json as _json
        pj_path = os.path.join(get_data_dir(), "projects.json")
        if os.path.isfile(pj_path):
            with open(pj_path, encoding="utf-8") as _f:
                pj = _json.load(_f)
            for proj in pj.get("projects", []):
                proj_dir = proj.get("dir", "")
                if not proj_dir:
                    continue
                for name in ("agbcc.exe", "agbcc"):
                    p = os.path.join(proj_dir, "tools", "agbcc", "bin", name)
                    if os.path.isfile(p):
                        return "found_project"
    except Exception:
        pass

    # 3. Scan subdirectories of the app folder and its parent for
    #    tools/agbcc/bin/ — catches project folders that aren't registered yet.
    try:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scan_dirs = {app_dir}
        parent = os.path.dirname(app_dir)
        if parent and parent != app_dir:
            scan_dirs.add(parent)
        seen: set[str] = set()
        for scan_root in scan_dirs:
            try:
                entries = os.listdir(scan_root)
            except OSError:
                continue
            for entry in entries:
                candidate = os.path.join(scan_root, entry, "tools", "agbcc", "bin")
                if candidate in seen:
                    continue
                seen.add(candidate)
                for name in ("agbcc.exe", "agbcc"):
                    if os.path.isfile(os.path.join(candidate, name)):
                        return "found_project"
    except Exception:
        pass

    return False


def _find_agbcc_source() -> str:
    """Return the root agbcc toolchain dir if fully built, else ''.

    Called by _run_make to auto-provision the whole agbcc tree into a project.
    Returns the root (containing bin/, lib/, include/) rather than just bin/,
    so that _run_make can copy the entire tree into tools/agbcc/.

    Checks the porysuite toolchain store first, then falls back to any
    registered project that already has a tools/agbcc/ tree.
    """
    # 1. Porysuite toolchain store
    root = _agbcc_system_dir()
    for name in ("agbcc.exe", "agbcc"):
        if os.path.isfile(os.path.join(root, "bin", name)):
            return root

    # 2. Any registered project's tools/agbcc/
    try:
        import json as _json
        pj_path = os.path.join(get_data_dir(), "projects.json")
        if os.path.isfile(pj_path):
            with open(pj_path, encoding="utf-8") as _f:
                pj = _json.load(_f)
            for proj in pj.get("projects", []):
                proj_dir = proj.get("dir", "")
                if not proj_dir:
                    continue
                candidate = os.path.join(proj_dir, "tools", "agbcc")
                for name in ("agbcc.exe", "agbcc"):
                    if os.path.isfile(os.path.join(candidate, "bin", name)):
                        return candidate
    except Exception:
        pass

    # 3. Scan subdirectories of the app folder and its parent
    try:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scan_dirs = {app_dir}
        parent = os.path.dirname(app_dir)
        if parent and parent != app_dir:
            scan_dirs.add(parent)
        seen: set[str] = set()
        for scan_root in scan_dirs:
            try:
                entries = os.listdir(scan_root)
            except OSError:
                continue
            for entry in entries:
                candidate = os.path.join(scan_root, entry, "tools", "agbcc")
                if candidate in seen:
                    continue
                seen.add(candidate)
                for name in ("agbcc.exe", "agbcc"):
                    if os.path.isfile(os.path.join(candidate, "bin", name)):
                        return candidate
    except Exception:
        pass

    return ""


def _install_agbcc(parent=None):
    """Clone pret/agbcc and build it with MSYS2's MinGW64 gcc.

    Produces agbcc.exe / agbcc_arm.exe / old_agbcc.exe (Windows PE) plus the
    supporting lib/ and include/ trees that pokefirered's Makefile needs.
    Runs inside the in-app build dialog so the user can see all output.
    After it finishes click Re-check in the wizard to update the status badge.

    Build process
    -------------
    pret/agbcc has two scripts:
      build.sh  — runs configure + make to compile the compilers and libs
      install.sh — only copies already-built files; it does NOT compile anything
    So we run build.sh first, then copy the results ourselves.
    """
    if _agbcc_compiled():
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            parent, "agbcc Already Built",
            "agbcc is already compiled and ready.\n\n"
            "No rebuild is needed."
        )
        return

    bash = _find_bash()
    if not bash:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(
            parent, "MSYS2 Required",
            "Building agbcc requires MSYS2 with MinGW64.\n\n"
            "Install MSYS2 from https://www.msys2.org/, then install the\n"
            "mingw-w64-x86_64-gcc package via pacman, and try again."
        )
        return

    # Root of our stored agbcc toolchain (mirrors tools/agbcc/ inside a project)
    agbcc_root_msys = _win_path_to_msys(_agbcc_system_dir())

    script = (
        # No set -e — we check results manually for clear error messages.
        "echo '=== Building agbcc for PorySuite-Z ==='; "
        "command -v gcc >/dev/null 2>&1 "
        "  || { echo 'ERROR: gcc not found. Install mingw-w64-x86_64-gcc via pacman first.'; exit 1; }; "
        "command -v make >/dev/null 2>&1 "
        "  || { echo 'ERROR: make not found. Install make via pacman first.'; exit 1; }; "
        "command -v git >/dev/null 2>&1 "
        "  || { echo 'ERROR: git not found. Install git via pacman first.'; exit 1; }; "
        "AGBCC_SRC=$(mktemp -d); "
        "echo '--- Cloning pret/agbcc ---'; "
        "git clone --depth=1 https://github.com/pret/agbcc \"$AGBCC_SRC\" || exit 1; "
        "cd \"$AGBCC_SRC\"; "
        # build.sh compiles agbcc, agbcc_arm, old_agbcc, libgcc.a, libc.a
        "echo '--- Running build.sh (this takes about a minute) ---'; "
        "chmod +x build.sh; "
        "./build.sh 2>&1; "
        "BUILD_EXIT=$?; "
        "if [ $BUILD_EXIT -ne 0 ]; then "
        "  echo ''; echo '=== ERROR: build.sh failed (exit $BUILD_EXIT). See output above. ==='; exit 1; "
        "fi; "
        # Copy binaries to our toolchain store.
        # MinGW64 gcc produces .exe files; old GCC Makefiles may omit the .exe.
        # We always store as .exe so MSYS2's Makefile (OS=Windows_NT) finds them.
        f"DEST='{agbcc_root_msys}'; "
        "mkdir -p \"$DEST/bin\" \"$DEST/lib\" \"$DEST/include\"; "
        "echo '--- Copying binaries ---'; "
        "FOUND=0; "
        "for bin in agbcc agbcc_arm old_agbcc; do "
        "  SRC=''; "
        "  [ -f \"${bin}.exe\" ] && SRC=\"${bin}.exe\"; "
        "  [ -z \"$SRC\" ] && [ -f \"${bin}\" ] && SRC=\"${bin}\"; "
        "  if [ -n \"$SRC\" ]; then "
        "    cp \"$SRC\" \"$DEST/bin/${bin}.exe\" && echo \"  $SRC  ->  $DEST/bin/${bin}.exe\"; "
        "    FOUND=1; "
        "  else "
        "    echo \"  WARNING: ${bin} not found after build\"; "
        "  fi; "
        "done; "
        # Copy libraries
        "echo '--- Copying libraries ---'; "
        "[ -f libgcc.a ] && cp libgcc.a \"$DEST/lib/\" && echo '  libgcc.a'; "
        "[ -f libc.a ]   && cp libc.a   \"$DEST/lib/\" && echo '  libc.a'; "
        # Copy headers (libc/include/ contents and ginclude/ contents)
        "echo '--- Copying headers ---'; "
        "[ -d libc/include ] && cp -R libc/include/. \"$DEST/include/\" && echo '  libc/include/'; "
        "[ -d ginclude ]     && cp -R ginclude/.     \"$DEST/include/\" && echo '  ginclude/'; "
        # Cleanup
        "cd /; rm -rf \"$AGBCC_SRC\"; "
        "echo '--- Installed toolchain ---'; "
        "ls -la \"$DEST/bin/\" 2>/dev/null; "
        "ls -la \"$DEST/lib/\" 2>/dev/null; "
        "if [ $FOUND -eq 1 ]; then "
        f"  echo ''; echo '=== agbcc built successfully. Click Close, then Re-check in PorySuite-Z. ==='; "
        "else "
        "  echo ''; echo '=== ERROR: No agbcc binaries were produced. Check the output above. ==='; exit 1; "
        "fi"
    )
    _bash_dialog("Build agbcc", script, parent=parent)


def _install_devkitpro():
    """Download the devkitPro Windows installer from GitHub and launch it.

    Opens a PowerShell console that fetches the latest release asset,
    downloads it to %TEMP%, and runs the installer.  The user works through
    the wizard (selecting GBA / devkitARM), then clicks Re-check in PorySuite.
    Falls back to the wiki page on non-Windows or if PowerShell fails.
    """
    if sys.platform != "win32":
        webbrowser.open("https://devkitpro.org/wiki/Getting_Started")
        return

    # PowerShell script: fetch latest release from GitHub API, download exe,
    # launch the wizard, then wait for the user before closing the console.
    ps = (
        "Write-Host 'Fetching devkitPro installer info...' -ForegroundColor Cyan; "
        "try { "
        "  $r = Invoke-RestMethod 'https://api.github.com/repos/devkitPro/installer/releases/latest'; "
        "  $asset = $r.assets | Where-Object { $_.name -like '*.exe' } | Select-Object -First 1; "
        "  $out = Join-Path $env:TEMP $asset.name; "
        "  Write-Host \"Downloading $($asset.name)...\" -ForegroundColor Cyan; "
        "  Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $out; "
        "  Write-Host 'Launching installer...' -ForegroundColor Green; "
        "  Start-Process $out -Wait; "
        "  Write-Host '' ; "
        "  Write-Host 'Installer closed. Switch back to PorySuite and click Re-check.' -ForegroundColor Green "
        "} catch { "
        "  Write-Host \"Error: $_\" -ForegroundColor Red; "
        "  Start-Process 'https://devkitpro.org/wiki/Getting_Started' "
        "}; "
        "Read-Host 'Press Enter to close this window'"
    )
    try:
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps],
            creationflags=(subprocess.CREATE_NEW_CONSOLE
                           | subprocess.CREATE_NEW_PROCESS_GROUP),
            close_fds=True,
        )
    except Exception:
        webbrowser.open("https://devkitpro.org/wiki/Getting_Started")


def _pip_install(package: str, parent=None):
    """Install a pip package into the same Python that is running the app."""
    _pip_dialog(f"Install {package}", package, parent=parent)


def _pkg_installed(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def _devkitarm_works() -> bool:
    """Return True only if arm-none-eabi-gcc exists AND actually runs."""
    gcc = r"C:\devkitPro\devkitARM\bin\arm-none-eabi-gcc.exe"
    if not os.path.isfile(gcc):
        return False
    prev = _suppress_win_error_dialogs()
    try:
        r = subprocess.run([gcc, "--version"], capture_output=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        _restore_win_error_mode(prev)


# ── WSL helpers ────────────────────────────────────────────────

def _wsl_exe() -> str:
    return shutil.which("wsl") or r"C:\Windows\System32\wsl.exe"


def _wsl_available() -> bool:
    exe = _wsl_exe()
    if not os.path.isfile(exe):
        return False
    try:
        r = subprocess.run([exe, "true"], capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def _wsl_has(tool: str) -> bool:
    exe = _wsl_exe()
    if not os.path.isfile(exe):
        return False
    try:
        r = subprocess.run([exe, "which", tool], capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def _wsl_has_lib(package: str) -> bool:
    """Check if an apt package is installed inside WSL using dpkg."""
    exe = _wsl_exe()
    if not os.path.isfile(exe):
        return False
    try:
        r = subprocess.run(
            [exe, "dpkg", "-l", package],
            capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return r.returncode == 0 and b"ii  " + package.encode() in r.stdout
    except Exception:
        return False


def _wsl_apt_dialog(packages: str, parent=None):
    """Open a real WSL terminal window to install apt packages.

    Runs apt-get in an interactive WSL terminal so the user can type their
    sudo password if required.  After the terminal closes the user clicks
    Re-check in the wizard to update the status badge.
    """
    exe = _wsl_exe()
    if not os.path.isfile(exe):
        _open_url("ms-windows-store://pdp/?productid=9PDXGNCFSCZV")
        return
    cmd = (
        f"sudo apt-get update && sudo apt-get install -y {packages}; "
        "echo; echo '--- Done. You can close this window, then click Re-check in PorySuite-Z. ---'; "
        "read -r _"
    )
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", exe, "--", "bash", "-c", cmd],
            creationflags=0,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
#  Dependency definitions
# ──────────────────────────────────────────────────────────────

DEPS = [
    # ── category: App / Editor ─────────────────────────────
    {
        "category": "App / Editor",
        "name": "PyQt6",
        "description": "Qt GUI framework — the UI library that powers PorySuite-Z. Required to start the app.",
        "platform": None,
        "check": lambda: _pkg_installed("PyQt6"),
        "install_label": "pip install",
        "install_fn": lambda: _pip_install("PyQt6~=6.6.1"),
    },
    {
        "category": "App / Editor",
        "name": "platformdirs",
        "description": "Provides cross-platform paths for user data and config directories.",
        "platform": None,
        "check": lambda: _pkg_installed("platformdirs"),
        "install_label": "pip install",
        "install_fn": lambda: _pip_install("platformdirs~=4.1.0"),
    },
    {
        "category": "App / Editor",
        "name": "Unidecode",
        "description": "Transliterates Unicode to ASCII — used for Pokémon names with special characters.",
        "platform": None,
        "check": lambda: _pkg_installed("unidecode"),
        "install_label": "pip install",
        "install_fn": lambda: _pip_install("Unidecode~=1.3.7"),
    },
    {
        "category": "App / Editor",
        "name": "typing_extensions",
        "description": "Backports newer Python typing features for compatibility across Python versions.",
        "platform": None,
        "check": lambda: _pkg_installed("typing_extensions"),
        "install_label": "pip install",
        "install_fn": lambda: _pip_install("typing_extensions~=4.9.0"),
    },
    # ── category: Sound Editor ────────────────────────────
    {
        "category": "App / Editor",
        "name": "numpy",
        "description": "Numerical computing library — used by the Sound Editor for audio synthesis and mixing.",
        "platform": None,
        "check": lambda: _pkg_installed("numpy"),
        "install_label": "pip install",
        "install_fn": lambda: _pip_install("numpy>=2.0"),
    },
    {
        "category": "App / Editor",
        "name": "sounddevice",
        "description": "Audio output library — used by the Sound Editor to play song previews through your speakers.",
        "platform": None,
        "check": lambda: _pkg_installed("sounddevice"),
        "install_label": "pip install",
        "install_fn": lambda: _pip_install("sounddevice>=0.5"),
    },
    {
        "category": "App / Editor",
        "name": "mido",
        "description": "MIDI file reader — used by the MIDI Import wizard to read track info, instruments, and measure counts.",
        "platform": None,
        "check": lambda: _pkg_installed("mido"),
        "install_label": "pip install",
        "install_fn": lambda: _pip_install("mido>=1.3"),
    },
    # ── category: Git ──────────────────────────────────────
    {
        "category": "Git",
        "name": "Git",
        "optional": True,
        "description": (
            "Optional — only needed for Pull from Remote and New Project (cloning from GitHub). "
            "You can edit Pokémon data and build ROMs without it."
        ),
        "platform": None,
        "check": lambda: shutil.which("git") is not None,
        "install_label": "Install Git",
        "install_fn": _install_git,
    },
    # ── category: Build / Make ─────────────────────────────
    {
        "category": "Build / Make",
        "name": "MSYS2",
        "description": (
            "Provides the bash shell and GNU make on Windows. "
            "Required to build GBA ROMs — install from https://www.msys2.org/"
        ),
        "platform": "win32",
        "check": lambda: os.path.isfile(r"C:\msys64\usr\bin\bash.exe"),
        "install_label": "Install MSYS2",
        "install_fn": _install_msys2,
    },
    {
        "category": "Build / Make",
        "name": "make  (via MSYS2)",
        "description": (
            "GNU make inside MSYS2 — drives the pokefirered build system. "
            "Install via MSYS2's pacman package manager."
        ),
        "platform": "win32",
        "check": lambda: os.path.isfile(r"C:\msys64\usr\bin\make.exe"),
        "install_label": "Install via pacman",
        "install_fn": _install_make_via_pacman,
    },
    {
        "category": "Build / Make",
        "name": "gcc  (MinGW64)",
        "description": (
            "MinGW64 GCC compiler inside MSYS2 — needed to compile the GBA host tools "
            "(gbagfx, bin2c, gbafix) for Windows and to build agbcc. "
            "GCC 14+ may show warnings when building host tools — PorySuite "
            "handles this automatically."
        ),
        "platform": "win32",
        "check": lambda: os.path.isfile(r"C:\msys64\mingw64\bin\gcc.exe"),
        "install_label": "Install via pacman",
        "install_fn": _install_mingw_gcc,
    },
    {
        "category": "Build / Make",
        "name": "libpng  (MinGW64)",
        "description": (
            "libpng and zlib for MinGW64 — required to compile gbagfx on Windows. "
            "Installs mingw-w64-x86_64-libpng and mingw-w64-x86_64-zlib via pacman."
        ),
        "platform": "win32",
        "check": lambda: (
            os.path.isfile(r"C:\msys64\mingw64\include\png.h")
            and os.path.isfile(r"C:\msys64\mingw64\lib\libpng.a")
        ),
        "install_label": "Install via pacman",
        "install_fn": _install_gba_build_libs,
    },
    {
        "category": "Build / Make",
        "name": "devkitPro",
        "description": (
            "ARM GBA toolchain (devkitARM). Provides arm-none-eabi-gcc and the GBA "
            "standard libraries. Install the Windows version to C:\\devkitPro — "
            "select GBA Development during setup."
        ),
        "platform": "win32",
        "check": _devkitarm_works,
        "install_label": "Install devkitPro",
        "install_fn": _install_devkitpro,
    },
    {
        "category": "Build / Make",
        "name": "agbcc",
        "description": (
            "Custom GBA C compiler (pret/agbcc). pokefirered's Makefile uses it "
            "as CC1 for all non-modern C files. Detected in the global toolchain "
            "store or inside any project's tools/agbcc/ folder. If missing, click "
            "Build to clone and compile from source with MinGW64."
        ),
        "platform": "win32",
        "check": _agbcc_compiled,
        "install_label": "Build agbcc",
        "install_fn": _install_agbcc,
    },
]


# ──────────────────────────────────────────────────────────────
#  Background dependency checker
# ──────────────────────────────────────────────────────────────

class _CheckWorker(QObject):
    """Runs all dep checks off the main thread, emits results one by one."""
    result = pyqtSignal(int, str)   # (dep_index, "found" | "missing" | "na")
    finished = pyqtSignal()

    def __init__(self, deps: list):
        super().__init__()
        self._deps = deps

    def run(self):
        for i, dep in enumerate(self._deps):
            platform = dep.get("platform")
            if platform is not None and sys.platform != platform:
                self.result.emit(i, "na")
                continue
            try:
                ok = dep["check"]()
            except Exception:
                ok = False
            if isinstance(ok, str):
                # Check returned a custom status string (e.g. "found_project")
                self.result.emit(i, ok)
            else:
                self.result.emit(i, "found" if ok else "missing")
        self.finished.emit()


# ──────────────────────────────────────────────────────────────
#  Single dependency row widget  (compact single-line)
# ──────────────────────────────────────────────────────────────

class _DepRow(QWidget):
    """Compact single-line row: name | description tooltip | status badge | install btn."""

    def __init__(self, dep: dict, parent=None):
        super().__init__(parent)
        self._dep = dep
        self._status = "checking"

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(8)

        name_lbl = QLabel(dep["name"])
        name_font = QFont()
        name_font.setBold(True)
        name_lbl.setFont(name_font)
        name_lbl.setFixedWidth(160)
        name_lbl.setToolTip(dep.get("description", ""))
        row.addWidget(name_lbl)

        if dep.get("optional"):
            opt = QLabel("optional")
            opt.setStyleSheet("color:#888; font-size:9px; border:1px solid #555;"
                              " border-radius:3px; padding:0 4px;")
            opt.setFixedHeight(16)
            row.addWidget(opt)

        row.addStretch(1)

        self._badge = QLabel("Checking…")
        self._badge.setFixedWidth(90)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pad = "border-radius:3px; padding:1px 6px; font-weight:bold; font-size:11px;"
        self._badge.setStyleSheet(pad + "background:#333; color:#aaa;")
        row.addWidget(self._badge)

        self._install_btn = QPushButton(dep.get("install_label", "Install"))
        self._install_btn.setFixedWidth(150)
        self._install_btn.setFixedHeight(24)
        self._install_btn.setAutoDefault(False)
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._on_install)
        row.addWidget(self._install_btn)

    # ── public ───────────────────────────────────────────────

    def set_status(self, status: str):
        """Called from the main thread after a background check completes."""
        self._status = status
        pad = "border-radius:3px; padding:1px 6px; font-weight:bold; font-size:11px;"
        if status == "na":
            self._badge.setText("N/A")
            self._badge.setStyleSheet(pad + "background:#444; color:#999;")
            self._install_btn.setVisible(False)
        elif status == "found":
            self._badge.setText("✓ Found")
            self._badge.setStyleSheet(pad + "background:#2d6e2d; color:#ccffcc;")
            self._install_btn.setVisible(False)
        elif status == "found_project":
            self._badge.setText("✓ Found")
            self._badge.setStyleSheet(pad + "background:#2d6e2d; color:#ccffcc;")
            self._badge.setToolTip("Found in a project folder (not installed globally)")
            self._install_btn.setVisible(False)
        elif status == "checking":
            self._badge.setText("Checking…")
            self._badge.setStyleSheet(pad + "background:#333; color:#aaa;")
            self._install_btn.setVisible(False)
        else:
            missing_label = "Optional" if self._dep.get("optional") else "Missing"
            self._badge.setText(f"✗ {missing_label}")
            color = "#555533" if self._dep.get("optional") else "#7a2020"
            self._badge.setStyleSheet(pad + f"background:{color}; color:#ffeecc;")
            self._install_btn.setVisible(True)
            self._install_btn.setEnabled(True)
            self._install_btn.setText(self._dep.get("install_label", "Install"))

    def is_satisfied(self) -> bool:
        return self._status in ("found", "found_project", "na") or self._dep.get("optional", False)

    # ── private ──────────────────────────────────────────────

    def _on_install(self):
        fn = self._dep.get("install_fn")
        if not fn:
            return
        self._install_btn.setEnabled(False)
        self._install_btn.setText("Working…")

        # Drop WindowStaysOnTopHint while the install dialog is open so it
        # can appear in front of this Setup window instead of behind it.
        top_win = self.window()
        had_hint = bool(top_win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if had_hint:
            top_win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            top_win.show()

        try:
            import inspect
            try:
                inspect.signature(fn).bind(top_win)
                fn(top_win)
            except TypeError:
                fn()
        finally:
            # Restore WindowStaysOnTopHint now that the install dialog is gone
            if had_hint:
                top_win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                top_win.show()

            # Re-check this single dep in a background thread after install
            self.set_status("checking")
            dep = self._dep
            row = self

            class _SingleCheck(QThread):
                done = pyqtSignal(str)
                def run(self_t):
                    platform = dep.get("platform")
                    if platform is not None and sys.platform != platform:
                        self_t.done.emit("na")
                        return
                    try:
                        self_t.done.emit("found" if dep["check"]() else "missing")
                    except Exception:
                        self_t.done.emit("missing")

            t = _SingleCheck(self)
            t.done.connect(lambda s, r=row: r.set_status(s))
            t.done.connect(lambda s: self.parent()._update_finish_btn()
                           if self.parent() and hasattr(self.parent(), '_update_finish_btn') else None)
            t.start()
            self._check_thread = t  # keep reference


# ──────────────────────────────────────────────────────────────
#  Main dialog
# ──────────────────────────────────────────────────────────────

class ProgramSetup(QDialog):
    """Checks that all required build tools are installed and offers install buttons."""

    _recheck_signal = pyqtSignal()  # emit from background thread → runs _run_checks on main thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setting Up - PorySuite-Z")
        self.resize(700, 520)
        self.setMinimumSize(540, 400)

        self._rows: list[_DepRow] = []
        self._finish_btn: QPushButton | None = None

        self._recheck_signal.connect(self._run_checks)

        root = QVBoxLayout(self)
        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._stack.addWidget(self._make_welcome_page())
        self._stack.addWidget(self._make_checklist_page())
        self._stack.setCurrentIndex(0)

    # ── Page 1 : welcome ─────────────────────────────────────

    def _make_welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()

        title = QLabel("Environment Check")
        title_font = QFont()
        title_font.setPointSize(24)
        title.setFont(title_font)
        layout.addWidget(title)

        body = QLabel(
            "Before you can build GBA ROMs, PorySuite needs to verify that the "
            "required development tools are installed on your system.\n\n"
            "The next page shows the status of each tool. If anything is missing, "
            "you can install it directly from this screen.\n\n"
            "You can still edit Pokémon data without the build tools — only ROM "
            "compilation requires WSL (Ubuntu) and devkitPro.\n\n"
            "Press Continue to run the check."
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        quit_btn = QPushButton("Quit")
        quit_btn.setAutoDefault(False)
        quit_btn.clicked.connect(self.reject)
        btn_row.addWidget(quit_btn)

        cont_btn = QPushButton("Continue")
        cont_btn.setDefault(True)
        cont_btn.clicked.connect(self._go_to_checklist)
        btn_row.addWidget(cont_btn)

        layout.addLayout(btn_row)
        return page

    # ── Page 2 : checklist ───────────────────────────────────

    _CATEGORY_NOTES = {
        "App / Editor": "Required to run PorySuite-Z. Hover over a name to see what it does.",
        "Git": "Optional. Needed for Pull from Remote and New Project (GitHub clone). "
               "You can edit data and build ROMs without Git.",
        "Build / Make": "Required to compile your GBA ROM. Install MSYS2 first, then "
                        "devkitPro (ARM toolchain) and agbcc (GBA C compiler). "
                        "All tools run natively on Windows via MSYS2's MinGW64 environment.",
    }

    def _make_checklist_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(6)

        heading = QLabel("Dependency Status")
        heading_font = QFont()
        heading_font.setPointSize(13)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        hint = QLabel("Hover a name for details. Install missing tools, then click Re-check.")
        hint.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        layout.addWidget(hint)

        # Scrollable dep list grouped by category
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(2)
        inner_layout.setContentsMargins(0, 0, 6, 0)

        # Group deps by category, preserving order
        from collections import OrderedDict
        groups: OrderedDict[str, list] = OrderedDict()
        for dep in DEPS:
            cat = dep.get("category", "Other")
            groups.setdefault(cat, []).append(dep)

        for cat, deps in groups.items():
            # Category header
            cat_lbl = QLabel(cat)
            cat_font = QFont()
            cat_font.setBold(True)
            cat_font.setPointSize(10)
            cat_lbl.setFont(cat_font)
            cat_lbl.setStyleSheet("color: #cccccc; margin-top: 8px;")
            inner_layout.addWidget(cat_lbl)

            # Category note
            note_text = self._CATEGORY_NOTES.get(cat, "")
            if note_text:
                note = QLabel(note_text)
                note.setWordWrap(True)
                note.setStyleSheet("color: #888888; font-size: 9px; margin-bottom: 2px;")
                inner_layout.addWidget(note)

            # Separator line
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("color: #444;")
            inner_layout.addWidget(line)

            for dep in deps:
                row = _DepRow(dep)
                self._rows.append(row)
                inner_layout.addWidget(row)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        # Footer buttons
        btn_row = QHBoxLayout()

        recheck_btn = QPushButton("Re-check")
        recheck_btn.setAutoDefault(False)
        recheck_btn.clicked.connect(self._run_checks)
        btn_row.addWidget(recheck_btn)

        btn_row.addStretch()

        self._finish_btn = QPushButton("Finish")
        self._finish_btn.setDefault(True)
        self._finish_btn.clicked.connect(self._on_finish)
        btn_row.addWidget(self._finish_btn)

        layout.addLayout(btn_row)
        return page

    # ── Slot implementations ─────────────────────────────────

    def _go_to_checklist(self):
        self._stack.setCurrentIndex(1)
        self._run_checks()

    def _run_checks(self):
        """Start all dependency checks on a background thread."""
        # Reset everything to "Checking…" immediately so the UI is responsive
        for row in self._rows:
            row.set_status("checking")
        if self._finish_btn:
            self._finish_btn.setEnabled(False)

        worker = _CheckWorker(DEPS)
        thread = QThread(self)
        worker.moveToThread(thread)

        def _on_result(idx: int, status: str):
            if 0 <= idx < len(self._rows):
                self._rows[idx].set_status(status)

        def _on_done():
            self._update_finish_btn()
            thread.quit()

        worker.result.connect(_on_result)
        worker.finished.connect(_on_done)
        thread.started.connect(worker.run)
        thread.start()
        # Keep references so they are not garbage collected
        self._check_thread = thread
        self._check_worker = worker

    def _update_finish_btn(self):
        if self._finish_btn is None:
            return
        all_ok = all(r.is_satisfied() for r in self._rows)
        self._finish_btn.setEnabled(True)
        if all_ok:
            self._finish_btn.setText("Finish")
            self._finish_btn.setStyleSheet("")
            self._finish_btn.setToolTip("All dependencies are satisfied.")
        else:
            self._finish_btn.setText("Finish Anyway")
            self._finish_btn.setStyleSheet(
                "color: #ffaa44; font-weight: bold;"
            )
            self._finish_btn.setToolTip(
                "Some tools are missing. You can still use PorySuite for data "
                "editing, but ROM builds will fail until they are installed."
            )

    def _on_finish(self):
        path = get_setup_complete_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("complete")
        self.accept()

    # ── Legacy shim (kept for any stale signal connections) ──

    def next_page(self):
        self._go_to_checklist()
