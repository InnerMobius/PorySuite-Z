"""
Scroll-wheel guard for QComboBox / QSpinBox / QDoubleSpinBox / QSlider widgets.

Per project rule (``CLAUDE.md`` → Persistent Instructions → UI/UX Rules):
the mouse wheel MUST NEVER change a guarded widget's value while the widget
is in its closed/idle state. The user clicks the widget (opening the
dropdown for a combo, or focusing the spinbox/slider) and picks a value
explicitly. Scrolling is for page scrolling, not value editing.

This matters because the user runs PorySuite via Chrome Remote Desktop with
two-finger scrolling. A focused-but-closed dropdown sitting under the
scrolling cursor would otherwise commit dozens of silent value changes
while the user scrolls the page — repeatedly observed via piano roll
instrument dropdowns silently mutating song VOICE values to nearby slot
indices like 53 (the empty slot in a typical voicegroup).

When a combo box's popup IS open, wheel events go to the popup's QListView
(a separate widget), so swallowing wheel events on the combo box itself
does NOT prevent list navigation while the dropdown is open.

Usage
-----
    from ui.custom_widgets.scroll_guard import install_scroll_guard

    combo = QComboBox()
    install_scroll_guard(combo)          # single widget

    install_scroll_guard_recursive(form) # every combo/spin in a tree
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QComboBox, QSpinBox, QDoubleSpinBox, QSlider, QWidget

_GUARDED_TYPES = (QComboBox, QSpinBox, QDoubleSpinBox, QSlider)


class _WheelGuard(QObject):
    """Event filter that ALWAYS eats wheel events on the guarded widget.

    Previously this filter only swallowed wheel events when the widget
    lacked focus, on the theory that "user clicked it → user wants to
    interact." That theory is wrong for value-bearing widgets in this
    app — a clicked-then-scrolled dropdown can commit a value change
    the user never intended, especially with remote-desktop scroll
    streams. The rule is: wheel never commits a value. Period.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel and isinstance(obj, QWidget):
            event.ignore()
            return True  # swallow — never let a wheel event commit a value
        return False


# Single shared instance — lightweight, no per-widget overhead.
_guard = _WheelGuard()


def install_scroll_guard(widget: QWidget) -> None:
    """Make *widget* ignore the mouse wheel entirely (both focused and
    unfocused). Click the widget and pick a value explicitly — scroll
    never changes the value.
    """
    global _guard
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    try:
        widget.installEventFilter(_guard)
    except RuntimeError:
        # The shared guard's underlying C++ object was torn down (can happen
        # across QApplication lifecycles, e.g. in tests). Recreate and retry.
        _guard = _WheelGuard()
        widget.installEventFilter(_guard)


def install_scroll_guard_recursive(root: QWidget) -> None:
    """Walk *root*'s widget tree and guard every combo / spin box."""
    for child in root.findChildren(QWidget):
        if isinstance(child, _GUARDED_TYPES):
            install_scroll_guard(child)
