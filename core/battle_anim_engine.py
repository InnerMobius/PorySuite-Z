"""Python driver for the headless battle-animation engine (WASM).

Loads ``anim_engine.wasm`` (pokefirered's real animation code, compiled to
wasm32 — see ``enginehost/``) via the ``wasmtime`` runtime and drives it: create
sprites/tasks from a parsed move script, step frames, and read every sprite's
per-frame OAM straight out of wasm linear memory. The engine computes MOTION
only; the editor renders the result with the project's PNGs/palettes.

This replaces the hand-ported approximation (``battle_anim_vm.py`` /
``battle_anim_tasks.py``): the motion is the game's own code, so it's correct by
construction for every move, including ones a project edits.

``wasmtime`` is an optional dependency — import this module lazily and surface
``EngineUnavailable`` so the Battle Anims tab can show a "install the animation
engine" fallback instead of crashing.
"""

from __future__ import annotations

import json
import os
import struct
from typing import Dict, List, Optional


class EngineUnavailable(RuntimeError):
    """Raised when the wasm runtime or the engine artifact is missing."""


# struct Snap in enginehost/driver.c — 22 int32 fields, in this exact order.
_SNAP_FIELDS = (
    "id", "x", "y", "x2", "y2", "tileNum", "shape", "size",
    "matrixNum", "mA", "mB", "mC", "mD", "hFlip", "vFlip", "affineMode",
    "priority", "subpriority", "paletteNum", "invisible", "templateIndex", "isMon",
    "tileTag", "isClone", "blendCoeff", "blendColor", "alpha",
)
_SNAP = struct.Struct("<%di" % len(_SNAP_FIELDS))


