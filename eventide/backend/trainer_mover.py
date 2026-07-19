"""Move a trainer battle from one map to another — cleanly.

A trainer battle lives on a map as three things, all named after the map:
  1. a script block   `<Map>_EventScript_<Name>::` in data/maps/<Map>/scripts.inc
  2. dialogue text     `<Map>_Text_<Name>Intro/Defeat/PostBattle` in text.inc
  3. an object-event   in map.json whose "script" points at (1)

Hand-copying a battle to another map leaves every label still named after the
OLD map, so the symbol is defined twice (build error: "already defined") and the
copy's text pointers still aim at the old map's file. This service does the part
copy-paste doesn't: it re-prefixes every map-scoped label to the destination map,
rewrites the text pointers to match, re-homes each piece in the destination's
files, and removes them from the source — a true move that leaves nothing behind.

Standalone (pathlib/re/json only) so it runs without the Qt UI; the Trainer
editor's "Move battle to another map…" button drives it
(see trainers_tab_widget._on_move_battle).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_LABEL_RE = re.compile(r'^(\w+)::')


# ── raw .inc block helpers ───────────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def _write(path: Path, text: str) -> None:
    with path.open('w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def _label_blocks(text: str):
    """Yield (label, block_text) for each `label::` block — the label line
    through the line before the next top-level `word::` label (or EOF)."""
    lines = text.splitlines(keepends=True)
    i, n = 0, len(lines)
    while i < n:
        m = _LABEL_RE.match(lines[i])
        if m:
            label = m.group(1)
            start = i
            i += 1
            while i < n and not _LABEL_RE.match(lines[i]):
                i += 1
            yield label, ''.join(lines[start:i])
        else:
            i += 1


def _extract_block(text: str, label: str) -> str | None:
    for lb, block in _label_blocks(text):
        if lb == label:
            return block
    return None


def _defined_labels(text: str) -> set[str]:
    return {lb for lb, _ in _label_blocks(text)}


def _strip_blocks(text: str, labels: set[str]) -> tuple[str, int]:
    """Remove the `<label>::` block for each label in *labels* (label line
    through the next `word::` label or EOF). Collapses runs of blank lines."""
    labels = {l for l in labels if l}
    if not labels:
        return text, 0
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i, n, removed = 0, len(lines), 0
    while i < n:
        m = _LABEL_RE.match(lines[i])
        if m and m.group(1) in labels:
            removed += 1
            i += 1
            while i < n and not _LABEL_RE.match(lines[i]):
                i += 1
        else:
            out.append(lines[i])
            i += 1
    result = re.sub(r'\n{3,}', '\n\n', ''.join(out))
    return result, removed


def _apply_label_map(block: str, label_map: dict[str, str]) -> str:
    """Whole-word rename of every old→new label inside a block. `\\b` keeps a
    short label from matching inside a longer one (Rick vs Rick2)."""
    for old, new in label_map.items():
        block = re.sub(r'\b' + re.escape(old) + r'\b', new, block)
    return block


def _append_blocks(path: Path, blocks: list[str]) -> None:
    existing = _read(path)
    parts = [existing.rstrip('\n')] if existing.strip() else []
    parts.extend(b.rstrip('\n') for b in blocks)
    _write(path, '\n\n'.join(parts) + '\n')


def _write_json(path: Path, data) -> None:
    with path.open('w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False))
        f.write('\n')


# Vanilla FireRed keeps many trainers' battle scripts in ONE global file rather
# than a per-map scripts.inc. Their EventScript label still carries the owning
# map's name (Route11_EventScript_Yasu), so the map that owns the trainer's text
# and NPC is recoverable from the label prefix.
_GLOBAL_TRAINER_SCRIPTS = ("data", "scripts", "trainers.inc")


def _map_prefix_of(label: str) -> str:
    """Owning-map folder from a map-prefixed label:
    Route11_EventScript_Yasu -> Route11 ; Route11_Text_YasuIntro -> Route11.
    Returns '' when the label carries no recognisable map prefix."""
    for marker in ("_EventScript_", "_Text_"):
        if marker in label:
            return label.split(marker, 1)[0]
    return ""


def _has_primary_trainerbattle(block: str, const_tok: re.Pattern) -> bool:
    """True if the block starts an actual battle for the trainer (a
    ``trainerbattle_single/double/no_intro`` — NOT just a ``_rematch``
    sub-script, which shares the trainer const but isn't the primary entry)."""
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("trainerbattle") and const_tok.search(s):
            if "rematch" not in s.split(None, 1)[0]:
                return True
    return False


# ── the mover ────────────────────────────────────────────────────────────────

class TrainerMover:
    def __init__(self, root_dir: str):
        self.root = Path(root_dir)
        self.maps_dir = self.root / 'data' / 'maps'

    def list_maps(self) -> list[str]:
        """All map folder names (those with a map.json), sorted."""
        if not self.maps_dir.is_dir():
            return []
        return sorted(p.name for p in self.maps_dir.iterdir()
                      if (p / 'map.json').is_file())

    def _global_scripts_path(self) -> Path:
        return self.root.joinpath(*_GLOBAL_TRAINER_SCRIPTS)

    def find_placements(self, trainer_const: str) -> list[dict]:
        """Every place this trainer has a primary battle: per-map scripts.inc AND
        the vanilla global data/scripts/trainers.inc. Returns
        [{'map_folder', 'script_label', 'script_file'}] — 'map_folder' is the map
        that owns the trainer's text/NPC (derived from the label for global
        entries); 'script_file' is where the script block actually lives."""
        out = []
        seen = set()
        tok = re.compile(r'\b' + re.escape(trainer_const) + r'\b')
        # 1) per-map scripts.inc
        for folder in self.list_maps():
            sp = self.maps_dir / folder / 'scripts.inc'
            content = _read(sp)
            if not content or trainer_const not in content:
                continue
            for label, block in _label_blocks(content):
                if _has_primary_trainerbattle(block, tok):
                    key = (folder, label)
                    if key not in seen:
                        seen.add(key)
                        out.append({'map_folder': folder, 'script_label': label,
                                    'script_file': str(sp)})
        # 2) global trainers.inc (owning map derived from the label prefix)
        gpath = self._global_scripts_path()
        gcontent = _read(gpath)
        if gcontent and trainer_const in gcontent:
            for label, block in _label_blocks(gcontent):
                if _has_primary_trainerbattle(block, tok):
                    mapf = _map_prefix_of(label)
                    if mapf and (self.maps_dir / mapf).is_dir():
                        key = (mapf, label)
                        if key not in seen:
                            seen.add(key)
                            out.append({'map_folder': mapf, 'script_label': label,
                                        'script_file': str(gpath)})
        return out

    def _external_refs(self, labels: list[str], src_folder: str) -> dict[str, set]:
        """Maps (other than the source) whose scripts.inc/text.inc still name
        one of the labels being moved — those refs would break on rename."""
        hits: dict[str, set] = {}
        toks = [(l, re.compile(r'\b' + re.escape(l) + r'\b')) for l in labels]
        for folder in self.list_maps():
            if folder == src_folder:
                continue
            for fn in ('scripts.inc', 'text.inc'):
                c = _read(self.maps_dir / folder / fn)
                if not c:
                    continue
                found = {l for l, rx in toks if rx.search(c)}
                if found:
                    hits[f"data/maps/{folder}/{fn}"] = found
        return hits

    def _move_object_event(self, src_json: Path, dst_json: Path,
                           old_label: str, new_label: str, x: int, y: int) -> bool:
        try:
            sd = json.loads(_read(src_json))
            dd = json.loads(_read(dst_json))
        except (ValueError, OSError):
            return False
        s_objs = sd.get('object_events') or []
        moved = None
        for oe in list(s_objs):
            if oe.get('script') == old_label:
                moved = oe
                s_objs.remove(oe)
                break
        if moved is None:
            return False
        new_oe = dict(moved)
        new_oe['x'] = x
        new_oe['y'] = y
        new_oe['script'] = new_label
        dd.setdefault('object_events', []).append(new_oe)
        sd['object_events'] = s_objs
        _write_json(src_json, sd)
        _write_json(dst_json, dd)
        return True

    def move(self, trainer_const: str, src_folder: str, dst_folder: str,
             x: int = 1, y: int = 1, ignore_ref_warnings: bool = False) -> dict:
        """Perform (or preflight) the move. Returns a dict:
          ok            — the move happened
          blocked       — a hard error stopped it (nothing changed); see message
          needs_confirm — soft warnings only; re-call with ignore_ref_warnings=True
          message, warnings, summary
        No files are touched unless ok is True."""
        res = {'ok': False, 'blocked': False, 'needs_confirm': False,
               'message': '', 'warnings': [], 'notes': [], 'summary': {}}

        if src_folder == dst_folder:
            res['blocked'] = True
            res['message'] = "Source and destination are the same map."
            return res

        src_dir, dst_dir = self.maps_dir / src_folder, self.maps_dir / dst_folder
        src_scripts, src_text, src_json = (src_dir / 'scripts.inc',
                                           src_dir / 'text.inc', src_dir / 'map.json')
        dst_scripts, dst_text, dst_json = (dst_dir / 'scripts.inc',
                                           dst_dir / 'text.inc', dst_dir / 'map.json')
        if not src_json.is_file():
            res['blocked'] = True
            res['message'] = f"Source map '{src_folder}' not found."
            return res
        if not dst_json.is_file():
            res['blocked'] = True
            res['message'] = f"Destination map '{dst_folder}' not found."
            return res

        # 1) locate the trainer's primary battle block — first in the source
        #    map's scripts.inc, then in the vanilla global trainers.inc (where
        #    many FR trainers live). `script_src` is the file that actually holds
        #    the block; we strip from and write back to THAT file, while the text
        #    and NPC always come from the source map.
        tok = re.compile(r'\b' + re.escape(trainer_const) + r'\b')
        script_src = src_scripts
        src_scripts_txt = _read(src_scripts)
        script_label = script_block = None
        for label, block in _label_blocks(src_scripts_txt):
            if _has_primary_trainerbattle(block, tok):
                script_label, script_block = label, block
                break
        if not script_label:
            gpath = self._global_scripts_path()
            gtxt = _read(gpath)
            if gtxt and trainer_const in gtxt:
                for label, block in _label_blocks(gtxt):
                    if (_has_primary_trainerbattle(block, tok)
                            and _map_prefix_of(label) == src_folder):
                        script_src, src_scripts_txt = gpath, gtxt
                        script_label, script_block = label, block
                        break
        if not script_label:
            res['blocked'] = True
            res['message'] = (f"Couldn't find a trainerbattle script for "
                              f"{trainer_const} in {src_folder}.")
            return res

        prefix = src_folder + '_'
        if not script_label.startswith(prefix):
            res['notes'].append(
                f"Script label '{script_label}' isn't prefixed with the source "
                f"map name — it's moved as-is, not renamed.")

        # 2) which dialogue text moves: labels referenced by THIS block, defined
        #    in the source text.inc, prefixed with the source map, and NOT used
        #    by any other script on this map (shared text is left in place and
        #    referenced across maps).
        src_text_txt = _read(src_text)
        src_text_keys = _defined_labels(src_text_txt)
        referenced = set(re.findall(r'\b(\w+)\b', script_block))
        other_blocks = ''.join(b for lb, b in _label_blocks(src_scripts_txt)
                               if lb != script_label)
        # When the script lives in the global file, the map's own scripts.inc is
        # a separate file — include it so text shared with a map script is kept.
        if script_src != src_scripts:
            other_blocks += _read(src_scripts)
        move_texts, shared_left = [], []
        for l in src_text_keys:
            if l in referenced and l.startswith(prefix):
                if re.search(r'\b' + re.escape(l) + r'\b', other_blocks):
                    shared_left.append(l)
                else:
                    move_texts.append(l)
        for l in shared_left:
            res['notes'].append(
                f"Text '{l}' is shared with another script on {src_folder}; "
                f"left in place and referenced across maps.")

        # Warn if the moved block calls sibling scripts (e.g. a _rematch battle)
        # that stay behind in the source file — they won't follow the move.
        src_labels = {lb for lb, _ in _label_blocks(src_scripts_txt)} - {script_label}
        called_siblings = sorted(
            set(re.findall(r'\b(\w+_EventScript_\w+)\b', script_block)) & src_labels)
        for c in called_siblings:
            res['notes'].append(
                f"The moved script still calls '{c}', which stays on the source "
                f"(e.g. a rematch). Move or update it separately if needed.")

        # 3) new names — only source-prefixed labels are renamed
        rename = [script_label] if script_label.startswith(prefix) else []
        rename += move_texts
        # new prefix keeps the separating underscore: ViridianForest_X -> HyruleField_X
        label_map = {l: dst_folder + '_' + l[len(prefix):] for l in rename}
        new_script_label = label_map.get(script_label, script_label)

        # 4) duplicate-label guard (the "already defined" build error, pre-empted)
        dst_defined = _defined_labels(_read(dst_scripts)) | _defined_labels(_read(dst_text))
        # also labels the global trainers file already defines for the dest map
        dst_defined |= {l for l in _defined_labels(_read(self._global_scripts_path()))
                        if l.startswith(dst_folder + '_')}
        clash = sorted({nl for nl in ([new_script_label] + list(label_map.values()))
                        if nl in dst_defined})
        if clash:
            res['blocked'] = True
            res['message'] = ("The destination map already defines: "
                              + ", ".join(clash)
                              + ".\nMove cancelled so the build won't hit a "
                                "duplicate-label error.")
            return res

        # 5) dangling-reference guard (soft — user may proceed)
        ref_hits = self._external_refs(sorted(label_map.keys()), src_folder)
        if ref_hits and not ignore_ref_warnings:
            res['needs_confirm'] = True
            res['warnings'] = [f"{f} still references: {', '.join(sorted(ls))}"
                               for f, ls in sorted(ref_hits.items())]
            res['message'] = ("Other files reference the labels being moved. If "
                              "you continue, those references keep the OLD names "
                              "and may break the build.")
            return res

        # ── perform ──────────────────────────────────────────────────────────
        text_blocks = {l: _extract_block(src_text_txt, l) for l in move_texts}
        new_script_block = _apply_label_map(script_block, label_map)
        new_text_blocks = [_apply_label_map(text_blocks[l], label_map)
                           for l in move_texts if text_blocks.get(l) is not None]

        _append_blocks(dst_scripts, [new_script_block])
        if new_text_blocks:
            _append_blocks(dst_text, new_text_blocks)

        stripped_scripts, _ = _strip_blocks(src_scripts_txt, {script_label})
        _write(script_src, stripped_scripts)
        if move_texts:
            stripped_text, _ = _strip_blocks(src_text_txt, set(move_texts))
            _write(src_text, stripped_text)

        npc_moved = self._move_object_event(
            src_json, dst_json, script_label, new_script_label, x, y)
        if not npc_moved:
            res['notes'].append(
                "No NPC object-event was linked to this battle on the source "
                "map - nothing to move; place one in Porymap.")

        res['ok'] = True
        res['summary'] = {
            'trainer': trainer_const,
            'src_folder': src_folder, 'dst_folder': dst_folder,
            'new_script_label': new_script_label,
            'moved_texts': [label_map[l] for l in move_texts],
            'npc_moved': npc_moved, 'x': x, 'y': y,
        }
        return res
