"""Round-trip tests for the Palette Editor's .pal / .gbapal read+write.

These exercise the pure file-I/O staticmethods directly (no widget construction)
— the general palette-file mode must round-trip GBA-valid colours unchanged (no
phantom diffs) and keep saved files on the GBA's 5-bit colour grid.
"""

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

import pytest  # noqa: E402

try:
    import PyQt6  # noqa: F401  (palette_baker_tab imports PyQt6 at module load)
    _QT = True
except Exception:
    _QT = False


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_pal_roundtrip_identity_for_gba_valid_colours():
    from ui.palette_baker_tab import PaletteBakerTab
    cols = [(8, 16, 24), (248, 128, 0), (40, 80, 120)] + [(0, 0, 0)] * 13
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.pal")
        ok, err = PaletteBakerTab._write_palette_file(p, cols)
        assert ok, err
        assert PaletteBakerTab._read_palette_file(p)[:3] == cols[:3]   # no phantom diff


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_pal_save_snaps_to_gba_grid():
    from ui.palette_baker_tab import PaletteBakerTab
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.pal")
        PaletteBakerTab._write_palette_file(p, [(255, 0, 0)] + [(0, 0, 0)] * 15)
        assert PaletteBakerTab._read_palette_file(p)[0] == (248, 0, 0)   # 255 -> GBA grid


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_gbapal_roundtrip_idempotent():
    from ui.palette_baker_tab import PaletteBakerTab
    with tempfile.TemporaryDirectory() as d:
        g = os.path.join(d, "t.gbapal")
        PaletteBakerTab._write_palette_file(
            g, [(255, 0, 0), (40, 80, 120)] + [(0, 0, 0)] * 14)
        b1 = PaletteBakerTab._read_palette_file(g)
        PaletteBakerTab._write_palette_file(g, b1)
        assert PaletteBakerTab._read_palette_file(g) == b1   # stable on the 5-bit grid