def _default_paths():
    """Prefer the bundled, shipped engine (enginehost/dist/); fall back to the
    local dev build (enginehost/buildwasm/) when working on the engine itself."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, ".."))
    dist = os.path.join(root, "enginehost", "dist")
    if os.path.exists(os.path.join(dist, "anim_engine.wasm")):
        return (os.path.join(dist, "anim_engine.wasm"),
                os.path.join(dist, "names.json"))
    base = os.path.join(root, "enginehost", "buildwasm")
    return (os.path.join(base, "anim_engine_reactor.wasm"),
            os.path.join(base, "names.json"))


class AnimEngine:
    """Loads the wasm engine once; each :meth:`play` runs a fresh instance."""

    def __init__(self, wasm_path: Optional[str] = None,
                 names_path: Optional[str] = None):
        dw, dn = _default_paths()
        self.wasm_path = wasm_path or dw
        self.names_path = names_path or dn
        try:
            import wasmtime  # noqa: F401
        except Exception as e:  # pragma: no cover - environment dependent
            raise EngineUnavailable(
                "the 'wasmtime' package is not installed") from e
        if not os.path.exists(self.wasm_path):
            raise EngineUnavailable("engine artifact missing: %s" % self.wasm_path)
        import wasmtime
        self._wt = wasmtime
        # Enable fuel so a runaway task (an animation that loops forever under
        # the host's stubbed conditions — e.g. a palette-wait that never
        # satisfies) TRAPS instead of hanging the whole app. We refuel before
        # each engine call; a single call that burns the per-call budget traps.
        cfg = wasmtime.Config()
        self._fuel = False
        try:
            cfg.consume_fuel = True
            self._fuel = True
        except Exception:
            pass
        self._engine = wasmtime.Engine(cfg)
        self._module = wasmtime.Module.from_file(self._engine, self.wasm_path)
        self._linker = wasmtime.Linker(self._engine)
        self._linker.define_wasi()
        names = json.load(open(self.names_path, encoding="utf-8"))
        self._tpl_names: List[str] = list(names["templates"])
        self._task_names: List[str] = list(names["tasks"])
        self._tpl_index: Dict[str, int] = {n: i for i, n in enumerate(self._tpl_names)}
        self._task_index: Dict[str, int] = {n: i for i, n in enumerate(self._task_names)}

    def template_name(self, index: int) -> Optional[str]:
        """Reverse of the name->index map (snapshot gives templateIndex)."""
        if 0 <= index < len(self._tpl_names):
            return self._tpl_names[index]
        return None

    # -- capability queries --------------------------------------------------
    def has_template(self, name: str) -> bool:
        return name in self._tpl_index

    def has_task(self, name: str) -> bool:
        return name in self._task_index

    # Per-call instruction budget. A normal engine_step costs ~1.8k (empty) to a
    # few hundred-k (busy) instructions; 100M is a >100x margin over any real
    # step, yet an infinite loop burns it in a fraction of a second and traps.
    _CALL_FUEL = 100_000_000
    _INIT_FUEL = 2_000_000_000   # instantiate + _initialize + setup

    def _refuel(self, store):
        """Reset the per-call fuel budget so the next engine call can't run away
        and hang the app (it traps on exhaustion instead)."""
        if self._fuel:
            try:
                store.set_fuel(self._CALL_FUEL)
            except Exception:
                pass

    # -- low-level instance --------------------------------------------------
    def _new_instance(self):
        wt = self._wt
        store = wt.Store(self._engine)
        store.set_wasi(wt.WasiConfig())
        if self._fuel:
            try:
                store.set_fuel(self._INIT_FUEL)
            except Exception:
                self._fuel = False
        inst = self._linker.instantiate(store, self._module)
        ex = inst.exports(store)
        init = ex.get("_initialize")
        if init is not None:
            init(store)
        return store, ex

    def _snapshot(self, store, ex) -> List[dict]:
        n = ex["engine_snapshot"](store)
        if n <= 0:
            return []
        addr = ex["engine_snapshot_addr"](store)
        stride = ex["engine_snap_stride"](store)
        mem = ex["memory"]
        raw = mem.read(store, addr, addr + n * stride)
        out = []
        for i in range(n):
            vals = _SNAP.unpack_from(raw, i * stride)
            out.append(dict(zip(_SNAP_FIELDS, vals)))
        return out

    # -- low-level driving (used by tests + the timeline player) -------------
    def open(self, attacker_is_player: bool = True):
        """Return a live (store, exports) session with the scene reset."""
        store, ex = self._new_instance()
        ex["engine_reset"](store, 1 if attacker_is_player else 0)
        return store, ex

    def create_sprite(self, store, ex, template: str, battler: int,
                      subpriority: int, args: List[int]) -> int:
        idx = self._tpl_index.get(template)
        if idx is None:
            return -1
        for i, v in enumerate(args[:8]):
            ex["engine_set_arg"](store, i, int(v))
        return ex["engine_create_sprite"](store, idx, battler, subpriority)

    def create_task(self, store, ex, func: str, args: List[int]) -> int:
        idx = self._task_index.get(func)
        if idx is None:
            return -1
        for i, v in enumerate(args[:8]):
            ex["engine_set_arg"](store, i, int(v))
        return ex["engine_create_task"](store, idx)

    def step(self, store, ex) -> List[dict]:
        ex["engine_step"](store)
        return self._snapshot(store, ex)

    def snapshot(self, store, ex) -> List[dict]:
        return self._snapshot(store, ex)

    # -- whole-move player ---------------------------------------------------
    def play_timeline(self, ops: List[dict], attacker_is_player: bool = True,
                      max_frames: int = 600, wait_cap: int = 240,
                      sounds_out: Optional[list] = None,
                      bgscroll_out: Optional[list] = None,
                      bg2scroll_out: Optional[list] = None) -> List[List[dict]]:
        """Run a whole move and return one OAM snapshot per GBA frame, RESILIENT
        to a single sprite/task whose creation traps the host engine.

        Some sprite callbacks hit a wasm memory fault under the host's stubbed
        state (e.g. a latent sign-extension in the game's address-reconstruction
        that only bites at certain wasm addresses). A trap poisons the whole wasm
        instance, so the rest of the move would be lost. We BAN the sprite/task
        that trapped and REPLAY without it, then KEEP THE BEST run — the one with
        the most VISIBLE effect-sprite content. That way a move whose trapping
        sprite is incidental (Metal Claw's shake) keeps the banned replay (with
        its claw slash), while a move whose trapping sprite IS the visual (Hyper
        Beam's orb) keeps the partial pre-trap run (with its orbs) rather than an
        empty banned one. Project-agnostic: no hardcoded move list.
        """
        banned: set = set()
        best = None   # (score, frames, sounds, bg, bg2)
        for _attempt in range(8):
            s_tmp, b_tmp, b2_tmp = [], [], []
            frames, trapped = self._play_once(
                ops, attacker_is_player, max_frames, wait_cap,
                s_tmp if sounds_out is not None else None,
                b_tmp if bgscroll_out is not None else None,
                b2_tmp if bg2scroll_out is not None else None,
                banned)
            score = self._content_score(frames)
            if best is None or score > best[0]:
                best = (score, frames, s_tmp, b_tmp, b2_tmp)
            if trapped is None:
                break
            banned.add(trapped)                   # skip the trapping item, replay
        if best is None:
            best = (0, [], [], [], [])
        if sounds_out is not None:
            sounds_out[:] = best[2]
        if bgscroll_out is not None:
            bgscroll_out[:] = best[3]
        if bg2scroll_out is not None:
            bg2scroll_out[:] = best[4]
        return best[1]

    @staticmethod
    def _content_score(frames):
        """Visible effect-sprite content of a run: count non-mon, VISIBLE,
        renderable sprite instances across all frames. Used to keep the better
        of a partial (pre-trap) run vs a banned replay."""
        n = 0
        for fr in frames:
            for s in fr:
                if (s.get("isMon", -1) == -1 and not s.get("invisible", 0)
                        and (s.get("templateIndex", -1) >= 0
                             or s.get("tileTag", -1) >= 10000)):
                    n += 1
        return n

    def _play_once(self, ops, attacker_is_player, max_frames, wait_cap,
                   sounds_out, bgscroll_out, bg2scroll_out, banned):
        """One playthrough. Returns (frames, trapped_item): trapped_item is the
        template/func name whose CREATION trapped (so the caller can ban + replay)
        or None if the run finished without a creation trap."""
        store, ex = self.open(attacker_is_player)
        frames: List[List[dict]] = []
        dead = [False]   # set when a wasm trap (UB in some move) halts the engine
        trapped_on = [None]   # template/func being created when a trap hit

        def _safe(fn, *a):
            """Call a wasm export; on a trap (divide-by-zero UB, OR fuel
            exhaustion from a task that loops forever under the host's stubbed
            conditions) stop the move cleanly instead of hanging/crashing the
            tab. Refuel first so a runaway call traps in a fraction of a second
            instead of freezing the app."""
            if dead[0]:
                return None
            self._refuel(store)
            try:
                return fn(store, *a)
            except Exception:
                dead[0] = True
                return None

        def _step():
            if dead[0]:
                return
            _safe(ex["engine_step"])
            if not dead[0]:
                try:
                    frames.append(self._snapshot(store, ex))
                except Exception:
                    dead[0] = True
                if bgscroll_out is not None and not dead[0]:
                    try:
                        v = ex["engine_bg_scroll"](store)
                        bgscroll_out.append(((v >> 16) & 0xFFFF, v & 0xFFFF))
                    except Exception:
                        bgscroll_out.append((0, 0))
                if bg2scroll_out is not None and not dead[0]:
                    try:
                        v = ex["engine_bg2_scroll"](store)
                        bg2scroll_out.append(((v >> 16) & 0xFFFF, v & 0xFFFF))
                    except Exception:
                        bg2scroll_out.append((0, 0))

        def _busy():
            r = _safe(ex["engine_busy"])
            return 0 if r is None else r

        def _sig(fr):
            # Visual signature of a frame: every sprite's render pos / frame /
            # flip / scale / visibility. Identical sig across frames == nothing
            # is moving.
            return tuple(
                (s["id"], s["x"] + s["x2"], s["y"] + s["y2"], s["tileNum"],
                 s["hFlip"], s["vFlip"], s["invisible"], s["mA"], s["mD"])
                for s in fr)

        _SETTLED = 30   # frames of zero visual change == animation has settled

        def _run_until_idle(cap):
            # Step until the engine reports idle, OR the scene stops changing
            # (a non-terminating task — e.g. GrowAndShrink — left a static
            # picture), OR a frame cap. Without the settle check those moves
            # would spin to max_frames and "play" for many empty seconds.
            same, last = 0, None
            n = 0
            while len(frames) < max_frames and n < cap:
                if not _busy() or dead[0]:
                    break
                _step()
                if dead[0]:
                    break
                sig = _sig(frames[-1]) if frames else None
                if sig == last:
                    same += 1
                    if same >= _SETTLED:
                        break
                else:
                    same, last = 0, sig
                n += 1

        for op in ops:
            if dead[0] or len(frames) >= max_frames:
                break
            k = op.get("op")
            if k == "createsprite":
                tpl = op["template"]
                idx = self._tpl_index.get(tpl)
                if idx is not None and tpl not in banned:
                    for i, v in enumerate(op.get("args", [])[:8]):
                        _safe(ex["engine_set_arg"], i, int(v))
                    _safe(ex["engine_create_sprite"], idx,
                          int(op.get("battler", 1)), int(op.get("subpriority", 3)))
                    if dead[0]:                   # its creation trapped → ban + replay
                        trapped_on[0] = tpl
                        break
            elif k == "createvisualtask":
                fn = op["func"]
                idx = self._task_index.get(fn)
                if idx is not None and fn not in banned:
                    for i, v in enumerate(op.get("args", [])[:8]):
                        _safe(ex["engine_set_arg"], i, int(v))
                    _safe(ex["engine_create_task"], idx)
                    if dead[0]:
                        trapped_on[0] = fn
                        break
            elif k == "delay":
                for _ in range(max(1, int(op.get("frames", 1)))):
                    if dead[0] or len(frames) >= max_frames:
                        break
                    _step()
            elif k == "sound":
                if sounds_out is not None and op.get("se"):
                    sounds_out.append((len(frames), op["se"]))  # fires at this frame
            elif k in ("waitforvisualfinish", "waitsound"):
                _run_until_idle(wait_cap)
            elif k in ("end", "return"):
                break
            # sounds / gfx loads / blends: no effect on motion, ignored
        # drain remaining live sprites + tasks
        _run_until_idle(wait_cap)
        if not frames and not dead[0]:       # move with no delays — show spawn frame
            try:
                frames.append(self._snapshot(store, ex))
            except Exception:
                pass
        return frames, trapped_on[0]
