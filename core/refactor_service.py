import os
import json
import re
import logging
from typing import List, Tuple

from local_env import LocalUtil


PreviewEntry = Tuple[str, int, str, str]


class RefactorService:
    """Utility for renaming constants across a FireRed project."""

    def __init__(self, project_info: dict):
        self.util = LocalUtil(project_info)
        self.project_info = project_info
        root = self.util.repo_root()
        self.history_path = os.path.join(root, "src", "data", "rename_history.json")
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                self.history = json.load(f)
        except Exception:
            self.history = {}
        # Pending operations staged until File > Save
        self.pending: list[dict] = []
        self._logger = None
        self._changed_files: set[str] = set()

    def _build_tokens(self, old_const: str, new_const: str, display: str | None) -> list[tuple[re.Pattern, str]]:
        old_base = old_const[len("SPECIES_") :] if old_const.startswith("SPECIES_") else old_const
        new_base = new_const[len("SPECIES_") :] if new_const.startswith("SPECIES_") else new_const
        old_camel = self._camel(old_base)
        new_camel = self._camel(display or new_base)
        old_slug = self._slug(old_base)
        new_slug = self._slug(display or new_base)

        patterns: list[tuple[re.Pattern, str]] = []
        patterns.append((re.compile(rf"\b{re.escape(old_const)}\b"), new_const))
        patterns.append((re.compile(rf"\bNATIONAL_DEX_{re.escape(old_base)}\b"), f"NATIONAL_DEX_{new_base}"))
        for sym in ("gMonFrontPic_", "gMonBackPic_", "gMonIcon_", "gMonFootprint_"):
            patterns.append((re.compile(rf"\b{sym}{re.escape(old_camel)}\b"), f"{sym}{new_camel}"))
        patterns.append((re.compile(rf"\bg{re.escape(old_camel)}PokedexText\b"), f"g{new_camel}PokedexText"))
        patterns.append((re.compile(rf"\bCRY_{re.escape(old_base)}\b"), f"CRY_{new_base}"))
        patterns.append((re.compile(rf"graphics/pokemon/{re.escape(old_slug)}\b"), f"graphics/pokemon/{new_slug}"))
        patterns.append((re.compile(rf"\b{re.escape(old_slug)}\b"), new_slug))
        patterns.append((re.compile(rf"\b{re.escape(old_camel)}\b"), new_camel))
        patterns.append((re.compile(rf"\b{re.escape(old_base.upper())}\b"), new_base.upper()))
        return patterns

    def _build_patch_plan(self, old_const: str, new_const: str, display: str | None) -> dict:
        """Build a narrow, FireRed-safe patch plan for a single species.

        Files targeted:
        - src/data/text/species_names.h (one line for the species)
        - src/data/pokemon/pokedex_entries.h (index const and .description symbol line)
        - src/data/pokemon/pokedex_text_fr.h (definition line for the description symbol)
        """
        root = self.util.repo_root()
        plan = {"files": [], "count": 0}

        # species_names.h
        names_path = os.path.join(root, "src", "data", "text", "species_names.h")
        if os.path.isfile(names_path):
            try:
                with open(names_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                lines = []
            edits = []
            pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*_\(\"(.*?)\"\)")
            for i, ln in enumerate(lines):
                m = pat.search(ln)
                if not m:
                    continue
                const = m.group(1)
                if const == old_const or const == new_const:
                    new_line = re.sub(r'_\(".*?"\)', f'_("{display}")', ln)
                    if const == old_const:
                        new_line = new_line.replace(old_const, new_const)
                    if new_line != ln:
                        edits.append({"line": i + 1, "before": ln.rstrip("\n"), "after": new_line.rstrip("\n")})
                    break
            if edits:
                plan["files"].append({"path": os.path.relpath(names_path, root), "edits": edits})
                plan["count"] += len(edits)

        # pokedex_entries.h: capture species block and description symbol
        entries_path = os.path.join(root, "src", "data", "pokemon", "pokedex_entries.h")
        desc_symbol_old = None
        desc_symbol_new = None
        if os.path.isfile(entries_path):
            try:
                with open(entries_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                lines = []
            edits = []
            i = 0
            start_pat = re.compile(r"^\s*\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*\{")
            while i < len(lines):
                ln = lines[i]
                m = start_pat.match(ln)
                if m and (m.group(1) == old_const or m.group(1) == new_const):
                    # If header uses old const, replace with new const
                    if m.group(1) == old_const:
                        new_header = ln.replace(old_const, new_const)
                        if new_header != ln:
                            edits.append({"line": i + 1, "before": ln.rstrip("\n"), "after": new_header.rstrip("\n")})
                    # Scan block for description
                    j = i + 1
                    brace_depth = 1
                    desc_pat = re.compile(r"\.description\s*=\s*(g[A-Za-z0-9_]+)\s*,")
                    while j < len(lines) and brace_depth > 0:
                        l2 = lines[j]
                        brace_depth += l2.count("{")
                        brace_depth -= l2.count("}")
                        dm = desc_pat.search(l2)
                        if dm and desc_symbol_old is None:
                            desc_symbol_old = dm.group(1)
                            # Build new symbol from display/new base
                            base = new_const[len("SPECIES_") :] if new_const.startswith("SPECIES_") else new_const
                            new_camel = self._camel(base)
                            desc_symbol_new = f"g{new_camel}PokedexText"
                            new_line = l2.replace(desc_symbol_old, desc_symbol_new)
                            if new_line != l2:
                                edits.append({"line": j + 1, "before": l2.rstrip("\n"), "after": new_line.rstrip("\n")})
                        j += 1
                    break
                i += 1
            if edits:
                plan["files"].append({"path": os.path.relpath(entries_path, root), "edits": edits})
                plan["count"] += len(edits)

        # pokedex_text_fr.h: rename only the captured description symbol definition
        text_path = os.path.join(root, "src", "data", "pokemon", "pokedex_text_fr.h")
        if desc_symbol_old and desc_symbol_new and os.path.isfile(text_path):
            try:
                with open(text_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                lines = []
            edits = []
            def_pat = re.compile(rf"^\s*const\s+u8\s+{re.escape(desc_symbol_old)}\s*\[\]\s*=\s*_")
            for i, ln in enumerate(lines):
                if def_pat.match(ln):
                    new_ln = ln.replace(desc_symbol_old, desc_symbol_new)
                    if new_ln != ln:
                        edits.append({"line": i + 1, "before": ln.rstrip("\n"), "after": new_ln.rstrip("\n")})
                    break
            if edits:
                plan["files"].append({"path": os.path.relpath(text_path, root), "edits": edits})
                plan["count"] += len(edits)

        return plan

    def _write_history(self) -> None:
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2)

    def _record(self, category: str, old: str, new: str) -> None:
        self.history.setdefault(category, []).append({"old": old, "new": new})
        self._write_history()

    def _rename_in_file(self, path: str, old: str, new: str, preview: List[PreviewEntry]) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
                lines = f.readlines()
        except OSError:
            return
        changed = False
        for idx, ln in enumerate(lines):
            if old in ln:
                newline = ln.replace(old, new)
                if newline != ln:
                    preview.append((os.path.relpath(path, self.util.repo_root()), idx + 1, ln.rstrip(), newline.rstrip()))
                    lines[idx] = newline
                    changed = True
        if changed:
            with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
                f.writelines(lines)
            rel = os.path.relpath(path, self.util.repo_root())
            self._changed_files.add(rel)
            if self._logger:
                self._logger(f"Updated {rel}")

    def _multi_search_and_replace(self, pairs: "List[Tuple[str, str]]", preview_only: bool) -> List[PreviewEntry]:
        """Scan the source tree once, applying ALL (old, new) pairs atomically per line.

        This is the cascade-safe alternative to calling :meth:`_search_and_replace`
        repeatedly in a loop.  The old behaviour was:

            for a, b in tokens:
                self._search_and_replace(a, b, preview_only=False)

        which re-reads every file per token and, critically, re-scans the
        PREVIOUS pass's output.  When a new name contains the old name as a
        substring (e.g. renaming ``Octo`` → ``Octorock``), a later token like
        ``OCTO → OCTOROCK`` finds its own pattern inside ``SPECIES_OCTOROCK``
        that the first pass just wrote, and cascades to ``SPECIES_OCTOROCKROCK``.

        This method builds a single combined regex from all token keys
        (``re.escape`` + ``|`` alternation), sorted longest-first so overlapping
        prefixes resolve to the longer match (``SPECIES_OCTO`` beats ``OCTO`` at
        the same position).  ``re.sub`` advances past each replacement, so a
        newly-written substring can never be re-matched by a later token in the
        SAME sweep.  That is the exact property needed to kill the cascade.

        Word-boundary matching is NOT applied here — the existing substring
        semantics are preserved for compatibility with identifiers that embed
        the base name (e.g. ``gMonFrontPic_Octo``).  The fix is strictly about
        stopping self-cascade, not about tightening which matches occur.
        """
        # Deduplicate identity and empty pairs; preserve first occurrence.
        seen: set = set()
        clean: "List[Tuple[str, str]]" = []
        for a, b in pairs:
            if not a or a == b or a in seen:
                continue
            seen.add(a)
            clean.append((a, b))
        if not clean:
            return []
        # Longest old-key first so longer patterns win over shorter prefixes.
        clean.sort(key=lambda p: len(p[0]), reverse=True)
        mapping = {a: b for a, b in clean}
        combined = re.compile("|".join(re.escape(a) for a, _ in clean))

        root = self.util.repo_root()
        preview: List[PreviewEntry] = []
        scan_spec = [
            ("src",     {".c", ".h"}),
            ("include", {".c", ".h"}),
            ("data",    {".inc", ".s", ".json", ".pory"}),
            ("sound",   {".inc", ".s"}),
            (".",       {".mk"}),
        ]
        try:
            from PyQt6.QtWidgets import QApplication
            _app = QApplication.instance()
        except Exception:
            _app = None
        _file_count = 0
        for folder, exts in scan_spec:
            base = os.path.join(root, folder)
            if not os.path.isdir(base):
                continue
            for dirpath, _, filenames in os.walk(base):
                for name in filenames:
                    if not any(name.endswith(ext) for ext in exts):
                        continue
                    file_path = os.path.join(dirpath, name)
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="surrogateescape") as f:
                            lines = f.readlines()
                    except OSError:
                        continue
                    changed = False
                    new_lines = list(lines)
                    for idx, ln in enumerate(lines):
                        if not combined.search(ln):
                            continue
                        new_ln = combined.sub(lambda m: mapping[m.group(0)], ln)
                        if new_ln != ln:
                            preview.append((os.path.relpath(file_path, root), idx + 1, ln.rstrip(), new_ln.rstrip()))
                            new_lines[idx] = new_ln
                            changed = True
                    if changed and not preview_only:
                        try:
                            with open(file_path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
                                f.writelines(new_lines)
                            rel = os.path.relpath(file_path, root)
                            self._changed_files.add(rel)
                            if self._logger:
                                self._logger(f"Updated {rel}")
                        except OSError:
                            pass
                    _file_count += 1
                    if _app is not None and _file_count % 50 == 0:
                        _app.processEvents()
        return preview

    def _search_and_replace(self, old: str, new: str, preview_only: bool) -> List[PreviewEntry]:
        root = self.util.repo_root()
        preview: List[PreviewEntry] = []
        # Walk C/header files under src/ and include/, plus assembler/script
        # includes under data/ (maps, event scripts, etc.)
        scan_spec = [
            ("src",     {".c", ".h"}),
            ("include", {".c", ".h"}),
            # .json covers data/maps/**/map.json hidden-item / event-item entries
            # (separate from src/data/*.json which are handled by _rename_in_json)
            # .pory covers Poryscript source files under data/
            ("data",    {".inc", ".s", ".json", ".pory"}),
            # sound/ has .inc files that reference cry filenames by slug
            # (e.g. "sound/direct_sound_samples/cries/bulbasaur.bin")
            ("sound",   {".inc", ".s"}),
            # .mk files at the project root reference graphics assets by
            # slug name (e.g. old_bulbasaur.4bpp in graphics_file_rules.mk).
            (".",       {".mk"}),
        ]
        try:
            from PyQt6.QtWidgets import QApplication
            _app = QApplication.instance()
        except Exception:
            _app = None
        _file_count = 0
        for folder, exts in scan_spec:
            base = os.path.join(root, folder)
            if not os.path.isdir(base):
                continue
            for dirpath, _, filenames in os.walk(base):
                for name in filenames:
                    if not any(name.endswith(ext) for ext in exts):
                        continue
                    file_path = os.path.join(dirpath, name)
                    if preview_only:
                        try:
                            with open(file_path, "r", encoding="utf-8", errors="surrogateescape") as f:
                                for i, ln in enumerate(f, 1):
                                    if old in ln:
                                        preview.append((os.path.relpath(file_path, root), i, ln.rstrip(), ln.rstrip().replace(old, new)))
                        except OSError:
                            continue
                    else:
                        self._rename_in_file(file_path, old, new, preview)
                    # Let Qt process events every 50 files so the UI stays responsive
                    _file_count += 1
                    if _app is not None and _file_count % 50 == 0:
                        _app.processEvents()
        return preview

    def _rename_in_json(self, path: str, old: str, new: str, display: str | None = None) -> None:
        """Rename a key in a JSON mapping and update display fields when present.

        - For species.json, also updates species_info.speciesName and any form entries.
        - For other JSONs (items/moves), updates the "name" field when present.
        """
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if old not in data:
            return
        entry = data.pop(old)
        data[new] = entry
        if isinstance(entry, dict) and display is not None:
            try:
                entry["name"] = display
            except Exception:
                pass
            si = entry.get("species_info") if isinstance(entry, dict) else None
            if isinstance(si, dict):
                si["speciesName"] = display
            forms = entry.get("forms") if isinstance(entry, dict) else None
            if isinstance(forms, dict):
                for form in forms.values():
                    if isinstance(form, dict):
                        fsi = form.get("species_info")
                        if isinstance(fsi, dict):
                            fsi["speciesName"] = display
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2)
        except Exception:
            logging.exception("Failed writing %s", path)

    def _rename_in_evolutions_json(self, path: str, old: str, new: str) -> bool:
        """Rename species key and any targetSpecies references in evolutions.json."""
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        changed = False
        if old in data:
            data[new] = data.pop(old)
            changed = True
        for evos in data.values():
            if isinstance(evos, list):
                for evo in evos:
                    if isinstance(evo, dict) and evo.get("targetSpecies") == old:
                        evo["targetSpecies"] = new
                        changed = True
        if changed:
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                logging.exception("Failed writing %s", path)
                return False
        return changed

    def _rename_in_moves_json(self, path: str, old: str, new: str) -> bool:
        """Rename species key inside species_moves section of moves.json."""
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        sm = data.get("species_moves") if isinstance(data, dict) else None
        if not isinstance(sm, dict) or old not in sm:
            return False
        sm[new] = sm.pop(old)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2)
        except Exception:
            logging.exception("Failed writing %s", path)
            return False
        return True

    def _rename_in_species_graphics_json(self, path: str, old_camel: str, new_camel: str, old_slug: str, new_slug: str) -> bool:
        """Rename graphic symbol keys and path values in species_graphics.json.

        Keys use the form ``<prefix>_<CamelBase>`` (e.g. ``gMonFrontPic_Pika``).
        Values use the form ``graphics/pokemon/<slug>/<file>.<ext>``.

        Matching is anchored — NOT substring — to avoid the cascade bug where
        renaming ``Pika → Pikachu`` while an unrelated ``Pikachu`` species
        already exists would mangle ``gMonFrontPic_Pikachu`` into
        ``gMonFrontPic_Pikachuchu`` via a naive ``str.replace``.
        """
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        if not isinstance(data, dict):
            return False

        # Anchored key rewrite: only swap the suffix ``_<old_camel>`` at the end
        # of a symbol key.  ``Pika`` inside ``Pikachu`` is safe because the
        # check requires a leading underscore AND end-of-string.
        camel_suffix = "_" + old_camel

        def _rewrite_key(k: str) -> str:
            if k.endswith(camel_suffix):
                return k[: -len(old_camel)] + new_camel
            return k

        # Anchored path rewrite: split on ``/`` and only swap segments that
        # EXACTLY equal ``old_slug``.  ``pika`` inside ``pikachu`` is safe.
        def _rewrite_path(p: str) -> str:
            if "/" not in p and p != old_slug:
                return p
            segs = p.split("/")
            return "/".join(new_slug if s == old_slug else s for s in segs)

        updated = {}
        changed = False
        for k, v in data.items():
            new_k = _rewrite_key(k)
            if new_k != k:
                changed = True
            new_v = {}
            if isinstance(v, dict):
                for vk, vv in v.items():
                    if isinstance(vv, str):
                        # Symbol-style values (rare but possible) get the same
                        # anchored key rewrite; otherwise treat as a path.
                        if vv.endswith(camel_suffix) or vv == old_camel:
                            new_vv = vv[: -len(old_camel)] + new_camel if vv.endswith(camel_suffix) else new_camel
                        else:
                            new_vv = _rewrite_path(vv)
                        if new_vv != vv:
                            changed = True
                        new_v[vk] = new_vv
                    else:
                        new_v[vk] = vv
            else:
                new_v = v
            updated[new_k] = new_v
        if changed:
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(updated, f, indent=2)
            except Exception:
                logging.exception("Failed writing %s", path)
                return False
        return changed

    def _rename_in_starters_json(self, path: str, old: str, new: str) -> bool:
        """Update species constant in starters.json entries."""
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        if not isinstance(data, list):
            return False
        changed = False
        for entry in data:
            if isinstance(entry, dict) and entry.get("species") == old:
                entry["species"] = new
                changed = True
        if changed:
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                logging.exception("Failed writing %s", path)
                return False
        return changed

    def _camel(self, name: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", " ", name).strip()
        return "".join(part.capitalize() for part in base.split())

    def _slug(self, name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    def rename_species_thorough(self, old_const: str, new_const: str, display_name: str, preview: bool = False) -> List[PreviewEntry]:
        """Rename species constant, display names, references, and assets."""
        root = self.util.repo_root()
        previews: List[PreviewEntry] = []

        # Derive related tokens
        old_base = old_const[len("SPECIES_") :] if old_const.startswith("SPECIES_") else old_const
        new_base = new_const[len("SPECIES_") :] if new_const.startswith("SPECIES_") else new_const
        old_camel = self._camel(old_base)
        new_camel = self._camel(display_name or new_base)
        old_slug = self._slug(old_base)
        new_slug = self._slug(display_name or new_base)

        # 1) Replace common tokens across sources
        pairs = [
            (old_const, new_const),
            (f"NATIONAL_DEX_{old_base}", f"NATIONAL_DEX_{new_base}"),
            (f"gMonFrontPic_{old_camel}", f"gMonFrontPic_{new_camel}"),
            (f"gMonBackPic_{old_camel}", f"gMonBackPic_{new_camel}"),
            (f"gMonIcon_{old_camel}", f"gMonIcon_{new_camel}"),
            (f"gMonFootprint_{old_camel}", f"gMonFootprint_{new_camel}"),
            (f"g{old_camel}PokedexText", f"g{new_camel}PokedexText"),
            (f"CRY_{old_base}", f"CRY_{new_base}"),
            (f"graphics/pokemon/{old_slug}", f"graphics/pokemon/{new_slug}"),
        ]
        # General name replacements to catch remaining references
        pairs.extend([
            (old_camel, new_camel),
            (old_base.upper(), new_base.upper()),
            (old_slug, new_slug),
        ])
        for old, new in pairs:
            try:
                previews.extend(self._search_and_replace(old, new, True))
            except Exception:
                logging.exception("Rename token replacement failed: %s -> %s", old, new)

        # 2) Update species_names.h display strings (and const when present)
        names_path = os.path.join(root, "src", "data", "text", "species_names.h")
        if os.path.isfile(names_path):
            try:
                with open(names_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                lines = []
            changed = False
            pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*_\(\"(.*?)\"\)")
            for i, ln in enumerate(lines):
                m = pat.search(ln)
                if not m:
                    continue
                const = m.group(1)
                if const == old_const or const == new_const:
                    new_line = re.sub(r'_\(".*?"\)', f'_("{display_name}")', ln)
                    if const == old_const:
                        new_line = new_line.replace(old_const, new_const)
                    if new_line != ln:
                        previews.append((os.path.relpath(names_path, root), i + 1, ln.rstrip(), new_line.rstrip()))
                        lines[i] = new_line
                        changed = True
            # Defer actual write until apply_pending is called

        # 3) Rename graphics folder (with case-insensitive FS fallback)
        old_dir = os.path.join(root, "graphics", "pokemon", old_slug)
        new_dir = os.path.join(root, "graphics", "pokemon", new_slug)
        if os.path.isdir(old_dir) and old_dir != new_dir:
            previews.append((os.path.relpath(old_dir, root), 0, old_dir, new_dir))

        # 4) species.json key + display updates
        # Queue operation for apply on Save
        self.pending.append(
            {
                "op": "rename_species",
                "old": old_const,
                "new": new_const,
                "display": display_name,
            }
        )

        return previews

    def rename_species(self, old_const: str, new_const: str, display_name: str, preview: bool = False) -> List[PreviewEntry]:
        plan = self._build_patch_plan(old_const, new_const, display_name)
        previews: List[PreviewEntry] = []
        for f in plan.get("files", []):
            for e in f.get("edits", []):
                previews.append((f["path"], e["line"], e["before"], e["after"]))
        # Include token replacement previews across sources for SPECIES_* so
        # callers can see affected files like include/constants/species.h
        if preview:
            try:
                previews.extend(self._search_and_replace(old_const, new_const, preview_only=True))
                # Normalize Windows paths to forward slashes for stable previews
                previews = [
                    (p[0].replace("\\", "/"), p[1], p[2], p[3]) for p in previews
                ]
            except Exception:
                logging.exception("preview token search failed")
        self.pending.append({
            "op": "rename_species",
            "old": old_const,
            "new": new_const,
            "display": display_name,
            "plan": plan,
        })
        # Apply immediately for non-preview calls so tests and UI see changes
        # without requiring File > Save. This includes species.h token updates
        # performed in apply_pending.
        if not preview:
            try:
                self.apply_pending()
            except Exception:
                logging.exception("apply_pending failed for rename_species")
        return previews

    def preview_patch_plan(self, old_const: str, new_const: str, display_name: str) -> List[PreviewEntry]:
        """Build a patch plan without queuing and return as preview entries.

        Includes all JSON cache files that will be updated on Save so the
        shown count matches what apply_pending actually does.
        """
        plan = self._build_patch_plan(old_const, new_const, display_name)
        previews: List[PreviewEntry] = []
        for f in plan.get("files", []):
            for e in f.get("edits", []):
                previews.append((f["path"], e["line"], e["before"], e["after"]))

        root = self.util.repo_root()
        old_base = old_const[len("SPECIES_"):] if old_const.startswith("SPECIES_") else old_const
        new_base = new_const[len("SPECIES_"):] if new_const.startswith("SPECIES_") else new_const
        old_camel = self._camel(old_base)
        new_camel = self._camel(display_name or new_base)

        # species_graphics.json — one preview entry per renamed key
        sg_path = os.path.join(root, "src", "data", "species_graphics.json")
        if os.path.isfile(sg_path):
            try:
                with open(sg_path, "r", encoding="utf-8") as f:
                    sg = json.load(f)
                rel = os.path.relpath(sg_path, root).replace("\\", "/")
                for k in sg:
                    if old_camel in k:
                        previews.append((rel, 0, k, k.replace(old_camel, new_camel)))
            except Exception:
                pass

        # src/data/starters.json — flag each affected starter entry
        starters_path = os.path.join(root, "src", "data", "starters.json")
        if os.path.isfile(starters_path):
            try:
                with open(starters_path, "r", encoding="utf-8") as f:
                    starters = json.load(f)
                rel = os.path.relpath(starters_path, root).replace("\\", "/")
                if isinstance(starters, list):
                    for i, entry in enumerate(starters):
                        if isinstance(entry, dict) and entry.get("species") == old_const:
                            previews.append((rel, i + 1, old_const, new_const))
            except Exception:
                pass

        # src/data/evolutions.json — key rename + targetSpecies references
        evo_path = os.path.join(root, "src", "data", "evolutions.json")
        if os.path.isfile(evo_path):
            try:
                with open(evo_path, "r", encoding="utf-8") as f:
                    evos = json.load(f)
                rel = os.path.relpath(evo_path, root).replace("\\", "/")
                if isinstance(evos, dict):
                    if old_const in evos:
                        previews.append((rel, 0, old_const, new_const))
                    for evlist in evos.values():
                        if isinstance(evlist, list):
                            for evo in evlist:
                                if isinstance(evo, dict) and evo.get("targetSpecies") == old_const:
                                    previews.append((rel, 0, f"targetSpecies: {old_const}", f"targetSpecies: {new_const}"))
            except Exception:
                pass

        # src/data/moves.json — species_moves key
        moves_path = os.path.join(root, "src", "data", "moves.json")
        if os.path.isfile(moves_path):
            try:
                with open(moves_path, "r", encoding="utf-8") as f:
                    mv = json.load(f)
                rel = os.path.relpath(moves_path, root).replace("\\", "/")
                sm = mv.get("species_moves") if isinstance(mv, dict) else None
                if isinstance(sm, dict) and old_const in sm:
                    previews.append((rel, 0, old_const, new_const))
            except Exception:
                pass

        return previews

    def queue_species_rename(self, old_const: str, new_const: str, display_name: str, plan: dict | None = None) -> None:
        if plan is None:
            plan = self._build_patch_plan(old_const, new_const, display_name)
        self.pending.append({
            "op": "rename_species",
            "old": old_const,
            "new": new_const,
            "display": display_name,
            "plan": plan,
        })

    def rename_item(self, old_const: str, new_const: str, display_name: str, preview: bool = False) -> List[PreviewEntry]:
        previews = self._search_and_replace(old_const, new_const, True)
        # Queue for Save
        self.pending.append({"op": "rename_item", "old": old_const, "new": new_const, "display": display_name})
        return previews

    def rename_move(self, old_const: str, new_const: str, display_name: str, preview: bool = False) -> List[PreviewEntry]:
        previews = self._search_and_replace(old_const, new_const, True)
        # Queue for Save
        self.pending.append({"op": "rename_move", "old": old_const, "new": new_const, "display": display_name})
        return previews

    def _trainer_to_party_symbol(self, trainer_const: str) -> str:
        """Convert a TRAINER_* constant to its sParty_* C symbol.

        E.g. TRAINER_RIVAL_OAKS_LAB_BULBASAUR -> sParty_RivalOaksLabBulbasaur
        """
        base = trainer_const[len("TRAINER_"):] if trainer_const.startswith("TRAINER_") else trainer_const
        return "sParty_" + self._camel(base)

    def rename_ability(self, old_const: str, new_const: str, display_name: str, preview: bool = False) -> List[PreviewEntry]:
        """Rename an ability constant across the whole repo.

        Updates:
        - The ABILITY_* constant in abilities.h, battle_util.c, species_info.h, etc.
        - The display name in src/data/text/abilities.h (gAbilityNames entry)
        - The description variable name (sXxxDescription → sNewDescription)
        - The src/data/abilities.json key
        """
        old_base = old_const[len("ABILITY_"):] if old_const.startswith("ABILITY_") else old_const
        new_base = new_const[len("ABILITY_"):] if new_const.startswith("ABILITY_") else new_const
        old_desc_var = "s" + self._camel(old_base) + "Description"
        new_desc_var = "s" + self._camel(new_base) + "Description"

        previews: List[PreviewEntry] = []
        previews.extend(self._search_and_replace(old_const, new_const, preview_only=True))
        if old_desc_var != new_desc_var:
            previews.extend(self._search_and_replace(old_desc_var, new_desc_var, preview_only=True))

        if not preview:
            self.pending.append({
                "op": "rename_ability",
                "old": old_const,
                "new": new_const,
                "display": display_name,
                "old_desc_var": old_desc_var,
                "new_desc_var": new_desc_var,
            })

        return previews

    def rename_trainer_class(self, old_const: str, new_const: str, display_name: str, preview: bool = False) -> List[PreviewEntry]:
        """Rename a TRAINER_CLASS_* constant across the whole repo.

        Updates:
        - The TRAINER_CLASS_* constant itself everywhere it appears (opponents.h
          and trainers.h #define, trainer_class_names.h index, battle_main.c
          gTrainerMoneyTable key, data/trainers.json trainerClass field, etc.).
        - The display-name string in src/data/text/trainer_class_names.h.

        Intentionally does NOT rename the FACILITY_CLASS_* constant — that is
        a separate include/constants/trainer_types.h enum used by facility
        battles, and renaming the trainer class does not imply the facility
        class mapping should change.
        """
        previews: List[PreviewEntry] = []
        previews.extend(self._search_and_replace(old_const, new_const, preview_only=True))
        if not preview:
            self.pending.append({
                "op": "rename_trainer_class",
                "old": old_const,
                "new": new_const,
                "display": display_name,
            })
        return previews

    def rename_trainer(self, old_const: str, new_const: str, preview: bool = False) -> List[PreviewEntry]:
        """Rename a trainer constant across the whole repo.

        Updates:
        - The TRAINER_* constant itself in opponents.h, trainers.h, scripts, maps, etc.
        - The derived sParty_* party symbol in trainer_parties.h and trainers.h
        - The src/data/trainers.json key
        """
        old_party = self._trainer_to_party_symbol(old_const)
        new_party = self._trainer_to_party_symbol(new_const)

        previews: List[PreviewEntry] = []
        # Collect preview hits for both the constant and the party symbol
        previews.extend(self._search_and_replace(old_const, new_const, preview_only=True))
        if old_party != new_party:
            previews.extend(self._search_and_replace(old_party, new_party, preview_only=True))

        if not preview:
            self.pending.append({
                "op": "rename_trainer",
                "old": old_const,
                "new": new_const,
                "old_party": old_party,
                "new_party": new_party,
            })
            try:
                self.apply_pending()
            except Exception:
                logging.exception("apply_pending failed for rename_trainer")

        return previews

    def apply_pending(self, logger=None):
        """Apply all queued rename operations to disk. Called on File > Save."""
        if not self.pending:
            return []
        root = self.util.repo_root()
        ops = self.pending
        self.pending = []
        self._logger = logger
        self._changed_files = set()
        applied = []

        for op in ops:
            kind = op.get("op")
            old = op.get("old")
            new = op.get("new")
            display = op.get("display")
            if kind == "rename_species":
                plan = op.get("plan") or self._build_patch_plan(old, new, display)
                failures = []
                # Phase 1: validate and prepare updated buffers
                prepared: dict[str, list[str]] = {}
                for f in plan.get("files", []):
                    abs_path = os.path.join(root, f["path"]) if not os.path.isabs(f["path"]) else f["path"]
                    try:
                        with open(abs_path, "r", encoding="utf-8") as fh:
                            lines = fh.readlines()
                    except OSError:
                        failures.append(f["path"])
                        continue
                    new_lines = list(lines)
                    for e in f.get("edits", []):
                        idx = e["line"] - 1
                        if idx < 0 or idx >= len(new_lines):
                            failures.append(f["path"]) ; continue
                        if new_lines[idx].rstrip("\n") != e["before"]:
                            failures.append(f["path"]) ; continue
                        new_lines[idx] = e["after"] + "\n"
                    if f["path"] not in failures:
                        prepared[abs_path] = new_lines
                if failures:
                    if self._logger:
                        self._logger("Plan files changed since preview (skipping plan writes): " + ", ".join(sorted(set(failures))))
                    prepared = {}  # skip plan file writes, but still run token replacements below
                # Phase 2: backup and write
                backups: dict[str, list[str]] = {}
                try:
                    for abs_path, buf in prepared.items():
                        try:
                            with open(abs_path, "r", encoding="utf-8") as fh:
                                backups[abs_path] = fh.readlines()
                            with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
                                fh.writelines(buf)
                            rel = os.path.relpath(abs_path, root)
                            self._changed_files.add(rel)
                            if self._logger:
                                self._logger(f"Updated {rel}")
                        except OSError:
                            raise
                except Exception:
                    # Roll back plan file writes, but still proceed with token replacements
                    for abs_path, original in backups.items():
                        try:
                            with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
                                fh.writelines(original)
                        except Exception:
                            pass
                    if self._logger:
                        self._logger("Rolled back plan file writes — token replacements will still run")
                old_base = old[len("SPECIES_") :] if old.startswith("SPECIES_") else old
                new_base = new[len("SPECIES_") :] if new.startswith("SPECIES_") else new
                old_camel = self._camel(old_base)
                new_camel = self._camel(display or new_base)
                old_slug = self._slug(old_base)
                new_slug = self._slug(display or new_base)
                tokens = [
                    (old, new),
                    (f"NATIONAL_DEX_{old_base}", f"NATIONAL_DEX_{new_base}"),
                    (f"gMonFrontPic_{old_camel}", f"gMonFrontPic_{new_camel}"),
                    (f"gMonBackPic_{old_camel}", f"gMonBackPic_{new_camel}"),
                    (f"gMonIcon_{old_camel}", f"gMonIcon_{new_camel}"),
                    (f"gMonFootprint_{old_camel}", f"gMonFootprint_{new_camel}"),
                    (f"g{old_camel}PokedexText", f"g{new_camel}PokedexText"),
                    (f"CRY_{old_base}", f"CRY_{new_base}"),
                    (f"graphics/pokemon/{old_slug}", f"graphics/pokemon/{new_slug}"),
                    (old_camel, new_camel),
                    (old_base.upper(), new_base.upper()),
                    (old_slug, new_slug),
                ]
                # Atomic multi-token sweep.  Each file is scanned once with a
                # combined regex; longer tokens win at overlap; no pass can
                # re-match the output of an earlier pass.  This kills the
                # cascade bug where renaming e.g. Octo → Octorock produced
                # SPECIES_OCTOROCKROCK because the later OCTO→OCTOROCK token
                # was finding OCTO inside the just-written OCTOROCK.
                self._multi_search_and_replace(tokens, preview_only=False)

                # names file covered by plan

                # 2b) src/data/trainers.json — _search_and_replace only walks
                # data/ for .json and src/ for .c/.h, so src/data/trainers.json
                # is never reached.  Trainer constants embed the species base name
                # (e.g. TRAINER_RIVAL_OAKS_LAB_BULBASAUR) and must be updated so
                # parse_to_c_code regenerates trainers.h with the right constants.
                trainers_json_path = os.path.join(root, "src", "data", "trainers.json")
                if os.path.isfile(trainers_json_path):
                    _dummy: list = []
                    # Replace base-name token (e.g. BULBASAUR -> GHOMAKTITE) which
                    # is what embeds into trainer constant names.
                    self._rename_in_file(trainers_json_path, old_base.upper(), new_base.upper(), _dummy)
                    # Also replace the full SPECIES_ constant if it appears in trainer data.
                    self._rename_in_file(trainers_json_path, old, new, _dummy)
                    if self._logger:
                        self._logger("Updated src/data/trainers.json")

                # 3) Rename graphics folder now
                old_dir = os.path.join(root, "graphics", "pokemon", old_slug)
                new_dir = os.path.join(root, "graphics", "pokemon", new_slug)
                if os.path.isdir(old_dir) and old_dir != new_dir:
                    # Remove stale destination if it exists (e.g. leftover compiled
                    # build artifacts that survived git clean because they are gitignored)
                    if os.path.isdir(new_dir):
                        try:
                            import shutil as _shutil
                            _shutil.rmtree(new_dir)
                        except OSError:
                            logging.exception("Could not remove existing dir: %s", new_dir)
                    try:
                        import shutil as _shutil
                        _shutil.move(old_dir, new_dir)
                    except OSError:
                        logging.exception("Folder rename failed: %s -> %s", old_dir, new_dir)
                    else:
                        rel_old = os.path.relpath(old_dir, root)
                        rel_new = os.path.relpath(new_dir, root)
                        if self._logger:
                            self._logger(f"Moved {rel_old} -> {rel_new}")

                # 3b) Rename slug-named files and folders across all graphics/ and
                #     sound/ asset trees, excluding graphics/pokemon/ (handled above).
                #     Uses topdown=False so children are renamed before parents.
                _pokemon_gfx = os.path.join(root, "graphics", "pokemon") + os.sep
                for _asset_root in (
                    os.path.join(root, "graphics"),
                    os.path.join(root, "sound"),
                ):
                    if not os.path.isdir(_asset_root):
                        continue
                    for _dirpath, _dirnames, _filenames in os.walk(_asset_root, topdown=False):
                        # Skip graphics/pokemon/ — already renamed as a folder above
                        if (_dirpath + os.sep).startswith(_pokemon_gfx):
                            continue
                        # Rename files containing old_slug
                        for _fname in _filenames:
                            if old_slug not in _fname:
                                continue
                            _new_fname = _fname.replace(old_slug, new_slug)
                            if _new_fname == _fname:
                                continue
                            _old_fp = os.path.join(_dirpath, _fname)
                            _new_fp = os.path.join(_dirpath, _new_fname)
                            try:
                                os.rename(_old_fp, _new_fp)
                                if self._logger:
                                    _r = os.path.relpath(_old_fp, root).replace("\\", "/")
                                    self._logger(f"Renamed {_r} -> {_new_fname}")
                            except OSError:
                                logging.exception("Asset file rename failed: %s -> %s", _old_fp, _new_fp)
                        # Rename the directory itself if its name contains old_slug
                        _dname = os.path.basename(_dirpath)
                        if old_slug in _dname:
                            _new_dname = _dname.replace(old_slug, new_slug)
                            _new_dirpath = os.path.join(os.path.dirname(_dirpath), _new_dname)
                            try:
                                os.rename(_dirpath, _new_dirpath)
                                if self._logger:
                                    _r = os.path.relpath(_dirpath, root).replace("\\", "/")
                                    self._logger(f"Renamed dir {_r} -> {_new_dname}")
                            except OSError:
                                logging.exception("Asset dir rename failed: %s -> %s", _dirpath, _new_dirpath)

                # 4) species.json & pokedex.json now
                sp_json = os.path.join(root, "src", "data", "species.json")
                self._rename_in_json(sp_json, old, new, display)
                if self._logger:
                    self._logger(f"Updated src/data/species.json")
                # pokedex.json adjust species and dex_constant only
                dex_path = os.path.join(root, "src", "data", "pokedex.json")
                if os.path.isfile(dex_path):
                    try:
                        with open(dex_path, "r", encoding="utf-8") as f:
                            dex = json.load(f)
                    except Exception:
                        dex = None
                    if isinstance(dex, dict):
                        changed = False
                        for key in ("national_dex", "regional_dex"):
                            lst = dex.get(key)
                            if isinstance(lst, list):
                                for entry in lst:
                                    if not isinstance(entry, dict):
                                        continue
                                    if entry.get("species") == old:
                                        entry["species"] = new
                                        changed = True
                                    dc = entry.get("dex_constant")
                                    if dc == f"NATIONAL_DEX_{old_base}":
                                        entry["dex_constant"] = f"NATIONAL_DEX_{new_base}"
                                        changed = True
                        if changed:
                            try:
                                with open(dex_path, "w", encoding="utf-8") as f:
                                    json.dump(dex, f, indent=2)
                            except Exception:
                                logging.exception("Failed writing %s", dex_path)
                            else:
                                if self._logger:
                                    self._logger("Updated src/data/pokedex.json")

                # 5) src/data/species_graphics.json — graphic symbol keys and path strings
                sg_path = os.path.join(root, "src", "data", "species_graphics.json")
                if self._rename_in_species_graphics_json(sg_path, old_camel, new_camel, old_slug, new_slug):
                    if self._logger:
                        self._logger("Updated src/data/species_graphics.json")

                # 6) src/data/starters.json — SPECIES_ constant in starter entries
                starters_path = os.path.join(root, "src", "data", "starters.json")
                if self._rename_in_starters_json(starters_path, old, new):
                    if self._logger:
                        self._logger("Updated data/starters.json")

                # 7) src/data/evolutions.json — species key + targetSpecies references
                evo_path = os.path.join(root, "src", "data", "evolutions.json")
                if self._rename_in_evolutions_json(evo_path, old, new):
                    if self._logger:
                        self._logger("Updated src/data/evolutions.json")

                # 8) src/data/moves.json — species_moves key
                moves_path_json = os.path.join(root, "src", "data", "moves.json")
                if self._rename_in_moves_json(moves_path_json, old, new):
                    if self._logger:
                        self._logger("Updated src/data/moves.json species_moves")

                self._record("species", old, new)
                applied.append(op)

            elif kind == "rename_item":
                # tokens across code
                self._search_and_replace(old, new, preview_only=False)
                self._rename_in_json(os.path.join(root, "src", "data", "items.json"), old, new, op.get("display"))
                if self._logger:
                    self._logger("Updated src/data/items.json")
                self._record("items", old, new)
            elif kind == "rename_move":
                self._search_and_replace(old, new, preview_only=False)
                self._rename_in_json(os.path.join(root, "src", "data", "moves.json"), old, new, op.get("display"))
                # Update the in-game display name in move_names.h
                display = op.get("display")
                if display:
                    names_h = os.path.join(root, "src", "data", "text", "move_names.h")
                    if os.path.isfile(names_h):
                        try:
                            with open(names_h, "r", encoding="utf-8") as fh:
                                text = fh.read()
                            # The constant was already renamed by _search_and_replace,
                            # so look for the new constant and update the string value.
                            text = re.sub(
                                r'(\[' + re.escape(new) + r'\]\s*=\s*_\(")[^"]*(")',
                                lambda m: m.group(1) + display + m.group(2),
                                text,
                            )
                            with open(names_h, "w", encoding="utf-8", newline="\n") as fh:
                                fh.write(text)
                            if self._logger:
                                self._logger(f"Updated move name in move_names.h: {display}")
                        except Exception:
                            logging.exception("Failed updating move_names.h")
                if self._logger:
                    self._logger("Updated src/data/moves.json")
                self._record("moves", old, new)
            elif kind == "rename_ability":
                # Replace the ABILITY_* constant everywhere
                self._search_and_replace(old, new, preview_only=False)
                # Replace the description variable name
                old_dv = op.get("old_desc_var", "")
                new_dv = op.get("new_desc_var", "")
                if old_dv and new_dv and old_dv != new_dv:
                    self._search_and_replace(old_dv, new_dv, preview_only=False)
                # Update display name in gAbilityNames in src/data/text/abilities.h
                ab_display = op.get("display")
                if ab_display:
                    text_h = os.path.join(root, "src", "data", "text", "abilities.h")
                    if os.path.isfile(text_h):
                        try:
                            with open(text_h, "r", encoding="utf-8") as fh:
                                text = fh.read()
                            text = re.sub(
                                r'(\[' + re.escape(new) + r'\]\s*=\s*_\(")[^"]*(")',
                                lambda m: m.group(1) + ab_display + m.group(2),
                                text,
                            )
                            with open(text_h, "w", encoding="utf-8", newline="\n") as fh:
                                fh.write(text)
                            if self._logger:
                                self._logger(f"Updated ability name in abilities.h: {ab_display}")
                        except Exception:
                            logging.exception("Failed updating abilities.h display name")
                # Update abilities.json key
                ab_json = os.path.join(root, "src", "data", "abilities.json")
                self._rename_in_json(ab_json, old, new, ab_display)
                if self._logger:
                    self._logger(f"Renamed ability {old} -> {new}")
                self._record("abilities", old, new)
                applied.append(op)
            elif kind == "rename_trainer_class":
                # Replace the TRAINER_CLASS_* constant everywhere. This also
                # catches the [TRAINER_CLASS_X] key inside trainer_class_names.h
                # — the key is renamed automatically, only the string literal
                # needs a separate pass.
                self._search_and_replace(old, new, preview_only=False)
                if self._logger:
                    self._logger(f"Renamed trainer class {old} -> {new}")
                # Update the display-name string in trainer_class_names.h.
                # The key was already renamed above, so match the NEW const.
                tc_display = op.get("display")
                if tc_display:
                    names_h = os.path.join(root, "src", "data", "text", "trainer_class_names.h")
                    if os.path.isfile(names_h):
                        try:
                            with open(names_h, "r", encoding="utf-8") as fh:
                                text = fh.read()
                            new_text = re.sub(
                                r'(\[' + re.escape(new) + r'\]\s*=\s*_\(")[^"]*(")',
                                lambda m: m.group(1) + tc_display + m.group(2),
                                text,
                            )
                            if new_text != text:
                                with open(names_h, "w", encoding="utf-8", newline="\n") as fh:
                                    fh.write(new_text)
                                self._changed_files.add(names_h)
                                if self._logger:
                                    self._logger(f"Updated class name in trainer_class_names.h: {tc_display}")
                        except Exception:
                            logging.exception("Failed updating trainer_class_names.h display name")
                self._record("trainer_classes", old, new)
                applied.append(op)
            elif kind == "rename_trainer":
                old_party = op.get("old_party") or self._trainer_to_party_symbol(old)
                new_party = op.get("new_party") or self._trainer_to_party_symbol(new)
                # Replace trainer constant everywhere (.c and .h under src/ and include/)
                self._search_and_replace(old, new, preview_only=False)
                if self._logger:
                    self._logger(f"Renamed trainer constant {old} -> {new}")
                # Replace party symbol everywhere
                if old_party != new_party:
                    self._search_and_replace(old_party, new_party, preview_only=False)
                    if self._logger:
                        self._logger(f"Renamed party symbol {old_party} -> {new_party}")
                # Update trainers.json key
                trainers_json = os.path.join(root, "src", "data", "trainers.json")
                self._rename_in_json(trainers_json, old, new, None)
                if self._logger:
                    self._logger("Updated src/data/trainers.json")
                self._record("trainers", old, new)
                applied.append(op)
        # Summary log
        if self._logger and self._changed_files:
            self._logger(f"Saved {len(self._changed_files)} source files")
        self._logger = None
        return applied

    def preview_changes(self, old_const: str, new_const: str) -> List[PreviewEntry]:
        return self._search_and_replace(old_const, new_const, True)
