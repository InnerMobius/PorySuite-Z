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


# struct Snap in enginehost/driver.c — int32 fields, in this exact order.
_SNAP_FIELDS = (
    "id", "x", "y", "x2", "y2", "tileNum", "shape", "size",
    "matrixNum", "mA", "mB", "mC", "mD", "hFlip", "vFlip", "affineMode",
    "priority", "subpriority", "paletteNum", "invisible", "templateIndex", "isMon",
    "tileTag", "isClone", "blendCoeff", "blendColor", "alpha", "objMode", "gray",
    "bgCopy", "bgCopyBaseY", "addlMon", "addlMonBattler", "addlMonBackpic",
    "subspriteCount",
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
    # Linear-memory size to grow each instance to (in 64KB pages). The GBA's
    # absolute hardware addresses (VRAM 0x06xxxxxx, OAM 0x07xxxxxx) and any
    # garbage pointer a not-loaded sprite sheet produces must land in-bounds so
    # the real game code runs to completion instead of trapping. wasmtime maps
    # pages lazily, so the cost is only what's actually touched.
    _MEM_TARGET_PAGES = 0x100000000 // 65536  # full 4GB — covers any 32-bit address,
    #   incl. sign-extended 0xFFFFxxxx pointers (AnimShakeMonOrBattleTerrain rebuilds
    #   &gSpriteCoordOffset via data[6]|(data[7]<<16); a bit-15-set low half sign-
    #   extends to 0xFFFFxxxx, which is OOB unless the whole 32-bit space is mapped).
    _debug_traps = False        # test hook: capture wasm trap strings into _trap_log
    _trap_log: list = []

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
        # Grow linear memory to cover the GBA absolute address space (VRAM
        # 0x06xxxxxx, OAM 0x07xxxxxx, palette/IO). The headless engine never
        # renders to VRAM, but the REAL game code still WRITES there — a sprite
        # whose tile sheet isn't loaded gets sheetTileStart = 0xFFFF and its tile
        # copy targets OBJ_VRAM0 + 0xFFFF*32 (~0x06210000). With the default
        # ~832KB memory that address is out of bounds and TRAPS, so the engine
        # bans the sprite (Sludge / Spark / Hyper Beam lose their projectile).
        # Growing makes those writes land in harmless zeroed memory so the real
        # code runs to completion. wasmtime maps the pages lazily, so the cost is
        # only the few pages actually touched.
        try:
            mem = ex["memory"]
            _PAGE = 65536
            # Try the full ~4GB; if a machine's wasmtime caps lower, fall back to
            # 1GB then 128MB so we still get the largest window that works (a grow
            # that exceeds the cap raises WITHOUT growing, so step down).
            for _target in (self._MEM_TARGET_PAGES,
                            0x40000000 // _PAGE, 0x08000000 // _PAGE):
                _have = mem.size(store)
                if _have >= _target:
                    break
                try:
                    mem.grow(store, _target - _have)
                    break
                except Exception:
                    continue
        except Exception:
            pass
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
        sub_addr = None
        for i in range(n):
            vals = _SNAP.unpack_from(raw, i * stride)
            d = dict(zip(_SNAP_FIELDS, vals))
            # Multi-OAM sprite (e.g. the frozen ice cube = 4 pieces): pull each
            # piece (x, y, shape, size, tileOffset) so the renderer can assemble
            # the whole sprite instead of just the main 64x64 piece.
            if d.get("subspriteCount", 0) > 0:
                try:
                    cnt = ex["engine_subsprites"](store, d["id"])
                    if cnt > 0:
                        if sub_addr is None:
                            sub_addr = ex["engine_subsprites_addr"](store)
                        sraw = mem.read(store, sub_addr, sub_addr + cnt * 5 * 4)
                        flat = struct.unpack("<%di" % (cnt * 5), sraw)
                        d["subsprites"] = [tuple(flat[k * 5:k * 5 + 5])
                                           for k in range(cnt)]
                except Exception:
                    pass
            out.append(d)
        return out

    # -- low-level driving (used by tests + the timeline player) -------------
    def open(self, attacker_is_player: bool = True, single_battler: bool = False):
        """Return a live (store, exports) session with the scene reset.

        ``single_battler`` mirrors the game's LaunchStatusAnimation, which sets the
        anim attacker AND target to the SAME (affected) battler — used for the
        Status Conditions table so burn/freeze/etc. land on the selected mon, not
        the opposite one. Moves and General/Special keep attacker != target."""
        store, ex = self._new_instance()
        ex["engine_reset"](store, 1 if attacker_is_player else 0,
                           1 if single_battler else 0)
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
                      bg2scroll_out: Optional[list] = None,
                      bg3scroll_out: Optional[list] = None,
                      shadow_out: Optional[list] = None,
                      bg_palette: Optional[list] = None,
                      bg_pal_out: Optional[list] = None,
                      bg_pal_slot: Optional[int] = None,
                      monfx_out: Optional[list] = None,
                      coord_offset_out: Optional[list] = None,
                      screenblend_out: Optional[list] = None,
                      bg_blend_out: Optional[list] = None,
                      single_battler: bool = False) -> List[List[dict]]:
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
        self._last_bg_screen_size = -1   # engine reveals the anim-BG SCREEN_SIZE
        best = None   # (score, frames, sounds, bg, bg2, shadow, bgpal, monfx, bg3, coordoff)
        for _attempt in range(8):
            s_tmp, b_tmp, b2_tmp, sh_tmp, bp_tmp, mf_tmp, b3_tmp, co_tmp, sb_tmp, bb_tmp = \
                [], [], [], [], [], [], [], [], [], []
            frames, trapped = self._play_once(
                ops, attacker_is_player, max_frames, wait_cap,
                s_tmp if sounds_out is not None else None,
                b_tmp if bgscroll_out is not None else None,
                b2_tmp if bg2scroll_out is not None else None,
                sh_tmp if shadow_out is not None else None,
                bg_palette,
                bp_tmp if bg_pal_out is not None else None,
                bg_pal_slot,
                banned,
                mf_tmp if monfx_out is not None else None,
                b3_tmp if bg3scroll_out is not None else None,
                co_tmp if coord_offset_out is not None else None,
                sb_tmp if screenblend_out is not None else None,
                bb_tmp if bg_blend_out is not None else None,
                single_battler)
            score = self._content_score(frames)
            if best is None or score > best[0]:
                best = (score, frames, s_tmp, b_tmp, b2_tmp, sh_tmp, bp_tmp,
                        mf_tmp, b3_tmp, co_tmp, sb_tmp, bb_tmp)
            if trapped is None:
                break
            banned.add(trapped)                   # skip the trapping item, replay
        if best is None:
            best = (0, [], [], [], [], [], [], [], [], [], [], [])
        if sounds_out is not None:
            sounds_out[:] = best[2]
        if bgscroll_out is not None:
            bgscroll_out[:] = best[3]
        if bg2scroll_out is not None:
            bg2scroll_out[:] = best[4]
        if bg_pal_out is not None:
            bg_pal_out[:] = best[6]
        if shadow_out is not None:
            shadow_out[:] = best[5]
        if monfx_out is not None:
            monfx_out[:] = best[7]
        if bg3scroll_out is not None:
            bg3scroll_out[:] = best[8]
        if coord_offset_out is not None:
            coord_offset_out[:] = best[9]
        if screenblend_out is not None:
            screenblend_out[:] = best[10]
        if bg_blend_out is not None:
            bg_blend_out[:] = best[11]
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
                   sounds_out, bgscroll_out, bg2scroll_out, shadow_out,
                   bg_palette, bg_pal_out, bg_pal_slot, banned, monfx_out=None,
                   bg3scroll_out=None, coord_offset_out=None,
                   screenblend_out=None, bg_blend_out=None, single_battler=False):
        """One playthrough. Returns (frames, trapped_item): trapped_item is the
        template/func name whose CREATION trapped (so the caller can ban + replay)
        or None if the run finished without a creation trap."""
        store, ex = self.open(attacker_is_player, single_battler)
        frames: List[List[dict]] = []
        dead = [False]   # set when a wasm trap (UB in some move) halts the engine
        trapped_on = [None]   # template/func being created when a trap hit
        # Engine-driven BG palette: write the move's real BG palette into the
        # displayed buffer at the engine's own BG slot, so the move's OWN tasks
        # (psychic rotation, white-flash, a project's custom palette task) animate
        # it. We read it back each frame — whatever the engine did is reflected,
        # with no per-move logic in the renderer.
        bg_pal_addr = bg_pal_idx = None
        if bg_palette is not None or bg_pal_out is not None:
            try:
                bg_pal_addr = ex["engine_pltt_addr"](store)
                # The slot the BG's tilemap references (Surf=8, psychic=2) — the
                # slot the move's task actually animates. Fall back to the engine's
                # default BG palette slot only if the caller didn't supply one.
                bg_pal_idx = (bg_pal_slot * 16 if bg_pal_slot is not None
                              else ex["engine_bg_pltt_index"](store))
                if bg_palette:
                    vals = [int(c) & 0xFFFF for c in bg_palette[:16]]
                    ex["memory"].write(
                        store, struct.pack("<%dH" % len(vals), *vals),
                        bg_pal_addr + bg_pal_idx * 2)
            except Exception:
                bg_pal_addr = bg_pal_idx = None

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
            except Exception as e:
                dead[0] = True
                if self._debug_traps:
                    try:
                        self._trap_log.append(str(e))
                    except Exception:
                        pass
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
                if bg3scroll_out is not None and not dead[0]:
                    try:
                        v = ex["engine_bg3_scroll"](store)
                        bg3scroll_out.append(((v >> 16) & 0xFFFF, v & 0xFFFF))
                    except Exception:
                        bg3scroll_out.append((0, 0))
                if coord_offset_out is not None and not dead[0]:
                    # gSpriteCoordOffsetX/Y — the GBA sprite-layer screen shake
                    # (Metal Claw / Dragon Claw impact). The renderer jitters the
                    # battler mons by it.
                    try:
                        v = ex["engine_coord_offset"](store)
                        coord_offset_out.append(((v >> 16) & 0xFFFF, v & 0xFFFF))
                    except Exception:
                        coord_offset_out.append((0, 0))
                if screenblend_out is not None and not dead[0]:
                    # Screen-wide tint/brighten overlay (Morning Sun's white flash,
                    # Eruption's red tint): the dominant palette blend across the
                    # scene slots, packed (coeff<<24)|BGR555. 0 = no screen flash.
                    try:
                        screenblend_out.append(ex["engine_screen_blend"](store))
                    except Exception:
                        screenblend_out.append(0)
                if bg_blend_out is not None and not dead[0]:
                    # BG-layer alpha-blend state: Morning Sun's light-beam BG fades
                    # via BLDALPHA EVA while BG1 is the blend top layer. Packed
                    # (EVA) | (BLDCNT TGT1 mask << 8) | (effect << 16) — lets the
                    # renderer alpha-fade a blended anim BG, not just BLEND sprites.
                    try:
                        bg_blend_out.append(ex["engine_bg_blend"](store))
                    except Exception:
                        bg_blend_out.append(0)
                if shadow_out is not None and not dead[0]:
                    # Memento soul-shadow: per-frame scanline-effect state. Only
                    # read the 160-row stretch buffer when a stretch is running
                    # (state != 0) — otherwise it's a cheap {state:0} placeholder
                    # so frame indices stay aligned with `frames`.
                    try:
                        st = ex["engine_scanline_state"](store)
                        state, eva = st & 0xFF, (st >> 8) & 0xFF
                        lyr, axis = (st >> 16) & 3, (st >> 18) & 3
                        wide = (st >> 20) & 1
                        if state:
                            w = ex["engine_win0h"](store)
                            addr = ex["engine_scanline_addr"](store)
                            # 320 u16: a 32-bit DMA (Acid Armor) interleaves
                            # HOFS,VOFS per row, so HOFS for row i is buf[2i].
                            raw = ex["memory"].read(store, addr, addr + 320 * 2)
                            buf = list(struct.unpack("<320H", raw))
                            shadow_out.append({
                                "state": state, "eva": eva, "layer": lyr, "axis": axis,
                                "wide": wide,
                                "win0h": ((w >> 8) & 0xFF, w & 0xFF), "buf": buf})
                        else:
                            shadow_out.append({"state": 0, "eva": eva})
                    except Exception:
                        shadow_out.append({"state": 0})
                if (bg_pal_out is not None and bg_pal_addr is not None
                        and not dead[0]):
                    # The live BG palette (slot from engine_bg_pltt_index), as the
                    # move's tasks left it this frame — 16 BGR555 u16.
                    try:
                        a = bg_pal_addr + bg_pal_idx * 2
                        raw = ex["memory"].read(store, a, a + 32)
                        bg_pal_out.append(list(struct.unpack("<16H", raw)))
                    except Exception:
                        bg_pal_out.append(None)
                if monfx_out is not None and not dead[0]:
                    # Transform morph: (mosaic 0-15 pixelation, species-swapped flag).
                    try:
                        mf = ex["engine_mon_fx"](store)
                        monfx_out.append((mf & 0xFF, (mf >> 8) & 1))
                    except Exception:
                        monfx_out.append((0, 0))

        def _busy():
            r = _safe(ex["engine_busy"])
            return 0 if r is None else r

        _had_effect_sprite = [False]
        _sprite_age = {}            # sprite id -> frame index first seen (this play)

        def _sig(fr):
            # Visual signature of a frame: each VISIBLE, ON-SCREEN sprite's render
            # pos / frame / flip / scale. Identical sig across frames == settled.
            # Excludes: invisible sprites; sprites drifted off-screen (Bubble's risen
            # bubbles / flown-off projectiles — gone visually, their OAM drift must
            # not pin the cap); and effect sprites still alive after ~2.5s, which are
            # stuck crawlers whose callback never self-destructed (Bubble's lingering
            # pop bubble) — their slow creep otherwise runs the move to the 600 cap.
            fidx = len(frames)
            rows = []
            for s in fr:
                if s["invisible"]:
                    continue
                x = s["x"] + s["x2"]
                y = s["y"] + s["y2"]
                if not (-32 <= x <= 272 and -32 <= y <= 192):
                    continue
                sid = s["id"]
                _sprite_age.setdefault(sid, fidx)
                if s.get("isMon", -1) == -1 and (fidx - _sprite_age[sid]) > 150:
                    continue
                rows.append((sid, x, y, s["tileNum"],
                             s["hFlip"], s["vFlip"], s["mA"], s["mD"]))
            sprites = tuple(rows)
            # Once a move has shown an EFFECT sprite (drill, beam, …), the SPRITE
            # settle governs — residual BG wiggle after the sprites stop must not
            # keep it spinning to the cap (Horn Drill blew up to 600 frames).
            if any(s.get("isMon", -1) == -1 and not s.get("invisible")
                   and s.get("objMode", 0) != 2 for s in fr):
                _had_effect_sprite[0] = True
            if _had_effect_sprite[0]:
                return (sprites, ())
            # PURE-BG move (Surf's sweeping wave = BG scroll + scanline, NO effect
            # sprites): fold the BG state into the signature so it isn't judged
            # "settled" while the wave is still sweeping — otherwise it gets cut
            # before the recede/fade ("ends too soon, no fade").
            bg = []
            for _lst in (bgscroll_out, bg2scroll_out, bg3scroll_out):
                if _lst:
                    bg.append(_lst[-1])
            if shadow_out and shadow_out[-1]:
                _sd = shadow_out[-1]
                bg.append((_sd.get("state", 0), _sd.get("axis", 0)))
            return (sprites, tuple(bg))

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
            if k == "monbg":
                # Copy a battler's mon to a BG layer (sets gBattle_BGn_X + bgCopy so
                # a per-scanline mon-warp has the right base — Acid Armor etc.).
                try:
                    ex["engine_monbg"](store, int(op.get("arg", 0)))
                except Exception:
                    pass
            elif k == "createsprite":
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
        # The move's BG-setup task (if any) has set the anim-BG SCREEN_SIZE; read
        # it so the caller assembles the tilemap's screenblocks with the right
        # layout (a wide 512-px BG like Surf, not a stacked 256-px one).
        try:
            sz = ex["engine_bg_screen_size"](store)
            if sz is not None and sz >= 0:
                self._last_bg_screen_size = sz
        except Exception:
            pass
        return frames, trapped_on[0]
