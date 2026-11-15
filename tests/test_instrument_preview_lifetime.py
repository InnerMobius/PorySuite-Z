"""A previewed note stops when you let go, and never outlives its player.

Pressing a key on the instrument panel renders TEN SECONDS of audio on a
background thread and only starts playing when that thread finishes. The gap
between the press and the sound is long enough for the user to act inside it,
and two things went wrong there.

**The note played on forever.** Releasing the key ran, in its entirety::

    if self._audio_player:
        self._audio_player.stop()
        self._audio_player = None

During the render there is no player yet, so the release landed on nothing and
nothing recorded that the key had come up. The render then finished and started
the full ten-second note with no one left to stop it. The next key press was
what finally stopped it — which is how it was reported: "kept playing it even
when not clicking".

**And a second press could take the process down.** The completion handler did
``self._audio_player = AudioPlayer()`` without stopping what was already there,
so a live player became garbage with its stream still running. ``AudioPlayer``
had no destructor, so closing that stream fell to sounddevice's own finaliser,
which runs whenever the collector runs, on whatever thread it runs on. Tearing
down a live PortAudio stream that way faults natively: the process dies with no
Python traceback, and none was ever written.

The fix is a token per request. Every render takes one, and a release takes one
too — which is how letting go cancels a render that has not landed. A result
arriving under a token that is no longer current is discarded rather than
played.

No audio device is opened here; the player is a stand-in that records what was
asked of it.
"""
from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import core.sound.audio_engine as audio_engine  # noqa: E402
import ui.instruments_tab as itab  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


class _FakePlayer:
    """Records what it was told to do, and whether it was left running."""

    created: list = []

    def __init__(self):
        self.volume = 0.0
        self.played = False
        self.stopped = False
        self.closed_by_finaliser = False
        _FakePlayer.created.append(self)

    def play(self, audio, rate, mono=False):
        self.played = True

    def stop(self):
        self.stopped = True

    @property
    def live(self) -> bool:
        """Started and never stopped — the state that kills the process."""
        return self.played and not self.stopped


class _Inst:
    is_keysplit = False
    is_directsound = True
    sample_label = 'viola'


@pytest.fixture
def tab(qapp, monkeypatch):
    _FakePlayer.created = []
    monkeypatch.setattr(audio_engine, 'AudioPlayer', _FakePlayer)

    t = itab.InstrumentsTab()
    # The least state `_play_instrument_note` needs to get as far as rendering.
    t._current_instrument = _Inst()
    t._voicegroup_data = object()
    t._sample_data = {'viola': object()}
    return t


@pytest.fixture
def render_gate(monkeypatch):
    """Hold the render until the test lets it finish.

    This is what makes the race testable: the press starts a render that is
    still running when the test releases the key, exactly as a ten-second
    render is still running when a human lets go.
    """
    gate = threading.Event()
    calls = []

    def _slow_render(inst, midi, samples, vg, duration_ms=0, velocity=100):
        calls.append(midi)
        gate.wait(timeout=5)
        return [0.0] * 16

    import core.sound.track_renderer as tr
    monkeypatch.setattr(tr, 'render_instrument_preview', _slow_render)
    gate.calls = calls
    return gate


