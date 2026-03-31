from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QAbstractItemView,
)


class PokedexReorderDialog(QDialog):
    """Dialog for reordering Pokédex entries."""

    def __init__(self, entries: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reorder Pokédex")

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for entry in entries:
            QListWidgetItem(entry, self.list_widget)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_order(self) -> list[str]:
        """Return the current order of items."""
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
