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
        self._engine = wasmtime.Engine()
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

    # -- low-level instance --------------------------------------------------
    def _new_instance(self):
        wt = self._wt
        store = wt.Store(self._engine)
        store.set_wasi(wt.WasiConfig())
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
        """Run a whole move and return one OAM snapshot per GBA frame.

        ``ops`` is the move's resolved commands as plain dicts (the tab builds
        these from its parsed timeline):
          {"op":"createsprite","template":str,"battler":int,"subpriority":int,"args":[int]}
          {"op":"createvisualtask","func":str,"args":[int]}
          {"op":"delay","frames":int}
          {"op":"waitforvisualfinish"}  /  {"op":"end"}

        Commands execute until a delay/wait (same frame, like the engine's script
        interpreter); each engine step yields one frame. After the timeline, any
        still-living sprites/tasks are drained so the move plays out fully (the
        engine self-destructs them) — no abrupt cut at ``end``.
        """
        store, ex = self.open(attacker_is_player)
        frames: List[List[dict]] = []
        dead = [False]   # set when a wasm trap (UB in some move) halts the engine

        def _safe(fn, *a):
            """Call a wasm export; on a trap (e.g. divide-by-zero from residual
            UB in some move) stop the move cleanly instead of crashing the tab."""
            if dead[0]:
                return None
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
                idx = self._tpl_index.get(op["template"])
                if idx is not None:
                    for i, v in enumerate(op.get("args", [])[:8]):
                        _safe(ex["engine_set_arg"], i, int(v))
                    _safe(ex["engine_create_sprite"], idx,
                          int(op.get("battler", 1)), int(op.get("subpriority", 3)))
            elif k == "createvisualtask":
                idx = self._task_index.get(op["func"])
                if idx is not None:
                    for i, v in enumerate(op.get("args", [])[:8]):
                        _safe(ex["engine_set_arg"], i, int(v))
                    _safe(ex["engine_create_task"], idx)
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
        return frames
