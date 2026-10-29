"""Regression guard: export_indexed_png must refuse what the palette cannot describe.

THE BUG (found 2026-10-29, fixed 2026-10-29). `export_indexed_png` validated only
the image's declared FORMAT (`Format_Indexed8`) and never the pixel INDICES. So a
sprite whose indices ran past the end of the supplied palette was written out
anyway. The route in was the Graphics tab's palette bake
(`ui/graphics_tab_widget.py::flush_to_disk`), which on a non-indexed sprite did
`convertToFormat(Format_Indexed8)` — Qt QUANTISES, inventing a table of up to 256
colours — and then handed that image to this function with a 16-colour palette.
Measured: a 32-bit source came through with max index 169 and 58 of 64 pixels
pointing past the palette.

Why it mattered more than it looks: gbagfx ACCEPTS an 8bpp indexed PNG and reduces
it to 4bpp with `index % 16` (`pokefirered/tools/gbagfx/convert_png.c:48-76`). A
non-indexed PNG on its own makes the build FAIL loudly ("unsupported color type",
`convert_png.c:90-91`) — but after a PorySuite palette save it became indexed, so
the build SUCCEEDED and shipped a garbled sprite. The repair turns that silent
corruption back into a refusal.

THE RULE (do not simplify away):
  * Validate against the SUPPLIED PALETTE's length, never a hardcoded 16. The
    GBA Image Indexer legitimately writes 256-colour images.
  * A refusal must NOT touch the file on disk.
  * The return type stays `bool` — 22 call sites across 8 files treat it as one.
    The reason is surfaced separately via `last_export_error()`.
  * The byte-equality guard (skip the write when bytes match) must stay: it is
    what stops the Graphics tab producing phantom diffs in the user's game repo.
  * `_rebuild_color_table`'s `max(len(ct), img.colorCount())` padding must stay:
    forcing 16 would change every sprite's native bit depth.

If you touch `export_indexed_png` or `_rebuild_color_table`, keep this green.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QImage, QColor
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")


def _app():
    return QApplication.instance() or QApplication([])


def _utils():
    from core import gba_image_utils
    return gba_image_utils


PAL16 = [(8 * i, 8 * i, 8 * i) for i in range(16)]
PAL256 = [(i, (i * 7) % 256, (i * 13) % 256) for i in range(256)]


def _indexed(w, h, table_len, index_fn):
    """Indexed8 image with `table_len` colour-table entries."""
    im = QImage(w, h, QImage.Format.Format_Indexed8)
    im.setColorCount(table_len)
    for i in range(table_len):
        r, g, b = (i * 3) % 256, (i * 5) % 256, (i * 7) % 256
        im.setColor(i, QColor(r, g, b).rgb())
    for y in range(h):
        for x in range(w):
            im.setPixel(x, y, index_fn(x, y))
    return im


# ── accepted cases ───────────────────────────────────────────────────────────

def test_valid_16_colour_sprite_is_written():
    _app()
    u = _utils()
    im = _indexed(8, 8, 16, lambda x, y: (x + y) % 16)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_valid16.png")
    try:
        assert u.export_indexed_png(im, PAL16, out) is True
        assert os.path.isfile(out)
        back = QImage(out)
        assert back.format() == QImage.Format.Format_Indexed8
        assert max(back.pixelIndex(x, y) for y in range(8)
                   for x in range(8)) <= 15
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_oversized_table_but_in_range_indices_is_written():
    """13 of the user's sprites have a 256-entry table with indices <= 15.
    Those are fine and must keep working."""
    _app()
    u = _utils()
    im = _indexed(8, 8, 256, lambda x, y: (x + y) % 16)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_bigtable.png")
    try:
        assert u.export_indexed_png(im, PAL16, out) is True
        assert os.path.isfile(out)
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_256_colour_palette_with_high_indices_is_written():
    """The Image Indexer legitimately produces 8bpp output. Validation must be
    against the PALETTE length, not a hardcoded 16."""
    _app()
    u = _utils()
    im = _indexed(8, 8, 256, lambda x, y: (x * 8 + y) % 256)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_pal256.png")
    try:
        assert u.export_indexed_png(im, PAL256, out) is True
        assert os.path.isfile(out)
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_transparent_index_zero_is_preserved():
    _app()
    u = _utils()
    im = _indexed(4, 4, 16, lambda x, y: 0 if (x == 0) else 5)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_alpha.png")
    try:
        assert u.export_indexed_png(im, PAL16, out, transparent_index=0) is True
        back = QImage(out)
        assert (back.color(0) >> 24) & 0xFF == 0, "slot 0 must stay transparent"
        assert (back.color(5) >> 24) & 0xFF == 255, "art slots stay opaque"
    finally:
        if os.path.isfile(out):
            os.remove(out)


# ── refused cases — the repair ────────────────────────────────────────────────

def test_index_past_palette_is_REFUSED():
    """Shamanblin's shape: a 256-entry table whose pixels reach index 16 while
    the palette holds 16 colours. Index 16 has no colour in that palette."""
    _app()
    u = _utils()
    im = _indexed(8, 8, 256, lambda x, y: 16 if (x == 0 and y == 0) else 3)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_oob.png")
    assert not os.path.isfile(out)
    try:
        assert u.export_indexed_png(im, PAL16, out) is False
        assert not os.path.isfile(out), "a refusal must not create the file"
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_quantised_rgb_image_is_REFUSED():
    """The real corruption route: a 32-bit sprite converted with
    convertToFormat(Indexed8) gains a ~256-colour table and high indices."""
    _app()
    u = _utils()
    rgb = QImage(8, 8, QImage.Format.Format_ARGB32)
    for y in range(8):
        for x in range(8):
            rgb.setPixelColor(x, y, QColor(x * 31, y * 31, (x * y) % 256))
    conv = rgb.convertToFormat(QImage.Format.Format_Indexed8)
    assert conv.format() == QImage.Format.Format_Indexed8
    assert max(conv.pixelIndex(x, y) for y in range(8) for x in range(8)) > 15
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_quant.png")
    try:
        assert u.export_indexed_png(conv, PAL16, out) is False
        assert not os.path.isfile(out)
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_refusal_does_NOT_overwrite_an_existing_good_file():
    """Requirement: validation failure must never damage the artwork on disk."""
    _app()
    u = _utils()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_keep.png")
    good = _indexed(8, 8, 16, lambda x, y: (x + y) % 16)
    try:
        assert u.export_indexed_png(good, PAL16, out) is True
        with open(out, "rb") as f:
            before = f.read()
        bad = _indexed(8, 8, 256, lambda x, y: 200)
        assert u.export_indexed_png(bad, PAL16, out) is False
        with open(out, "rb") as f:
            after = f.read()
        assert after == before, "the existing sprite must be byte-identical"
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_non_indexed_input_still_refused():
    """Pre-existing behaviour that must not regress."""
    _app()
    u = _utils()
    rgb = QImage(4, 4, QImage.Format.Format_ARGB32)
    rgb.fill(0xFF112233)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_rgb.png")
    try:
        assert u.export_indexed_png(rgb, PAL16, out) is False
        assert not os.path.isfile(out)
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_null_and_none_refused():
    _app()
    u = _utils()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_null.png")
    assert u.export_indexed_png(None, PAL16, out) is False
    assert u.export_indexed_png(QImage(), PAL16, out) is False
    assert not os.path.isfile(out)


# ── the reason, for callers that want to show it ─────────────────────────────

def test_refusal_reports_an_understandable_reason():
    _app()
    u = _utils()
    im = _indexed(8, 8, 256, lambda x, y: 16 if (x == 0 and y == 0) else 3)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_reason.png")
    try:
        assert u.export_indexed_png(im, PAL16, out) is False
        reason = u.last_export_error()
        assert reason, "a refusal must leave a reason"
        low = reason.lower()
        assert "16" in reason, "the reason should quote the numbers involved"
        assert any(w in low for w in ("colour", "color", "palette", "index")), \
            f"reason should be understandable, got: {reason!r}"
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_success_clears_the_reason():
    _app()
    u = _utils()
    im = _indexed(8, 8, 16, lambda x, y: (x + y) % 16)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_clear.png")
    try:
        bad = _indexed(8, 8, 256, lambda x, y: 99)
        u.export_indexed_png(bad, PAL16, out)
        assert u.last_export_error()
        assert u.export_indexed_png(im, PAL16, out) is True
        assert not u.last_export_error(), "a success must clear the reason"
    finally:
        if os.path.isfile(out):
            os.remove(out)


# ── protections that must survive the repair ─────────────────────────────────

def test_byte_equality_guard_still_skips_an_unchanged_write():
    _app()
    u = _utils()
    im = _indexed(8, 8, 16, lambda x, y: (x + y) % 16)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_tmp_bytes.png")
    try:
        assert u.export_indexed_png(im, PAL16, out) is True
        mtime1 = os.path.getmtime(out)
        with open(out, "rb") as f:
            first = f.read()
        assert u.export_indexed_png(im, PAL16, out) is True
        with open(out, "rb") as f:
            second = f.read()
        assert first == second
        assert os.path.getmtime(out) == mtime1, \
            "an identical re-bake must not rewrite the file"
    finally:
        if os.path.isfile(out):
            os.remove(out)


def test_rebuild_color_table_still_preserves_native_depth():
    _app()
    u = _utils()
    im16 = _indexed(4, 4, 16, lambda x, y: (x + y) % 16)
    res = u._rebuild_color_table(im16, PAL16, 0)
    assert res.colorCount() == 16, "a 16-colour sprite must not bloat to 256"
    im256 = _indexed(4, 4, 256, lambda x, y: (x + y) % 16)
    res2 = u._rebuild_color_table(im256, PAL16, 0)
    assert res2.colorCount() == 256, "an 8bpp sprite keeps its table size"
