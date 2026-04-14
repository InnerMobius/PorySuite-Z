import os
import json
import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QMovie
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QApplication,
    QFileDialog,
    QDialog,
    QMessageBox,
    QInputDialog,
)

from app_info import APP_NAME, AUTHOR, get_data_dir, VERSION
from app_util import reveal_directory, condense_path
import core as _core
from newproject import NewProject
from ui.ui_projectselector import Ui_ProjectSelector


class ProjectSelector(QMainWindow):
    close_signal = pyqtSignal()

    selected_app = "unified"  # kept for compat — always unified

    def __init__(self, parent=None, projects=None):
        super().__init__(parent)
        self.ui = Ui_ProjectSelector()
        self.ui.setupUi(self)
        self.setWindowTitle(f"PorySuite-Z {VERSION}")
        self.setWindowFlags(Qt.WindowType.Window |
                            Qt.WindowType.CustomizeWindowHint |
                            Qt.WindowType.WindowTitleHint)
        self.projects = projects or []
        self.selected_index = -1
        self.selected_app = "unified"
        gif_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "res", "images", "PorySuite.gif")
        movie = QMovie(gif_path)
        self.ui.label_icon.setMovie(movie)
        movie.start()

        # Version and credits below the title
        self.ui.label.setText(f"PorySuite-Z\n{VERSION}")
        self.ui.label_3.setText(
            '<span style="color: #999; font-size: 10px;">'
            "pokefirered projects only<br><br>"
            "Original PorySuite by jschoeny<br>"
            "PorySuite-Z by InnerMobius"
            "</span>"
        )
        self.ui.label_3.setTextFormat(Qt.TextFormat.RichText)

        # Hide buttons that are no longer needed in unified mode
        self.ui.button_newproject.hide()
        self.ui.button_plugins.hide()
        # Sort projects by last opened if available
        self.projects.sort(
            key=lambda x: x.get("last_opened", ""),
            reverse=True,
        )
        for i, project in enumerate(self.projects):
            name = project.get("name", "Unnamed")
            path = project.get("dir", "")
            self.add_project(name, path, i)

    def add_project(self, name: str, path: str, p_info_index: int):
        from PyQt6.QtWidgets import QFrame, QHBoxLayout

        row = QFrame(self)
        row.setStyleSheet(
            "QFrame { border-bottom: 1px solid #333; padding: 4px 0; }"
            "QPushButton { padding: 4px 12px; }"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(6, 4, 6, 4)

        # Project name + path
        name_label = QLabel(f"<b>{name}</b>")
        name_label.setMinimumWidth(120)
        row_layout.addWidget(name_label)

        path_label = QLabel(condense_path(path))
        path_label.setStyleSheet("color: #999; font-size: 11px;")
        row_layout.addWidget(path_label, 1)

        # Single Open button — unified window handles both editors
        from PyQt6.QtWidgets import QPushButton

        btn_open = QPushButton("Open")
        btn_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_open.setToolTip("Open project in PorySuite-Z")
        btn_open.clicked.connect(
            lambda _, idx=p_info_index: self.select_project(idx, "unified")
        )
        row_layout.addWidget(btn_open)

        self.ui.verticalLayout_projects.addWidget(row)

    def select_project(self, index: int, app: str = "unified"):
        self.selected_index = index
        self.close()

    def handle_project_click(self, event, index: int):
        """Legacy handler — kept for compatibility but no longer used by the UI."""
        self.select_project(index)

    def new_project(self):
        self.selected_index = -2
        new_ui = NewProject(parent=self)
        new_ui.exec()
        if new_ui.project_info is not None:
            self.close()
        else:
            self.selected_index = -1

    def open_existing_project(self):
        """Allow the user to choose an existing project directory."""
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            project_dir = os.path.normpath(dialog.selectedFiles()[0])
            project_json = os.path.join(project_dir, "project.json")
            config_json = os.path.join(project_dir, "config.json")

            if os.path.exists(project_json):
                try:
                    with open(project_json, "r") as f:
                        project_info = json.load(f)
                except Exception:
                    return
            elif os.path.exists(config_json):
                try:
                    with open(config_json, "r") as f:
                        project_info = json.load(f)
                except Exception:
                    return
            else:
                config_mk = os.path.join(project_dir, "config.mk")
                if os.path.exists(config_mk):
                    ret = QMessageBox.question(
                        self,
                        "Create config.json",
                        (
                            "No project.json or config.json found. "
                            "PorySuite requires a config.json file.\n"
                            "Create one using default settings?"
                        ),
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.No,
                    )
                    if ret == QMessageBox.StandardButton.Yes:
                        plugin = _core.plugin_info()
                        project_info = {
                            "name": os.path.basename(project_dir),
                            "project_name": os.path.basename(project_dir),
                            "version": {"major": 0, "minor": 0, "patch": 0},
                            "plugin_identifier": plugin["identifier"],
                            "plugin_version": plugin["version"],
                            "date_created": datetime.datetime.now().strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "date_modified": datetime.datetime.now().strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                        }
                        with open(config_json, "w") as f:
                            json.dump(project_info, f, indent=4)
                    else:
                        return
                else:
                    return

            required = {
                "project_name",
                "name",
                "version",
                "plugin_identifier",
                "plugin_version",
            }
            if not required.issubset(project_info.keys()):
                return

            data_dir = get_data_dir()
            os.makedirs(data_dir, exist_ok=True)
            projects_file = os.path.join(data_dir, "projects.json")
            if os.path.exists(projects_file):
                with open(projects_file, "r") as f:
                    projects = json.load(f)
            else:
                projects = {"projects": []}

            new_entry = {
                "name": project_info["name"],
                "project_name": project_info["project_name"],
                "dir": project_dir,
                "last_opened": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            projects["projects"] = [p for p in projects["projects"] if p.get("dir") != project_dir]
            projects["projects"].insert(0, new_entry)

            with open(projects_file, "w") as f:
                json.dump(projects, f)

            self.selected_index = 0
            self.close()

    def change_project_plugin(self, index: int):
        """No-op — plugin system removed. Only pokefirered is supported."""
        pass

    @staticmethod
    def open_plugins_folder():
        """No-op — plugin system removed."""
        pass

    def open_settings(self):
        from settingsdialog import SettingsDialog
        SettingsDialog(self).exec()

    def clear_settings(self):
        """Delete project history and app settings (keeps the plugins folder)."""
        import shutil
        ret = QMessageBox.question(
            self,
            "Clear Settings",
            "This will delete your project history and all app settings.\n"
            "Your plugins folder will be kept.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        data_dir = get_data_dir()

        # Clear project history (write empty file rather than deleting so the
        # AppData migration guard in app.py sees it as already present and
        # doesn't re-copy old projects back on the next launch).
        import json as _json
        projects_file = os.path.join(data_dir, "projects.json")
        with open(projects_file, "w") as _f:
            _json.dump({"projects": []}, _f)

        # Remove settings INI
        from app_info import get_settings_path
        settings_file = get_settings_path()
        if os.path.exists(settings_file):
            os.remove(settings_file)

        # Remove toolchain flag so first-run setup triggers again
        toolchain_dir = os.path.join(data_dir, "toolchain")
        if os.path.isdir(toolchain_dir):
            shutil.rmtree(toolchain_dir)

        QMessageBox.information(
            self,
            "Settings Cleared",
            "Settings cleared. PorySuite will now close — restart to continue.",
        )
        self.selected_index = -1
        super().close()
        self.destroy()
        QApplication.quit()

    def close(self):
        self.close_signal.emit()
        if self.selected_index == -1:
            self.hide()
            super().close()
            self.destroy()
            QApplication.quit()
        else:
            super().close()
            self.destroy()

    def quit(self):
        self.selected_index = -1
        self.close()
