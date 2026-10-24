"""
Reusable EVENTide widgets — searchable constant pickers, map selectors, etc.

These are building blocks used by the command widgets in the Event Editor tab.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit,
    QListWidget, QListWidgetItem, QDialog, QDialogButtonBox,
    QPushButton, QLabel, QSpinBox, QFormLayout, QCompleter,
)
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QPixmap, QImage


class ConstantPicker(QComboBox):
    """Searchable combo box for selecting from a list of constants.

    Supports type-ahead filtering. If ``show_pretty`` is True, items display
    as ``Poke Ball  (ITEM_POKE_BALL)`` but the value returned by
    ``selected_constant()`` is always the raw constant.

    Usage::

        picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        picker.set_constant('ITEM_POKE_BALL')
        raw = picker.selected_constant()  # 'ITEM_POKE_BALL'
    """

    def __init__(self, constants: list[str], prefix: str = '',
                 show_pretty: bool = True, keep_order: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(20)
        self._raw_constants = list(constants)
        self._prefix = prefix
        self._show_pretty = show_pretty

        # Build display items
        self._display_to_raw: dict[str, str] = {}
        display_items: list[str] = []
        for c in self._raw_constants:
            if show_pretty and prefix:
                pretty = c[len(prefix):].replace('_', ' ').title() if c.startswith(prefix) else c
                display = f'{pretty}  ({c})'
            else:
                display = c
            self._display_to_raw[display] = c
            display_items.append(display)

        # Sort alphabetically by the pretty name portion (case-insensitive)
        # so dropdowns are easy to scan — "None (TRAINER_NONE)" first when
        # present, then all names A→Z. Skipped when keep_order is set: some
        # lists (MOVEMENT_TYPE_*) have a meaningful header/Porymap order with
        # the project's custom entries grouped at the bottom, and must match
        # the plain header-order dropdowns elsewhere (e.g. Event Properties).
        if not keep_order:
            def _sort_key(d: str) -> tuple:
                key = d.split('  (')[0].lower()
                # Keep "none"/"__none__" at top by flagging it first
                is_none = ('_none' in d.lower()) or key in ('none', '')
                return (0 if is_none else 1, key)
            display_items.sort(key=_sort_key)

        self.addItems(display_items)

        # Enable type-ahead filtering
        completer = QCompleter(display_items, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(20)
        self.setCompleter(completer)

    def selected_constant(self) -> str:
        """Return the raw constant name (e.g. ``ITEM_POKE_BALL``)."""
        text = self.currentText()
        # Try display→raw lookup first
        raw = self._display_to_raw.get(text)
        if raw:
            return raw
        # Maybe the user typed a raw constant directly
        if text in self._raw_constants:
            return text
        # Or typed something custom — return as-is
        return text.strip()

    def set_constant(self, raw: str) -> None:
        """Set the picker to the given raw constant."""
        # Find display text for this raw constant
        for display, r in self._display_to_raw.items():
            if r == raw:
                idx = self.findText(display)
                if idx >= 0:
                    self.setCurrentIndex(idx)
                    return
        # Not found in list — set as edit text
        self.setEditText(raw)


class ValuePicker(QComboBox):
    """Searchable picker for "what value goes here" — a number OR a constant.

    Variables in a script hold plain numbers, but the number almost always
    MEANS something: an item, a species, a step in a cutscene, an entry in
    whatever table the project has invented. Offering a bare
    ``0 1 2 3 4 5 TRUE FALSE`` list, which is what this replaced, meant the
    only way to put a real constant in was to know its exact spelling and
    type it — which is not something somebody who does not write code can do.

    So the list is built from the PROJECT: every enum-style constant it
    defines, whatever they are called, with the prefix stripped for reading.
    Nothing here knows the name of a single game constant, so a project that
    invents its own shows them like any other.

    ``selected_value()`` gives back the raw constant, or the number as typed.
    """

    #: Small numbers come first — still the common case.
    _NUMBERS = [str(n) for n in range(0, 11)]
    _BOOLEANS = ["TRUE", "FALSE"]

    def __init__(self, choices: list[str] | None = None, value: str = "0",
                 parent: QWidget | None = None, *,
                 include_numbers: bool = True):
        """``include_numbers`` adds the small numbers and TRUE/FALSE at the
        top of the list. Turn it off where a bare number would be
        meaningless, such as a field that names a block of text."""
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(20)
        # Deliberately NOT width-capped. The old value box was 100px, which
        # truncated its own contents — you could not read what you had picked.
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)

        self._display_to_raw: dict[str, str] = {}
        items: list[str] = []
        if include_numbers:
            for n in self._NUMBERS + self._BOOLEANS:
                self._display_to_raw[n] = n
                items.append(n)
        for const in (choices or []):
            pretty = _prettify_constant(const)
            display = f"{pretty}  ({const})" if pretty != const else const
            if display not in self._display_to_raw:
                self._display_to_raw[display] = const
                items.append(display)

        self.addItems(items)
        completer = QCompleter(items, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(20)
        self.setCompleter(completer)
        self.set_value(value)

    def set_value(self, value) -> None:
        value = "" if value is None else str(value).strip()
        if not value:
            self.setCurrentIndex(0)
            return
        for display, raw in self._display_to_raw.items():
            if raw == value:
                idx = self.findText(display)
                if idx >= 0:
                    self.setCurrentIndex(idx)
                    return
        self.setEditText(value)

    def selected_value(self) -> str:
        text = self.currentText().strip()
        return self._display_to_raw.get(text, text)


def _prettify_constant(const: str) -> str:
    """``ITEM_GREAT_BALL`` -> ``Great Ball``; ``SOME_THING`` -> ``Some Thing``.

    Splits on the FIRST underscore group that looks like a prefix, so it works
    for constant families this code has never heard of. A constant with no
    underscore is returned unchanged rather than mangled.
    """
    if "_" not in const:
        return const
    head, _, tail = const.partition("_")
    body = tail if tail else head
    return body.replace("_", " ").title()


class MapPicker(QWidget):
    """Widget for selecting a map destination — combo + X/Y spinners.

    Combines a searchable map name combo with coordinate spinners, since
    map selection and coordinates nearly always go together (warps, teleports).
    """

    changed = pyqtSignal()

    def __init__(self, map_names: list[str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.map_combo = QComboBox()
        self.map_combo.setEditable(True)
        self.map_combo.setMinimumWidth(180)
        if map_names:
            self.map_combo.addItems(map_names)
        completer = QCompleter(map_names or [], self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.map_combo.setCompleter(completer)
        layout.addWidget(self.map_combo, 1)

        layout.addWidget(QLabel('X:'))
        self.x_spin = QSpinBox()
        self.x_spin.setRange(0, 999)
        layout.addWidget(self.x_spin)

        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox()
        self.y_spin.setRange(0, 999)
        layout.addWidget(self.y_spin)

        self.map_combo.currentTextChanged.connect(lambda: self.changed.emit())
        self.x_spin.valueChanged.connect(lambda: self.changed.emit())
        self.y_spin.valueChanged.connect(lambda: self.changed.emit())

    def set_values(self, map_name: str, x: int = 0, y: int = 0):
        idx = self.map_combo.findText(map_name)
        if idx >= 0:
            self.map_combo.setCurrentIndex(idx)
        else:
            self.map_combo.setEditText(map_name)
        self.x_spin.setValue(x)
        self.y_spin.setValue(y)

    def map_name(self) -> str:
        return self.map_combo.currentText().strip()

    def x(self) -> int:
        return self.x_spin.value()

    def y(self) -> int:
        return self.y_spin.value()


def overworld_frame_width(sheet_w: int, h: int) -> int:
    """How wide one frame is on an overworld sprite sheet.

    Sheets are horizontal strips and carry no frame size anywhere, so it has
    to be inferred from the proportions:

    * square sheet -> the whole thing is one frame (16x16, 32x32, 64x64)
    * a sheet that divides into 1-4 frames as wide as it is tall -> those
      (64x32 is two 32px frames, not four 16px ones)
    * everything else -> 16px frames, which covers 48x32, 144x32, 160x32

    Shared by the animated preview and the sprite gallery so the two cannot
    disagree about where a frame ends.
    """
    if sheet_w == h:
        return sheet_w
    if h >= 32 and sheet_w % h == 0 and (sheet_w // h) in (1, 2, 3, 4):
        return h
    return 16


class SpritePreview(QLabel):
    """Animated overworld sprite preview for the Event Editor.

    Cycles through all four walk directions (down → right → up → left)
    with the proper GBA walk animation per direction (stand → step1 →
    stand → step2).  Right-facing frames are the left-facing frames
    mirrored horizontally, matching how the GBA engine does it.

    Sprite sheet layouts (all horizontal strips):
      - People: 16×32 per frame, 9 frames
        [0] down-stand  [1] up-stand   [2] left-stand
        [3] down-walk1  [4] down-walk2
        [5] up-walk1    [6] up-walk2
        [7] left-walk1  [8] left-walk2
        Right = left mirrored (game engine handles this)
      - Pokemon overworld: 16×16 or 32×32, 1-3 frames
      - Misc objects: varies (16×16 to 64×64, 1-4 frames)

    Scaled 3× nearest-neighbor to keep pixel art crisp.  GBA palette
    index 0 (top-left pixel color) is made transparent manually since
    Qt doesn't reliably handle tRNS in 4-bit indexed PNGs.
    """

    _SCALE = 2        # 2× nearest-neighbor zoom
    _FRAME_MS = 150   # ~GBA speed (8 ticks @ 60fps ≈ 133ms)
    #: Nothing may grow past this. Sheets run from 16x16 up to 64x64, and at
    #: a fixed zoom the big ones would set the height of the whole row.
    _MAX_BOX = (64, 64)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(70, 72)
        self.setObjectName('spritePreview')
        self.setStyleSheet(
            '#spritePreview { background: palette(base); '
            'border: 1px solid palette(mid); border-radius: 4px; }')
        self.setText('No sprite')

        self._frames: list[QPixmap] = []
        self._frame_idx = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_frame)

    def _extract_frame(self, img: QImage, index: int,
                       w: int, h: int, mirror: bool = False) -> QPixmap:
        """Extract one frame from the sheet, optionally mirror, and scale up."""
        frame = img.copy(index * w, 0, w, h)
        if mirror:
            frame = frame.mirrored(True, False)  # horizontal flip
        tw, th = w * self._SCALE, h * self._SCALE
        # Shrink rather than clip when the zoomed frame is bigger than the box.
        bw, bh = self._MAX_BOX
        if tw > bw or th > bh:
            ratio = min(bw / tw, bh / th)
            tw, th = max(1, int(tw * ratio)), max(1, int(th * ratio))
        scaled = frame.scaled(
            tw, th,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation)
        return QPixmap.fromImage(scaled)

    def _walk_cycle(self, stand: QPixmap, step1: QPixmap,
                    step2: QPixmap) -> list[QPixmap]:
        """Build a 4-frame walk cycle: stand → step1 → stand → step2."""
        return [stand, step1, stand, step2]

    def set_sprite(self, path):
        """Load a sprite sheet and start the 4-direction walk animation."""
        self._timer.stop()
        self._frames.clear()
        self._frame_idx = 0

        from pathlib import Path
        if not path or not Path(path).exists():
            self.setText('No sprite')
            self.setPixmap(QPixmap())
            return

        img = QImage(str(path))
        if img.isNull():
            self.setText('No sprite')
            self.setPixmap(QPixmap())
            return

        # Make background transparent (GBA palette index 0 = top-left pixel)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        bg_rgb = img.pixel(0, 0) & 0x00FFFFFF
        for iy in range(img.height()):
            for ix in range(img.width()):
                if (img.pixel(ix, iy) & 0x00FFFFFF) == bg_rgb:
                    img.setPixel(ix, iy, 0x00000000)

        # Detect frame dimensions from the sheet.
        # GBA overworld sprites come in many sizes:
        #   - 16×16, 32×32: single-frame (pokemon, objects)
        #   - 48×32, 48×16: 3 frames at 16px wide (directional stands only)
        #   - 144×32, 160×32: 9-10 frames at 16px wide (full walk sheets)
        #   - 64×32: could be 4×16 or 2×32 — check if square first
        #   - 64×64, 128×64: large special sprites
        h = img.height()
        sheet_w = img.width()

        # Determine frame width:
        # If the sheet is square (w == h), it's a single frame (e.g. 32×32)
        # If sheet_w / h gives a clean integer and h >= 16, try h as frame width
        # Otherwise default to 16px frame width (standard for most walk sheets)
        w = overworld_frame_width(sheet_w, h)

        total_frames = sheet_w // w if w > 0 else 1

        if total_frames >= 9:
            # Full walk sheet: 9+ frames with this layout:
            #   Frame 0: down-stand   Frame 1: up-stand    Frame 2: left-stand
            #   Frame 3: down-walk1   Frame 4: down-walk2
            #   Frame 5: up-walk1     Frame 6: up-walk2
            #   Frame 7: left-walk1   Frame 8: left-walk2
            # Walk-down cycle: stand(0) → walk1(3) → stand(0) → walk2(4)
            self._frames = self._walk_cycle(
                self._extract_frame(img, 0, w, h),
                self._extract_frame(img, 3, w, h),
                self._extract_frame(img, 4, w, h))

        elif total_frames >= 3:
            # 3-frame sheets (like Agatha 48×32): directional stands only
            # (down, up, left) — no walk animation frames exist.
            # Show frame 0 (face down) as a static image.
            self._frames = [self._extract_frame(img, 0, w, h)]

        else:
            # 1-2 frames — single static sprite (32×32 pokemon, etc.)
            self._frames = [self._extract_frame(img, 0, w, h)]

        if self._frames:
            self.setPixmap(self._frames[0])
            if len(self._frames) > 1:
                self._timer.start(self._FRAME_MS)
        else:
            self.setText('No sprite')
            self.setPixmap(QPixmap())

    def _next_frame(self):
        """Advance to the next frame in the walk cycle."""
        if not self._frames:
            return
        self._frame_idx = (self._frame_idx + 1) % len(self._frames)
        self.setPixmap(self._frames[self._frame_idx])

    def stop(self):
        """Stop the animation timer (call before cleanup)."""
        self._timer.stop()
