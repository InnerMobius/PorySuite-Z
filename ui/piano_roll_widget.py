"""Piano Roll Widget for PorySuite-Z Sound Editor.

A custom QWidget that renders a DAW-style piano roll with:
- Visual display: grid, note bars, piano keys, ruler, loop region, cursor
- Editing: click to place, drag to move/resize, right-click delete, box select
- Snap to grid with configurable resolution
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QWheelEvent,
    QMouseEvent, QPaintEvent, QLinearGradient, QPolygonF,
)
from PyQt6.QtWidgets import QWidget, QScrollArea, QVBoxLayout, QHBoxLayout


# ── Constants ──────────────────────────────────────────────────────────────

_MIN_NOTE = 0
_MAX_NOTE = 127
_TOTAL_NOTES = _MAX_NOTE - _MIN_NOTE + 1

_PIANO_WIDTH = 48
_RULER_HEIGHT = 32
_DEFAULT_NOTE_HEIGHT = 10
_DEFAULT_TICK_WIDTH = 2.0
_TICKS_PER_BEAT = 24

# Colors
_BG_COLOR = QColor(30, 30, 35)
_BG_BLACK_KEY = QColor(25, 25, 30)
_GRID_LINE_COLOR = QColor(55, 55, 60)
_GRID_BEAT_COLOR = QColor(70, 70, 80)
_GRID_MEASURE_COLOR = QColor(100, 100, 110)
_RULER_BG = QColor(40, 40, 48)
_RULER_TEXT = QColor(180, 180, 190)
_PIANO_WHITE = QColor(230, 230, 235)
_PIANO_BLACK = QColor(40, 40, 45)
_PIANO_BORDER = QColor(80, 80, 85)
_PIANO_LABEL = QColor(100, 100, 105)
_LOOP_REGION_COLOR = QColor(60, 100, 60, 40)
_LOOP_MARKER_COLOR = QColor(80, 180, 80, 180)
_PLAYBACK_CURSOR_COLOR = QColor(255, 80, 80, 200)
_SELECTION_BOX_COLOR = QColor(100, 150, 255, 50)
_SELECTION_BOX_BORDER = QColor(100, 150, 255, 150)
_SELECTED_NOTE_OUTLINE = QColor(255, 255, 100, 220)
_GHOST_NOTE_COLOR = QColor(200, 200, 255, 80)

_TRACK_COLORS = [
    QColor(100, 160, 255), QColor(255, 120, 100),
    QColor(100, 220, 140), QColor(220, 160, 100),
    QColor(180, 120, 220), QColor(100, 200, 220),
    QColor(220, 100, 160), QColor(160, 200, 100),
]

_BLACK_KEYS = {1, 3, 6, 8, 10}
_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# Snap grid values in ticks (24 ticks per beat)
SNAP_VALUES = {
    '1/4 (beat)': 24,
    '1/8': 12,
    '1/16': 6,
    '1/32': 3,
    'Free': 1,
}

# Drag handle: how many pixels from the right edge counts as "resize"
_RESIZE_HANDLE_PX = 6


def _note_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _is_black_key(midi: int) -> bool:
    return (midi % 12) in _BLACK_KEYS


# ── Interaction modes ──────────────────────────────────────────────────────

class _Mode:
    NONE = 0
    PLACING = 1       # click placed a new note, dragging extends duration
    MOVING = 2        # dragging a note (or selection) to new position
    RESIZING = 3      # dragging the right edge of a note
    BOX_SELECT = 4    # rubber-band selection box
    RULER_SCRUB = 5   # dragging on the ruler to scrub playback position


# ═══════════════════════════════════════════════════════════════════════════
# Piano Roll Canvas
# ═══════════════════════════════════════════════════════════════════════════

class PianoRollCanvas(QWidget):
    """The drawing and editing surface for the piano roll."""

    hovered_note_changed = pyqtSignal(str)
    notes_changed = pyqtSignal()       # emitted when user edits notes
    status_message = pyqtSignal(str)   # transient status bar messages
    ruler_clicked = pyqtSignal(int)    # tick position — user clicked the ruler to seek

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Data
        self._notes: list[dict] = []
        self._total_ticks: int = 960
        self._beats_per_measure: int = 4
        self._ticks_per_beat: int = _TICKS_PER_BEAT
        self._loop_start: Optional[int] = None
        self._loop_end: Optional[int] = None
        self._playback_tick: int = -1

        # View
        self._zoom_x: float = 1.0
        self._zoom_y: float = 1.0
        self._note_height: float = _DEFAULT_NOTE_HEIGHT
        self._tick_width: float = _DEFAULT_TICK_WIDTH
        self._track_index: int = -1
        self._hover_note: int = -1

        # Editing
        self._editable: bool = True
        self._snap_ticks: int = 24       # snap to quarter note by default
        self._default_velocity: int = 100
        self._default_duration: int = 24  # one beat
        self._active_track: int = 0       # which track new notes go on

        # Selection
        self._selected: set[int] = set()  # indices into self._notes
        self._clipboard: list[dict] = []
        self._visible_tracks: Optional[set[int]] = None  # None = all visible

        # Drag state
        self._mode: int = _Mode.NONE
        self._drag_start: QPointF = QPointF()
        self._drag_note_idx: int = -1
        self._drag_offset_tick: int = 0
        self._drag_offset_pitch: int = 0
        self._drag_original: dict = {}  # snapshot for undo
        self._drag_originals: dict = {}  # {note_idx: {'tick': t, 'pitch': p}} for multi-select
        self._selection_rect: Optional[QRectF] = None

        self._recalc_size()

    # ── Public API ─────────────────────────────────────────────────────

    def set_notes(self, notes: list[dict], total_ticks: int,
                  beats_per_measure: int = 4,
                  ticks_per_beat: int = _TICKS_PER_BEAT):
        self._notes = notes
        self._total_ticks = max(total_ticks, 1)
        self._beats_per_measure = beats_per_measure
        self._ticks_per_beat = ticks_per_beat
        self._selected.clear()
        self._recalc_size()
        self.update()

    def get_notes(self) -> list[dict]:
        return list(self._notes)

    def set_loop_region(self, start: Optional[int], end: Optional[int]):
        self._loop_start = start
        self._loop_end = end
        self.update()

    def set_playback_tick(self, tick: int):
        self._playback_tick = tick
        self.update()

    def set_track_filter(self, track_index: int):
        self._track_index = track_index
        self._active_track = max(0, track_index)
        self._selected.clear()
        self.update()

    def set_zoom(self, zoom_x: float, zoom_y: float):
        self._zoom_x = max(0.1, min(10.0, zoom_x))
        self._zoom_y = max(0.5, min(4.0, zoom_y))
        self._recalc_size()
        self.update()

    def set_snap(self, snap_name: str):
        self._snap_ticks = SNAP_VALUES.get(snap_name, 24)

    def set_editable(self, editable: bool):
        self._editable = editable

    def zoom_x(self) -> float:
        return self._zoom_x

    def zoom_y(self) -> float:
        return self._zoom_y

    def select_all(self):
        if self._track_index < 0:
            # All tracks — select everything
            self._selected = set(range(len(self._notes)))
        else:
            # Single track — only select notes on the active track
            self._selected = {
                i for i, n in enumerate(self._notes)
                if n.get('track', 0) == self._track_index
            }
        self.update()

    def delete_selected(self):
        if not self._selected:
            return
        keep = [n for i, n in enumerate(self._notes) if i not in self._selected]
        self._notes = keep
        self._selected.clear()
        self.notes_changed.emit()
        self.update()

    def copy_selected(self):
        if not self._selected:
            return
        sel = [self._notes[i] for i in sorted(self._selected)]
        min_tick = min(n['tick'] for n in sel)
        self._clipboard = [
            {**n, 'tick': n['tick'] - min_tick} for n in sel
        ]
        self.status_message.emit(f"Copied {len(self._clipboard)} notes")

    def paste(self, at_tick: int = 0):
        if not self._clipboard:
            return
        self._selected.clear()
        base = len(self._notes)
        for n in self._clipboard:
            new = {**n, 'tick': n['tick'] + at_tick, 'track': self._active_track}
            self._notes.append(new)
            self._selected.add(base)
            base += 1
        self.notes_changed.emit()
        self.update()

    # ── Coordinate math ────────────────────────────────────────────────

    def _is_track_visible(self, track: int) -> bool:
        """Check if a track should be displayed."""
        if self._track_index >= 0 and track != self._track_index:
            return False
        if self._visible_tracks is not None and track not in self._visible_tracks:
            return False
        return True

    def _recalc_size(self):
        tw = self._tick_width * self._zoom_x
        nh = self._note_height * self._zoom_y
        w = int(self._total_ticks * tw) + _PIANO_WIDTH + 200
        h = int(_TOTAL_NOTES * nh) + 20
        self.setMinimumSize(w, h)
        self.resize(w, h)

    def _tick_to_x(self, tick: float) -> float:
        return _PIANO_WIDTH + tick * self._tick_width * self._zoom_x

    def _note_to_y(self, midi_note: int) -> float:
        return (_MAX_NOTE - midi_note) * self._note_height * self._zoom_y

    def _x_to_tick(self, x: float) -> int:
        tw = self._tick_width * self._zoom_x
        if tw <= 0:
            return 0
        return max(0, int((x - _PIANO_WIDTH) / tw))

    def _y_to_note(self, y: float) -> int:
        nh = self._note_height * self._zoom_y
        if nh <= 0:
            return 60
        row = y / nh
        return max(0, min(127, _MAX_NOTE - int(row)))

    def _snap(self, tick: int) -> int:
        if self._snap_ticks <= 1:
            return tick
        return round(tick / self._snap_ticks) * self._snap_ticks

    def _note_rect(self, note: dict) -> QRectF:
        tw = self._tick_width * self._zoom_x
        nh = self._note_height * self._zoom_y
        x = self._tick_to_x(note['tick'])
        y = self._note_to_y(note['pitch'])
        w = max(2, note['duration'] * tw)
        return QRectF(x, y, w, nh)

    def _hit_test(self, pos: QPointF) -> tuple[int, str]:
        """Find which note is under pos. Returns (index, 'body'|'resize') or (-1, '')."""
        for i in range(len(self._notes) - 1, -1, -1):
            n = self._notes[i]
            if not self._is_track_visible(n.get('track', 0)):
                continue
            r = self._note_rect(n)
            if r.contains(pos):
                # Check if near right edge (resize handle)
                if pos.x() >= r.right() - _RESIZE_HANDLE_PX:
                    return (i, 'resize')
                return (i, 'body')
        return (-1, '')

    # ── Painting ───────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        rect = event.rect()
        p.fillRect(rect, _BG_COLOR)

        nh = self._note_height * self._zoom_y
        tw = self._tick_width * self._zoom_x
        ticks_per_measure = self._ticks_per_beat * self._beats_per_measure
        content_top = 0
        content_bottom = self.height()

        # ── Black key row shading ──
        for midi in range(_MIN_NOTE, _MAX_NOTE + 1):
            y = self._note_to_y(midi)
            if y + nh < rect.top() or y > rect.bottom():
                continue
            if _is_black_key(midi):
                p.fillRect(QRectF(_PIANO_WIDTH, y, self.width(), nh), _BG_BLACK_KEY)

        # ── Vertical grid lines ──
        vis_start = max(0, self._x_to_tick(rect.left()))
        vis_end = min(self._total_ticks + 96, self._x_to_tick(rect.right()) + 1)

        tick_step = max(1, self._ticks_per_beat // 4)
        if tw * tick_step < 4:
            tick_step = self._ticks_per_beat // 2
        if tw * tick_step < 4:
            tick_step = self._ticks_per_beat
        if tw * tick_step < 4:
            tick_step = ticks_per_measure

        t = (vis_start // tick_step) * tick_step
        while t <= vis_end:
            x = self._tick_to_x(t)
            if t % ticks_per_measure == 0:
                p.setPen(QPen(_GRID_MEASURE_COLOR, 1))
            elif t % self._ticks_per_beat == 0:
                p.setPen(QPen(_GRID_BEAT_COLOR, 1))
            else:
                p.setPen(QPen(_GRID_LINE_COLOR, 1))
            p.drawLine(int(x), content_top, int(x), content_bottom)
            t += tick_step

        # ── Horizontal grid lines ──
        vis_top = self._y_to_note(rect.top())
        vis_bot = self._y_to_note(rect.bottom())
        for midi in range(max(0, vis_bot - 1), min(128, vis_top + 2)):
            y = int(self._note_to_y(midi) + nh)
            if midi % 12 == 0:
                p.setPen(QPen(_GRID_BEAT_COLOR, 1))
            else:
                p.setPen(QPen(_GRID_LINE_COLOR, 1))
            p.drawLine(_PIANO_WIDTH, y, self.width(), y)

        # ── Loop region ──
        if self._loop_start is not None and self._loop_end is not None:
            lx1 = self._tick_to_x(self._loop_start)
            lx2 = self._tick_to_x(self._loop_end)
            p.fillRect(QRectF(lx1, content_top, lx2 - lx1,
                              content_bottom - content_top), _LOOP_REGION_COLOR)
            p.setPen(QPen(_LOOP_MARKER_COLOR, 2))
            p.drawLine(int(lx1), content_top, int(lx1), content_bottom)
            p.drawLine(int(lx2), content_top, int(lx2), content_bottom)

        # ── Note bars ──
        for i, note in enumerate(self._notes):
            if not self._is_track_visible(note.get('track', 0)):
                continue

            r = self._note_rect(note)
            if r.right() < rect.left() or r.left() > rect.right():
                continue
            if r.bottom() < rect.top() or r.top() > rect.bottom():
                continue

            trk = note.get('track', 0)
            vel = note.get('velocity', 100)
            color = _TRACK_COLORS[trk % len(_TRACK_COLORS)]
            alpha = 120 + int(135 * min(127, vel) / 127)
            bar_color = QColor(color.red(), color.green(), color.blue(), alpha)

            # Draw with small visual gap but full hit rect
            draw_r = QRectF(r.left(), r.top() + 1, r.width(), r.height() - 2)
            p.fillRect(draw_r, bar_color)

            # Outline — highlight selected notes
            if i in self._selected:
                p.setPen(QPen(_SELECTED_NOTE_OUTLINE, 2))
            else:
                p.setPen(QPen(color.darker(130), 1))
            p.drawRect(draw_r)

            # Velocity bar at bottom of note (thin line showing relative velocity)
            if nh > 6:
                vel_frac = min(127, vel) / 127.0
                vel_w = r.width() * vel_frac
                p.fillRect(QRectF(r.left(), r.bottom() - 2, vel_w, 2),
                           color.lighter(140))

        # ── Playback cursor ──
        if self._playback_tick >= 0:
            cx = self._tick_to_x(self._playback_tick)
            p.setPen(QPen(_PLAYBACK_CURSOR_COLOR, 2))
            p.drawLine(int(cx), content_top, int(cx), content_bottom)

        # ── Selection box ──
        if self._selection_rect is not None:
            p.fillRect(self._selection_rect, _SELECTION_BOX_COLOR)
            p.setPen(QPen(_SELECTION_BOX_BORDER, 1, Qt.PenStyle.DashLine))
            p.drawRect(self._selection_rect)

        # ── Mini piano keyboard ──
        p.fillRect(QRectF(0, 0, _PIANO_WIDTH, self.height()), _PIANO_WHITE)
        for midi in range(_MIN_NOTE, _MAX_NOTE + 1):
            y = self._note_to_y(midi)
            if y + nh < rect.top() or y > rect.bottom():
                continue
            if _is_black_key(midi):
                p.fillRect(QRectF(0, y, _PIANO_WIDTH * 0.65, nh), _PIANO_BLACK)
            else:
                p.setPen(QPen(_PIANO_BORDER, 1))
                p.drawLine(0, int(y + nh), _PIANO_WIDTH, int(y + nh))
            if midi % 12 == 0 and nh >= 6:
                p.setPen(_PIANO_LABEL)
                p.setFont(QFont("", max(6, int(nh * 0.8))))
                p.drawText(int(_PIANO_WIDTH * 0.68), int(y + nh - 1),
                           f"C{midi // 12 - 1}")
        p.setPen(QPen(_PIANO_BORDER, 1))
        p.drawLine(_PIANO_WIDTH, 0, _PIANO_WIDTH, self.height())

        # Hover highlight on piano keys
        if self._hover_note >= 0:
            hy = self._note_to_y(self._hover_note)
            p.fillRect(QRectF(0, hy, _PIANO_WIDTH, nh), QColor(255, 255, 255, 30))

        p.end()

    # ── Mouse: hover ───────────────────────────────────────────────────

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()

        # Update hover note
        note = self._y_to_note(pos.y())
        if note != self._hover_note:
            self._hover_note = note
            self.hovered_note_changed.emit(_note_name(note))

        # Update cursor shape
        if self._mode == _Mode.NONE and pos.x() > _PIANO_WIDTH:
            idx, region = self._hit_test(pos)
            if region == 'resize':
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif region == 'body':
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

        # Handle active drag modes
        if self._mode == _Mode.PLACING:
            self._drag_extend_note(pos)
        elif self._mode == _Mode.MOVING:
            self._drag_move_notes(pos)
        elif self._mode == _Mode.RESIZING:
            self._drag_resize_note(pos)
        elif self._mode == _Mode.BOX_SELECT:
            self._drag_box_select(pos)

        # Log drag movement (throttled — only when mode is active)
        if self._mode in (_Mode.PLACING, _Mode.MOVING, _Mode.RESIZING):
            if self._drag_note_idx >= 0 and self._drag_note_idx < len(self._notes):
                n = self._notes[self._drag_note_idx]
                self._debug(
                    f"  DRAG mode={self._mode} pos=({pos.x():.0f},{pos.y():.0f}) "
                    f"-> note pitch={n['pitch']}({_note_name(n['pitch'])}) "
                    f"tick={n['tick']} dur={n['duration']}")

        self.update()

    def leaveEvent(self, event):
        self._hover_note = -1
        self.hovered_note_changed.emit("")
        self.update()

    # ── Mouse: press ───────────────────────────────────────────────────

    _debug_log = None

    def _debug(self, msg: str):
        """Write debug message to file for troubleshooting."""
        import time
        if PianoRollCanvas._debug_log is None:
            PianoRollCanvas._debug_log = open(
                'C:/GBA/porysuite/piano_roll_debug.log', 'w')
            PianoRollCanvas._debug_log.write(
                f"=== Piano Roll Debug Log — {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
        ts = time.strftime('%H:%M:%S')
        PianoRollCanvas._debug_log.write(f"[{ts}] {msg}\n")
        PianoRollCanvas._debug_log.flush()

    def mousePressEvent(self, event: QMouseEvent):
        pos = event.position()
        nh = self._note_height * self._zoom_y
        tw = self._tick_width * self._zoom_x

        self._debug(
            f"PRESS pos=({pos.x():.1f},{pos.y():.1f}) btn={event.button()} "
            f"mode={self._mode} nh={nh:.1f} tw={tw:.2f} "
            f"total_notes={len(self._notes)} active_track={self._active_track} "
            f"track_filter={self._track_index} editable={self._editable}")

        if pos.x() <= _PIANO_WIDTH:
            pitch = self._y_to_note(pos.y())
            self._debug(f"  -> Piano key: {_note_name(pitch)} (MIDI {pitch})")
            return

        if not self._editable:
            self._debug("  -> Not editable, ignoring")
            return

        if event.button() == Qt.MouseButton.RightButton:
            idx, _ = self._hit_test(pos)
            if idx >= 0:
                n = self._notes[idx]
                self._debug(
                    f"  -> RIGHT-CLICK DELETE note #{idx}: {_note_name(n['pitch'])} "
                    f"tick={n['tick']} track={n.get('track',0)}")
                self._selected.discard(idx)
                self._notes.pop(idx)
                self._selected = {i if i < idx else i - 1
                                  for i in self._selected if i != idx}
                self.notes_changed.emit()
                self.update()
            else:
                self._debug("  -> RIGHT-CLICK on empty space, no note to delete")
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Log what _y_to_note and _x_to_tick resolve to
        click_tick = self._x_to_tick(pos.x())
        click_pitch = self._y_to_note(pos.y())
        self._debug(
            f"  -> resolved tick={click_tick} pitch={click_pitch} "
            f"({_note_name(click_pitch)})")

        idx, region = self._hit_test(pos)
        self._debug(f"  -> hit_test result: idx={idx} region='{region}'")

        if idx >= 0:
            n = self._notes[idx]
            r = self._note_rect(n)
            self._active_track = n.get('track', 0)
            self._debug(
                f"  -> HIT note #{idx}: {_note_name(n['pitch'])} "
                f"tick={n['tick']} dur={n['duration']} track={n.get('track',0)} "
                f"rect=({r.x():.0f},{r.y():.0f},{r.width():.0f},{r.height():.0f})")

            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if ctrl:
                if idx in self._selected:
                    self._selected.discard(idx)
                else:
                    self._selected.add(idx)
                self.update()
                return

            if idx not in self._selected:
                self._selected = {idx}

            self._drag_start = pos
            self._drag_note_idx = idx
            self._drag_original = dict(n)

            if region == 'resize':
                self._mode = _Mode.RESIZING
                self._debug(f"  -> MODE = RESIZING")
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self._mode = _Mode.MOVING
                self._drag_offset_tick = self._x_to_tick(pos.x()) - n['tick']
                self._drag_offset_pitch = self._y_to_note(pos.y()) - n['pitch']
                # Snapshot all selected notes so we can move them together
                self._drag_originals = {}
                for si in self._selected:
                    if 0 <= si < len(self._notes):
                        sn = self._notes[si]
                        self._drag_originals[si] = {
                            'tick': sn['tick'], 'pitch': sn['pitch']}
                self._debug(
                    f"  -> MODE = MOVING  offset_tick={self._drag_offset_tick} "
                    f"offset_pitch={self._drag_offset_pitch} "
                    f"selected={len(self._drag_originals)}")
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self._debug(f"  -> MISS — no note under cursor")
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift:
                self._mode = _Mode.BOX_SELECT
                self._drag_start = pos
                self._selection_rect = QRectF(pos, pos)
                self._debug(f"  -> MODE = BOX_SELECT")
            else:
                tick = self._snap(self._x_to_tick(pos.x()))
                pitch = self._y_to_note(pos.y())
                new_note = {
                    'tick': tick,
                    'pitch': pitch,
                    'duration': self._snap_ticks,
                    'velocity': self._default_velocity,
                    'track': self._active_track,
                }
                self._notes.append(new_note)
                new_idx = len(self._notes) - 1
                self._selected = {new_idx}
                self._drag_note_idx = new_idx
                self._drag_start = pos
                self._mode = _Mode.PLACING
                self._debug(
                    f"  -> PLACE new note #{new_idx}: {_note_name(pitch)} "
                    f"tick={tick} dur={self._snap_ticks} track={self._active_track}")
                self.notes_changed.emit()
                self.update()

    # ── Mouse: release ─────────────────────────────────────────────────

    def mouseReleaseEvent(self, event: QMouseEvent):
        pos = event.position()
        self._debug(
            f"RELEASE pos=({pos.x():.1f},{pos.y():.1f}) mode={self._mode} "
            f"drag_idx={self._drag_note_idx} total_notes={len(self._notes)}")

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._mode == _Mode.BOX_SELECT:
            self._finish_box_select()

        if self._mode in (_Mode.MOVING, _Mode.RESIZING, _Mode.PLACING):
            if self._drag_note_idx >= 0 and self._drag_note_idx < len(self._notes):
                n = self._notes[self._drag_note_idx]
                self._debug(
                    f"  -> Final note state: {_note_name(n['pitch'])} "
                    f"tick={n['tick']} dur={n['duration']} track={n.get('track',0)}")
            else:
                self._debug(
                    f"  -> WARNING: drag_note_idx={self._drag_note_idx} "
                    f"out of range (notes len={len(self._notes)})")
            self.notes_changed.emit()

        self._mode = _Mode.NONE
        self._selection_rect = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    # ── Drag operations ────────────────────────────────────────────────

    def _drag_extend_note(self, pos: QPointF):
        """While placing a new note, drag adjusts pitch and duration independently.

        Vertical movement changes the note's pitch.
        Horizontal movement extends the note's duration from its start tick.
        """
        if self._drag_note_idx < 0:
            return
        n = self._notes[self._drag_note_idx]
        # Vertical: change pitch to wherever the mouse is
        new_pitch = self._y_to_note(pos.y())
        n['pitch'] = max(0, min(127, new_pitch))
        # Horizontal: extend duration only if mouse is to the right of the start
        end_tick = self._snap(self._x_to_tick(pos.x()))
        if end_tick > n['tick']:
            n['duration'] = end_tick - n['tick']
        # Keep at least one snap unit
        n['duration'] = max(self._snap_ticks, n['duration'])

    def _drag_move_notes(self, pos: QPointF):
        """Move all selected notes by the drag delta."""
        if self._drag_note_idx < 0:
            return
        if self._drag_note_idx not in self._drag_originals:
            return

        # Calculate delta from the primary (grabbed) note's original position
        new_tick = self._snap(self._x_to_tick(pos.x()) - self._drag_offset_tick)
        new_pitch = self._y_to_note(pos.y()) - self._drag_offset_pitch

        orig = self._drag_originals[self._drag_note_idx]
        delta_tick = new_tick - orig['tick']
        delta_pitch = new_pitch - orig['pitch']

        # Apply delta to all selected notes
        for si, so in self._drag_originals.items():
            if 0 <= si < len(self._notes):
                n = self._notes[si]
                n['tick'] = max(0, so['tick'] + delta_tick)
                n['pitch'] = max(0, min(127, so['pitch'] + delta_pitch))

    def _drag_resize_note(self, pos: QPointF):
        """Resize note duration by dragging the right edge."""
        if self._drag_note_idx < 0:
            return
        n = self._notes[self._drag_note_idx]
        end_tick = self._snap(self._x_to_tick(pos.x()))
        dur = max(self._snap_ticks, end_tick - n['tick'])
        n['duration'] = dur

    def _drag_box_select(self, pos: QPointF):
        """Update the rubber band selection rectangle."""
        x1 = min(self._drag_start.x(), pos.x())
        y1 = min(self._drag_start.y(), pos.y())
        x2 = max(self._drag_start.x(), pos.x())
        y2 = max(self._drag_start.y(), pos.y())
        self._selection_rect = QRectF(x1, y1, x2 - x1, y2 - y1)

    def _finish_box_select(self):
        """Select all notes whose bars intersect the selection rectangle."""
        if self._selection_rect is None:
            return
        self._selected.clear()
        for i, note in enumerate(self._notes):
            if not self._is_track_visible(note.get('track', 0)):
                continue
            r = self._note_rect(note)
            if self._selection_rect.intersects(r):
                self._selected.add(i)

    # ── Keyboard shortcuts (handled by the canvas directly) ────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete or event.key() == Qt.Key.Key_Backspace:
            self.delete_selected()
        elif event.key() == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.select_all()
        elif event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy_selected()
        elif event.key() == Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Paste at the current playback position or tick 0
            paste_tick = max(0, self._playback_tick) if self._playback_tick >= 0 else 0
            self.paste(paste_tick)
        elif event.key() == Qt.Key.Key_Escape:
            self._selected.clear()
            self.update()
        else:
            super().keyPressEvent(event)


# ═══════════════════════════════════════════════════════════════════════════
# Ruler Widget — fixed above the scroll area, never scrolls vertically
# ═══════════════════════════════════════════════════════════════════════════

class RulerWidget(QWidget):
    """Timeline ruler that sits above the piano roll scroll area.

    Draws measure numbers, beat ticks, playback cursor triangle.
    Supports click-and-drag scrubbing.  Syncs horizontally with the
    scroll area via set_scroll_offset().
    """

    ruler_clicked = pyqtSignal(int)  # tick position

    def __init__(self, canvas: PianoRollCanvas, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._canvas = canvas
        self._scroll_offset: int = 0
        self._scrubbing = False
        self.setFixedHeight(_RULER_HEIGHT)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_scroll_offset(self, offset: int):
        """Called when the scroll area's horizontal scrollbar moves."""
        self._scroll_offset = offset
        self.update()

    def _tick_to_local_x(self, tick: float) -> float:
        tw = self._canvas._tick_width * self._canvas._zoom_x
        return tick * tw - self._scroll_offset

    def _local_x_to_tick(self, x: float) -> int:
        tw = self._canvas._tick_width * self._canvas._zoom_x
        if tw <= 0:
            return 0
        return max(0, int((x + self._scroll_offset) / tw))

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w = self.width()
        h = self.height()

        # Background gradient
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(50, 50, 58))
        grad.setColorAt(1.0, QColor(35, 35, 42))
        p.fillRect(0, 0, w, h, grad)

        ticks_per_measure = self._canvas._ticks_per_beat * self._canvas._beats_per_measure
        tw = self._canvas._tick_width * self._canvas._zoom_x

        # Visible tick range based on scroll offset
        vis_start = max(0, self._local_x_to_tick(0))
        vis_end = self._local_x_to_tick(w + 1)

        # Beat ticks (small marks between measures)
        p.setPen(QPen(QColor(70, 70, 80), 1))
        bt = (vis_start // _TICKS_PER_BEAT) * _TICKS_PER_BEAT
        while bt <= vis_end:
            bx = self._tick_to_local_x(bt)
            if bt % ticks_per_measure != 0:
                p.drawLine(int(bx), h - 6, int(bx), h)
            bt += _TICKS_PER_BEAT

        # Measure numbers and main ticks
        p.setPen(QPen(_RULER_TEXT))
        p.setFont(QFont("", 9))
        m = (vis_start // ticks_per_measure) * ticks_per_measure
        while m <= vis_end:
            x = self._tick_to_local_x(m)
            num = m // ticks_per_measure + 1
            p.drawText(int(x) + 4, h - 8, str(num))
            p.setPen(QPen(QColor(140, 140, 150), 1))
            p.drawLine(int(x), h - 10, int(x), h)
            p.setPen(QPen(_RULER_TEXT))
            m += ticks_per_measure

        # Bottom border
        p.setPen(QPen(QColor(80, 80, 90), 1))
        p.drawLine(0, h - 1, w, h - 1)

        # Playback position marker — red triangle + vertical line
        tick = self._canvas._playback_tick
        if tick >= 0:
            cx = self._tick_to_local_x(tick)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 80, 80, 230))
            tri = QPolygonF([
                QPointF(cx - 7, 2),
                QPointF(cx + 7, 2),
                QPointF(cx, 16),
            ])
            p.drawPolygon(tri)
            p.setPen(QPen(_PLAYBACK_CURSOR_COLOR, 2))
            p.drawLine(int(cx), 16, int(cx), h)

        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing = True
            tick = max(0, self._local_x_to_tick(event.position().x()))
            self.ruler_clicked.emit(tick)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._scrubbing:
            tick = max(0, self._local_x_to_tick(event.position().x()))
            self.ruler_clicked.emit(tick)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing = False


