"""shared_data.py — Shared data layer for PorySuite-Z unified editor.

ProjectData is the single source of truth for all project data. Both the
PorySuite (data editing) and EVENTide (script/map editing) sides read from
and write to this object. Change signals notify the other side instantly.

Phase 2: wraps existing data sources (source_data and ConstantsManager)
and adds change signals between them. Does NOT replace their internal
storage yet — that comes in a later phase.
"""

import os
from pathlib import Path
from collections import OrderedDict

from PyQt6.QtCore import QObject, pyqtSignal


class ProjectData(QObject):
    """Single source of truth for all project data.

    Wraps PorySuite's source_data (PokemonDataManager) and EVENTide's
    ConstantsManager + text/script data, providing change signals so
    both sides stay in sync.
    """

    # ── Change signals ───────────────────────────────────────────────────
    # Emitted when the corresponding data is modified by either editor.
    # The other editor can connect to these and refresh its UI.
    trainers_changed = pyqtSignal()
    species_changed = pyqtSignal()
    items_changed = pyqtSignal()
    moves_changed = pyqtSignal()
    texts_changed = pyqtSignal()         # text.inc content changed
    scripts_changed = pyqtSignal()       # scripts.inc content changed
    maps_changed = pyqtSignal()          # map data changed
    constants_changed = pyqtSignal()     # any constants reloaded

    def __init__(self, project_info: dict, parent=None):
        super().__init__(parent)
        self.project_info = project_info
        self.project_dir = project_info.get("dir", "")
        self.project_name = project_info.get("name",
                                              os.path.basename(self.project_dir))

        # ── Data sources (set by attach methods) ─────────────────────────
        self._source_data = None          # PokemonDataManager from PorySuite
        self._constants_manager = None    # ConstantsManager class from EVENTide
        self._event_editor = None         # EventEditorTab reference

        # ── Text data cache ──────────────────────────────────────────────
        # Maps text label -> text content, aggregated from all text.inc files.
        # Updated when EVENTide loads/saves a map, or when the trainers tab
        # edits dialogue text.
        self._texts: OrderedDict = OrderedDict()

    # ═════════════════════════════════════════════════════════════════════
    # Attach data sources
    # ═════════════════════════════════════════════════════════════════════

    def attach_source_data(self, source_data):
        """Attach PorySuite's PokemonDataManager."""
        self._source_data = source_data

    def attach_constants_manager(self, constants_manager_class):
        """Attach EVENTide's ConstantsManager class (not an instance — it's a class with class attrs)."""
        self._constants_manager = constants_manager_class

    def attach_event_editor(self, event_editor_tab):
        """Attach EVENTide's EventEditorTab for text/script access."""
        self._event_editor = event_editor_tab

    # ═════════════════════════════════════════════════════════════════════
    # Trainer data — bridge between PorySuite and EVENTide
    # ═════════════════════════════════════════════════════════════════════

    def get_trainers(self) -> dict:
        """Get all trainers from PorySuite's source_data."""
        if self._source_data:
            return self._source_data.get_pokemon_trainers() or {}
        return {}

    def get_trainer(self, constant: str) -> dict:
        """Get a single trainer by constant name."""
        if self._source_data:
            return self._source_data.get_trainer(constant) or {}
        return {}

    def get_trainer_constants(self) -> list:
        """Get list of trainer constants from EVENTide's ConstantsManager."""
        if self._constants_manager:
            return list(self._constants_manager.TRAINERS or [])
        return list(self.get_trainers().keys())

    def notify_trainers_changed(self):
        """Call this after modifying trainer data — notifies all listeners."""
        self.trainers_changed.emit()

    # ═════════════════════════════════════════════════════════════════════
    # Species data
    # ═════════════════════════════════════════════════════════════════════

    def get_species(self) -> dict:
        if self._source_data:
            return self._source_data.get_pokemon_data() or {}
        return {}

    def get_species_list(self) -> list:
        """Return [(constant, display_name), ...] for dropdowns."""
        result = [("SPECIES_NONE", "None")]
        for k, v in self.get_species().items():
            name = v.get("name") or k.replace("SPECIES_", "").replace("_", " ").title()
            result.append((k, name))
        return result

    def notify_species_changed(self):
        self.species_changed.emit()

    # ═════════════════════════════════════════════════════════════════════
    # Items data
    # ═════════════════════════════════════════════════════════════════════

    def get_items(self) -> dict:
        if self._source_data:
            return self._source_data.get_pokemon_items() or {}
        return {}

    def get_items_list(self) -> list:
        """Return [(constant, display_name), ...] for dropdowns."""
        result = [("ITEM_NONE", "None")]
        for k, v in self.get_items().items():
            name = v.get("english") or v.get("name") or k
            result.append((k, name))
        return result

    def notify_items_changed(self):
        self.items_changed.emit()

    # ═════════════════════════════════════════════════════════════════════
    # Moves data
    # ═════════════════════════════════════════════════════════════════════

    def get_moves(self) -> dict:
        if self._source_data:
            return self._source_data.get_pokemon_moves() or {}
        return {}

    def get_moves_list(self) -> list:
        """Return [(constant, display_name), ...] for dropdowns."""
        result = [("MOVE_NONE", "None")]
        raw = self.get_moves()
        for k in sorted(raw.keys()):
            v = raw[k]
            name = v.get("name") or k.replace("MOVE_", "").replace("_", " ").title()
            result.append((k, name))
        return result

    def notify_moves_changed(self):
        self.moves_changed.emit()

    # ═════════════════════════════════════════════════════════════════════
    # Text data — from text.inc files
    # ═════════════════════════════════════════════════════════════════════

    def get_texts(self) -> OrderedDict:
        """Get current aggregated text labels from EVENTide."""
        if self._event_editor and hasattr(self._event_editor, '_texts'):
            return OrderedDict(self._event_editor._texts)
        return self._texts

    def set_text(self, label: str, content: str):
        """Update a text label's content."""
        self._texts[label] = content
        if self._event_editor and hasattr(self._event_editor, '_texts'):
            self._event_editor._texts[label] = content

    def notify_texts_changed(self):
        self.texts_changed.emit()

    # ═════════════════════════════════════════════════════════════════════
    # Text.inc search — find trainer dialogue labels
    # ═════════════════════════════════════════════════════════════════════

    def find_trainer_text_labels(self, trainer_constant: str) -> dict:
        """Search all text.inc files for labels related to a trainer.

        Returns a dict grouped by map:
        {
            "Route1": {
                "intro": ("Route1_Text_YoungsterBenIntro", "Let's battle!$"),
                "defeat": ("Route1_Text_YoungsterBenDefeat", "Aww, I lost!$"),
                "post": ("Route1_Text_YoungsterBenPostBattle", "That was fun!$"),
            },
            "Route2": { ... },
        }
        """
        results = {}

        # Strategy: search scripts.inc files for trainerbattle commands that
        # reference this trainer, then find the associated text labels.
        maps_dir = os.path.join(self.project_dir, "data", "maps")
        if not os.path.isdir(maps_dir):
            return results

        # Get a clean trainer name for label matching
        # e.g. TRAINER_YOUNGSTER_BEN -> YoungsterBen
        clean_name = trainer_constant.replace("TRAINER_", "")
        # Convert YOUNGSTER_BEN -> YoungsterBen
        parts = clean_name.split("_")
        camel_name = "".join(p.capitalize() for p in parts)

        for map_name in os.listdir(maps_dir):
            map_dir = os.path.join(maps_dir, map_name)
            scripts_path = os.path.join(map_dir, "scripts.inc")
            text_path = os.path.join(map_dir, "text.inc")

            if not os.path.isfile(scripts_path):
                continue

            # Check if this map's scripts reference this trainer
            try:
                with open(scripts_path, "r", encoding="utf-8") as f:
                    scripts_content = f.read()
            except Exception:
                continue

            if trainer_constant not in scripts_content:
                continue

            # Found a reference. Now check text.inc for associated labels.
            if not os.path.isfile(text_path):
                continue

            try:
                from eventide.backend.eventide_utils import parse_text_inc
                texts = parse_text_inc(Path(text_path))
            except Exception:
                continue

            map_texts = {}
            for label, content in texts.items():
                label_lower = label.lower()
                camel_lower = camel_name.lower()

                if camel_lower not in label_lower:
                    continue

                # Classify the label type
                if "intro" in label_lower:
                    map_texts["intro"] = (label, content)
                elif "defeat" in label_lower:
                    map_texts["defeat"] = (label, content)
                elif "postbattle" in label_lower or "post" in label_lower:
                    map_texts["post"] = (label, content)
                else:
                    # Generic text label for this trainer
                    map_texts.setdefault("other", [])
                    if isinstance(map_texts["other"], list):
                        map_texts["other"].append((label, content))

            if map_texts:
                results[map_name] = map_texts

        return results

    # ═════════════════════════════════════════════════════════════════════
    # Save all
    # ═════════════════════════════════════════════════════════════════════

    def save_all(self, porysuite_window=None, eventide_window=None):
        """Save all data from both editors.

        Returns True if anything was saved.
        """
        saved = False

        if porysuite_window and porysuite_window.isWindowModified():
            try:
                porysuite_window.update_save()
                saved = True
            except Exception as e:
                print(f"Error saving PorySuite data: {e}")

        if eventide_window and eventide_window.isWindowModified():
            try:
                eventide_window.event_editor_tab._on_save()
                eventide_window.setWindowModified(False)
                saved = True
            except Exception as e:
                print(f"Error saving EVENTide data: {e}")

        return saved
