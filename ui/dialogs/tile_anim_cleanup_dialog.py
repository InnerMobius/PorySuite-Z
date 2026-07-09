"""
Tile Animation Cleanup Dialog.

Finds orphaned frame PNGs — numbered frame images left on disk that no
animation in src/tileset_anims.c references — and lets the user delete them.

Two sources of orphans:
  * a single frame deleted from an animation (the N.png stayed on disk), and
  * a whole animation removed or renamed (its old folder still holds every PNG).

Modelled on the Sound Editor's cleanup dialog.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QMessageBox, QHeaderView,
)
from PyQt6.QtGui import QColor, QFont

from core.tileset_anim_data import find_orphaned_frames

_log = logging.getLogger("TileAnim.Cleanup")

_ENTRY_ROLE = Qt.ItemDataRole.UserRole + 211


def _fmt_size(n: int) -> str:
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


class TileAnimCleanupDialog(QDialog):
    """Find and delete orphaned tile-animation frame PNGs."""

    def __init__(self, project_dir: str, parent=None):
        super().__init__(parent)
        self._project_dir = project_dir
        self.setWindowTitle("Clean Up Animation Frames")
        self.setMinimumSize(720, 480)
        self._build_ui()
        self._run_scan()

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        desc = QLabel(
            "When you delete a frame or remove an animation, the picture files "
            "are left on disk on purpose (so nothing is lost by accident). Over "
            "time these pile up. The scan below finds frame pictures that no "
            "animation uses anymore.\n\n"
            "Tick the ones you want gone and click Delete Selected."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(desc)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Frame", "Location", "Size"])
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._tree)

        self._total_label = QLabel("Selected: 0 file(s)")
        self._total_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._total_label)

        btn_row = QHBoxLayout()
        self._btn_scan = QPushButton("Scan Again")
        self._btn_scan.clicked.connect(self._run_scan)
        btn_row.addWidget(self._btn_scan)

        btn_row.addStretch()
        self._btn_delete = QPushButton("Delete Selected")
        self._btn_delete.setStyleSheet(
            "QPushButton { background: #8b2020; color: white; }"
            "QPushButton:hover { background: #b02020; }"
            "QPushButton:disabled { background: #444; color: #888; }"
        )
        self._btn_delete.clicked.connect(self._on_delete)
        self._btn_delete.setEnabled(False)
        btn_row.addWidget(self._btn_delete)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _run_scan(self):
        self._tree.blockSignals(True)
        self._tree.clear()
        self._tree.blockSignals(False)

        try:
            orphans = find_orphaned_frames(self._project_dir)
        except Exception as exc:
            _log.exception("orphan scan failed")
            QMessageBox.critical(
                self, "Scan Failed",
                f"Could not scan for orphaned frames:\n{exc}")
            return

        if not orphans:
            info = QTreeWidgetItem(
                ["No leftover frames found — everything on disk is in use.",
                 "", ""])
            info.setFlags(Qt.ItemFlag.ItemIsEnabled)
            info.setForeground(0, QColor("#6a6"))
            self._tree.addTopLevelItem(info)
            self._btn_delete.setEnabled(False)
            self._total_label.setText("Selected: 0 file(s)")
            return

        # Group: whole removed animations vs. leftover single frames.
        removed_anims = [o for o in orphans if o.whole_anim_orphaned]
        leftover = [o for o in orphans if not o.whole_anim_orphaned]

        self._tree.blockSignals(True)
        try:
            self._add_category(
                "Removed / renamed animations (whole folders no longer used)",
                removed_anims)
            self._add_category(
                "Leftover deleted frames (folder still in use)",
                leftover)
        finally:
            self._tree.blockSignals(False)

        self._update_total()

    def _add_category(self, title: str, entries):
        if not entries:
            return
        cat = QTreeWidgetItem([title, "", ""])
        cat.setFlags(Qt.ItemFlag.ItemIsEnabled |
                     Qt.ItemFlag.ItemIsUserCheckable)
        cat.setCheckState(0, Qt.CheckState.Unchecked)
        cat.setFont(0, QFont("", -1, QFont.Weight.Bold))
        self._tree.addTopLevelItem(cat)

        for o in entries:
            label = f"{o.tileset} / {o.anim_name} / {os.path.basename(o.abs_path)}"
            path_str = o.rel_path
            if len(path_str) > 64:
                path_str = "…" + path_str[-61:]
            child = QTreeWidgetItem([label, path_str, _fmt_size(o.size)])
            child.setFlags(Qt.ItemFlag.ItemIsEnabled |
                           Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Unchecked)
            child.setData(0, _ENTRY_ROLE, o)
            cat.addChild(child)
        cat.setExpanded(True)

    # ------------------------------------------------------------------
    def _on_item_changed(self, item, column):
        if column != 0:
            return
        if item.childCount() > 0:
            self._tree.blockSignals(True)
            state = item.checkState(0)
            for i in range(item.childCount()):
                item.child(i).setCheckState(0, state)
            self._tree.blockSignals(False)
        else:
            parent = item.parent()
            if parent:
                self._tree.blockSignals(True)
                checked = sum(
                    1 for i in range(parent.childCount())
                    if parent.child(i).checkState(0) == Qt.CheckState.Checked)
                if checked == 0:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                elif checked == parent.childCount():
                    parent.setCheckState(0, Qt.CheckState.Checked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
                self._tree.blockSignals(False)
        self._update_total()

    def _selected_entries(self):
        selected = []
        for i in range(self._tree.topLevelItemCount()):
            cat = self._tree.topLevelItem(i)
            for j in range(cat.childCount()):
                child = cat.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    entry = child.data(0, _ENTRY_ROLE)
                    if entry:
                        selected.append(entry)
        return selected

    def _update_total(self):
        selected = self._selected_entries()
        total = sum(e.size for e in selected)
        self._total_label.setText(
            f"Selected: {len(selected)} file(s), ~{_fmt_size(total)} recoverable")
        self._btn_delete.setEnabled(len(selected) > 0)

    # ------------------------------------------------------------------
    def _on_delete(self):
        selected = self._selected_entries()
        if not selected:
            return

        total = sum(e.size for e in selected)
        reply = QMessageBox.question(
            self, "Confirm Cleanup",
            f"Delete {len(selected)} leftover frame file(s) "
            f"(~{_fmt_size(total)})?\n\n"
            f"These pictures aren't used by any animation. If you're using "
            f"Git you can still get them back from history.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        errors = []
        touched_dirs = set()
        for e in selected:
            try:
                os.remove(e.abs_path)
                deleted += 1
                touched_dirs.add(os.path.dirname(e.abs_path))
            except OSError as ex:
                errors.append(f"{e.rel_path}: {ex}")

        # Remove any anim folder we just emptied (and its now-empty parent
        # 'anim' folder), so a removed animation leaves nothing behind.
        removed_dirs = 0
        for d in sorted(touched_dirs, key=len, reverse=True):
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    removed_dirs += 1
                    parent = os.path.dirname(d)
                    if (os.path.basename(parent) == "anim"
                            and os.path.isdir(parent)
                            and not os.listdir(parent)):
                        os.rmdir(parent)
            except OSError:
                pass

        msg = f"Deleted {deleted} frame file(s)."
        if removed_dirs:
            msg += f"\nRemoved {removed_dirs} empty animation folder(s)."
        if errors:
            msg += ("\n\nSome files could not be deleted:\n"
                    + "\n".join(errors[:10])
                    + ("\n…" if len(errors) > 10 else ""))
        QMessageBox.information(self, "Cleanup Done", msg)

        self._run_scan()