def _settle(qapp, tab, timeout=5.0):
    """Pump Qt until every render thread has delivered its result."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if threading.active_count() <= 1:
            break
        time.sleep(0.01)
    qapp.processEvents()
    qapp.processEvents()


# ─────────────────────────────────────────────────────────────────────────────
# The stuck note
# ─────────────────────────────────────────────────────────────────────────────

def test_a_note_released_during_the_render_never_plays(qapp, tab, render_gate):
    """THE regression. This is the whole reported bug.

    Press, let go while it is still rendering, then let the render finish.
    Nothing may start playing.
    """
    tab._on_piano_key(60)
    tab._on_piano_key_released(60)        # key comes up mid-render
    render_gate.set()                      # render lands afterwards
    _settle(qapp, tab)

    assert not any(p.played for p in _FakePlayer.created), (
        'the note started after the key was released')


def test_a_note_held_through_the_render_does_play(qapp, tab, render_gate):
    """The control. Without it the test above passes on a preview that never
    plays at all, which would be a different bug rather than a fix."""
    tab._on_piano_key(60)
    render_gate.set()
    _settle(qapp, tab)

    assert any(p.played for p in _FakePlayer.created), (
        'a held note did not play — the fix broke ordinary playback'
    )


def test_releasing_bumps_the_token(tab):
    before = tab._preview_token
    tab._on_piano_key_released(60)
    assert tab._preview_token > before


def test_releasing_stops_a_player_that_does_exist(qapp, tab, render_gate):
    tab._on_piano_key(60)
    render_gate.set()
    _settle(qapp, tab)
    playing = [p for p in _FakePlayer.created if p.played]
    assert playing

    tab._on_piano_key_released(60)
    assert all(p.stopped for p in playing)
    assert tab._audio_player is None


# ─────────────────────────────────────────────────────────────────────────────
# The orphaned stream
# ─────────────────────────────────────────────────────────────────────────────

def test_a_second_note_never_leaves_the_first_player_running(qapp, tab,
                                                             render_gate):
    """No player may be dropped while its stream is live.

    That is the state whose teardown fell to the garbage collector and killed
    the process with no traceback.
    """
    tab._on_piano_key(60)
    render_gate.set()
    _settle(qapp, tab)

    render_gate.clear()
    tab._on_piano_key(67)
    render_gate.set()
    _settle(qapp, tab)

    live = [p for p in _FakePlayer.created if p.live]
    assert len(live) <= 1, (
        f'{len(live)} players left running at once — one was orphaned')


def test_only_the_newest_player_is_held(qapp, tab, render_gate):
    tab._on_piano_key(60)
    render_gate.set()
    _settle(qapp, tab)
    first = tab._audio_player

    render_gate.clear()
    tab._on_piano_key(67)
    render_gate.set()
    _settle(qapp, tab)

    assert tab._audio_player is not first
    assert first.stopped, 'the replaced player was dropped without stopping'


def test_installing_a_player_stops_the_one_it_replaces(qapp, tab):
    """The invariant itself, tested where it lives.

    Every call site goes through this one setter precisely so that "remember
    to stop the old one" cannot be forgotten at any of them — forgetting it at
    exactly one site is what produced the crash.
    """
    first, second = _FakePlayer(), _FakePlayer()
    first.play(None, 0)
    tab._set_preview_player(first)
    tab._set_preview_player(second)

    assert first.stopped
    assert tab._audio_player is second
    assert not second.stopped


def test_installing_the_same_player_twice_does_not_stop_it(qapp, tab):
    p = _FakePlayer()
    p.play(None, 0)
    tab._set_preview_player(p)
    tab._set_preview_player(p)
    assert not p.stopped


def test_a_failure_to_stop_does_not_lose_the_new_player(qapp, tab):
    """A device that errors on stop must not take the next note with it."""
    class _Stubborn(_FakePlayer):
        def stop(self):
            raise RuntimeError('device gone')

    bad = _Stubborn()
    bad.play(None, 0)
    tab._set_preview_player(bad)
    good = _FakePlayer()
    tab._set_preview_player(good)
    assert tab._audio_player is good


def test_a_new_key_silences_the_old_note_before_the_new_one_renders(
        qapp, tab, render_gate):
    """Pressing another key stops what is sounding NOW, not when the new
    render lands.

    A sustained render is ten seconds of audio and takes a moment to produce.
    Waiting for it before silencing the previous note would leave the two
    overlapping, and it is also the only thing separating this stop from the
    one inside the setter — without this the two are redundant and either
    could be deleted unnoticed.
    """
    tab._on_piano_key(60)
    render_gate.set()
    _settle(qapp, tab)
    first = tab._audio_player
    assert first is not None and first.played

    render_gate.clear()
    tab._on_piano_key(67)          # render deliberately NOT allowed to finish
    qapp.processEvents()

    assert first.stopped, 'the previous note kept sounding into the new one'
    render_gate.set()
    _settle(qapp, tab)


def test_nothing_installs_a_player_except_the_setter(qapp):
    """Structural. The crash was one assignment that skipped the stop.

    Read as source rather than matched as text: a comment mentioning the
    attribute, or the string in this docstring, must not satisfy it, and a new
    bare assignment anywhere in the file must fail it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(itab))
    offenders = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and target.attr == '_audio_player'
                        and isinstance(target.value, ast.Name)
                        and target.value.id == 'self'):
                    offenders.append(func.name)

    assert sorted(set(offenders)) == ['__init__', '_set_preview_player'], (
        f'a player is installed outside the setter, in: {sorted(set(offenders))}'
    )


def test_two_overlapping_renders_play_at_most_one(qapp, tab, render_gate):
    """Both land at once; only the current request may be heard."""
    tab._on_piano_key(60)
    tab._on_piano_key(67)      # supersedes the first while it renders
    render_gate.set()
    _settle(qapp, tab)

    assert len([p for p in _FakePlayer.created if p.played]) <= 1


