"""The item-rename preview must actually show what will change.

THE DEFECT (confirmed 2026-10-29). `ui/mainwindow.py` called
`RefactorService.rename_item(..., preview_only=True)`, but the parameter is
`preview`. The resulting `TypeError` was caught by a bare `except Exception:
pass` in the preview handler, so `dlg.set_preview(...)` was never reached and
the pane sat empty on every keystroke. The user approved a project-wide rename
with nothing shown.

THE SECOND FAULT, which made the obvious fix dangerous: `rename_item` appended
to `self.pending` UNCONDITIONALLY, and the preview runs on every keystroke.
Correcting only the keyword would have queued one rename per character typed.
`rename_ability` and `rename_trainer` already guard this with `if not preview:`
— that pattern is now applied to `rename_item` too.

DO NOT remove the `preview` parameter, and do not make preview write to disk or
to the pending queue. These tests run against a disposable project tree with
known item references, not against source text.
"""

import json
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402


# ── a tiny throwaway project with known item references ─────────────────────

@pytest.fixture
def project(tmp_path):
    """Build a miniature decomp tree referencing ITEM_OLD in several places."""
    root = tmp_path / "proj"
    (root / "src" / "data").mkdir(parents=True)
    (root / "include" / "constants").mkdir(parents=True)
    (root / "data" / "maps" / "TestMap").mkdir(parents=True)

    (root / "include" / "constants" / "items.h").write_text(
        "#define ITEM_OLD 42\n#define ITEM_KEEP 43\n", encoding="utf-8")
    (root / "src" / "data" / "items.json").write_text(json.dumps({"items": [
        {"itemId": "ITEM_OLD", "english": "Old Thing", "price": 10},
        {"itemId": "ITEM_KEEP", "english": "Keep Me", "price": 20},
    ]}, indent=2), encoding="utf-8")
    (root / "src" / "shop.c").write_text(
        "static const u16 sMart[] = { ITEM_OLD, ITEM_KEEP };\n",
        encoding="utf-8")
    (root / "data" / "maps" / "TestMap" / "scripts.inc").write_text(
        "\tgiveitem ITEM_OLD, 1\n\tgiveitem ITEM_KEEP, 1\n", encoding="utf-8")
    # a file that must NOT be touched: different constant, similar prefix
    (root / "src" / "unrelated.c").write_text(
        "u16 x = ITEM_OLDER;\n", encoding="utf-8")
    return root


@pytest.fixture
def svc(project):
    from core.refactor_service import RefactorService

    class _Util:
        def repo_root(self):
            return str(project)

    s = RefactorService.__new__(RefactorService)
    s.util = _Util()
    s.pending = []
    return s


def _snapshot(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


# ── 1. the signature the caller must use ────────────────────────────────────

def test_mainwindow_uses_the_parameter_the_service_declares():
    """THE DEFECT: `preview_only=` is not a parameter of rename_item."""
    import inspect
    import re
    from core.refactor_service import RefactorService
    sig = inspect.signature(RefactorService.rename_item)
    mw = open(os.path.join(_ROOT, "ui", "mainwindow.py"),
              encoding="utf-8", errors="replace").read()
    for call in re.findall(r"\.rename_item\(([^)]*)\)", mw):
        for kw in re.findall(r"(\w+)\s*=", call):
            assert kw in sig.parameters, (
                f"mainwindow calls rename_item with {kw!r}, which is not a "
                f"parameter ({list(sig.parameters)}). The TypeError is "
                f"swallowed, so the preview silently never appears."
            )


def test_the_preview_parameter_still_exists():
    """It is honoured now, not deleted."""
    import inspect
    from core.refactor_service import RefactorService
    assert "preview" in inspect.signature(
        RefactorService.rename_item).parameters


# ── 2. previewing must not change anything ──────────────────────────────────

def test_preview_does_not_modify_any_file(svc, project):
    before = _snapshot(project)
    svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="", preview=True)
    assert _snapshot(project) == before, "preview wrote to disk"


def test_preview_does_not_queue_a_rename(svc):
    """A keystroke must not queue work. rename_ability/trainer already guard
    this; rename_item did not, so correcting the keyword alone would have
    queued one rename per character typed."""
    for _ in range(5):
        svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="", preview=True)
    assert svc.pending == [], (
        f"previewing queued {len(svc.pending)} rename(s)")


# ── 3. the preview must actually say something ──────────────────────────────

def test_preview_reports_the_files_that_would_change(svc):
    previews = svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="",
                               preview=True)
    assert previews, "the preview must not be empty — that was the defect"
    files = {os.path.basename(str(p[0])) for p in previews}
    assert "shop.c" in files, files
    assert "scripts.inc" in files, files


