"""GUI-level tests for the Suggest EV Yield dialog (offscreen Qt) and for
the mainwindow Apply/Cancel glue, driven through the REAL shipping
``MainWindow._on_suggest_ev_yield`` method on a minimal stub.

Pinned behaviours, straight from the feature spec:

* the preview opens with the suggestion pre-selected and alternatives
  pickable;
* Apply writes through the ordinary EV spinboxes — the SAME path a manual
  edit takes, so the normal dirty tracking fires;
* Cancel leaves every spinbox untouched;
* existing EV values are never overwritten before Apply;
* the per-stat clamp respects the project's real control range;
* an uncertain stage shows the stage picker instead of guessing.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QSpinBox, QWidget,
)

app = QApplication.instance() or QApplication(sys.argv)

from ui.ev_suggest_dialog import EvSuggestDialog  # noqa: E402


OCTO_STATS = {"hp": 52, "attack": 49, "defense": 48,
              "sp_attack": 44, "sp_defense": 51, "speed": 48}
ZERO_EVS = {k: 0 for k in OCTO_STATS}


def _dlg(stats=None, current=None, category="first",
         basis="basis text", per_stat_max=3):
    return EvSuggestDialog("Octo", stats or dict(OCTO_STATS),
                           current or dict(ZERO_EVS), category, basis,
                           per_stat_max)


# ── the dialog itself ───────────────────────────────────────────────────

def test_suggestion_is_preselected_and_complete():
    d = _dlg()
    evs = d.selected_evs()
    assert evs is not None
    assert set(evs) == set(OCTO_STATS), "every stat present, zeros filled"
    assert sum(evs.values()) == 1, "first-stage Octo → 1 point"
    assert evs["hp"] == 1
    d.deleteLater()


def test_picking_an_alternative_changes_the_result():
    # A strong final: total 3 with real alternatives.
    d = _dlg(stats={"hp": 86, "attack": 97, "defense": 111,
                    "sp_attack": 61, "sp_defense": 105, "speed": 40},
             category="final")
    first = d.selected_evs()
    buttons = d._choice_group.buttons()
    assert len(buttons) >= 2, "alternatives must be offered"
    buttons[1].setChecked(True)
    second = d.selected_evs()
    assert second != first
    d.deleteLater()


def test_certain_stage_hides_the_stage_picker():
    d = _dlg(category="final")
    assert not d._stage_combo.isVisibleTo(d)
    d.deleteLater()


def test_uncertain_stage_shows_the_picker_and_rebuilds_on_choice():
    d = _dlg(category="uncertain")
    assert d._stage_combo.isVisibleTo(d), (
        "an unknown stage asks the user instead of guessing")
    assert sum(d.selected_evs().values()) == 1   # conservative default
    # Pick "Final stage" → a 500-BST-ish spread would move, but even
    # Octo's spread must rebuild against the final-stage pool (total 2+).
    idx = d._stage_combo.findData("final")
    d._stage_combo.setCurrentIndex(idx)
    assert sum(d.selected_evs().values()) >= 2
    d.deleteLater()


def test_per_stat_values_respect_the_controls_range():
    d = _dlg(stats={"hp": 60, "attack": 40, "defense": 60,
                    "sp_attack": 135, "sp_defense": 85, "speed": 100},
             category="final", per_stat_max=2)
    for choice in d._choices:
        assert all(v <= 2 for v in choice.values()), (
            "the project's real spinbox maximum caps every option")
    d.deleteLater()


def test_stage_combo_ignores_the_wheel_while_closed():
    d = _dlg(category="uncertain")
    combo = d._stage_combo
    before = combo.currentIndex()
    ev = QWheelEvent(
        QPointF(5, 5), combo.mapToGlobal(QPointF(5, 5)),
        QPoint(0, 0), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(combo, ev)
    assert combo.currentIndex() == before
    d.deleteLater()


# ── the mainwindow glue, on the real shipping method ─────────────────────

class _FakeSourceData:
    """Read-only getters; any write-shaped access raises."""

    def __init__(self):
        self._species = {
            "SPECIES_OCTO": {"speciesName": "Octo"},
            "SPECIES_OCTOROK": {"speciesName": "Octorok"},
        }
        self._evos = {
            "SPECIES_OCTO": [{"method": "EVO_LEVEL", "param": 16,
                              "targetSpecies": "SPECIES_OCTOROK"}],
        }

    def get_pokemon_data(self):
        return dict(self._species)

    def get_species_info(self, sp, key, form=None):
        return self._species.get(sp, {}).get(key)

    def get_evolutions(self, sp):
        return list(self._evos.get(sp, []))

    def __getattr__(self, name):
        if name.startswith(("set_", "save", "add_", "delete", "write")):
            raise AssertionError(
                f"Suggest EV Yield tried to call {name!r} — it must be "
                f"read-only toward the project")
        raise AttributeError(name)


class _Ui:
    def __init__(self):
        for n in ("base_hp", "base_atk", "base_def", "base_spatk",
                  "base_spdef", "base_speed"):
            sb = QSpinBox()
            sb.setMaximum(255)
            setattr(self, n, sb)
        for n in ("evs_hp", "evs_atk", "evs_def", "evs_spatk",
                  "evs_spdef", "evs_speed"):
            sb = QSpinBox()
            sb.setMaximum(3)
            setattr(self, n, sb)


class _Stub(QWidget):
    """Minimal window carrying exactly what the real handler reads (a
    QWidget, because the handler parents the dialog to the window)."""

    def __init__(self):
        super().__init__()
        self.ui = _Ui()
        self.source_data = _FakeSourceData()
        self.previous_selected_species = "SPECIES_OCTO"
        self.previous_selected_form = None
        self.ui.base_hp.setValue(52)
        self.ui.base_atk.setValue(49)
        self.ui.base_def.setValue(48)
        self.ui.base_spatk.setValue(44)
        self.ui.base_spdef.setValue(51)
        self.ui.base_speed.setValue(48)
        # existing EV values that must survive until an explicit Apply
        self.ui.evs_atk.setValue(2)
        # the stand-in for mainwindow's blanket dirty wiring: every EV
        # spinbox change lands here, exactly like a manual edit would
        self.dirty_events = []
        for n in ("evs_hp", "evs_atk", "evs_def", "evs_spatk",
                  "evs_spdef", "evs_speed"):
            getattr(self.ui, n).valueChanged.connect(
                lambda v, n=n: self.dirty_events.append((n, v)))


def _real_handler():
    from ui.mainwindow import MainWindow
    fn = getattr(MainWindow, "_on_suggest_ev_yield", None)
    assert fn is not None, "the shipping handler must exist"
    return fn


def _evs_now(stub):
    u = stub.ui
    return (u.evs_hp.value(), u.evs_atk.value(), u.evs_def.value(),
            u.evs_spatk.value(), u.evs_spdef.value(), u.evs_speed.value())


def test_cancel_leaves_everything_untouched(monkeypatch):
    import ui.ev_suggest_dialog as mod

    class _CancelDlg(EvSuggestDialog):
        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(mod, "EvSuggestDialog", _CancelDlg)
    stub = _Stub()
    before = _evs_now(stub)
    _real_handler()(stub)
    assert _evs_now(stub) == before == (0, 2, 0, 0, 0, 0)
    assert stub.dirty_events == [], "Cancel must not touch the edit path"


def test_apply_writes_through_the_normal_spinbox_path(monkeypatch):
    import ui.ev_suggest_dialog as mod

    class _ApplyDlg(EvSuggestDialog):
        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mod, "EvSuggestDialog", _ApplyDlg)
    stub = _Stub()
    _real_handler()(stub)
    # Octo, first stage → +1 HP; the pre-existing +2 Attack is replaced
    # only because the user (here: the test) clicked Apply.
    assert _evs_now(stub) == (1, 0, 0, 0, 0, 0)
    # And the write went through valueChanged — the manual-edit path.
    assert ("evs_hp", 1) in stub.dirty_events
    assert ("evs_atk", 0) in stub.dirty_events


def test_handler_is_read_only_until_apply(monkeypatch):
    # The raising FakeSourceData doubles as the proof: a full handler run
    # (with Cancel) completes without any write-shaped attribute access.
    import ui.ev_suggest_dialog as mod

    class _CancelDlg(EvSuggestDialog):
        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(mod, "EvSuggestDialog", _CancelDlg)
    stub = _Stub()
    _real_handler()(stub)   # would raise AssertionError on any mutation


def test_handler_does_nothing_without_a_species(monkeypatch):
    stub = _Stub()
    stub.previous_selected_species = None
    before = _evs_now(stub)
    _real_handler()(stub)
    assert _evs_now(stub) == before
