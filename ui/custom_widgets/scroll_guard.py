"""
Scroll-wheel guard for QComboBox and QSpinBox widgets.

Prevents accidental value changes when the mouse wheel scrolls over a
combo box or spin box that the user hasn't clicked into.  The widget must
have strong focus (i.e. the user clicked it) before the scroll wheel will
do anything.

Usage
-----
    from ui.custom_widgets.scroll_guard import install_scroll_guard

    combo = QComboBox()
    install_scroll_guard(combo)          # single widget

    install_scroll_guard_recursive(form) # every combo/spin in a tree
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QComboBox, QSpinBox, QDoubleSpinBox, QWidget

_GUARDED_TYPES = (QComboBox, QSpinBox, QDoubleSpinBox)


class _WheelGuard(QObject):
    """Event filter that eats wheel events unless the widget has focus."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel and isinstance(obj, QWidget):
            if not obj.hasFocus():
                event.ignore()
                return True  # swallow — don't let the widget handle it
        return False


# Single shared instance — lightweight, no per-widget overhead.
_guard = _WheelGuard()


def install_scroll_guard(widget: QWidget) -> None:
    """Make *widget* ignore scroll-wheel changes unless it has focus."""
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_guard)


def install_scroll_guard_recursive(root: QWidget) -> None:
    """Walk *root*'s widget tree and guard every combo / spin box."""
    for child in root.findChildren(QWidget):
        if isinstance(child, _GUARDED_TYPES):
            install_scroll_guard(child)
