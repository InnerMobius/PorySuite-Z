"""
Porymap Installer — handles downloading, patching, and building Porymap.

Pipeline:
1. Clone Porymap source from GitHub (or pull if already cloned)
2. Apply patch files from porymap_patches/
3. Download Qt SDK + MinGW toolchain via aqtinstall (if not present)
4. Compile with qmake + mingw32-make
5. Deploy binary + Qt DLLs to porymap/ runtime folder

Uses MinGW (not MSVC) so no Visual Studio Build Tools are required.
The matching MinGW 8.1 toolchain is downloaded alongside the Qt SDK.
"""

import glob
import os
import shutil
import subprocess
import sys

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton,
    QTextEdit, QMessageBox,
)

from porymap_bridge.porymap_launcher import porymap_exe_path, porymap_source_path, _exe_sha256


PORYMAP_REPO_URL = "https://github.com/huderlem/porymap.git"

# Qt SDK + MinGW toolchain versions (must match each other)
QT_VERSION = "6.8.3"
QT_ARCH = "win64_mingw"
MINGW_TOOL = "tools_mingw1310"
MINGW_TOOL_VARIANT = "qt.tools.win64_mingw1310"


def _porysuite_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _patches_dir() -> str:
    return os.path.join(_porysuite_root(), "porymap_patches")


def _qt_sdk_dir() -> str:
    return os.path.join(_porysuite_root(), "qt_sdk")


def _runtime_dir() -> str:
    """Where the final built binary goes."""
    return os.path.join(_porysuite_root(), "porymap")


# ═════════════════════════════════════════════════════════════════════════════
# Worker thread — runs the install pipeline
# ═════════════════════════════════════════════════════════════════════════════

