import os
import sys
import subprocess

from PyQt6.QtWidgets import QMessageBox


def reveal_directory(directory, is_file=False):
    """
    Opens the file explorer on the given file path and selects the file.
    Supports Windows, macOS, and Linux (with xdg-open).
    """
    if not os.path.exists(directory):
        print(f"Warning: The directory {directory} does not exist.")
        return

    try:
        if sys.platform == 'win32':  # Windows
            if is_file:
                # Using explorer and /select flag to open the folder and select the file
                subprocess.Popen(fr'explorer /select,"{os.path.normpath(directory)}"')
            else:
                # Using explorer to open the folder
                subprocess.Popen(['explorer', os.path.normpath(directory)])
        elif sys.platform == 'darwin':  # macOS
            if is_file:
                # Using open and -R to reveal the file in Finder
                subprocess.Popen(['open', '-R', directory])
            else:
                # Using open to open the folder in Finder
                subprocess.Popen(['open', directory])
        else:
            # Linux or other Unix-like systems can be trickier because of the
            # variety of file managers, but xdg-open is a good guess.
            if 'XDG_CURRENT_DESKTOP' in os.environ:
                try:
                    file_manager = {
                        'GNOME': 'nautilus',
                        'Unity': 'nautilus',
                        'XFCE': 'thunar',
                        'KDE': 'dolphin',
                    }[os.environ['XDG_CURRENT_DESKTOP']]

                    # Attempt to use the file manager directly to open the folder
                    subprocess.Popen([file_manager, directory])
                except KeyError:
                    # If the desktop environment is unknown, fall back to xdg-open
                    subprocess.Popen(['xdg-open', directory])
                except subprocess.CalledProcessError:
                    # If the guessed file manager fails, fall back to xdg-open
                    subprocess.Popen(['xdg-open', directory])
            else:
                # When $XDG_CURRENT_DESKTOP is not set, use xdg-open as a last resort
                subprocess.Popen(['xdg-open', directory])
    except Exception as e:
        print(f"Warning: Failed to open {directory}: {e}")


def open_plugins_folder():
    """Open the porysuite plugins folder in the host OS file browser."""
    plugins_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
    reveal_directory(plugins_dir)


def condense_path(full_path: str | None) -> str:
    """Return a short version of ``full_path`` relative to the user home.

    ``condense_path`` previously assumed a non-empty string which caused a
    ``ValueError`` when an empty path was provided.  Some callers may now pass
    ``None`` or ``""`` so we defensively handle those cases and simply return an
    empty string.
    """

    if not full_path:
        return ""

    home_dir = os.path.expanduser("~")
    try:
        condensed_path = os.path.relpath(full_path, home_dir)
    except ValueError:
        return full_path
    if not condensed_path.startswith(".."):
        condensed_path = "~" + os.path.sep + condensed_path
    return condensed_path


def create_unsaved_changes_dialog(parent, message: str = None) -> int:
    """
    Creates a dialog to ask the user if they want to save changes.

    Args:
        parent: The parent widget.
        message (str): The message to display in the dialog.

    Returns:
        int: The ``QMessageBox`` button that was clicked. (``QMessageBox.StandardButton.Save``,
            ``QMessageBox.StandardButton.Discard``, or ``QMessageBox.StandardButton.Cancel``)
    """
    if message is None:
        message = "Your project has unsaved changes. Would you like to save?"
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Unsaved Changes")
    dialog.setText(message)
    dialog.setStandardButtons(
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel
    )
    dialog.setDefaultButton(QMessageBox.StandardButton.Save)
    return dialog.exec()