def test_preview_entries_render_as_the_dialog_expects(svc):
    """set_preview formats 4-tuples as 'file:line  old -> new'."""
    previews = svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="",
                               preview=True)
    for p in previews:
        assert len(p) == 4, p
        line = "{0}:{1}  {2}  ->  {3}".format(*p)
        assert "ITEM_" in line


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN DEFECT, confirmed 2026-10-29, deliberately NOT fixed here. "
    "_search_and_replace matches constants as SUBSTRINGS with no word "
    "boundary, so renaming ITEM_OLD also rewrites ITEM_OLDER. This is not "
    "cosmetic: the same function performs the real rename (apply_pending -> "
    "_search_and_replace(preview_only=False)), and the user's project really "
    "contains ITEM_TEA as a prefix of ITEM_TEACHY_TV — renaming ITEM_TEA to "
    "ITEM_CHAI produces the undefined constant ITEM_CHAICHY_TV and breaks the "
    "build. Proved in scratchpad/prove_prefix_bug.py. "
    "_search_and_replace is shared by EVERY rename type (species, moves, "
    "abilities, trainers, items) and already carries deliberate special-case "
    "protection for species/MAPSEC collisions, so adding word boundaries is a "
    "change to a shared system and needs its own authorisation. "
    "When it is fixed, this test starts passing and strict=True will say so."))
def test_preview_does_not_claim_unrelated_files(svc):
    """ITEM_OLDER must not be reported — or rewritten — as an ITEM_OLD hit."""
    previews = svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="",
                               preview=True)
    bad = [p for p in previews if os.path.basename(str(p[0])) == "unrelated.c"]
    assert not bad, f"unrelated.c was reported as affected: {bad}"


# ── 4. cancel vs confirm ────────────────────────────────────────────────────

def test_cancelling_after_a_preview_leaves_everything_unchanged(svc, project):
    """Several previews, then the user cancels: nothing written, nothing queued."""
    before = _snapshot(project)
    for name in ("ITEM_A", "ITEM_AB", "ITEM_ABC"):
        svc.rename_item("ITEM_OLD", name, display_name="", preview=True)
    # user presses Cancel -> no confirming call is made
    assert _snapshot(project) == before
    assert svc.pending == []


def test_confirming_queues_exactly_one_rename(svc):
    svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="", preview=True)
    svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="")
    ops = [p for p in svc.pending if p.get("op") == "rename_item"]
    assert len(ops) == 1, f"expected one queued rename, got {svc.pending}"
    assert ops[0]["old"] == "ITEM_OLD" and ops[0]["new"] == "ITEM_NEW"


def test_unrelated_constants_survive_a_confirmed_rename(svc, project, tmp_path):
    """Actually APPLY the rename — queueing it proves nothing.

    `rename_item` only appends to `pending`; Save runs `apply_pending`, which
    is where the files are really rewritten. An earlier version of this test
    asserted on the queueing call alone and passed without a single byte
    changing.

    ITEM_OLDER is NOT asserted here: it is currently corrupted by the
    substring match, which `test_preview_does_not_claim_unrelated_files`
    records as a known defect.
    """
    svc.history = {}
    svc.history_path = str(tmp_path / "history.json")
    svc._logger = None
    svc.rename_item("ITEM_OLD", "ITEM_NEW", display_name="")
    svc.apply_pending()

    shop = (project / "src" / "shop.c").read_text(encoding="utf-8")
    assert "ITEM_NEW" in shop, "the rename did not reach the file"
    assert "ITEM_KEEP" in shop, "an unrelated constant was rewritten"

    inc = (project / "data" / "maps" / "TestMap" / "scripts.inc").read_text(
        encoding="utf-8")
    assert "giveitem ITEM_NEW" in inc, "map scripts were not updated"
    assert "giveitem ITEM_KEEP" in inc, "an unrelated giveitem was rewritten"

    # NOTE: src/data/items.json is deliberately NOT RefactorService's job.
    # _search_and_replace excludes it (see its scan_spec comment) and
    # _rename_in_json expects a constant-keyed dict, while items.json is
    # {"items": [...]}. In the application the editor renames its own record
    # via ItemsTabWidget.rename_item_key() and save_items_table() writes it
    # out. Asserting on items.json here would be testing the wrong component.


# ── 5. errors must be visible ───────────────────────────────────────────────

def test_the_preview_handler_no_longer_discards_errors_silently():
    """A bare `except: pass` is what hid the TypeError for months."""
    import re
    mw = open(os.path.join(_ROOT, "ui", "mainwindow.py"),
              encoding="utf-8", errors="replace").read()
    i = mw.index("item rename preview failed") if \
        "item rename preview failed" in mw else -1
    assert i > 0, (
        "the item rename preview handler must log its failure rather than "
        "discard it"
    )
    # and it must tell the user, where the preview would have been
    window = mw[i - 1200:i + 600]
    assert "preview unavailable" in window, (
        "a failed preview must say so in the preview pane")
