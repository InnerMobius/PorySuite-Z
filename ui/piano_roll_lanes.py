"""Automation and velocity lanes under the Piano Roll canvas.

One lane is shown at a time, for the ACTIVE track only, so an edit here can
never touch another track or channel:

* **Note velocity** — one bar per note of the track; drag across the bars to
  set them. Velocity belongs to each note (it is saved with the notes). The
  game plays velocity in steps of 4 (mid2agb rounds up), so values snap to
  what the game will actually play — a save that would play something else is
  refused, so offering other values would only lead to a refusal.
* **Track controls** — volume, pan, pitch bend, bend range and the vibrato /
  tuning controls. Each event is a point; the line holds a value until the
  next point. Click empty space to add a point, drag a point to move it,
  right-click a point to delete it. These are the track's own settings over
  time (saved as the track's mid-song control changes).

The point at tick 0 is the track's STARTING value. It is not a mid-song event:
it lives in the track's opening commands, so editing it is passed to the
window (``start_value_changed``), which changes that command — the same place
the sidebar's sliders write to.

Every press is one undo step (``canvas.push_undo``); positions are on the
playback clock the canvas draws notes on, so repeats show every playing.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PyQt6.QtWidgets import QWidget

#: (key, menu text, lowest, highest, value meaning "no change" or None)
LANES = [
    ('velocity', 'Note velocity (per note)', 4, 127, None),
    ('VOICE', 'Instrument (voicegroup slot)', 0, 127, None),
    ('VOL', 'Volume', 0, 127, None),
    ('PAN', 'Pan (64 = centre)', 0, 127, 64),
    ('BEND', 'Pitch bend (64 = none)', 0, 127, 64),
    ('BENDR', 'Pitch-bend range (semitones)', 0, 24, None),
    ('MOD', 'Vibrato depth', 0, 127, None),
    ('LFOS', 'Vibrato speed', 0, 127, None),
    ('MODT', 'Vibrato type (0 pitch, 1 volume, 2 pan)', 0, 2, None),
    ('LFODL', 'Vibrato delay', 0, 127, None),
    ('TUNE', 'Fine tune (64 = none)', 0, 127, 64),
    # Song-wide: mid2agb puts every tempo change on track 1. Values are the
    # song's own tempo units (BPM x tempo base / 2); the lane shows BPM.
    ('TEMPO', 'Tempo (whole song)', 1, 255, None),
]
_INFO = {k: (lo, hi, mid) for k, _t, lo, hi, mid in LANES}

LANE_HEIGHT = 110
_PAD = 8
_HIT = 6


def game_velocity(v: int) -> int:
    """The velocity the game plays for *v*: rounded up to a multiple of 4,
    at most 127 (mid2agb's rule; see midi_patch.agb_velocity)."""
    v = max(1, min(127, int(v)))
    return min(127, (v + 3) // 4 * 4)


class AutomationLane(QWidget):
    """The lane strip. Reads and edits the canvas's notes and control events
    for its active track; coordinates follow the canvas zoom and scroll."""

    edited = pyqtSignal()                         # notes or controls changed
    start_value_changed = pyqtSignal(int, str)    # track, kind — tick-0 point changed

    def __init__(self, canvas, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._canvas = canvas
        self._kind = 'velocity'
        self._scroll = 0
        self._drag: Optional[dict] = None
        self.tempo_base = 1          # the song's tbs, for showing tempo in BPM
        self.voice_names: list = []  # instrument names by slot, for the Instrument lane
        self.setFixedHeight(LANE_HEIGHT)
        self.setMouseTracking(True)
        self.setToolTip("")

    # ── public ─────────────────────────────────────────────────────────
    @property
    def kind(self) -> str:
        return self._kind

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._drag = None
        self.update()

    def set_scroll_offset(self, offset: int) -> None:
        self._scroll = offset
        self.update()

    # ── geometry ───────────────────────────────────────────────────────
    def _tw(self) -> float:
        return self._canvas._tick_width * self._canvas._zoom_x

    def tick_to_x(self, tick: float) -> float:
        return tick * self._tw() - self._scroll

    def x_to_tick(self, x: float) -> int:
        return max(0, int(round((x + self._scroll) / self._tw())))

    def _range(self):
        if self._kind == 'velocity':
            return 4, 127
        lo, hi, _mid = _INFO[self._kind]
        return lo, hi

    def value_to_y(self, v: float) -> float:
        lo, hi = self._range()
        span = max(1, hi - lo)
        return _PAD + (1 - (v - lo) / span) * (self.height() - 2 * _PAD)

    def y_to_value(self, y: float) -> int:
        lo, hi = self._range()
        frac = 1 - (y - _PAD) / max(1, self.height() - 2 * _PAD)
        return int(round(lo + max(0.0, min(1.0, frac)) * (hi - lo)))

    def _track(self) -> int:
        if self._kind == 'TEMPO':
            return 0                      # tempo is the whole song's, on track 1
        return getattr(self._canvas, '_active_track', 0) or 0

    def _shown(self, value: int) -> str:
        if self._kind == 'TEMPO':
            return f"{value * 2 / max(1, self.tempo_base):g} BPM"
        if self._kind == 'VOICE' and 0 <= value < len(self.voice_names) and self.voice_names[value]:
            return f"{value} ({self.voice_names[value]})"
        return str(value)

    def _events(self):
        t = self._track()
        return [e for e in self._canvas._control_events
                if e.get('track', 0) == t and e.get('type') == self._kind]

    def _notes(self):
        t = self._track()
        return [n for n in self._canvas._notes if n.get('track', 0) == t]

    # ── paint ──────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(28, 28, 34))
        p.setPen(QPen(QColor(55, 55, 65)))
        for t, _n in self._canvas.bars_in(self.x_to_tick(0), self.x_to_tick(self.width())):
            x = self.tick_to_x(t)
            p.drawLine(int(x), 0, int(x), self.height())
        lo, hi, mid = (4, 127, None) if self._kind == 'velocity' else _INFO[self._kind]
        if mid is not None:
            p.setPen(QPen(QColor(80, 80, 95), 1, Qt.PenStyle.DashLine))
            y = self.value_to_y(mid)
            p.drawLine(0, int(y), self.width(), int(y))
        from ui.piano_roll_widget import _TRACK_COLORS
        color = _TRACK_COLORS[self._track() % len(_TRACK_COLORS)]
        if self._kind == 'velocity':
            p.setPen(Qt.PenStyle.NoPen)
            for n in self._notes():
                x = self.tick_to_x(n['tick'])
                if x < -4 or x > self.width() + 4:
                    continue
                y = self.value_to_y(n.get('velocity') or 100)
                p.setBrush(QBrush(color))
                p.drawRect(QRectF(x, y, 3, self.height() - _PAD - y))
        else:
            evs = sorted(self._events(), key=lambda e: e['tick'])
            p.setPen(QPen(color, 2))
            for a, b in zip(evs, evs[1:] + [None]):
                x1 = self.tick_to_x(a['tick'])
                x2 = self.tick_to_x(b['tick']) if b else self.width()
                y = self.value_to_y(a['value'])
                p.drawLine(QPointF(x1, y), QPointF(x2, y))
                if b:
                    p.drawLine(QPointF(x2, y), QPointF(x2, self.value_to_y(b['value'])))
            for e in evs:
                x, y = self.tick_to_x(e['tick']), self.value_to_y(e['value'])
                p.setBrush(QBrush(QColor(28, 28, 34) if e['tick'] == 0 else color))
                p.drawEllipse(QPointF(x, y), 4, 4)
        p.setPen(QColor(150, 150, 160))
        p.setFont(QFont("", 8))
        p.drawText(4, 12, "Whole song" if self._kind == 'TEMPO' else f"Track {self._track() + 1}")
        p.end()

    # ── mouse ──────────────────────────────────────────────────────────
    def _hit_event(self, pos):
        best = None
        for e in self._events():
            dx = abs(self.tick_to_x(e['tick']) - pos.x())
            dy = abs(self.value_to_y(e['value']) - pos.y())
            if dx <= _HIT and dy <= _HIT and (best is None or dx + dy < best[0]):
                best = (dx + dy, e)
        return best[1] if best else None

    def mousePressEvent(self, ev):
        if not getattr(self._canvas, '_editable', True):
            return
        pos = ev.position()
        if self._kind == 'velocity':
            if ev.button() == Qt.MouseButton.LeftButton:
                self._canvas.push_undo()
                self._drag = {'mode': 'paint'}
                self._paint_velocity(pos)
            return
        hit = self._hit_event(pos)
        if ev.button() == Qt.MouseButton.RightButton:
            if hit is not None:
                self._canvas.push_undo()
                self._canvas._control_events.remove(hit)
                self._changed(hit['tick'] == 0)
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        self._canvas.push_undo()
        if hit is None:
            tick = self._canvas._snap(self.x_to_tick(pos.x()))
            same = [e for e in self._events() if e['tick'] == tick]
            if same:
                hit = same[-1]
                hit['value'] = self.y_to_value(pos.y())
            else:
                hit = {'tick': tick, 'type': self._kind,
                       'value': self.y_to_value(pos.y()), 'track': self._track()}
                self._canvas._control_events.append(hit)
            self._changed(tick == 0)
        self._drag = {'mode': 'point', 'event': hit, 'start': hit['tick'] == 0}

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._drag is None:
            self.setToolTip(self._describe(pos))
            return
        if self._drag['mode'] == 'paint':
            self._paint_velocity(pos)
            return
        e = self._drag['event']
        e['value'] = self.y_to_value(pos.y())
        if not self._drag['start']:
            # A mid-song point moves along the timeline, but not onto tick 0
            # (that is the track's starting value, edited in place).
            e['tick'] = max(1, self._canvas._snap(self.x_to_tick(pos.x())))
        self._changed(self._drag['start'])

    def mouseReleaseEvent(self, _ev):
        self._drag = None

    def _paint_velocity(self, pos):
        v = game_velocity(self.y_to_value(pos.y()))
        x = pos.x()
        hit = False
        for n in self._notes():
            nx = self.tick_to_x(n['tick'])
            if nx - 2 <= x <= nx + 5 and n.get('velocity') != v:
                n['velocity'] = v
                hit = True
        if hit:
            self._changed(False)

    def _changed(self, start_value: bool):
        if start_value:
            self.start_value_changed.emit(self._track(), self._kind)
        self._canvas.update()
        self.update()
        self.edited.emit()

    def _describe(self, pos) -> str:
        tick = self.x_to_tick(pos.x())
        if self._kind == 'velocity':
            return f"tick {tick}: drag up / down to set note velocity (the game plays steps of 4)"
        hit = self._hit_event(pos)
        if hit is not None:
            what = "starting value" if hit['tick'] == 0 else f"tick {hit['tick']}"
            return f"{what}: {self._shown(hit['value'])} — drag to move, right-click to delete"
        return f"tick {tick}: click to add a point"
