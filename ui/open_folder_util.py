"""Shared utility: open a file's containing folder in the OS file manager."""

import os
import subprocess
import sys


def open_in_folder(file_path: str) -> bool:
    """Open the OS file manager with *file_path* selected.
    Returns True if the file exists and the command was launched."""
    if not file_path or not os.path.isfile(file_path):
        return False
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(file_path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", file_path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(file_path)])
        return True
    except Exception:
        return False


def open_folder(folder_path: str) -> bool:
    """Open a folder in the OS file manager.
    Returns True if the folder exists and the command was launched."""
    if not folder_path or not os.path.isdir(folder_path):
        return False
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", os.path.normpath(folder_path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder_path])
        else:
            subprocess.Popen(["xdg-open", folder_path])
        return True
    except Exception:
        return False
