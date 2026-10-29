"""Shared PyQt6 stub helpers for the window tests.

Several tests import the real window module (``ui/mainwindow.py``) without a
GUI runtime, against a fake PyQt6. Those fakes used to hand-list every Qt name
the UI imports, in each test file separately, which meant one new
``from PyQt6.QtGui import X`` in the app broke ten test files at once -- and ten
near-identical copies of the same scaffolding had to be edited to fix it.

Everything here exists so that list is not needed:

* ``autofill_qt_stub(*modules)`` -- a name the stub does not define is invented
  on first access, and bare ``type("X", (), {})`` placeholders are upgraded so
  they can invent nested attributes (Qt enums) and swallow constructor args.
* ``StubQtFinder`` -- manufactures any ``PyQt6.Qt*`` submodule, because
  ``from PyQt6.QtWidgets import X`` goes through the import system, which does
  not consult a parent module's ``__getattr__``.
* ``UiStub`` -- stands in for the generated ``Ui_MainWindow``, which really
  does have every widget on it.

What is deliberately NOT invented: snake_case and _underscored names. Those are
the app's own attributes, so a missing one means the test's hand-built window
never seeded it, and that has to stay a loud failure -- it is how the missing
``_loading_depth`` on those windows was found rather than silently papered over.

A fake with real behaviour -- anything with members of its own, including a
context manager that is all ``__enter__``/``__exit__`` -- is left untouched, and
the test file's own classes keep precedence.
"""

import sys
import types


class AutoQtMeta(type):
    """Metaclass for invented Qt placeholders.

    Nested names resolve on demand, and the placeholder behaves as the integer
    0 where arithmetic is involved -- the UI does things like
    `Qt.ItemDataRole.UserRole + 500` while its module body runs, and a plain
    class cannot be added to an int.
    """

    _counter = 0

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        # Build nested names through the same factory, so they also swallow
        # call arguments -- Qt statics get called, e.g.
        # QApplication.processEvents(QEventLoop.ProcessEventsFlag.X).
        sub = auto_qt_class(name)
        setattr(cls, name, sub)
        return sub

    # Qt enum members are integers; give each placeholder a stable distinct
    # value so roles and flags built from them do not all collide.
    def __index__(cls):
        if "_value" not in cls.__dict__:
            AutoQtMeta._counter += 1
            cls._value = AutoQtMeta._counter
        return cls._value

    __int__ = __index__

    def __add__(cls, other):
        return int(cls) + other

    __radd__ = __add__

    def __sub__(cls, other):
        return int(cls) - other

    def __rsub__(cls, other):
        return other - int(cls)

    def __or__(cls, other):
        return int(cls) | int(other)

    __ror__ = __or__

    def __and__(cls, other):
        return int(cls) & int(other)

    __rand__ = __and__

    def __invert__(cls):
        return ~int(cls)

    def __lt__(cls, other):
        return int(cls) < int(other)

    def __hash__(cls):
        return type.__hash__(cls)


# Single-word Qt methods, which the camelCase rule below cannot spot.
QT_PLAIN_METHODS = frozenset({
    "show", "hide", "close", "exec", "update", "repaint", "raise_", "lower",
    "clear", "deleteLater", "activate", "sender", "parent", "window",
    "accept", "reject", "done", "focus", "scroll", "move", "resize", "adjustSize",
})


def is_qt_method_name(name):
    """True for names that look like Qt's API: camelCase, or a known verb.

    The app's own attributes are snake_case or _underscored, so this keeps a
    genuine "the window never set this attribute" error loud instead of
    silently handing the caller a no-op and passing for nothing. That rule is
    what made the missing `_loading_depth` on these hand-built windows visible
    rather than invisible.
    """
    if not name.isidentifier() or name.startswith("_"):
        return False
    if name in QT_PLAIN_METHODS:
        return True
    return name[:1].islower() and any(c.isupper() for c in name[1:])


# Qt accessors whose real return type matters: handing the caller None breaks
# it (`range(w.count())`, `len(w.text())`). An unpopulated real widget returns
# these same empties, so they are the honest neutral answer.
QT_NEUTRAL_RETURNS = {
    "count": 0, "value": 0, "text": "", "toPlainText": "", "currentIndex": -1,
    "currentText": "", "currentData": None, "currentItem": None,
    "isChecked": False, "isEnabled": True, "isVisible": True,
    "selectedItems": (), "topLevelItemCount": 0, "rowCount": 0,
    "columnCount": 0, "item": None, "topLevelItem": None, "widget": None,
    "styleSheet": "", "toolTip": "", "objectName": "",
}


