import os
import sys
import json
import datetime

# Add subfolders to sys.path so modules can be imported by name
# from their new locations without needing shim files in root.
_root = os.path.dirname(os.path.abspath(__file__))
for _subdir in ("core", "ui", os.path.join("ui", "dialogs")):
    _p = os.path.join(_root, _subdir)
    if _p not in sys.path:
        sys.path.insert(0, _p)


# Initialize logging as early as possible so import-time errors are captured
try:
    import crashlog as _early_crashlog
    _keep, _cap = _early_crashlog.read_purge_settings()
    _early_crashlog.purge_old_logs(_keep, _cap)
    if not _early_crashlog.session_json_path():
        _early_crashlog.init_logging()
        _early_crashlog.install_std_redirects()
except Exception:
    pass

from PyQt6.QtCore import QEventLoop, Qt
from PyQt6.QtGui import QFontDatabase, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
)

import res.resources_rc as resources_rc
import crashlog
from app_info import APP_NAME, AUTHOR, get_data_dir
from loadingproject import LoadingProject
from mainwindow import MainWindow
from projectselector import ProjectSelector
from programsetup import ProgramSetup, get_setup_complete_path


class App:

    def __init__(self):
        # Ensure logging is initialized (it may already be from module import)
        try:
            if not crashlog.session_json_path():
                log_path = crashlog.init_logging()
                crashlog.install_std_redirects()
        except Exception:
            log_path = None
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("PorySuite-Z")
        self.app.setApplicationDisplayName("PorySuite-Z")
        # After QApplication exists, capture Qt messages too
        try:
            crashlog.install_qt_message_handler()
        except Exception:
            pass
        self.__initialize_fonts()
        self.__initialize_resources()
        self.main = None
        self.project_selector = None
        self.loading_dialog = None

    @staticmethod
    def __initialize_fonts():
        QFontDatabase.addApplicationFont(":/fonts/SourceCodePro-Regular.ttf")

    @staticmethod
    def __initialize_resources():
        resources_rc.qInitResources()

    def start(self):
        """
        Starts the application by performing the necessary setup and loading the main window.

        This method performs the following:
        1. Makes necessary directories.
        2. Shows project selector window.
        3. Handles project selection.
        4. Loads project into main window.
        """

        # Set up local data directory (migrate from AppData on first run)
        data_dir = get_data_dir()
        os.makedirs(os.path.join(data_dir, "plugins"), exist_ok=True)
        self._migrate_from_appdata(data_dir)

        # Ensure projects.json exists with at least one project for first launch
        projects_file = os.path.join(data_dir, "projects.json")
        default_project = {"projects": []}
        modified = False
        if not os.path.exists(projects_file):
            projects_data = default_project
            modified = True
        else:
            try:
                with open(projects_file, "r") as f:
                    projects_data = json.load(f)
            except Exception:
                projects_data = default_project
                modified = True
        if not projects_data.get("projects"):
            projects_data = default_project
            modified = True
        if modified:
            os.makedirs(data_dir, exist_ok=True)
            with open(projects_file, "w") as f:
                json.dump(projects_data, f)

        # Check if the required toolchain has been set up
        setup_path = get_setup_complete_path()
        if not os.path.exists(setup_path):
            setup_dialog = ProgramSetup()
            try:
                # Make sure the setup dialog is visible and focused, even when
                # launched from a console window via the batch script.
                setup_dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                setup_dialog.show()
                setup_dialog.raise_()
                setup_dialog.activateWindow()
            except Exception:
                pass
            if setup_dialog.exec() != QDialog.DialogCode.Accepted:
                sys.exit()
            os.makedirs(os.path.dirname(setup_path), exist_ok=True)
            with open(setup_path, "w") as f:
                f.write("complete")

        # Get projects from projects.json
        projects = App.get_projects(data_dir)

        # Show project selector
        self.project_selector = ProjectSelector(projects=projects["projects"])
        self.project_selector.show()
        try:
            self.project_selector.raise_()
            self.project_selector.activateWindow()
        except Exception:
            pass



        # Wait for project selector to close
        loop = QEventLoop()
        self.project_selector.close_signal.connect(loop.quit)
        loop.exec()

        # Handle project selection
        if self.project_selector.selected_index == -1:
            # Exit if the user presses cancel
            sys.exit()
        else:
            # Reload projects to capture any modifications from the selector
            projects = App.get_projects(data_dir)
            if self.project_selector.selected_index == -2:
                # Open a new project
                self.project_selector.selected_index = 0

        # Unified window is the only launch mode now

        # Load project information
        p_info = projects["projects"][self.project_selector.selected_index]
        local_info_path = os.path.join(p_info["dir"], "project.json")
        if not os.path.exists(local_info_path):
            local_info_path = os.path.join(p_info["dir"], "config.json")
        if not os.path.exists(local_info_path):
            config_mk = os.path.join(p_info["dir"], "config.mk")
            if os.path.exists(config_mk):
                # Create a minimal config.json for projects that don’t have one
                default_config = {
                    "name": p_info.get("name", os.path.basename(p_info["dir"])),
                    "project_name": p_info.get("project_name", os.path.basename(p_info["dir"])),
                    "version": {"major": 0, "minor": 0, "patch": 0},
                    "date_created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "date_modified": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                ret = QMessageBox.question(
                    None,
                    "Create config.json",
                    (
                        "No project.json or config.json found.\n"
                        "Create one using default settings?"
                    ),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                )
                if ret == QMessageBox.StandardButton.Yes:
                    with open(os.path.join(p_info["dir"], "config.json"), "w") as f:
                        json.dump(default_config, f, indent=4)
                    local_info_path = os.path.join(p_info["dir"], "config.json")
                else:
                    QMessageBox.critical(None, "Error", "Cannot load project without config.json")
                    sys.exit(1)
            else:
                QMessageBox.critical(None, "Error", "Project configuration file not found")
                sys.exit(1)

        with open(local_info_path, "r") as f:
            local_p_info = json.load(f)

        # Update last opened timestamp
        p_info["last_opened"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update projects.json
        projects["projects"][self.project_selector.selected_index] = p_info
        with open(os.path.join(data_dir, "projects.json"), "w") as file_projects:
            json.dump(projects, file_projects)

        merged_info = p_info | local_p_info

        self._launch_unified(merged_info, projects, data_dir)

        # Log where this session’s JSON log lives for easy discovery
        try:
            jpath = crashlog.session_json_path() or crashlog.latest_json_log_file()
            if jpath:
                print(f"Session log (JSONL): {jpath}")
        except Exception:
            pass

    def _launch_unified(self, merged_info: dict, projects: dict, data_dir: str):
        """Launch the unified PorySuite-Z window with all editors."""
        from eventide.mainwindow import EventideMainWindow
        from unified_mainwindow import UnifiedMainWindow

        # Create both child windows (hidden — we only use their widgets)
        porysuite_win = MainWindow()
        eventide_win = EventideMainWindow()

        # Show loading dialog on PorySuite (the heavier one)
        self.loading_dialog = LoadingProject(porysuite_win)
        self.loading_dialog.show()
        self.loading_dialog.update_progress(10)

        # Load PorySuite data
        porysuite_win.load_data(merged_info)
        self.loading_dialog.update_progress(40)

        # Select first Pokemon so the tree is initialized
        try:
            porysuite_win.ui.tree_pokemon.setCurrentItem(
                porysuite_win.ui.tree_pokemon.topLevelItem(0))
            porysuite_win.setWindowModified(False)
        except Exception:
            pass
        self.loading_dialog.update_progress(50)

        # Load EVENTide data
        eventide_win.load_data(merged_info)
        self.loading_dialog.update_progress(70)

        # Create the unified window
        self.main = UnifiedMainWindow()
        self.main.setup_pages(porysuite_win, eventide_win)
        self.loading_dialog.update_progress(85)

        # Load project info into unified window
        self.main.load_data(merged_info)
        self.main.setWindowFilePath(merged_info.get("dir", ""))

        self.loading_dialog.update_progress(100)
        self.loading_dialog.close()

        # Keep references to prevent garbage collection
        self._porysuite_win = porysuite_win
        self._eventide_win = eventide_win

        self.main.showMaximized()
        self.main.activateWindow()
        self.main.setFocus()
        # Defer a second maximize after the event loop has started — on some
        # Windows / remote-desktop setups, showMaximized() alone is ignored
        # when called before the event loop is running.
        from PyQt6.QtCore import QTimer
        def _force_maximize():
            from PyQt6.QtCore import Qt as _Qt
            self.main.setWindowState(
                self.main.windowState() | _Qt.WindowState.WindowMaximized
            )
        QTimer.singleShot(0, _force_maximize)

    @staticmethod
    def _migrate_from_appdata(data_dir: str) -> None:
        """One-time migration: copy projects.json and plugins from AppData to the
        local data/ directory so existing users don't lose their project history."""
        import shutil
        try:
            import platformdirs
            from app_info import APP_NAME, AUTHOR
            old_dir = platformdirs.user_data_dir(APP_NAME, AUTHOR)
        except Exception:
            return

        if not os.path.isdir(old_dir):
            return

        # Don't migrate if the local data dir already has a projects.json
        local_projects = os.path.join(data_dir, "projects.json")
        if os.path.exists(local_projects):
            return

        # Copy projects.json
        old_projects = os.path.join(old_dir, "projects.json")
        if os.path.exists(old_projects):
            try:
                shutil.copy2(old_projects, local_projects)
            except Exception:
                pass

        # Copy plugins (non-destructive: only files not already present)
        old_plugins = os.path.join(old_dir, "plugins")
        new_plugins = os.path.join(data_dir, "plugins")
        if os.path.isdir(old_plugins):
            try:
                for item in os.listdir(old_plugins):
                    src = os.path.join(old_plugins, item)
                    dst = os.path.join(new_plugins, item)
                    if not os.path.exists(dst):
                        if os.path.isdir(src):
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)
            except Exception:
                pass

        # Copy toolchain flag so setup doesn't re-run for existing users
        old_toolchain = os.path.join(old_dir, "toolchain", "setup_complete")
        new_toolchain = os.path.join(data_dir, "toolchain", "setup_complete")
        if os.path.exists(old_toolchain) and not os.path.exists(new_toolchain):
            try:
                os.makedirs(os.path.dirname(new_toolchain), exist_ok=True)
                shutil.copy2(old_toolchain, new_toolchain)
            except Exception:
                pass

    @staticmethod
    def get_projects(path: str) -> dict:
        """
        Retrieve the projects from the projects.json file and return them as a dictionary.

        Args:
            path (str): The path to the directory containing the projects.json file.

        Returns:
            dict: A dictionary containing the projects retrieved from the projects.json file.
        """
        # Define the path to the projects.json file
        projects_file = os.path.join(path, "projects.json")
        
        # If the projects.json file doesn't exist, create an empty projects dictionary and save it to the file
        if not os.path.exists(projects_file):
            projects = {"projects": []}
            with open(projects_file, "w") as file_projects:
                json.dump(projects, file_projects)
        
        # Load the projects from the projects.json file
        with open(projects_file, "r") as file_projects:
            projects = json.load(file_projects)

        # Normalize old entries that used 'path' instead of 'dir'
        normalized = False
        for project in projects.get("projects", []):
            if "dir" not in project and "path" in project:
                project["dir"] = project.pop("path")
                normalized = True

        if normalized:
            with open(projects_file, "w") as file_projects:
                json.dump(projects, file_projects)

        # Sort the projects by last opened timestamp in descending order
        projects["projects"].sort(
            key=lambda x: (
                datetime.datetime.strptime(
                    x.get("last_opened", ""), "%Y-%m-%d %H:%M:%S"
                )
                if x.get("last_opened")
                else datetime.datetime.min
            ),
            reverse=True,
        )
        
        return projects


if __name__ == "__main__":
    app = App()
    app.start()
    sys.exit(QApplication.exec())
