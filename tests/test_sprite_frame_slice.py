"""Tests for mon sprite-sheet frame slicing (core/sprite_render).

A front/back battle sheet is 64px wide and 64*N tall (N stacked 64x64 frames).
A still preview must show frame 0, not the whole sheet drawn as one tall sprite
(the Deoxys "stacked forms" bug). 1-frame mons must be returned unchanged.

The slice helpers are duck-typed on the Qt size/copy interface (isNull / width /
height / copy(x,y,w,h)), so these tests drive them with QImage — which is
headless-safe under pytest (bare QPixmap needs a paint device the offscreen
platform won't init in-process). The runtime path passes QPixmap, which has the
identical copy/size semantics.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtGui import QImage
    _QT = True
except Exception:
    _QT = False


def _sheet(w, h):
    im = QImage(w, h, QImage.Format.Format_ARGB32)
    im.fill(0xFF0A141E)
    return im


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_frame_count():
    from core.sprite_render import mon_sheet_frame_count
    assert mon_sheet_frame_count(_sheet(64, 64)) == 1
    assert mon_sheet_frame_count(_sheet(64, 128)) == 2      # Deoxys-shaped
    assert mon_sheet_frame_count(_sheet(64, 192)) == 3
    assert mon_sheet_frame_count(None) == 0
    assert mon_sheet_frame_count(QImage()) == 0             # null


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_single_frame_returned_unchanged():
    from core.sprite_render import mon_sheet_frame
    out = mon_sheet_frame(_sheet(64, 64), 0)
    assert (out.width(), out.height()) == (64, 64)


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_multi_frame_sheet_sliced_to_square():
    from core.sprite_render import mon_sheet_frame
    pm = _sheet(64, 128)
    assert (mon_sheet_frame(pm, 0).width(), mon_sheet_frame(pm, 0).height()) == (64, 64)
    assert (mon_sheet_frame(pm, 1).width(), mon_sheet_frame(pm, 1).height()) == (64, 64)
    # index clamps to the available frame range
    assert (mon_sheet_frame(pm, 9).width(), mon_sheet_frame(pm, 9).height()) == (64, 64)


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_frame0_and_frame1_differ_in_content():
    """Slicing must actually pick different rows, not just resize."""
    from core.sprite_render import mon_sheet_frame
    im = QImage(64, 128, QImage.Format.Format_ARGB32)
    im.fill(0)
    for x in range(64):
        im.setPixel(x, 0, 0xFFFF0000)      # red row in frame 0
        im.setPixel(x, 64, 0xFF00FF00)     # green row in frame 1
    f0 = mon_sheet_frame(im, 0)
    f1 = mon_sheet_frame(im, 1)
    assert f0.pixel(0, 0) == 0xFFFF0000
    assert f1.pixel(0, 0) == 0xFF00FF00


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_real_deoxys_front_sheet_is_two_frames():
    from core.sprite_render import mon_sheet_frame, mon_sheet_frame_count
    p = os.path.join(_ROOT, "pokefirered", "graphics", "pokemon", "deoxys", "front.png")
    if not os.path.exists(p):
        pytest.skip("deoxys front.png not present")
    im = QImage(p)
    assert (im.width(), im.height()) == (64, 128)
    assert mon_sheet_frame_count(im) == 2
    f0 = mon_sheet_frame(im, 0)
    assert (f0.width(), f0.height()) == (64, 64)
