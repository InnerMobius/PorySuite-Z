"""Group B: lossless move flags, and all-or-nothing new-move writes.

FLAGS — the engine defines fourteen move flags (`FLAG_*` in
`include/constants/pokemon.h`); the editor shows six. The detail panel's
`collect()` rebuilt the field from the six it has, so editing ANY property of
a move — power, PP, accuracy — silently dropped the other eight. Measured on
this project's data: 58 of 355 moves carry at least one, and MOVE_FIRE_PUNCH
lost FLAG_PUNCHING_MOVE, which is what Iron Fist checks.

NEW MOVES — a new move is written to FOUR engine files. The writers ran
straight through, so a failure in one still let the others write, leaving
`move_names.h` naming a constant `moves.h` never defined. Verified against a
real build: `error: 'MOVE_...' undeclared here (not in a function)`.

DO NOT REGRESS:
  * The flag merge belongs at the DATA-MODEL boundary (`save_current`), not on
    the detail widget — a widget-local copy dies when the selection changes,
    the tab reloads, or the widget is rebuilt.
  * An explicit untick of a flag the UI DOES show must still be honoured.
    Preservation must not become "flags can never be removed".
  * A failed new-move write must restore every target file and leave the move
    pending, so a retry can finish the job.
  * Snapshot-and-restore is NOT atomicity. A failed restore must be reported,
    never swallowed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── flags ────────────────────────────────────────────────────────────────

def _merge():
    from ui.moves_tab_widget import MovesTabWidget
    return MovesTabWidget._merge_flags


FIRE_PUNCH = ("FLAG_MAKES_CONTACT | FLAG_PROTECT_AFFECTED | "
              "FLAG_MIRROR_MOVE_AFFECTED | FLAG_PUNCHING_MOVE")
# what collect() can rebuild — the six representable flags only
COLLECTED = ("FLAG_MAKES_CONTACT | FLAG_PROTECT_AFFECTED | "
             "FLAG_MIRROR_MOVE_AFFECTED")


def test_an_unrelated_edit_keeps_a_flag_the_editor_cannot_show():
    merged = _merge()(FIRE_PUNCH, COLLECTED)
    assert "FLAG_PUNCHING_MOVE" in merged


def test_the_representable_flags_still_round_trip():
    merged = _merge()(FIRE_PUNCH, COLLECTED)
    for flag in ("FLAG_MAKES_CONTACT", "FLAG_PROTECT_AFFECTED",
                 "FLAG_MIRROR_MOVE_AFFECTED"):
        assert flag in merged


def test_unticking_a_visible_flag_is_honoured():
    """Preservation must not mean 'flags can never be removed'."""
    merged = _merge()(FIRE_PUNCH,
                      "FLAG_MAKES_CONTACT | FLAG_MIRROR_MOVE_AFFECTED")
    assert "FLAG_PROTECT_AFFECTED" not in merged
    assert "FLAG_PUNCHING_MOVE" in merged          # still preserved


def test_a_hidden_flag_survives_even_with_nothing_ticked():
    assert _merge()("FLAG_SOUND_MOVE", "0") == "FLAG_SOUND_MOVE"


def test_a_move_with_no_flags_stays_zero():
    assert _merge()("0", "0") == "0"
    assert _merge()("", "") == "0"


def test_every_unexposed_engine_flag_is_preserved():
    from ui.constants import MOVE_FLAGS
    exposed = {f for f, _t in MOVE_FLAGS}
    hidden = ["FLAG_PUNCHING_MOVE", "FLAG_BITING_MOVE", "FLAG_SOUND_MOVE",
              "FLAG_BALLISTIC_MOVE", "FLAG_SLICING_MOVE", "FLAG_PULSE_MOVE",
              "FLAG_WIND_MOVE", "FLAG_POWDER_MOVE"]
    assert not (set(hidden) & exposed), "test fixture is stale"
    original = " | ".join(hidden + ["FLAG_MAKES_CONTACT"])
    merged = _merge()(original, "FLAG_MAKES_CONTACT")
    for flag in hidden:
        assert flag in merged, flag


def test_flags_given_as_a_list_are_handled():
    merged = _merge()(["FLAG_SOUND_MOVE", "FLAG_MAKES_CONTACT"],
                      ["FLAG_MAKES_CONTACT"])
    assert "FLAG_SOUND_MOVE" in merged


# ── new-move all-or-nothing ──────────────────────────────────────────────

class _Dlg:
    def __init__(self):
        self.lines = []

    def log_line(self, s):
        self.lines.append(s)


def _stub(tmp_path):
    """A stand-in carrying the real guarded writer and its dependencies."""
    import mainwindow as mw
    MW = mw.MainWindow

    class Stub:
        _note_write = MW._note_write
        _NEW_MOVE_TARGETS = MW._NEW_MOVE_TARGETS
        _write_new_moves_guarded = MW._write_new_moves_guarded

        def __init__(self):
            self.project_info = {"dir": str(tmp_path)}
            self._write_failures = []

    for _label, parts in MW._NEW_MOVE_TARGETS:
        p = tmp_path.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("ORIGINAL %s\n" % parts[-1], encoding="utf-8")
    return Stub()


def _contents(tmp_path):
    import mainwindow as mw
    return {parts[-1]: tmp_path.joinpath(*parts).read_text(encoding="utf-8")
            for _l, parts in mw.MainWindow._NEW_MOVE_TARGETS}


@pytest.mark.parametrize("fail_at", [0, 1, 2, 3])
def test_a_failed_writer_leaves_every_file_as_it_was(tmp_path, fail_at):
    s = _stub(tmp_path)
    before = _contents(tmp_path)
    order = ["_write_new_move_constants", "_write_new_move_names",
             "_write_new_move_descriptions", "_write_new_move_animations"]

    for i, name in enumerate(order):
        if i == fail_at:
            setattr(s, name, lambda *a, **k: -1)
        else:
            def _ok(*a, _p=tmp_path, _n=i, **k):
                # a successful writer really does modify its file
                parts = s._NEW_MOVE_TARGETS[_n][1]
                _p.joinpath(*parts).write_text("CHANGED\n", encoding="utf-8")
                return 1
            setattr(s, name, _ok)

    assert s._write_new_moves_guarded({"MOVE_P": {"id": 1}}, {}, _Dlg()) is False
    assert _contents(tmp_path) == before, "files were left modified"
    assert s._write_failures, "the failure was not reported"
    assert any("still pending" in f for f in s._write_failures)


def test_all_four_succeeding_commits_the_changes(tmp_path):
    s = _stub(tmp_path)
    for i, name in enumerate(["_write_new_move_constants",
                              "_write_new_move_names",
                              "_write_new_move_descriptions",
                              "_write_new_move_animations"]):
        def _ok(*a, _n=i, **k):
            parts = s._NEW_MOVE_TARGETS[_n][1]
            tmp_path.joinpath(*parts).write_text("CHANGED\n", encoding="utf-8")
            return 1
        setattr(s, name, _ok)
    assert s._write_new_moves_guarded({"MOVE_P": {"id": 1}}, {}, _Dlg()) is True
    assert s._write_failures == []
    assert all(v == "CHANGED\n" for v in _contents(tmp_path).values())


def test_a_raising_writer_is_treated_as_a_failure(tmp_path):
    s = _stub(tmp_path)
    before = _contents(tmp_path)

    def _boom(*a, **k):
        raise OSError("disk went away")

    s._write_new_move_constants = _boom
    s._write_new_move_names = lambda *a, **k: 1
    s._write_new_move_descriptions = lambda *a, **k: 1
    s._write_new_move_animations = lambda *a, **k: 1
    assert s._write_new_moves_guarded({"MOVE_P": {"id": 1}}, {}, _Dlg()) is False
    assert _contents(tmp_path) == before


def test_a_failed_restore_is_reported_not_swallowed(tmp_path, monkeypatch):
    s = _stub(tmp_path)
    s._write_new_move_constants = lambda *a, **k: -1
    s._write_new_move_names = lambda *a, **k: 1
    s._write_new_move_descriptions = lambda *a, **k: 1
    s._write_new_move_animations = lambda *a, **k: 1

    real_open = open

    def refuse_write(path, mode="r", *a, **k):
        if "w" in mode and "move" in str(path):
            raise OSError("cannot restore")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", refuse_write)
    s._write_new_moves_guarded({"MOVE_P": {"id": 1}}, {}, _Dlg())
    monkeypatch.undo()
    assert any("undoing the partial write did not fully succeed" in f
               for f in s._write_failures), s._write_failures