QT_WIDGET_FACTORIES = frozenset({
    "statusBar", "menuBar", "centralWidget", "style", "palette", "font",
    "layout", "viewport", "model", "selectionModel", "header",
    "horizontalHeader", "verticalHeader",
})
# NOT in that set: parentWidget / topLevelWidget / parent. Code walks up a
# parent chain, so handing it a fresh widget every call never terminates --
# None means "no parent" and ends the walk.


def auto_qt_class(name):
    """A placeholder Qt class.

    Swallows constructor args, invents nested names (Qt enums), answers the
    accessors above with a neutral value of the right type, and answers any
    other camelCase call with a no-op -- the app calls Qt methods on itself
    (`self.parentWidget()`), and a stub cannot model all of Qt by hand.
    """

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, item):
        if item in QT_WIDGET_FACTORIES:
            # These return another Qt object that the caller then uses
            # (`self.statusBar().showMessage(...)`), so None is not enough.
            return lambda *a, **k: auto_qt_class(item.title())()
        if item in QT_NEUTRAL_RETURNS:
            value = QT_NEUTRAL_RETURNS[item]
            if isinstance(value, tuple):
                return lambda *a, **k: list(value)
            return lambda *a, **k: value
        if is_qt_method_name(item):
            return lambda *a, **k: None
        raise AttributeError(
            f"{name} stub has no {item!r} -- if the app needs it, the test "
            f"should model it rather than the stub inventing it")

    return AutoQtMeta(name, (), {"__init__": __init__, "__getattr__": __getattr__})


def autofill_qt_stub(*modules):
    for mod in modules:
        for attr, value in list(vars(mod).items()):
            if attr.startswith("__") or not isinstance(value, type):
                continue
            if isinstance(value, AutoQtMeta):
                continue
            # A "bare placeholder" is `type("X", (), {})` and nothing else.
            # Anything with members of its own -- INCLUDING dunders, since a
            # context manager is all __enter__/__exit__ -- is a fake with real
            # behaviour and must be left alone. Replacing one of those broke
            # the QSignalBlocker stand-in's `with` support.
            own = set(vars(value)) - {
                "__module__", "__qualname__", "__dict__", "__weakref__",
                "__doc__", "__firstlineno__", "__static_attributes__",
            }
            if own:
                continue
            setattr(mod, attr, auto_qt_class(attr))

        def _make(mod):
            def __getattr__(name):
                if name.startswith("__"):
                    raise AttributeError(name)
                # On the PyQt6 package itself, an unknown "QtSomething" is a
                # SUBMODULE, not a class -- `from PyQt6.QtWidgets import X`
                # fails with "'PyQt6' is not a package" otherwise. Register it
                # so the import machinery can find it.
                if getattr(mod, "__name__", "") == "PyQt6" and name.startswith("Qt"):
                    return stub_qt_submodule(name)
                cls = auto_qt_class(name)
                setattr(mod, name, cls)
                return cls
            return __getattr__
        mod.__getattr__ = _make(mod)

class StubQtFinder:
    """Manufactures any PyQt6 submodule the app imports.

    `from PyQt6.QtWidgets import X` goes through the import system, which does
    NOT fall back to the parent module's __getattr__ -- so a stub package with
    nothing in it fails with "No module named 'PyQt6.QtWidgets'". This finder
    answers for any PyQt6.Qt* name, which is what keeps the stub from needing a
    hand-written list of submodules.
    """

    @staticmethod
    def find_spec(fullname, path=None, target=None):
        if not fullname.startswith("PyQt6.") or fullname.count(".") != 1:
            return None
        leaf = fullname.split(".", 1)[1]
        if not leaf.startswith("Qt"):
            return None
        import importlib.machinery
        return importlib.machinery.ModuleSpec(
            fullname, StubQtLoader(), is_package=False)


class StubQtLoader:
    @staticmethod
    def create_module(spec):
        mod = types.ModuleType(spec.name)
        autofill_qt_stub(mod)
        return mod

    @staticmethod
    def exec_module(module):
        pass


def stub_qt_submodule(leaf):
    """Get (creating and registering if needed) the fake PyQt6.<leaf>."""
    name = f"PyQt6.{leaf}"
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        autofill_qt_stub(mod)
        sys.modules[name] = mod
    return mod


if not any(isinstance(f, StubQtFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, StubQtFinder())

class UiStub(types.SimpleNamespace):
    """Stands in for the generated Ui_MainWindow form object.

    The real form has EVERY widget on it, so an unknown attribute here means
    the test simply did not list a widget that genuinely exists -- not that the
    app asked for something wrong. Hand back a widget that absorbs Qt calls
    instead of raising, and let the widgets a test passes in (the ones it
    inspects) win. Chasing these one AttributeError at a time is how these
    fixtures fell behind the window in the first place.
    """

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        widget = auto_qt_class("UiWidget")()
        setattr(self, name, widget)
        return widget
