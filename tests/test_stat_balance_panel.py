"""GUI-level tests for the Stat Balance panel (offscreen Qt).

These drive the REAL ``StatBalancePanel`` — the widget the Stats tab
embeds — against a fake, read-only data model, and pin the behaviours the
feature spec calls out by name:

* the BST line and classification update live, including the exact
  290 → 291 flip, without reopening anything;
* the Octo validation case (330, the modest/unevolved band,
  40 points above an Early-game target);
* the panel NEVER calls a mutating method on the data model (any
  ``set_*``/``save*`` access on the fake raises);
* tier choices persist per species per project through the real
  ``BalancePrefsStore``, and reload on the next visit;
* reverse evolutions (devolution) display as information, never as the
  old bogus "less BST than its evolution" note;
* the combos ignore mouse-wheel while closed (CLAUDE.md UI rule);
* evolution lines are built from real evolution edges, branching kept
  as separate lines, and the project's genuine Octorok→BigOcto→Octorok
  cycle terminates.
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
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from core.stat_balance import BalancePrefsStore  # noqa: E402
from ui.stat_balance_panel import StatBalancePanel  # noqa: E402


# ── a read-only data model that BITES if mutated ─────────────────────────

class FakeSourceData:
    """Just the three getters the panel is allowed to use. Accessing any
    name that smells like a write (`set_*`, `save*`, `add_*`, `delete*`)
    raises — the panel being analytical/read-only is a hard requirement."""

    def __init__(self, species: dict, evolutions: dict):
        self._species = species
        self._evos = evolutions

    def get_pokemon_data(self):
        return dict(self._species)

    def get_species_info(self, sp, key, form=None):
        return self._species.get(sp, {}).get(key)

    def get_evolutions(self, sp):
        return list(self._evos.get(sp, []))

    def __getattr__(self, name):
        if name.startswith(("set_", "save", "add_", "delete", "write")):
            raise AssertionError(
                f"StatBalancePanel tried to call {name!r} — the panel must "
                f"be read-only toward species data")
        raise AttributeError(name)


def _species_row(name, hp, atk, deff, spa, spd, spe):
    return {"speciesName": name, "baseHP": hp, "baseAttack": atk,
            "baseDefense": deff, "baseSpAttack": spa, "baseSpDefense": spd,
            "baseSpeed": spe}


def _octo_world():
    """A small project: Octo's line (with the REAL cycle shape) plus
    filler species spread through the 180–290 band so tier analysis has a
    usable sample."""
    species = {
        "SPECIES_OCTO": _species_row("Octo", 74, 49, 69, 39, 51, 48),
        "SPECIES_OCTOROK": _species_row("Octorok", 80, 70, 80, 60, 60, 60),
        "SPECIES_BIGOCTO": _species_row("BigOcto", 95, 85, 95, 70, 75, 80),
        "SPECIES_SEAOCTO": _species_row("SeaOcto", 90, 75, 85, 80, 70, 80),
    }
    for i in range(12):
        species[f"SPECIES_F{i}"] = _species_row(
            f"Filler{i}", 38 + i, 40, 38 + (i % 5), 35, 36 + (i % 4),
            34 + (i % 3))
    evolutions = {
        "SPECIES_OCTO": [{"method": "EVO_LEVEL", "param": 16,
                          "targetSpecies": "SPECIES_OCTOROK"}],
        "SPECIES_OCTOROK": [
            {"method": "EVO_ITEM", "param": "ITEM_WATER_STONE",
             "targetSpecies": "SPECIES_BIGOCTO"},
            {"method": "EVO_ITEM", "param": "ITEM_NORMAL_STONE",
             "targetSpecies": "SPECIES_SEAOCTO"}],
        # The project's real devolution convention: every stage walks
        # back with the same item.
        "SPECIES_BIGOCTO": [{"method": "EVO_ITEM", "param": "ITEM_TIME_SHARD",
                             "targetSpecies": "SPECIES_OCTOROK"}],
        "SPECIES_SEAOCTO": [{"method": "EVO_ITEM", "param": "ITEM_TIME_SHARD",
                             "targetSpecies": "SPECIES_OCTOROK"}],
    }
    evolutions["SPECIES_OCTOROK"].append(
        {"method": "EVO_ITEM", "param": "ITEM_TIME_SHARD",
         "targetSpecies": "SPECIES_OCTO"})
    return FakeSourceData(species, evolutions)


OCTO_STATS = {"hp": 74, "attack": 49, "defense": 69,
              "sp_attack": 39, "sp_defense": 51, "speed": 48}


@pytest.fixture()
def panel(tmp_path):
    store = BalancePrefsStore(str(tmp_path / "prefs.json"))
    p = StatBalancePanel(prefs_store=store)
    p._test_store_path = str(tmp_path / "prefs.json")   # for reload tests
    yield p
    p.deleteLater()


def _show_octo(panel, project=r"C:\proj\zeldamon"):
    panel.update_context(project, _octo_world(), "SPECIES_OCTO", None)
    panel.update_stats(dict(OCTO_STATS))


# ── live BST + classification ───────────────────────────────────────────

def test_octo_shows_330_in_the_modest_band(panel):
    _show_octo(panel)
    assert panel._bst_label.text() == "Base Stat Total: 330"
    assert panel._class_label.text() == \
        "Modest — typical unevolved range in Gen 3"
    assert "#4dd0e1" in panel._class_label.styleSheet()


def test_the_descriptive_line_never_contradicts_the_target_line(panel):
    # The exact contradiction from the feature review: a 290-BST creature
    # with an Early-game target used to read "Very weak" in red next to a
    # green "within target". The descriptive line must stay neutral.
    _show_octo(panel)
    s = dict(OCTO_STATS)
    s["hp"] = 34               # BST 290
    panel.update_stats(s)
    panel._tier_combo.setCurrentText("Early-game wild encounter")
    assert "Within the selected target range" in panel._target_label.text()
    cls = panel._class_label.text().lower()
    for word in ("weak", "strong", "error", "bad"):
        assert word not in cls, cls


def test_the_290_to_291_flip_happens_live(panel):
    _show_octo(panel)
    s = dict(OCTO_STATS)
    s["hp"] = 34               # 330 - 40 → 290
    panel.update_stats(s)
    assert panel._class_label.text() == "Low — early-game range in Gen 3"
    low_color = panel._class_label.styleSheet()
    s["hp"] = 35               # 291
    panel.update_stats(s)
    assert panel._class_label.text() == \
        "Modest — typical unevolved range in Gen 3"
    assert panel._class_label.styleSheet() != low_color


def test_no_species_selected_shows_a_placeholder(panel):
    panel.clear()
    assert panel._bst_label.text() == "Base Stat Total: —"


# ── target analysis ──────────────────────────────────────────────────────

def test_early_game_tier_reports_octo_40_points_above(panel):
    _show_octo(panel)
    panel._tier_combo.setCurrentText("Early-game wild encounter")
    assert "40 BST points above" in panel._target_label.text()
    assert "stronger than intended" in panel._target_label.text()


def test_no_tier_means_no_assumption_and_no_accusation(panel):
    _show_octo(panel)
    assert panel._tier_combo.currentText() == "Unspecified"
    assert "nothing is assumed" in panel._target_label.text().lower()
    assert panel._notes_label.text() == ""


def test_custom_range_spec_example_15_points_above(panel):
    _show_octo(panel)
    panel._tier_combo.setCurrentText("Custom")
    panel._custom_min.setValue(230)
    panel._custom_max.setValue(270)
    s = dict(OCTO_STATS)
    s["hp"] = 29               # 330 - 45 → 285
    panel.update_stats(s)
    assert "15 BST points above" in panel._target_label.text()


def test_reversed_custom_range_is_reported_not_guessed(panel):
    _show_octo(panel)
    panel._tier_combo.setCurrentText("Custom")
    panel._custom_min.setValue(400)
    panel._custom_max.setValue(100)
    assert "valid custom range" in panel._target_label.text()


# ── read-only guarantee ──────────────────────────────────────────────────

def test_panel_never_mutates_the_data_model(panel):
    # FakeSourceData raises on any set_*/save* access; a full workout of
    # the panel must therefore complete without an AssertionError.
    _show_octo(panel)
    panel._tier_combo.setCurrentText("Early-game wild encounter")
    panel._tier_combo.setCurrentText("Custom")
    panel._custom_min.setValue(100)
    panel._custom_max.setValue(200)
    panel.update_stats({k: 200 for k in OCTO_STATS})
    panel.clear()


# ── persistence ──────────────────────────────────────────────────────────

def test_tier_persists_per_species_and_reloads(panel, tmp_path):
    _show_octo(panel)
    panel._tier_combo.setCurrentText("Early-game wild encounter")

    # Switch away and back: the choices reload from the store.
    panel.update_context(r"C:\proj\zeldamon", _octo_world(),
                         "SPECIES_OCTOROK", None)
    assert panel._tier_combo.currentText() == "Unspecified", (
        "another species must not inherit Octo's choices")
    panel.update_context(r"C:\proj\zeldamon", _octo_world(),
                         "SPECIES_OCTO", None)
    assert panel._tier_combo.currentText() == "Early-game wild encounter"

    # And a different project sees none of it (fresh panel, same file).
    p2 = StatBalancePanel(
        prefs_store=BalancePrefsStore(panel._test_store_path))
    p2.update_context(r"C:\proj\other", _octo_world(), "SPECIES_OCTO", None)
    assert p2._tier_combo.currentText() == "Unspecified"
    p2.deleteLater()


def test_loading_prefs_never_writes_prefs(panel, tmp_path):
    # update_context sets the combos programmatically; that must not count
    # as the user editing them (classic recursive-signal trap).
    _show_octo(panel)
    panel._tier_combo.setCurrentText("Final evolution")
    store = BalancePrefsStore(panel._test_store_path)
    before = store.get(r"C:\proj\zeldamon", "SPECIES_OCTO")
    # Re-visiting the species re-populates the combos from the store…
    panel.update_context(r"C:\proj\zeldamon", _octo_world(),
                         "SPECIES_OCTO", None)
    after = BalancePrefsStore(panel._test_store_path).get(
        r"C:\proj\zeldamon", "SPECIES_OCTO")
    assert after == before


# ── wheel guard (CLAUDE.md UI rule) ─────────────────────────────────────

def _wheel(widget):
    ev = QWheelEvent(
        QPointF(5, 5), widget.mapToGlobal(QPointF(5, 5)),
        QPoint(0, 0), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_closed_combos_ignore_the_mouse_wheel(panel):
    _show_octo(panel)
    for combo in (panel._tier_combo,):
        before = combo.currentIndex()
        _wheel(combo)
        assert combo.currentIndex() == before, (
            "a closed dropdown changed value on scroll — the scroll-guard "
            "rule from CLAUDE.md is broken")
    before = panel._custom_min.value()
    panel._tier_combo.setCurrentText("Custom")
    _wheel(panel._custom_min)
    assert panel._custom_min.value() == before


# ── evolution comparison ─────────────────────────────────────────────────

def test_evolution_lines_show_bst_progression_and_branches(panel):
    _show_octo(panel)
    text = panel._evo_label.text()
    # Octo 330 → Octorok 410 → both branches, as separate lines — the
    # devolution edges must NOT leak into the forward chains.
    assert "Octo 330" in text
    assert "Octorok 410 (+80)" in text
    assert "BigOcto 500 (+90)" in text
    assert "SeaOcto 480 (+70)" in text
    assert text.count("Octo 330") >= 2, "branches are separate lines"


def test_reverse_evolution_is_information_not_a_warning(panel):
    _show_octo(panel)
    text = panel._evo_label.text()
    # The intentional devolution shows as a labelled fact…
    assert "Octorok 410  →  Octo 330 (-80, reverse evolution)" in text
    # …and the old bogus regression note about it is gone.
    assert "less BST" not in text


def test_an_undecidable_cycle_is_reported_as_such(panel):
    world = _octo_world()
    # Replace the evolutions with a mutual pair using the SAME mechanism
    # both ways — genuinely undecidable.
    world._evos = {
        "SPECIES_OCTO": [{"method": "EVO_ITEM", "param": "ITEM_MOON_STONE",
                          "targetSpecies": "SPECIES_OCTOROK"}],
        "SPECIES_OCTOROK": [{"method": "EVO_ITEM",
                             "param": "ITEM_MOON_STONE",
                             "targetSpecies": "SPECIES_OCTO"}],
    }
    panel.update_context(r"C:\p", world, "SPECIES_OCTO", None)
    panel.update_stats(dict(OCTO_STATS))
    text = panel._evo_label.text()
    assert "could not be determined" in text
    assert "reverse evolution" not in text, "no direction may be invented"


def test_the_live_cycle_terminates_and_unsaved_edits_count(panel):
    _show_octo(panel)                      # traversal already crossed the
    s = dict(OCTO_STATS)                   # devolution ring
    s["hp"] = 94                           # unsaved edit: BST 350
    panel.update_stats(s)
    assert "Octo 350" in panel._evo_label.text(), (
        "the comparison must reflect the UNSAVED value being edited")


def test_species_without_evolutions_says_so(panel):
    world = _octo_world()
    panel.update_context(r"C:\p", world, "SPECIES_F3", None)
    panel.update_stats({k: 40 for k in OCTO_STATS})
    assert "No evolution data" in panel._evo_label.text()


# ── references ───────────────────────────────────────────────────────────

def test_custom_creature_gets_project_peers_plus_labelled_vanilla(panel):
    _show_octo(panel)
    text = panel._ref_label.text()
    assert "Closest in this project:" in text
    # Octo is custom: the vanilla line is clearly a reference, never a
    # claimed relationship, and never contains project species.
    assert "Similar Gen 3 totals (reference only):" in text
    assert "Octo" not in text.split("reference only):")[1]


def _vanilla_world():
    species = {
        "SPECIES_BULBASAUR": _species_row("Bulbasaur", 45, 49, 49, 65, 65, 45),
        "SPECIES_CHARMANDER": _species_row("Charmander", 39, 52, 43, 60, 50, 65),
        "SPECIES_SQUIRTLE": _species_row("Squirtle", 44, 48, 65, 50, 64, 43),
    }
    return FakeSourceData(species, {})


BULBA_STATS = {"hp": 45, "attack": 49, "defense": 49,
               "sp_attack": 65, "sp_defense": 65, "speed": 45}


def test_vanilla_identical_species_skips_redundant_comparisons(panel):
    # A vanilla FireRed project: the species IS the reference, so a
    # nearest-vanilla list would just repeat it.
    panel.update_context(r"C:\p", _vanilla_world(), "SPECIES_BULBASAUR",
                         None)
    panel.update_stats(dict(BULBA_STATS))
    text = panel._ref_label.text()
    assert "Stats match vanilla Gen 3 Bulbasaur exactly (BST 318)." in text
    assert "Similar Gen 3 totals" not in text


def test_modified_vanilla_species_shows_the_difference(panel):
    panel.update_context(r"C:\p", _vanilla_world(), "SPECIES_BULBASAUR",
                         None)
    s = dict(BULBA_STATS)
    s["hp"] = 77               # buffed: BST 350
    panel.update_stats(s)
    text = panel._ref_label.text()
    assert "Vanilla Gen 3 Bulbasaur had BST 318" in text
    assert "350 (+32)" in text


# ── layout ───────────────────────────────────────────────────────────────

def test_panel_fits_a_narrow_column_without_forcing_width(panel):
    # The Stats tab's Base Stats column can get narrow on small windows;
    # the panel must not force horizontal scrolling there.
    _show_octo(panel)
    panel._tier_combo.setCurrentText("Early-game wild encounter")
    # Before the layout pass the tier combo alone forced 576px. The bound
    # is generous because offscreen font metrics differ from the desktop,
    # but it locks the regression: no single control may balloon the
    # column's minimum again.
    assert panel.minimumSizeHint().width() <= 480
    panel.resize(300, 600)   # must not raise or relayout endlessly
    app.processEvents()


def test_long_tooltips_are_rich_text_so_they_wrap(panel):
    # Plain-text tooltips render as ONE line and spill across neighboring
    # panels; rich text wraps at a readable width.
    for w in (panel._bst_label, panel._class_label, panel._tier_combo,
              panel._evo_label, panel._ref_label):
        tip = w.toolTip()
        assert tip.startswith("<qt"), (w, tip[:40])
