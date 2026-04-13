import os
import sys
import io
import shutil
import subprocess
import threading

# Suppress console windows on Windows for all subprocesses spawned here
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

from PyQt6.QtCore import pyqtSignal


class LocalUtil:
    def __init__(self, project_info: dict):
        self.project_info = project_info
        self.project_dir = project_info["dir"]
        self.project_dir_name = project_info["project_name"]
        self.source_prefix = project_info.get("source_prefix")

    def repo_root(self):
        """Return the project's repository root directory.

        The base directory is determined from ``source_prefix`` if provided. If
        no prefix is set, ``source`` is used when that subdirectory exists.
        After calculating this base path, the method checks whether it (or any of
        its parents) contains a ``project.json`` or ``config.json`` file.
        The search walks upwards until the filesystem root, returning the first
        directory with either file. If no such directory is found, the original
        base path is returned.
        """

        if self.source_prefix is not None:
            prefix = self.source_prefix.rstrip("/\\")
            if prefix == "":
                base = self.project_dir
            else:
                base = os.path.join(self.project_dir, prefix)
        else:
            base = (
                os.path.join(self.project_dir, "source")
                if os.path.isdir(os.path.join(self.project_dir, "source"))
                else self.project_dir
            )

        root = base
        while True:
            if os.path.isfile(os.path.join(root, "project.json")) or os.path.isfile(
                os.path.join(root, "config.json")
            ):
                if os.path.isdir(os.path.join(root, "src")) and os.path.isdir(
                    os.path.join(root, "include")
                ):
                    return root
                print(f"Skipping invalid repo root candidate {root}")
            parent = os.path.dirname(root)
            if parent == root:
                break
            root = parent

        print(f"repo_root fallback to {os.path.abspath(base)}")
        return base

    def _convert_path(self, path: str | None) -> str | None:
        if path is None:
            return None
        repl = self.project_dir
        if self.project_dir_name in path:
            path = path.replace(f"/root/projects/{self.project_dir_name}", repl)
            path = path.replace(f"./projects/{self.project_dir_name}", repl)
            path = path.replace(f"../projects/{self.project_dir_name}", repl)
        if path.startswith("/root/agbcc"):
            agbcc_dir = os.environ.get("AGBCC_DIR", os.path.join(os.getcwd(), "agbcc"))
            path = path.replace("/root/agbcc", agbcc_dir)
        return path

    def run_command(self, args, wdir=None, logger: pyqtSignal = None, stdin_open=False):
        cmd = [self._convert_path(a) if isinstance(a, str) else a for a in args]
        working_dir = self._convert_path(wdir) or self.repo_root()
        try:
            if stdin_open:
                proc = subprocess.Popen(
                    cmd, cwd=working_dir, stdin=subprocess.PIPE,
                    creationflags=_NO_WINDOW,
                )
                return proc
            result = subprocess.run(
                cmd, cwd=working_dir, capture_output=True, text=True,
                creationflags=_NO_WINDOW,
            )
            if logger is not None:
                for line in result.stdout.splitlines():
                    logger.emit(line.strip())
                for line in result.stderr.splitlines():
                    logger.emit(line.strip())
            return result
        except Exception as e:
            print(e)
            if logger is not None:
                logger.emit(str(e))
            return None

    def get_nproc(self) -> str | None:
        try:
            return str(os.cpu_count())
        except Exception:
            return None


    def copy_file(self, source: str, dest: str):
        src_path = os.path.join(self.repo_root(), os.path.normpath(source))
        dest_path = dest if os.path.isabs(dest) else os.path.join(self.project_dir, os.path.normpath(dest))
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"Source file not found for copy: {src_path}")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copyfile(src_path, dest_path)

    def write_file(self, fileobj: io.StringIO, dest: str):
        dest_path = dest if os.path.isabs(dest) else os.path.join(self.repo_root(), dest.lstrip("/"))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        fileobj.seek(0)
        with open(dest_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(fileobj.read())

    def makedirs(self, path):
        os.makedirs(os.path.join(self.repo_root(), path), exist_ok=True)

    def copyfile(self, source, dest):
        shutil.copyfile(os.path.join(self.repo_root(), source), os.path.join(self.repo_root(), dest))

    def removefile(self, path):
        os.remove(os.path.join(self.repo_root(), path))

    def file_exists(self, path):
        return os.path.exists(os.path.join(self.repo_root(), path))

    def getmtime(self, path):
        full = os.path.join(self.repo_root(), path)
        if not os.path.exists(full):
            return None
        return os.path.getmtime(full)

    def export_rom(self, logger: pyqtSignal = None):
        threading.Thread(target=self.try_export_rom, args=(logger,)).start()

    def try_export_rom(self, logger: pyqtSignal):
        logger.emit("5")
        version = self.project_info["version"]
        nproc = self.get_nproc()
        logger.emit("10")
        rom_name = f'{self.project_dir_name}_v{version["major"]}_{version["minor"]}_{version["patch"]}.gba'
        make_args = ["make", f"ROM_NAME={rom_name}", f"MODERN_ROM_NAME={rom_name}"]
        if nproc is not None:
            make_args.insert(1, f"-j{nproc}")
        self.run_command(
            wdir=self.repo_root(),
            args=make_args,
            logger=logger
        )
        logger.emit("cd build")
        logger.emit("90")
        source_rom_path = rom_name
        build_rom_path = os.path.join("build", rom_name)
        self.copy_file(source_rom_path, build_rom_path)
        os.remove(os.path.join(self.repo_root(), source_rom_path))
        logger.emit(f"Exported ROM: {os.path.join(self.project_dir, build_rom_path)}")

    def open_terminal(self):
        thread = threading.Thread(target=self.try_open_terminal)
        thread.start()

    def try_open_terminal(self):
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", "Terminal", self.project_dir])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/K", "cd", "/d", self.project_dir])
        else:
            terminal = os.environ.get("TERMINAL", "x-terminal-emulator")
            subprocess.Popen([terminal], cwd=self.project_dir)