# ═══════════════════════════════════════════════════════════════════════════
# Piano Roll Widget — ruler + scroll area composite
# ═══════════════════════════════════════════════════════════════════════════

class PianoRollWidget(QWidget):
    """Composite widget: fixed ruler on top, scrollable piano roll below.

    The ruler stays pinned at the top and syncs horizontally with the
    scroll area so you always see measure numbers and can click to scrub.
    """

    hovered_note_changed = pyqtSignal(str)
    notes_changed = pyqtSignal()
    status_message = pyqtSignal(str)
    ruler_clicked = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._canvas = PianoRollCanvas()
        self._canvas.hovered_note_changed.connect(self.hovered_note_changed.emit)
        self._canvas.notes_changed.connect(self.notes_changed.emit)
        self._canvas.status_message.connect(self.status_message.emit)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidget(self._canvas)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self._ruler = RulerWidget(self._canvas)
        self._ruler.ruler_clicked.connect(self.ruler_clicked.emit)

        # Sync ruler horizontal scroll with the scroll area
        self._scroll_area.horizontalScrollBar().valueChanged.connect(
            self._ruler.set_scroll_offset)

        # Layout: ruler on top, scroll area below — both offset by piano width
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Ruler row: blank spacer for piano column + ruler
        ruler_row = QHBoxLayout()
        ruler_row.setContentsMargins(0, 0, 0, 0)
        ruler_row.setSpacing(0)
        self._ruler_spacer = QWidget()
        self._ruler_spacer.setFixedWidth(_PIANO_WIDTH)
        self._ruler_spacer.setFixedHeight(_RULER_HEIGHT)
        self._ruler_spacer.setStyleSheet(
            f"background: rgb({_RULER_BG.red()},{_RULER_BG.green()},{_RULER_BG.blue()});")
        ruler_row.addWidget(self._ruler_spacer)
        ruler_row.addWidget(self._ruler, 1)
        # Account for the scroll area's vertical scrollbar width
        self._ruler_scrollbar_spacer = QWidget()
        self._ruler_scrollbar_spacer.setFixedHeight(_RULER_HEIGHT)
        self._ruler_scrollbar_spacer.setFixedWidth(
            self._scroll_area.verticalScrollBar().sizeHint().width())
        self._ruler_scrollbar_spacer.setStyleSheet(
            f"background: rgb({_RULER_BG.red()},{_RULER_BG.green()},{_RULER_BG.blue()});")
        ruler_row.addWidget(self._ruler_scrollbar_spacer)

        layout.addLayout(ruler_row)
        layout.addWidget(self._scroll_area, 1)

    @property
    def canvas(self) -> PianoRollCanvas:
        return self._canvas

    def load_song_data(self, song_data, track_index: int = -1):
        """Load a parsed SongData into the piano roll.

        Flattens PATT/PEND/GOTO so the full song is visible. Loop points
        and duration are computed from the flattened sequence (not raw
        commands, which have different tick positions for structured songs).
        """
        from core.sound.track_renderer import (
            flatten_track_commands, get_flattened_loop_info,
        )

        notes = []
        total_ticks = 0
        loop_start = None
        loop_end = None

        for ti, track in enumerate(song_data.tracks):
            flat_cmds = flatten_track_commands(track.commands, loop_count=0)
            for cmd in flat_cmds:
                if cmd.cmd == 'NOTE' and cmd.pitch is not None:
                    notes.append({
                        'tick': cmd.tick,
                        'pitch': cmd.pitch,
                        'duration': cmd.duration,
                        'velocity': cmd.velocity if cmd.velocity else 100,
                        'track': ti,
                    })

            # Use first track to get loop info and duration
            if ti == 0:
                ls, le, dur = get_flattened_loop_info(track.commands)
                loop_start = ls
                loop_end = le
                total_ticks = dur

            # Also check other tracks for longer duration
            for cmd in flat_cmds:
                end = cmd.tick + (cmd.duration or 0)
                if end > total_ticks:
                    total_ticks = end

        if total_ticks <= 0:
            total_ticks = 960

        self._canvas.set_notes(notes, total_ticks)
        self._canvas.set_track_filter(track_index)
        self._canvas.set_loop_region(loop_start, loop_end)

        # Scroll to middle C
        mid_y = self._canvas._note_to_y(60)
        self._scroll_area.verticalScrollBar().setValue(
            int(mid_y - self._scroll_area.viewport().height() / 2))

    def set_playback_tick(self, tick: int):
        """Update the playback cursor on both the canvas and the ruler."""
        self._canvas.set_playback_tick(tick)
        self._ruler.update()

    def scroll_to_tick(self, tick: int):
        """Scroll horizontally so a given tick is visible."""
        x = self._canvas._tick_to_x(tick)
        vp_w = self._scroll_area.viewport().width()
        sb = self._scroll_area.horizontalScrollBar()
        if x < sb.value() or x > sb.value() + vp_w:
            sb.setValue(max(0, int(x - vp_w * 0.3)))

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.15 if delta > 0 else 1 / 1.15
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                new_zy = self._canvas.zoom_y() * factor
                self._canvas.set_zoom(self._canvas.zoom_x(), new_zy)
            else:
                new_zx = self._canvas.zoom_x() * factor
                self._canvas.set_zoom(new_zx, self._canvas.zoom_y())
            self._ruler.update()
            event.accept()
        else:
            self._scroll_area.wheelEvent(event)