# ─────────────────────────────────────────────────────────────────────────────
# Results are filed per request, not in one shared slot
# ─────────────────────────────────────────────────────────────────────────────

def test_a_stale_result_is_discarded_not_played(qapp, tab):
    """Delivering an old token directly must not start anything."""
    tab._preview_token = 7
    with tab._preview_lock:
        tab._preview_results[3] = [0.0] * 16
    tab._on_preview_ready(3)
    assert not _FakePlayer.created


def test_a_discarded_result_is_not_kept_in_memory(qapp, tab):
    """A rendered buffer is several megabytes; nothing will ask for it again."""
    tab._preview_token = 7
    with tab._preview_lock:
        tab._preview_results[3] = [0.0] * 16
        tab._preview_results[4] = [0.0] * 16
    tab._on_preview_ready(4)
    assert tab._preview_results == {}


def test_a_result_with_no_audio_is_survivable(qapp, tab):
    tab._preview_token = 2
    tab._on_preview_ready(2)       # nothing was ever filed under 2
    assert not _FakePlayer.created


def test_a_failed_render_does_not_report_over_a_newer_one(qapp, tab):
    tab._preview_token = 5
    tab._on_preview_error(2, 'boom')      # stale failure
    assert 'boom' not in tab._preview_status.text()


def test_a_current_failure_is_reported(qapp, tab):
    tab._preview_token = 5
    tab._on_preview_error(5, 'boom')
    assert 'boom' in tab._preview_status.text()


# ─────────────────────────────────────────────────────────────────────────────
# Leaving the tab, and reloading the project
# ─────────────────────────────────────────────────────────────────────────────

def test_hiding_the_tab_stops_the_note(qapp, tab, render_gate):
    """Ten seconds of viola should not follow you to another tab.

    The widget is shown first on purpose: Qt delivers no hideEvent to a widget
    that was never visible, so hiding one that was already hidden would pass
    this whether the handler existed or not.
    """
    tab.show()
    qapp.processEvents()
    tab._on_piano_key(60)
    render_gate.set()
    _settle(qapp, tab)
    playing = [p for p in _FakePlayer.created if p.played]
    assert playing

    tab.hide()
    qapp.processEvents()
    assert all(p.stopped for p in playing)


def test_hiding_also_cancels_a_render_in_flight(qapp, tab, render_gate):
    tab.show()
    qapp.processEvents()
    tab._on_piano_key(60)
    tab.hide()
    qapp.processEvents()
    render_gate.set()
    _settle(qapp, tab)
    assert not any(p.played for p in _FakePlayer.created)


def test_reloading_the_project_stops_the_note(qapp, tab, render_gate):
    """What is playing was rendered from the data about to be replaced."""
    tab._on_piano_key(60)
    render_gate.set()
    _settle(qapp, tab)
    playing = [p for p in _FakePlayer.created if p.played]
    assert playing

    tab.load_data('', None)
    assert all(p.stopped for p in playing)
    assert tab._preview_results == {}


# ─────────────────────────────────────────────────────────────────────────────
# The last-resort net in AudioPlayer
# ─────────────────────────────────────────────────────────────────────────────

def test_the_player_stops_its_stream_when_collected():
    """A dropped player must close its own stream, not leave it to chance.

    The caller-side defect is fixed, so this should never fire in practice.
    It stays because the failure it catches is an instant process death with
    nothing written to any log.
    """
    closed = []

    class _Stream:
        def stop(self):
            closed.append('stop')

        def close(self):
            closed.append('close')

    p = audio_engine.AudioPlayer()
    p._stream = _Stream()
    p._playing = True
    p.__del__()
    assert closed == ['stop', 'close']


def test_the_finaliser_never_raises():
    """It can run during interpreter shutdown, where an exception is
    unraisable noise rather than a diagnosis."""
    class _Hostile:
        def stop(self):
            raise RuntimeError('device gone')

        def close(self):
            raise RuntimeError('device gone')

    p = audio_engine.AudioPlayer()
    p._stream = _Hostile()
    p.__del__()      # must simply return


def test_stopping_twice_is_harmless():
    class _Stream:
        def stop(self):
            pass

        def close(self):
            pass

    p = audio_engine.AudioPlayer()
    p._stream = _Stream()
    p.stop()
    p.stop()
    assert p._stream is None
