"""Regression test for the items.json data-loss bug.

PorySuite once collapsed items.json into a dict keyed by itemId on load. The 68
identical ITEM_NONE filler slots (and their array positions) can't be represented
as dict keys, so a 376-item file was silently re-saved as 309 — shifting every
later item onto wrong data and crashing the bag. These tests pin the lossless
round-trip and the hard pre-write count guard.
"""
import os
import sys
import json
import types
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# The app runs with both the project root (for ``core`` package imports) and
# ``core/`` (for top-level imports like ``local_env`` / ``app_info``) on the path.
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Stub minimal PyQt6 so core.pokemon_data imports headlessly.
_qt = types.ModuleType("PyQt6")
_qtc = types.ModuleType("PyQt6.QtCore")
_qtg = types.ModuleType("PyQt6.QtGui")
_qtc.pyqtSignal = lambda *a, **k: None
class _Blk:
    def __init__(self, *_): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
_qtc.QSignalBlocker = _Blk
_qtg.QImage = type("QImage", (), {})
_qtg.QPixmap = type("QPixmap", (), {})
_qt.QtCore = _qtc
_qt.QtGui = _qtg
sys.modules.setdefault("PyQt6", _qt)
sys.modules.setdefault("PyQt6.QtCore", _qtc)
sys.modules.setdefault("PyQt6.QtGui", _qtg)

from core.pokemon_data import PokemonItems

_FILLER = {
    "english": "????????", "itemId": "ITEM_NONE", "price": 0,
    "holdEffect": "HOLD_EFFECT_NONE", "holdEffectParam": 0,
    "description_english": "?????", "importance": 0, "registrability": 0,
    "pocket": "POCKET_ITEMS", "type": "ITEM_TYPE_BAG_MENU",
    "fieldUseFunc": "FieldUseFunc_OakStopsYou", "battleUsage": 0,
    "battleUseFunc": "NULL", "secondaryId": 0,
}


def _make_items(n_named=308, n_filler=68):
    """Synthetic items.json mimicking the real shape: named items interleaved
    with identical ITEM_NONE filler slots. Total = 1 + n_named + (n_filler-1)."""
    items = [dict(_FILLER)]            # index 0 is ITEM_NONE, like vanilla
    left = n_filler - 1
    for i in range(n_named):
        items.append({
            "english": f"ITEM {i}", "itemId": f"ITEM_TEST_{i:03d}",
            "price": i * 10, "holdEffect": "HOLD_EFFECT_NONE",
            "holdEffectParam": 0, "description_english": f"desc {i}",
            "importance": 0, "registrability": 0, "pocket": "POCKET_ITEMS",
            "type": "ITEM_TYPE_BAG_MENU", "fieldUseFunc": "NULL",
            "battleUsage": 0, "battleUseFunc": "NULL", "secondaryId": 0,
        })
        if left > 0 and i % 5 == 4:    # scatter filler through the array
            items.append(dict(_FILLER))
            left -= 1
    while left > 0:
        items.append(dict(_FILLER))
        left -= 1
    return {"items": items}


class _LU:
    def __init__(self, root): self._root = root
    def repo_root(self): return self._root


class ItemsRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "src", "data"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "include", "constants"),
                    exist_ok=True)
        with open(os.path.join(self.root, "include", "constants", "items.h"),
                  "w", encoding="utf-8") as f:
            f.write("#define ITEM_NONE 0\n#define ITEMS_COUNT 376\n")
        self.json_path = os.path.join(self.root, "src", "data", "items.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _model(self, data, write_disk=False):
        obj = PokemonItems.__new__(PokemonItems)
        obj.project_info = {"dir": self.root}
        obj.DATA_FILE = "items.json"
        obj.data = json.loads(json.dumps(data))
        obj._items_full_order = None
        obj.original_data = None
        obj.pending_changes = False
        obj.local_util = _LU(self.root)
        obj._synchronise_header_target = lambda: None   # skip extractor/header
        if write_disk:
            with open(self.json_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=True))
        return obj

    def _read(self):
        with open(self.json_path, encoding="utf-8") as f:
            return json.load(f)["items"]

    def test_lossless_roundtrip_no_edit(self):
        data = _make_items()
        self.assertEqual(len(data["items"]), 376)
        obj = self._model(data)
        obj.save()
        out = self._read()
        self.assertEqual(len(out), 376, "save must preserve every item")
        self.assertEqual(sum(1 for e in out if e["itemId"] == "ITEM_NONE"), 68)
        # high-index named item survives intact
        hi = [e for e in out if e["itemId"] == "ITEM_TEST_307"]
        self.assertEqual(len(hi), 1)
        orig_hi = [e for e in data["items"] if e["itemId"] == "ITEM_TEST_307"][0]
        self.assertEqual(hi[0], orig_hi)
        # whole list byte-identical on a no-edit round-trip (order + fields)
        self.assertEqual(out, data["items"], "order + fields must be identical")

    def test_edit_preserves_all_items(self):
        data = _make_items()
        obj = self._model(data)
        obj._ensure_map()                      # collapse to editing dict
        obj.data["ITEM_TEST_307"]["price"] = 99999
        obj.save()
        out = self._read()
        self.assertEqual(len(out), 376)
        edited = [e for e in out if e["itemId"] == "ITEM_TEST_307"][0]
        self.assertEqual(edited["price"], 99999)
        self.assertEqual(sum(1 for e in out if e["itemId"] == "ITEM_NONE"), 68)

    def test_guard_blocks_shrink(self):
        """If reconstruction can't recover the full list, the write is refused
        and the good on-disk file is left intact (plus a backup)."""
        good = _make_items()
        obj = self._model(good, write_disk=True)
        # Simulate the failure mode: collapsed 309-key dict, no captured order.
        collapsed = {}
        for e in good["items"]:
            collapsed[e["itemId"]] = {k: v for k, v in e.items()
                                      if k != "itemId"}
        obj.data = collapsed
        obj._items_full_order = None
        obj.original_data = None
        obj.save()                              # must ABORT
        out = self._read()
        self.assertEqual(len(out), 376, "guard must not let a short list reach disk")
        self.assertTrue(os.path.isfile(self.json_path + ".prewrite_backup"))


if __name__ == "__main__":
    unittest.main()