class InstallWorker(QThread):
    """Runs the full clone → patch → build → deploy pipeline in a background thread."""

    progress = pyqtSignal(str)       # Status message
    step_changed = pyqtSignal(int)   # Step number (0-based) for progress bar
    finished_ok = pyqtSignal()
    finished_err = pyqtSignal(str)

    STEPS = [
        "Cloning Porymap source...",
        "Applying patches...",
        "Checking Qt SDK...",
        "Compiling Porymap...",
        "Deploying binary...",
    ]

    def run(self):
        try:
            self._do_clone()
            self._do_patch()
            self._do_check_qt()
            self._do_compile()
            self._do_deploy()
            self.finished_ok.emit()
        except Exception as e:
            self.finished_err.emit(str(e))

    def _emit_step(self, step: int, msg: str = ""):
        self.step_changed.emit(step)
        self.progress.emit(msg or self.STEPS[step])

    def _run_cmd(self, cmd: list, cwd: str = None, label: str = "",
                 env: dict = None, timeout: int = 600) -> str:
        """Run a command, capture output, raise on failure."""
        self.progress.emit(f"  Running: {' '.join(cmd[:4])}...")
        # CREATE_NO_WINDOW prevents flashing black console windows
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"{label or cmd[0]} failed (exit {result.returncode}):\n{err[:500]}")
        return result.stdout

    def _run_cmd_streaming(self, cmd: list, cwd: str = None, label: str = "",
                           env: dict = None, timeout: int = 600):
        """Run a command, streaming .cpp file names as progress updates."""
        import time
        self.progress.emit(f"  Running: {' '.join(cmd[:4])}...")
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        file_count = 0
        last_update = time.time()
        output_lines = []
        for line in proc.stdout:
            output_lines.append(line)
            # Extract the .cpp filename being compiled
            if ".cpp" in line or ".cc" in line:
                file_count += 1
                # Throttle updates to avoid flooding
                now = time.time()
                if now - last_update >= 0.5:
                    # Pull just the filename from the compile command
                    parts = line.strip().split()
                    fname = ""
                    for p in parts:
                        if p.endswith(".cpp") or p.endswith(".cc"):
                            fname = os.path.basename(p)
                            break
                    if fname:
                        self.progress.emit(f"  Compiling ({file_count} files): {fname}")
                    last_update = now
            elif "Linking" in line or "linking" in line:
                self.progress.emit("  Linking porymap.exe...")
        proc.wait(timeout=timeout)
        if proc.returncode != 0:
            err = "".join(output_lines[-20:])
            raise RuntimeError(
                f"{label or cmd[0]} failed (exit {proc.returncode}):\n{err[:500]}")

    # ── Step 1: Clone / pull ────────────────────────────────────────────────

    def _do_clone(self):
        self._emit_step(0)
        src = porymap_source_path()

        if os.path.isdir(os.path.join(src, ".git")):
            # Already cloned — pull latest
            self.progress.emit("  Source exists, pulling latest...")
            self._run_cmd(["git", "fetch", "--all"], cwd=src, label="git fetch")
            self._run_cmd(["git", "reset", "--hard", "origin/master"], cwd=src,
                          label="git reset")
        else:
            # Fresh clone
            self.progress.emit("  Cloning from GitHub...")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            self._run_cmd(
                ["git", "clone", PORYMAP_REPO_URL, src],
                label="git clone",
            )

    # ── Step 2: Apply patches ───────────────────────────────────────────────

    def _do_patch(self):
        self._emit_step(1)
        src = porymap_source_path()

        # Use our Python patcher script (resilient to upstream line changes)
        patcher = os.path.join(_patches_dir(), "apply_patches.py")
        if os.path.isfile(patcher):
            self.progress.emit("  Applying PorySuite-Z patches...")
            self._run_cmd(
                [sys.executable, patcher, src],
                label="apply_patches.py",
            )
        else:
            # Fallback: try traditional .patch files
            patches = sorted(glob.glob(os.path.join(_patches_dir(), "*.patch")))
            if not patches:
                self.progress.emit("  No patch files found (stock build)")
                return
            for patch_file in patches:
                name = os.path.basename(patch_file)
                self.progress.emit(f"  Applying {name}...")
                self._run_cmd(
                    ["git", "apply", "--check", patch_file],
                    cwd=src, label=f"patch check {name}",
                )
                self._run_cmd(
                    ["git", "apply", patch_file],
                    cwd=src, label=f"patch apply {name}",
                )

    # ── Step 3: Check / install Qt SDK + MinGW ──────────────────────────────

    def _do_check_qt(self):
        self._emit_step(2)
        qt_dir = _qt_sdk_dir()
        qmake = self._find_qmake(qt_dir)

        if qmake:
            self.progress.emit(f"  Qt SDK found: {qmake}")
            self._qmake_path = qmake
            self._find_mingw_gcc(qt_dir)
            return

        # Need to download Qt + MinGW via aqtinstall
        self.progress.emit("  Installing Qt SDK via aqtinstall (this may take a few minutes)...")

        # Ensure aqtinstall is available
        self._run_cmd(
            [sys.executable, "-m", "pip", "install", "aqtinstall"],
            label="pip install aqtinstall",
        )

        os.makedirs(qt_dir, exist_ok=True)

        # Install Qt for MinGW (no MSVC/Visual Studio needed)
        self.progress.emit("  Downloading Qt SDK (MinGW)...")
        self._run_cmd(
            [sys.executable, "-m", "aqt", "install-qt",
             "--outputdir", qt_dir,
             "windows", "desktop", QT_VERSION, QT_ARCH,
             "-m", "qtcharts"],
            label="aqtinstall (Qt)",
        )

        # Install matching MinGW toolchain (gcc, g++, mingw32-make)
        self.progress.emit("  Downloading MinGW toolchain...")
        self._run_cmd(
            [sys.executable, "-m", "aqt", "install-tool",
             "--outputdir", qt_dir,
             "windows", "desktop", MINGW_TOOL, MINGW_TOOL_VARIANT],
            label="aqtinstall (MinGW)",
        )

        qmake = self._find_qmake(qt_dir)
        if not qmake:
            raise RuntimeError(
                "Qt SDK installed but qmake not found. "
                "Check qt_sdk/ directory structure.")
        self._qmake_path = qmake
        self._find_mingw_gcc(qt_dir)

    def _find_qmake(self, qt_dir: str) -> str:
        """Search for qmake.exe under the Qt SDK directory (skip Tools/)."""
        for root, dirs, files in os.walk(qt_dir):
            # Skip the Tools directory — qmake lives under the Qt version dir
            if "Tools" in root.split(os.sep):
                continue
            for f in files:
                if f.lower() == "qmake.exe":
                    return os.path.join(root, f)
        return ""

    def _find_mingw_gcc(self, qt_dir: str):
        """Find the MinGW toolchain's bin directory (contains gcc, g++, mingw32-make)."""
        tools_dir = os.path.join(qt_dir, "Tools")
        if not os.path.isdir(tools_dir):
            # Try system MSYS2 as fallback
            self._mingw_bin = ""
            return

        # Look for mingw32-make.exe under Tools/
        for root, dirs, files in os.walk(tools_dir):
            for f in files:
                if f.lower() == "mingw32-make.exe":
                    self._mingw_bin = root
                    self.progress.emit(f"  MinGW toolchain: {root}")
                    return

        self._mingw_bin = ""

    # ── Step 4: Compile ─────────────────────────────────────────────────────

    def _build_env(self) -> dict:
        """Build a clean environment with only our MinGW on PATH.

        Removes any system MinGW/MSYS2 directories from PATH to prevent
        DLL and linker version conflicts during compilation.
        """
        env = os.environ.copy()
        mingw_bin = getattr(self, '_mingw_bin', '')
        qt_bin = os.path.dirname(self._qmake_path)

        # Filter out other MinGW/MSYS2 installations from PATH
        path_dirs = env.get("PATH", "").split(os.pathsep)
        clean_dirs = [d for d in path_dirs
                      if not any(x in d.lower() for x in
                                 ["mingw", "msys"])]

        # Put our toolchain first
        new_path = []
        if mingw_bin:
            new_path.append(mingw_bin)
        new_path.append(qt_bin)
        new_path.extend(clean_dirs)
        env["PATH"] = os.pathsep.join(new_path)
        return env

    def _do_compile(self):
        self._emit_step(3)
        src = porymap_source_path()

        # Build environment with only our MinGW toolchain
        env = self._build_env()

        # Create build directory
        build_dir = os.path.join(src, "build")
        os.makedirs(build_dir, exist_ok=True)

        # Run qmake with MinGW spec
        pro_file = os.path.join(src, "porymap.pro")
        self.progress.emit("  Running qmake...")
        qmake_cmd = [self._qmake_path, pro_file, "CONFIG+=release"]

        # Tell qmake to use MinGW spec (not MSVC)
        qt_bin = os.path.dirname(self._qmake_path)
        mingw_spec = os.path.join(os.path.dirname(qt_bin), "mkspecs",
                                  "win32-g++")
        if os.path.isdir(mingw_spec):
            qmake_cmd.extend(["-spec", "win32-g++"])

        self._run_cmd(qmake_cmd, cwd=build_dir, label="qmake", env=env)

        # Find mingw32-make
        make_cmd = self._find_make(env)
        self.progress.emit(f"  Compiling with {os.path.basename(make_cmd)}...")
        self._run_cmd_streaming(
            [make_cmd, "-j4"], cwd=build_dir, label="make", env=env,
            timeout=600,
        )

    def _find_make(self, env: dict = None) -> str:
        """Find mingw32-make (preferred) or other make tool."""
        search_env = env or os.environ
        path = search_env.get("PATH", "")

        # Prefer mingw32-make (matches the MinGW Qt SDK)
        for cmd in ["mingw32-make", "make"]:
            found = shutil.which(cmd, path=path)
            if found:
                return found

        # Check the bundled MinGW toolchain directly
        mingw_bin = getattr(self, '_mingw_bin', '')
        if mingw_bin:
            candidate = os.path.join(mingw_bin, "mingw32-make.exe")
            if os.path.isfile(candidate):
                return candidate

        # Check Qt SDK bin
        qt_bin = os.path.dirname(self._qmake_path)
        for cmd in ["mingw32-make.exe"]:
            candidate = os.path.join(qt_bin, cmd)
            if os.path.isfile(candidate):
                return candidate

        raise RuntimeError(
            "No make tool found (mingw32-make). "
            "The Qt SDK installation may be incomplete — "
            "try deleting qt_sdk/ and reinstalling Porymap.")

    # ── Step 5: Deploy ──────────────────────────────────────────────────────

    def _do_deploy(self):
        self._emit_step(4)
        src = porymap_source_path()
        runtime = _runtime_dir()

        # Find the compiled binary
        build_dir = os.path.join(src, "build")
        exe_candidates = glob.glob(os.path.join(build_dir, "**", "porymap.exe"),
                                   recursive=True)
        if not exe_candidates:
            # Try release subfolder
            exe_candidates = glob.glob(os.path.join(build_dir, "release", "porymap.exe"))
        if not exe_candidates:
            raise RuntimeError(
                f"Compiled porymap.exe not found in {build_dir}. Build may have failed.")

        built_exe = exe_candidates[0]
        self.progress.emit(f"  Found binary: {built_exe}")

        # Create runtime directory (preserve existing if any)
        os.makedirs(runtime, exist_ok=True)

        # Copy exe
        shutil.copy2(built_exe, os.path.join(runtime, "porymap.exe"))

        # Drop a marker file so the launcher can tell this binary was built
        # with our patches (CLI map arg, bridge file writer, command reader).
        # Missing marker ⇒ treat as stock Porymap and degrade gracefully.
        # Also stores the git commit hash so we can detect upstream updates.
        try:
            commit_hash = ""
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=src, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    commit_hash = result.stdout.strip()
            except Exception:
                pass
            import datetime
            deployed_exe = os.path.join(runtime, "porymap.exe")
            exe_hash = _exe_sha256(deployed_exe)
            with open(os.path.join(runtime, ".psinstalled"),
                      "w", encoding="utf-8") as mf:
                mf.write("PORYSUITE-Z PATCHED PORYMAP\n")
                mf.write(f"built: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
                mf.write(f"commit: {commit_hash}\n")
                mf.write(f"exe_hash: {exe_hash}\n")
        except OSError:
            pass

        # Copy MinGW runtime DLLs that the exe needs
        mingw_bin = getattr(self, '_mingw_bin', '')
        if mingw_bin:
            for dll in ["libgcc_s_seh-1.dll", "libstdc++-6.dll",
                        "libwinpthread-1.dll"]:
                dll_path = os.path.join(mingw_bin, dll)
                if os.path.isfile(dll_path):
                    shutil.copy2(dll_path, os.path.join(runtime, dll))

        # Deploy Qt DLLs and plugins
        qt_bin = os.path.dirname(self._qmake_path)
        qt_base = os.path.dirname(qt_bin)  # e.g. qt_sdk/6.8.3/mingw_64
        deploy_env = self._build_env()

        # Try windeployqt first
        windeployqt = os.path.join(qt_bin, "windeployqt.exe")
        # Qt6 uses windeployqt6.exe
        if not os.path.isfile(windeployqt):
            windeployqt = os.path.join(qt_bin, "windeployqt6.exe")
        deployed_ok = False
        if os.path.isfile(windeployqt):
            self.progress.emit("  Deploying Qt DLLs via windeployqt...")
            try:
                self._run_cmd(
                    [windeployqt, "--release", "--no-translations",
                     os.path.join(runtime, "porymap.exe")],
                    label="windeployqt", env=deploy_env,
                )
                deployed_ok = True
            except RuntimeError:
                self.progress.emit("  windeployqt failed — deploying manually...")

        # Manual fallback: copy Qt DLLs and plugins directly
        if not deployed_ok:
            self.progress.emit("  Copying Qt DLLs manually...")
            # Copy all Qt6*.dll files from bin/ (covers Core, Gui, Widgets, etc.)
            for f in os.listdir(qt_bin):
                if f.lower().startswith("qt6") and f.lower().endswith(".dll"):
                    shutil.copy2(os.path.join(qt_bin, f),
                                 os.path.join(runtime, f))

            # Platform plugin (required for any Qt app to run) + image formats
            plugins_dir = os.path.join(qt_base, "plugins")
            for subdir in ["platforms", "imageformats", "styles",
                           "tls", "networkinformation"]:
                src_dir = os.path.join(plugins_dir, subdir)
                dst_dir = os.path.join(runtime, subdir)
                if os.path.isdir(src_dir):
                    os.makedirs(dst_dir, exist_ok=True)
                    for f in os.listdir(src_dir):
                        if f.endswith(".dll"):
                            shutil.copy2(os.path.join(src_dir, f),
                                         os.path.join(dst_dir, f))

        self.progress.emit("  Done!")


# ═════════════════════════════════════════════════════════════════════════════
# Install dialog — shows progress to the user
# ═════════════════════════════════════════════════════════════════════════════

class InstallDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install Porymap")
        self.setMinimumWidth(500)
        self.setMinimumHeight(350)

        layout = QVBoxLayout(self)

        self._status_label = QLabel("Preparing...")
        self._status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, len(InstallWorker.STEPS))
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(__import__("PyQt6").QtGui.QFont("Courier New", 9))
        layout.addWidget(self._log)

        self._close_btn = QPushButton("Close")
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.accept)
        layout.addWidget(self._close_btn)

        self._worker = InstallWorker()
        self._worker.progress.connect(self._on_progress)
        self._worker.step_changed.connect(self._on_step)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.finished_err.connect(self._on_error)

    def start(self):
        self._worker.start()

    def _on_progress(self, msg: str):
        self._log.append(msg)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum())

    def _on_step(self, step: int):
        self._progress.setValue(step + 1)
        if step < len(InstallWorker.STEPS):
            self._status_label.setText(InstallWorker.STEPS[step])

    def _on_done(self):
        self._status_label.setText("Porymap installed successfully!")
        self._progress.setValue(self._progress.maximum())
        self._close_btn.setEnabled(True)

    def _on_error(self, err: str):
        self._status_label.setText("Installation failed")
        self._log.append(f"\nERROR: {err}")
        self._close_btn.setEnabled(True)

    def closeEvent(self, event):
        if self._worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


# ═════════════════════════════════════════════════════════════════════════════
# Entry point — called from unified_mainwindow.py
# ═════════════════════════════════════════════════════════════════════════════

def run_install(parent=None):
    """Show the install dialog and run the pipeline."""
    dialog = InstallDialog(parent)
    dialog.start()
    dialog.exec()
