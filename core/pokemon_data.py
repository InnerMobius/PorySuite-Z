from typing_extensions import override
from core import pokemon_data_base as pokemon_data
from core.pokemon_data_base import ReadSourceFile, WriteSourceFile, MissingSourceError
import re
import os
import json
import logging
from pathlib import Path
from local_env import LocalUtil
from . import pokemon_data_extractor as pee
from .refactor_service import RefactorService


logger = logging.getLogger(__name__)


class SpeciesData(pokemon_data.SpeciesData):
    """
    A class that represents the data for a species of Pokemon.

    This class provides methods for retrieving and setting information about a species, as well as
    processing generation operations and values. It also includes a method for parsing the species
    information for C code generation.

    Attributes:
        GEN_CONSTANTS (dict): A dictionary mapping generation constants to their corresponding values.
    """

    # pokefirered keeps sources at the repo root (src/data/...), not inside
    # a ``source/`` subdirectory.  Without this override the base class
    # prepends "source/" to every file path, making reads/writes miss.
    SOURCE_PREFIX = ""

    GEN_CONSTANTS = {
        "GEN_1": 0,
        "GEN_2": 1,
        "GEN_3": 2,
        "GEN_4": 3,
        "GEN_5": 4,
        "GEN_6": 5,
        "GEN_7": 6,
        "GEN_8": 7,
        "GEN_9": 8,
        "GEN_LATEST": 8,
    }

    def __init__(self, project_info, parent=None):
        # Initialise base class first so project_info is available
        super().__init__(project_info, parent)

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "species_info.h"),
            file_key="SPECIES_INFO_H",
        )
        # Also track Pokédex sources so edits in the UI are reflected in-engine
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "pokedex_entries.h"),
            file_key="POKEDEX_ENTRIES_H",
        )
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "pokedex_text_fr.h"),
            file_key="POKEDEX_TEXT_FR_H",
        )

        # No generated overlays; edits patch canonical headers in place only.

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.SpeciesDataExtractor)

    def missing_required_sources(self) -> list[str]:
        # SpeciesData handles all missing source files gracefully (logs and skips),
        # so it never blocks the top-level pre-flight check.
        return []

    def process_gen_operation(self, operation: dict) -> any:
        """
        Process the given operation of GEN_CONSTANTS and return the result.

        Args:
            operation (dict): The operation to be processed.

        Returns:
            any: The result of the operation.
        """
        if "param1" not in operation:
            return operation

        value = 0
        condition = operation["condition"]
        if condition == ">=":
            if (
                self.GEN_CONSTANTS[operation["param1"]]
                >= self.GEN_CONSTANTS[operation["param2"]]
            ):
                value = operation["true_value"]
            else:
                value = operation["false_value"]
        elif condition == "==":
            if (
                self.GEN_CONSTANTS[operation["param1"]]
                == self.GEN_CONSTANTS[operation["param2"]]
            ):
                value = operation["true_value"]
            else:
                value = operation["false_value"]
        elif condition == "<=":
            if (
                self.GEN_CONSTANTS[operation["param1"]]
                <= self.GEN_CONSTANTS[operation["param2"]]
            ):
                value = operation["true_value"]
            else:
                value = operation["false_value"]
        try:
            value = int(value)
        except ValueError:
            pass
        return value

    def process_value(self, value: dict | list) -> any:
        """
        Process the given value by applying the `process_gen_operation` method to each element in a list,
        or directly to the value if it is a dictionary.

        Args:
            value (list or dict): The value to be processed.

        Returns:
            The processed value.
        """
        if isinstance(value, list):
            for i in range(len(value)):
                if isinstance(value[i], dict):
                    value[i] = self.process_gen_operation(value[i])
        elif isinstance(value, dict):
            value = self.process_gen_operation(value)

        return value

    @override
    def get_species_info(self, species, key, form=None, index=None):
        """
        Retrieve information about a species.

        Args:
            species (str): The name of the species.
            key (str): The key of the information to retrieve.
            form (str, optional): The form of the species. Defaults to None.
            index (int, optional): The index of the value to retrieve if the value is a list. Defaults to None.

        Returns:
            The requested information about the species.
        """
        try:
            if form is None:
                value = self.data[species]["species_info"][key]
            else:
                value = self.data[species]["forms"][form]["species_info"][key]

            if isinstance(value, list) or isinstance(value, dict):
                value = self.process_value(value)

        except KeyError:
            if key.startswith("item"):
                value = "ITEM_NONE"
            elif key.startswith("description"):
                value = ""
            else:
                value = 0

        # FireRed fallback for Pokédex category/description when missing
        if key in ("categoryName", "description"):
            # Treat non-strings or empty strings as missing
            missing = not (isinstance(value, str) and value.strip())
            if missing and getattr(self, "parent", None):
                try:
                    dex_obj = self.parent.data.get("pokedex")
                    dex = getattr(dex_obj, "data", {}) if dex_obj else {}
                    nat = dex.get("national_dex", []) if isinstance(dex, dict) else []
                    entry = None
                    # Attempt to locate by dex_constant first
                    dex_const = None
                    try:
                        dex_const = self.parent.get_species_data(species, "dex_constant")
                    except Exception:
                        dex_const = None
                    if dex_const:
                        for d in nat:
                            if isinstance(d, dict) and d.get("dex_constant") == dex_const:
                                entry = d
                                break
                    # Fallback locate by species key
                    if entry is None:
                        for d in nat:
                            if isinstance(d, dict) and d.get("species") == species:
                                entry = d
                                break
                    if entry:
                        if key == "categoryName":
                            cat = entry.get("categoryName")
                            if isinstance(cat, str) and cat:
                                value = cat
                        elif key == "description":
                            text = entry.get("descriptionText")
                            if not text and entry.get("description"):
                                ptext = dex.get("pokedex_text", {})
                                text = ptext.get(entry.get("description"))
                            if isinstance(text, str):
                                value = text
                except Exception:
                    pass

        if index is not None and isinstance(value, list):
            try:
                value = value[index]
            except (TypeError, IndexError):
                value = 0

        return value

    @override
    def set_species_info(self, species, key, value, form=None):
        """
        Sets the information of a species or form.

        Overrides abstract method in the parent class.

        Args:
            species (str): The species name.
            key (str): The key of the information to be set.
            value (any): The value to be set.
            form (str, optional): The form name. Defaults to None.

        Returns:
            None
        """
        if key == "genderRatio" and not isinstance(value, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                pass

        if form is None:
            self.data[species]["species_info"][key] = value
        else:
            self.data[species]["forms"][form]["species_info"][key] = value

    @override
    def get_species_ability(self, species, ability_index, form=None) -> str:
        """
        Retrieves the ability of a species at a given ability index.

        Args:
            species (str): The species of the Pokemon.
            ability_index (int): The index of the ability.
            form (str, optional): The form of the Pokemon. Defaults to None.

        Returns:
            str: The ability of the species at the given ability index.
        """
        if form is None:
            species_info = self.data.get(species, {}).get("species_info", {})
        else:
            species_info = (
                self.data.get(species, {})
                .get("forms", {})
                .get(form, {})
                .get("species_info", {})
            )

        abilities = species_info.get("abilities", [])
        try:
            ability_value = abilities[ability_index]
        except IndexError:
            ability_value = "ABILITY_NONE"

        try:
            index = int(ability_value)
            if self.parent is not None:
                ability_value = self.parent.get_ability_by_id(index)
        except ValueError:
            pass

        return ability_value

    @override
    def species_info_key_exists(self, species, key, form=None):
        """
        Check if a specific key exists in the species_info dictionary for a given species and form.

        Args:
            species (str): The species of the Pokemon.
            key (str): The key to check for existence in the species_info dictionary.
            form (str, optional): The form of the Pokemon. Defaults to None.

        Returns:
            bool: True if the key exists in the species_info dictionary, False otherwise.
        """
        if form is None:
            species_info = self.data.get(species, {}).get("species_info", {})
        else:
            species_info = (
                self.data.get(species, {})
                .get("forms", {})
                .get(form, {})
                .get("species_info", {})
            )

        return key in species_info

    @override
    def parse_species_info(self, species_name, form_name=None):
        """
        Parses the species information for a given species and form.

        Args:
            species_name (str): The name of the species.
            form_name (str, optional): The name of the form. Defaults to None.

        Returns:
            str: The formatted C code for the species information.
        """

        def get(key, index=None):
            return self.get_species_info(species_name, key, form=form_name, index=index)

        if form_name is not None:
            species_constant = form_name
        else:
            species_constant = species_name

        # Split the species description into lines and format them for C code
        species_description = get("description").split("\n")
        species_description = [f'{" " * 12}"{line}' for line in species_description]
        species_description = (
            "COMPOUND_STRING(\n" + '\\n"\n'.join(species_description) + '")'
        )

        # Get the evolution data and format it for C code
        evolutions = get("evolutions")
        if isinstance(evolutions, list):
            evolutions = (
                "EVOLUTION("
                + (
                    ", ".join(
                        [
                            f"{{ {evo['method']}, {evo['param']}, {evo['targetSpecies']} }}"
                            for evo in evolutions
                        ]
                    )
                )
                + ")"
            )
        else:
            evolutions = 0

        code = f"""
    [{species_constant}] =
    {{
        .baseHP = {get("baseHP")},
        .baseAttack = {get("baseAttack")},
        .baseDefense = {get("baseDefense")},
        .baseSpeed = {get("baseSpeed")},
        .baseSpAttack = {get("baseSpAttack")},
        .baseSpDefense = {get("baseSpDefense")},
        .types = {{ {get("types", 0)}, {get("types", 1)} }},
        .catchRate = {get("catchRate")},
        .expYield = {get("expYield")},
        .evYield_HP = {get("evYield_HP")},
        .evYield_Attack = {get("evYield_Attack")},
        .evYield_Defense = {get("evYield_Defense")},
        .evYield_Speed = {get("evYield_Speed")},
        .evYield_SpAttack = {get("evYield_SpAttack")},
        .evYield_SpDefense = {get("evYield_SpDefense")},
        .itemCommon = {get("itemCommon")},
        .itemRare = {get("itemRare")},
        .genderRatio = {get("genderRatio")},
        .eggCycles = {get("eggCycles")},
        .friendship = {get("friendship")},
        .growthRate = {get("growthRate")},
        .eggGroups = {{ {get("eggGroups", 0)}, {get("eggGroups", 1)} }},
        .abilities = {{ {get("abilities", 0)}, {get("abilities", 1)}, {get("abilities", 2)} }},
        .safariZoneFleeRate = {get("safariZoneFleeRate")},
        .categoryName = _("{get("categoryName")}"),
        .speciesName = _("{get("speciesName")}"),
        .cryId = {get("cryId")},
        .natDexNum = {get("natDexNum")},
        .height = {get("height")},
        .weight = {get("weight")},
        .pokemonScale = {get("pokemonScale")},
        .pokemonOffset = {get("pokemonOffset")},
        .trainerScale = {get("trainerScale")},
        .trainerOffset = {get("trainerOffset")},
        .description = {species_description},
        .bodyColor = {get("bodyColor")},
        .noFlip = {get("noFlip")},
        .frontPic = {get("frontPic")}, .frontPicSize = MON_COORDS_SIZE({get("frontPicSize", 0)}, {get("frontPicSize", 1)}),
        .frontPicFemale = {get("frontPicFemale")},
        .frontPicSizeFemale = MON_COORDS_SIZE({get("frontPicSizeFemale", 0)}, {get("frontPicSizeFemale", 1)}),
        .frontPicYOffset = {get("frontPicYOffset")},
        .frontAnimFrames = {get("frontAnimFrames")},
        .frontAnimId = {get("frontAnimId")},
        .enemyMonElevation = {get("enemyMonElevation")},
        .frontAnimDelay = {get("frontAnimDelay")},
        .backPic = {get("backPic")}, .backPicSize = MON_COORDS_SIZE({get("backPicSize", 0)}, {get("backPicSize", 1)}),
        .backPicFemale = {get("backPicFemale")},
        .backPicSizeFemale = MON_COORDS_SIZE({get("backPicSizeFemale", 0)}, {get("backPicSizeFemale", 1)}),
        .backPicYOffset = {get("backPicYOffset")},
        .backAnimId = {get("backAnimId")},
        .palette = {get("palette")}, .shinyPalette = {get("shinyPalette")},
        .paletteFemale = {get("paletteFemale")}, .shinyPaletteFemale = {get("shinyPaletteFemale")},
        .iconSprite = {get("iconSprite")},
        .iconSpriteFemale = {get("iconSpriteFemale")},
        .iconPalIndex = {get("iconPalIndex")},
        .iconPalIndexFemale = {get("iconPalIndexFemale")},
        .footprint = {get("footprint")},
        .levelUpLearnset = {get("levelUpLearnset")}, .teachableLearnset = {get("teachableLearnset")},
        .evolutions = {evolutions},
        .formSpeciesIdTable = {get("formSpeciesIdTable")},
        .formChangeTable = {get("formChangeTable")},
        .isLegendary = {get("isLegendary")},
        .isMythical = {get("isMythical")},
        .isUltraBeast = {get("isUltraBeast")},
        .isParadoxForm = {get("isParadoxForm")},
        .isMegaEvolution = {get("isMegaEvolution")},
        .isPrimalReversion = {get("isPrimalReversion")},
        .isUltraBurst = {get("isUltraBurst")},
        .isGigantamax = {get("isGigantamax")},
        .isAlolanForm = {get("isAlolanForm")},
        .isGalarianForm = {get("isGalarianForm")},
        .isHisuianForm = {get("isHisuianForm")},
        .isPaldeanForm = {get("isPaldeanForm")},
        .cannotBeTraded = {get("cannotBeTraded")},
        .allPerfectIVs = {get("allPerfectIVs")},
    }},
        """
        return code

    @override
    def parse_to_c_code(self):
        """Persist species data into engine-used C headers.

        When _skip_parse_to_c is set, the mainwindow's direct header writers
        have already handled species_info.h, pokedex_entries.h, and
        pokedex_text_fr.h — skip to avoid double-writes.
        """
        super().parse_to_c_code()

        if getattr(self, "_skip_parse_to_c", False):
            return

        # ── Read species_info.h directly — no ReadSourceFile wrappers ──
        root = self.project_info.get("dir", "")
        header_path = os.path.join(root, "src", "data", "pokemon", "species_info.h")

        try:
            with open(header_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Warning: could not read {header_path}: {e}")
            return

        if not lines:
            print(f"Warning: {header_path} is empty; skipping header write")
            return

        def escape_c_string(s: str) -> str:
            # Convert to a safe C string, preserve newlines as \n
            return (
                s.replace("\\", r"\\").replace("\"", r"\"")
                .replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")
            )

        def format_description(desc: str) -> str:
            return f"COMPOUND_STRING(\"{escape_c_string(desc)}\")"

        def set_field(block: list[str], field: str, value_src: str) -> list[str]:
            # Replace `.field = ...,` line or insert before closing `},`
            pat = f".{field} ="
            replaced = False
            for i, ln in enumerate(block):
                if pat in ln:
                    # keep trailing comma and indentation
                    indent = ln.split(pat)[0]
                    # detect comma at end
                    comma = "," if "," in ln else ","
                    block[i] = f"{indent}{pat} {value_src}{comma}\n"
                    replaced = True
                    break
            if not replaced:
                # Insert before closing brace of this species struct
                for j in range(len(block) - 1, -1, -1):
                    if block[j].strip().startswith('}'):
                        indent = ""
                        k = j
                        # try to reuse indent from previous data line
                        for t in range(j - 1, max(j - 10, -1), -1):
                            stripped = block[t].lstrip()
                            if stripped and stripped[0] == '.':
                                indent = block[t][: block[t].find('.')]
                                break
                        block.insert(j, f"{indent}.{field} = {value_src},\n")
                        break
            return block

        # Rewrite per species block
        out: list[str] = []
        i = 0
        total_updates = 0
        while i < len(lines):
            ln = lines[i]
            out.append(ln)
            # Look for the start of a species entry:  [SPECIES_XXX] =
            if '[' in ln and '] =' in ln:
                # Extract species const if present
                try:
                    start_idx = ln.index('[') + 1
                    end_idx = ln.index(']')
                    species_const = ln[start_idx:end_idx].strip()
                except ValueError:
                    species_const = None
                # Accumulate block until closing `},`
                block: list[str] = []
                j = i + 1
                brace_depth = 0
                while j < len(lines):
                    block.append(lines[j])
                    if '{' in lines[j]:
                        brace_depth += lines[j].count('{')
                    if '}' in lines[j]:
                        brace_depth -= lines[j].count('}')
                        # Heuristic: a `},` line marks end of this species entry
                        if brace_depth <= 0 and '}' in lines[j]:
                            break
                    j += 1
                # If we know this species and have data, apply updates
                if species_const and species_const in self.data:
                    info = self.data[species_const].get("species_info", {})
                    before = list(block)

                    # ── Identity fields ──
                    sname = info.get("speciesName") or ""
                    cname = info.get("categoryName") or ""
                    desc = info.get("description") or ""
                    if sname:
                        block = set_field(block, "speciesName", f'_("{escape_c_string(sname)}")')
                    if cname:
                        block = set_field(block, "categoryName", f'_("{escape_c_string(cname)}")')
                    if desc:
                        block = set_field(block, "description", format_description(desc))

                    # ── Base stats ──
                    for stat_key in ("baseHP", "baseAttack", "baseDefense",
                                     "baseSpeed", "baseSpAttack", "baseSpDefense"):
                        sv = info.get(stat_key)
                        if isinstance(sv, int):
                            block = set_field(block, stat_key, str(sv))

                    # ── Types ──
                    types = info.get("types")
                    if isinstance(types, list) and len(types) >= 2:
                        block = set_field(block, "types", f'{{ {types[0]}, {types[1]} }}')

                    # ── Catch rate / EXP yield ──
                    for int_key in ("catchRate", "expYield"):
                        iv = info.get(int_key)
                        if isinstance(iv, int):
                            block = set_field(block, int_key, str(iv))

                    # ── EV yields ──
                    for ev_key in ("evYield_HP", "evYield_Attack", "evYield_Defense",
                                   "evYield_Speed", "evYield_SpAttack", "evYield_SpDefense"):
                        ev = info.get(ev_key)
                        if isinstance(ev, int):
                            block = set_field(block, ev_key, str(ev))

                    # ── Held items ──
                    for item_key in ("itemCommon", "itemRare"):
                        item_val = info.get(item_key)
                        if isinstance(item_val, str) and item_val:
                            block = set_field(block, item_key, item_val)

                    # ── Gender ratio ──
                    gr = info.get("genderRatio")
                    if isinstance(gr, int):
                        block = set_field(block, "genderRatio", str(gr))

                    # ── Breeding / growth ──
                    ec = info.get("eggCycles")
                    if isinstance(ec, int):
                        block = set_field(block, "eggCycles", str(ec))
                    fr = info.get("friendship")
                    if isinstance(fr, int):
                        block = set_field(block, "friendship", str(fr))
                    grate = info.get("growthRate")
                    if isinstance(grate, str) and grate:
                        block = set_field(block, "growthRate", grate)

                    # ── Egg groups ──
                    eg = info.get("eggGroups") or []
                    if isinstance(eg, list):
                        eg0 = eg[0] if len(eg) > 0 and eg[0] else "EGG_GROUP_NONE"
                        eg1 = eg[1] if len(eg) > 1 and eg[1] else eg0
                    else:
                        eg0 = eg1 = "EGG_GROUP_NONE"
                    if eg0 or eg1:
                        block = set_field(block, "eggGroups", f'{{ {eg0}, {eg1} }}')

                    # ── Abilities ──
                    ab = info.get("abilities")
                    if isinstance(ab, list) and len(ab) >= 2:
                        a0 = ab[0] if ab[0] else "ABILITY_NONE"
                        a1 = ab[1] if ab[1] else "ABILITY_NONE"
                        block = set_field(block, "abilities", f'{{ {a0}, {a1} }}')

                    # ── Misc flags ──
                    sfr = info.get("safariZoneFleeRate")
                    if isinstance(sfr, int):
                        block = set_field(block, "safariZoneFleeRate", str(sfr))
                    bc = info.get("bodyColor")
                    if isinstance(bc, str) and bc:
                        block = set_field(block, "bodyColor", bc)
                    nf = info.get("noFlip")
                    if nf in ("TRUE", "FALSE"):
                        block = set_field(block, "noFlip", nf)

                    if block != before:
                        total_updates += 1
                # Write back modified block
                out[-1:] = [lines[i]]  # ensure header line retained
                out.extend(block)
                i = j  # continue after block end
            i += 1

        # ── Write species_info.h directly — no WriteSourceFile wrappers ──
        try:
            with open(header_path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(out)
            print(f"Updated species_info.h for {total_updates} species")
        except Exception as e:
            print(f"Failed writing species_info.h: {e}")

        # Phase 2 (expanded): Update localized Pokédex sources
        # 1) Update .categoryName in pokedex_entries.h and capture description symbols per species
        dex_path = os.path.join(root, "src", "data", "pokemon", "pokedex_entries.h")
        try:
            with open(dex_path, "r", encoding="utf-8") as f:
                dex_lines = f.readlines()
        except Exception:
            dex_lines = []

        desc_symbol_by_species: dict[str, str] = {}
        updated_categories = 0
        if dex_lines:
            dout: list[str] = []
            i = 0
            current_nat: str | None = None
            species_const: str | None = None
            while i < len(dex_lines):
                ln = dex_lines[i]
                dout.append(ln)
                # Detect start of a National Dex block:
                # [NATIONAL_DEX_FOO] = {
                try:
                    stripped = ln.strip()
                except Exception:
                    stripped = ln
                if stripped.startswith("[") and "] =" in stripped:
                    # Extract NATIONAL_DEX_* token
                    try:
                        start = stripped.index("[") + 1
                        end = stripped.index("]")
                        current_nat = stripped[start:end].strip()
                    except ValueError:
                        current_nat = None
                    # Derive SPECIES_* from NATIONAL_DEX_*
                    species_const = None
                    if current_nat and current_nat.startswith("NATIONAL_DEX_"):
                        sc = "SPECIES_" + current_nat[len("NATIONAL_DEX_"):]
                        if sc in self.data:
                            species_const = sc
                # If inside a block for a known species, scan ahead to collect the block
                if species_const:
                    block: list[str] = []
                    j = i + 1
                    brace_depth = 0
                    while j < len(dex_lines):
                        block.append(dex_lines[j])
                        if '{' in dex_lines[j]:
                            brace_depth += dex_lines[j].count('{')
                        if '}' in dex_lines[j]:
                            brace_depth -= dex_lines[j].count('}')
                            if brace_depth <= 0 and '}' in dex_lines[j]:
                                break
                        j += 1

                    info = self.data.get(species_const, {}).get("species_info", {})
                    cname = info.get("categoryName") or ""
                    before = list(block)
                    # Update .categoryName if present in our data
                    if cname:
                        pat = ".categoryName ="
                        replaced = False
                        for k, bl in enumerate(block):
                            if pat in bl:
                                indent = bl.split(pat)[0]
                                block[k] = f'{indent}{pat} _("{cname.replace("\\", r"\\").replace("\"", r"\"")}"),\n'
                                replaced = True
                                break
                        if replaced:
                            updated_categories += 1
                    # Capture the description symbol for later replacement
                    try:
                        for bl in block:
                            if ".description" in bl and "=" in bl:
                                sym = bl.split("=", 1)[1].split(",", 1)[0].strip()
                                if sym:
                                    desc_symbol_by_species[species_const] = sym
                                    break
                    except Exception:
                        pass
                    # Write back modified block and skip ahead
                    dout[-1:] = [dex_lines[i]]
                    dout.extend(block)
                    i = j
                    species_const = None
                i += 1

            try:
                with open(dex_path, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(dout)
                if updated_categories:
                    print(f"Updated pokedex_entries.h categoryName for {updated_categories} species")
            except Exception as e:
                print(f"Failed writing pokedex_entries.h: {e}")

        # 2) Update description strings in pokedex_text_fr.h
        fr_path = os.path.join(root, "src", "data", "pokemon", "pokedex_text_fr.h")
        if desc_symbol_by_species:
            try:
                with open(fr_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
            except Exception:
                text_content = ""

            def esc(s: str) -> str:
                """Escape characters for C string literals.
                Only double-quotes need escaping; backslashes in user text
                are unlikely and would break the game's text encoder anyway.
                Normalise line endings to Unix."""
                return (
                    s.replace("\"", r"\"")
                    .replace("\r\n", "\n").replace("\r", "\n")
                )

            rewrites = 0
            if text_content:
                # For each species with a symbol, replace the entire definition
                # using vanilla formatting:
                # const u8 gFooPokedexText[] = _(
                #     "Line 1\n"
                #     "Line 2\n"
                #     "Last line");
                import re
                for sp, sym in desc_symbol_by_species.items():
                    new_text = (
                        self.data.get(sp, {}).get("species_info", {}).get("description")
                        or None
                    )
                    if not isinstance(new_text, str) or new_text.strip() == "":
                        continue

                    # Split into lines preserving manual breaks; escape quotes/backslashes
                    parts = esc(new_text).split("\n")
                    if not parts:
                        continue

                    # Build vanilla-formatted body with 4-space indentation
                    body_lines = []
                    for i, line in enumerate(parts):
                        suffix = r"\n" if i < len(parts) - 1 else ""
                        body_lines.append(f"    \"{line}{suffix}\"")
                    # Last line closes the definition with ");" on the same line
                    if body_lines:
                        body_lines[-1] = body_lines[-1] + ");"
                    replacement = (
                        f"const u8 {sym}[] = _(\n" + "\n".join(body_lines)
                    )

                    # Replace by simple string search to avoid regex pitfalls
                    prefix = f"const u8 {sym}[] = _("  # exact vanilla start
                    idx = text_content.find(prefix)
                    if idx == -1:
                        continue
                    # Find the end of the definition by locating the next ");"
                    end = text_content.find(");", idx)
                    if end == -1:
                        continue
                    new_def = prefix + "\n" + "\n".join(body_lines)
                    text_content = text_content[:idx] + new_def + text_content[end + 2 :]
                    rewrites += 1
                    print(f"Updated pokedex_text_fr.h: {sym}")

                if rewrites:
                    try:
                        with open(fr_path, "w", encoding="utf-8", newline="\n") as f:
                            f.write(text_content)
                        print(f"Updated pokedex_text_fr.h for {rewrites} entries")
                    except Exception as e:
                        print(f"Failed writing pokedex_text_fr.h: {e}")

            # Also update pokedex_text_lg.h so both files stay in sync.
            # The extractor reads both and the last one read wins; keeping
            # them identical prevents stale LeafGreen text from overriding
            # FireRed edits on the next reload.
            try:
                lg_path = os.path.join(root, "src", "data", "pokemon", "pokedex_text_lg.h")
                if os.path.isfile(lg_path):
                    with open(lg_path, "r", encoding="utf-8") as f:
                        lg_content = f.read()
                    lg_rewrites = 0
                    for sp, sym in desc_symbol_by_species.items():
                        new_text = (
                            self.data.get(sp, {}).get("species_info", {}).get("description")
                            or None
                        )
                        if not isinstance(new_text, str) or new_text.strip() == "":
                            continue
                        parts = esc(new_text).split("\n")
                        if not parts:
                            continue
                        body_lines = []
                        for k, line in enumerate(parts):
                            suffix = r"\n" if k < len(parts) - 1 else ""
                            body_lines.append(f"    \"{line}{suffix}\"")
                        if body_lines:
                            body_lines[-1] = body_lines[-1] + ");"
                        prefix = f"const u8 {sym}[] = _("
                        idx = lg_content.find(prefix)
                        if idx == -1:
                            continue
                        end = lg_content.find(");", idx)
                        if end == -1:
                            continue
                        new_def = prefix + "\n" + "\n".join(body_lines)
                        lg_content = lg_content[:idx] + new_def + lg_content[end + 2:]
                        lg_rewrites += 1
                    if lg_rewrites:
                        with open(lg_path, "w", encoding="utf-8", newline="\n") as f:
                            f.write(lg_content)
                        print(f"Updated pokedex_text_lg.h for {lg_rewrites} entries")
            except Exception as e:
                print(f"Note: could not update pokedex_text_lg.h: {e}")

        # 3) Removed legacy overlay emit (pory_species.h). Only canonical headers are updated.


class SpeciesGraphics(pokemon_data.SpeciesGraphics):
    """
    A class that represents the graphics data for a species of Pokemon.

    Not yet fully implemented.
    """

    def __init__(self, project_info, parent=None):
        # Call super().__init__ before adding backup or generated files
        super().__init__(project_info, parent)

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.SpeciesGraphicsDataExtractor)

    @override
    def parse_to_c_code(self):
        """Write any additional C code for species graphics.

        The FireRed plugin keeps all graphics related data inside
        ``species_graphics.json`` so there is nothing to emit when
        generating C code.  This override simply calls the base
        implementation to ensure any backing files are managed.
        """
        super().parse_to_c_code()
        # No additional C code generation required.
        return


class PokemonAbilities(pokemon_data.PokemonAbilities):
    """
    A class that represents the abilities data for Pokemon.

    Not yet fully implemented.
    """

    def __init__(self, project_info, parent=None):
        # Call super().__init__ before adding backup or generated files
        super().__init__(project_info, parent)

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("include", "constants", "abilities.h"),
            file_key="ABILITIES_H",
        )

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.AbilitiesDataExtractor)

    @override
    def parse_to_c_code(self):
        # FireRed: Do not modify include/constants/abilities.h.
        # Abilities editing is not supported here, and preserving the original
        # header byte-for-byte (including blank lines) is required to avoid
        # breaking Make and downstream tooling. Intentionally a no-op.
        return


class PokemonItems(pokemon_data.PokemonItems):
    """
    A class that represents items data.

    Not yet fully implemented.
    """

    HEADER_CANDIDATES = [
        os.path.join("src", "data", "items.h"),
        os.path.join("src", "data", "graphics", "items.h"),
    ]

    def __init__(self, project_info, parent=None):
        # Call super().__init__ before adding backup or generated files
        super().__init__(project_info, parent)

        # ``items.json`` stores editable item data; ``items.h`` is regenerated from it
        header_path = os.path.join("src", "data", "graphics", "items.h")
        self.add_file_to_backup(header_path, file_key="ITEMS_H")
        self.add_generated_file(header_path, file_key="ITEMS_H")
        self._items_header_path: str | None = None

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.ItemsDataExtractor)
        self._synchronise_header_target()
        self._ensure_map()
        # After _ensure_map transforms data from list→dict, sync original_data
        # so the save function doesn't think data changed when it didn't.
        self.original_data = json.loads(json.dumps(self.data))

    def _ensure_map(self):
        """Convert list-based data into a dictionary keyed by item constant."""
        items = self.data
        if isinstance(items, dict) and "items" in items:
            items = items.get("items")
        if isinstance(items, list):
            mapping = {}
            for entry in items:
                const = entry.get("itemId")
                if const:
                    mapping[const] = {
                        k: v for k, v in entry.items() if k != "itemId"
                    }
            self.data = mapping
        elif isinstance(items, dict) and items is not self.data:
            self.data = items

    def _set_items_header_path(self, rel_path: str):
        rel_norm = os.path.normpath(rel_path)
        from app_info import get_cache_dir
        cache = get_cache_dir(self.project_info.get("dir", ""))
        backup_abs = os.path.normpath(os.path.join(cache, "backups", rel_norm))
        if "ITEMS_H" in self.FILES:
            self.FILES["ITEMS_H"]["original"] = rel_norm
            self.FILES["ITEMS_H"]["backup"] = backup_abs
        if "ITEMS_H" in self.GENERATED_FILES:
            self.GENERATED_FILES["ITEMS_H"] = rel_norm
        if getattr(self, "EXTRACTOR", None) is not None:
            files = getattr(self.EXTRACTOR, "FILES", None)
            if isinstance(files, dict):
                files["ITEMS_H"] = {"original": rel_norm, "backup": backup_abs}
        self._items_header_path = rel_norm

    def _synchronise_header_target(self):
        extractor = getattr(self, "EXTRACTOR", None)
        candidates: list[str] = []
        if extractor is not None:
            header_used = getattr(extractor, "header_used", None)
            if header_used:
                candidates.append(header_used)
        candidates.extend(self.HEADER_CANDIDATES)
        root = self.local_util.repo_root()
        for rel in candidates:
            if not rel:
                continue
            rel_norm = os.path.normpath(rel)
            if os.path.isfile(os.path.join(root, rel_norm)):
                self._set_items_header_path(rel_norm)
                return
        # No candidate exists; retain current path for error reporting
        current = self.FILES.get("ITEMS_H", {}).get("original")
        if current:
            self._set_items_header_path(os.path.normpath(current))
        else:
            self._items_header_path = None

    @override
    def parse_to_c_code(self):
        self._synchronise_header_target()
        missing = self.missing_required_sources()
        if missing:
            raise MissingSourceError(missing)

        super().parse_to_c_code()

        # Skip header patching when direct writers already handled items.h
        if getattr(self, "_skip_parse_to_c", False):
            return

        header_rel = self._items_header_path
        if not header_rel:
            return

        with ReadSourceFile(self.project_info, header_rel) as src:
            original_text = src.read()

        updated_text = self._patch_items_header(original_text)

        if updated_text != original_text:
            with WriteSourceFile(self.project_info, header_rel, require_existing=True) as out:
                out.write(updated_text)

    @override
    def save(self):
        self._synchronise_header_target()
        self._ensure_map()
        mapping = self.data if isinstance(self.data, dict) else {}
        payload = None
        if isinstance(mapping, dict):
            encoded = []
            for const, info in mapping.items():
                if isinstance(info, dict):
                    # Re-insert itemId after the first key to match original order
                    keys = list(info.keys())
                    entry = {}
                    if keys:
                        entry[keys[0]] = info[keys[0]]
                    entry["itemId"] = const
                    for k in keys[1:] if keys else []:
                        entry[k] = info[k]
                else:
                    entry = {"itemId": const}
                encoded.append(entry)
            payload = {"items": encoded}
        if payload is not None:
            original = self.data
            try:
                self.data = payload
                # Use 2-space indent to match the original items.json format
                file_path = os.path.join(
                    self.project_info["dir"], "src", "data", self.DATA_FILE
                )
                # Compare mapping (not payload) against original_data since both
                # should be in dict-keyed format after _ensure_map.
                should_save = self.original_data is not None and mapping != self.original_data
                if not os.path.isfile(file_path) or should_save:
                    if self.data:
                        json_str = json.dumps(self.data, indent=2, ensure_ascii=False)
                        with open(file_path, 'w', encoding="utf-8", newline="\n") as json_file:
                            json_file.write(json_str)
                        self.original_data = json.loads(json_str)
                        self.pending_changes = True
                        print(f"Saved {self.DATA_FILE} file.")
            finally:
                self.data = mapping
                try:
                    self.original_data = json.loads(json.dumps(mapping))
                except Exception:
                    self.original_data = mapping
        else:
            super().save()

    @override
    def missing_required_sources(self) -> list[str]:
        self._synchronise_header_target()
        return super().missing_required_sources()

    # --- Internal helpers -------------------------------------------------

    def _patch_items_header(self, text: str) -> str:
        parse_result = self._scan_item_blocks(text)
        if parse_result is None:
            # Fallback: attempt targeted in-place replacements within each [ITEM_*] block,
            # preserving surrounding whitespace/comments.
            try:
                import re
                updated = text
                for const, fields in (self.data or {}).items():
                    # Match from [CONST] to the end of its block (either '},' or '};')
                    blk_pat = re.compile(
                        rf"(\[{re.escape(const)}\]\s*=\s*\{{)([\s\S]*?)(\}},|\}};)",
                        re.M,
                    )
                    m = blk_pat.search(updated)
                    if not m:
                        continue
                    head, body, tail = m.groups()
                    body_lines = body.splitlines()
                    for k, v in fields.items():
                        # Replace only the RHS of .key = value, keeping indentation and trailing comma
                        key_pat = re.compile(rf"(\s*\.{re.escape(k)}\s*=\s*)([^,\n]+)(,?)")
                        replaced = False
                        for idx, ln in enumerate(body_lines):
                            km = key_pat.search(ln)
                            if km:
                                prefix, _, comma = km.groups()
                                body_lines[idx] = prefix + str(v) + (comma or ",")
                                replaced = True
                                break
                        if not replaced:
                            # Insert before closing brace, copy indentation from first field line if any
                            indent = "        "
                            for ln in body_lines:
                                if ln.strip().startswith('.'):
                                    indent = ln[: len(ln) - len(ln.lstrip())]
                                    break
                            # ensure previous non-empty line ends with a comma
                            j = len(body_lines) - 1
                            while j >= 0 and not body_lines[j].strip():
                                j -= 1
                            if j >= 0 and not body_lines[j].strip().endswith(',') and body_lines[j].strip() != '{':
                                body_lines[j] = body_lines[j].rstrip() + ','
                            body_lines.insert(len(body_lines), f"{indent}.{k} = {v},")
                    new_body = "\n".join(body_lines)
                    updated = updated[: m.start()] + head + new_body + tail + updated[m.end() :]
                return updated
            except Exception:
                print("Warning: unable to patch items header; layout not recognised")
                return text

        blocks, line_to_const = parse_result
        if not blocks:
            # File uses old positional format (no [ITEM_XXX] = designators).
            # We can't safely patch it, so leave it unchanged.
            return text
        updated_blocks: dict[str, list[str]] = {}
        existing_consts = set(blocks.keys())

        for const, fields in self.data.items():
            if const in blocks:
                block_lines = list(blocks[const]["lines"])
                updated_blocks[const] = self._update_block_lines(block_lines, fields)
            else:
                updated_blocks[const] = self._render_new_block(const, fields, blocks)

        lines = text.splitlines()
        new_lines: list[str] = []
        i = 0
        while i < len(lines):
            const = line_to_const.get(i)
            if const:
                block_info = blocks[const]
                new_lines.extend(updated_blocks.get(const, block_info["lines"]))
                i = block_info["end"]
            else:
                new_lines.append(lines[i])
                i += 1

        new_block_lines: list[str] = []
        for const, block in updated_blocks.items():
            if const not in existing_consts:
                if new_block_lines and block and block[0]:
                    new_block_lines.append("")
                new_block_lines.extend(block)

        if new_block_lines:
            insert_idx = None
            for idx in range(len(new_lines) - 1, -1, -1):
                if new_lines[idx].strip().startswith('};'):
                    insert_idx = idx
                    break
            if insert_idx is None:
                insert_idx = len(new_lines)
            if insert_idx > 0 and new_lines[insert_idx - 1].strip():
                new_block_lines.insert(0, "")
            new_lines = new_lines[:insert_idx] + new_block_lines + new_lines[insert_idx:]

        result = "\n".join(new_lines)
        if text.endswith("\n") and not result.endswith("\n"):
            result += "\n"
        return result

    def _scan_item_blocks(self, text: str):
        lines = text.splitlines()
        blocks: dict[str, dict] = {}
        line_to_const: dict[int, str] = {}
        i = 0

        while i < len(lines):
            line = lines[i]
            match = re.match(r"\s*\[(ITEM_[A-Z0-9_]+)\]\s*=", line)
            if match:
                const = match.group(1)
                start = i
                brace_depth = line.count('{') - line.count('}')
                j = i + 1
                while j < len(lines):
                    brace_depth += lines[j].count('{') - lines[j].count('}')
                    stripped = lines[j].strip()
                    if brace_depth <= 0 and (stripped.startswith('},') or stripped == '},'):
                        end = j + 1
                        break
                    j += 1
                else:
                    return None

                block_lines = lines[start:end]
                blocks[const] = {
                    "start": start,
                    "end": end,
                    "lines": block_lines,
                }
                line_to_const[start] = const
                i = end
            else:
                i += 1

        return blocks, line_to_const

    def _update_block_lines(self, block_lines: list[str], fields: dict) -> list[str]:
        lines = list(block_lines)
        key_to_idx: dict[str, int] = {}
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('.'):
                match = re.match(r"\.(\w+)\s*=", stripped)
                if match:
                    key_to_idx[match.group(1)] = idx

        closing_idx = None
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip().startswith('},'):
                closing_idx = idx
                break
        if closing_idx is None:
            closing_idx = len(lines)

        indent = self._infer_field_indent(lines)

        for key, value in fields.items():
            formatted_value = self._format_item_value(value)
            if key in key_to_idx:
                line_idx = key_to_idx[key]
                lines[line_idx] = self._replace_value_in_line(lines[line_idx], key, formatted_value)
            else:
                insert_idx = closing_idx
                if insert_idx > 0:
                    prev_line = lines[insert_idx - 1]
                    if prev_line.strip() and not prev_line.strip().endswith(',') and prev_line.strip() != '{':
                        lines[insert_idx - 1] = prev_line.rstrip() + ','
                new_line = f"{indent}.{key} = {formatted_value},"
                lines.insert(insert_idx, new_line)
                closing_idx += 1

        return lines

    def _render_new_block(self, const: str, fields: dict, existing_blocks: dict) -> list[str]:
        if existing_blocks:
            sample = next(iter(existing_blocks.values()))["lines"]
            open_line = sample[0]
            open_indent = open_line[: len(open_line) - len(open_line.lstrip())]
            has_inline_brace = '{' in open_line
            block_lines: list[str] = []
            if has_inline_brace:
                new_open = re.sub(r'\[ITEM_[A-Z0-9_]+\]', f'[{const}]', open_line, count=1)
                block_lines.append(new_open)
            else:
                new_open = re.sub(r'\[ITEM_[A-Z0-9_]+\]', f'[{const}]', open_line, count=1)
                block_lines.append(new_open)
                brace_line = sample[1] if len(sample) > 1 else f"{open_indent}{{"
                block_lines.append(brace_line)
            field_indent = self._infer_field_indent(sample)
            closing = None
            for line in reversed(sample):
                stripped = line.strip()
                if stripped.startswith('},') or stripped == '},':
                    closing = line
                    break
            if closing is None:
                closing = f"{open_indent}}},"
        else:
            open_indent = "    "
            field_indent = "        "
            block_lines = [f"{open_indent}[{const}] ="]
            block_lines.append(f"{open_indent}{{")
            closing = f"{open_indent}}},"

        for key, value in fields.items():
            formatted_value = self._format_item_value(value)
            block_lines.append(f"{field_indent}.{key} = {formatted_value},")

        block_lines.append(closing)
        return block_lines

    @staticmethod
    def _infer_field_indent(lines: list[str]) -> str:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('.'):
                return line[: len(line) - len(line.lstrip())]
        return "        "

    @staticmethod
    def _format_item_value(value) -> str:
        if isinstance(value, str):
            # Heuristic: treat ALL-CAPS identifiers as constants; otherwise emit a quoted C string
            ident_like = (
                value.isupper()
                and all(ch.isalnum() or ch == '_' for ch in value)
            )
            # Also allow known constant/function prefixes
            constant_prefixes = (
                "POCKET_",
                "ITEM_TYPE_",
                "HOLD_EFFECT_",
                "MOVE_TARGET_",
                "FieldUseFunc_",
                "BattleUseFunc_",
                "NULL",
            )
            if ident_like or value.startswith(constant_prefixes):
                return value
            # Otherwise, escape and wrap in a C string literal (no translation macro)
            s = value.replace("\r\n", "\n").replace("\r", "\n")
            s = s.replace("\\", r"\\").replace("\"", r"\"")
            # Ensure any raw newlines become \n sequences
            s = s.replace("\n", r"\n")
            return f'"{s}"'
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        return str(value)

    @staticmethod
    def _replace_value_in_line(line: str, key: str, new_value: str) -> str:
        if f".{key}" not in line:
            return line
        eq_index = line.find('=')
        if eq_index == -1:
            return line

        prefix = line[: eq_index + 1]
        suffix = line[eq_index + 1 :]

        comment_idx = None
        for marker in ('//', '/*'):
            idx = suffix.find(marker)
            if idx != -1:
                comment_idx = idx if comment_idx is None else min(comment_idx, idx)
        if comment_idx is not None:
            comment = suffix[comment_idx:]
            suffix = suffix[:comment_idx]
        else:
            comment = ''

        # Find the comma that terminates the field, skipping past any string literal
        # so commas inside quoted values (e.g. descriptions) are not mistaken for
        # the field separator.
        search_start = 0
        suffix_lstripped = suffix.lstrip()
        if suffix_lstripped.startswith('"'):
            # Locate the opening quote's position in the original suffix
            open_q = suffix.index('"')
            j = open_q + 1
            while j < len(suffix):
                if suffix[j] == '\\':
                    j += 2  # skip escape sequence
                elif suffix[j] == '"':
                    j += 1  # skip closing quote
                    break
                else:
                    j += 1
            search_start = j  # comma must be at or after closing quote
        comma_idx = suffix.find(',', search_start)
        if comma_idx != -1:
            value_segment = suffix[:comma_idx]
            remainder = suffix[comma_idx:]
        else:
            value_segment = suffix
            remainder = ''

        leading_len = len(value_segment) - len(value_segment.lstrip())
        trailing_len = len(value_segment.rstrip())
        prefix_ws = value_segment[:leading_len]
        trailing_ws = ''
        if trailing_len != len(value_segment):
            trailing_ws = value_segment[trailing_len:]

        updated = prefix + prefix_ws + new_value + trailing_ws + remainder + comment
        return updated


class PokemonEvolutions(pokemon_data.PokemonEvolutions):
    """Data for Pokémon evolutions."""

    def __init__(self, project_info, parent=None):
        super().__init__(project_info, parent)

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "evolution.h"),
            file_key="EVOLUTION_H",
        )

        # Instantiate the extractor for evolution data
        self.instantiate_extractor(pee.PokemonEvolutionsExtractor)

    @override
    def parse_to_c_code(self):
        path = self.get_file_path("EVOLUTION_H")
        if not os.path.isfile(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as tmp:
                tmp.write(
                    "const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] = {}\n"
                )
        super().parse_to_c_code()

        with WriteSourceFile(
            self.project_info, self.get_file_path("EVOLUTION_H")
        ) as f:
            f.write(
                "const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] =\n{\n"
            )
            # Calculate alignment width from the longest species name
            non_empty = {c: e for c, e in self.data.items() if e}
            max_len = max((len(f"[{c}]") for c in non_empty), default=0)
            def _fmt_param(p):
                """Format a numeric param to match original style:
                non-zero single digits padded to 2 chars, zero unpadded."""
                try:
                    v = int(p)
                    if v == 0:
                        return "0"
                    return f"{v:2d}"
                except (ValueError, TypeError):
                    return str(p)
            for const, evos in self.data.items():
                if not evos:
                    continue
                padded = f"[{const}]".ljust(max_len)
                prefix = f"    {padded} = {{"
                if len(evos) == 1:
                    e = evos[0]
                    f.write(f"{prefix}{{{e['method']}, {_fmt_param(e['param'])}, {e['targetSpecies']}}}}},\n")
                else:
                    parts = []
                    for e in evos:
                        parts.append(f"{{{e['method']}, {_fmt_param(e['param'])}, {e['targetSpecies']}}}")
                    continuation_pad = " " * len(prefix)
                    first = parts[0]
                    rest = ",\n".join(f"{continuation_pad}{p}" for p in parts[1:])
                    f.write(f"{prefix}{first},\n{rest}}},\n")
            f.write("};\n")


class PokemonConstants(pokemon_data.PokemonConstants):
    """
    A class that represents general constants data in the game.

    Not yet fully implemented.
    """

    def __init__(self, project_info, parent=None):
        # Call super().__init__ before adding backup or generated files
        super().__init__(project_info, parent)

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("src", "data", "constants.json"),
            file_key="CONSTANTS_JSON",
        )

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.PokemonConstantsExtractor)
        self._ensure_maps()

    def _ensure_maps(self):
        """Convert list-based constants into dictionaries if needed."""

        def to_map(key: str, prefix: str):
            val = self.data.get(key)
            if isinstance(val, list):
                mapping = {}
                for entry in val:
                    name = str(entry.get("name", "")).upper().replace(" ", "_")
                    mapping[f"{prefix}_{name}"] = entry
                self.data[key] = mapping

        to_map("types", "TYPE")
        to_map("evolution_types", "EVO")

    @override
    def parse_to_c_code(self):
        # Constants are stored directly in JSON form
        super().parse_to_c_code()

    @override
    def save(self):
        self._ensure_maps()
        super().save()


class PokemonStarters(pokemon_data.PokemonStarters):
    """
    A class that represents the starter Pokemon data.

    This class provides methods for retrieving and setting information about the starter Pokemon.
    The location of this data is in the files "src/field_specials.c" and "src/battle_setup.c",
    and the data is parsed and updated in these files.
    """

    def __init__(self, project_info, parent=None):
        # Initialise base class first so project_info is available
        super().__init__(project_info, parent)

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("src", "field_specials.c"),
            file_key="FIELD_SPECIALS_C",
        )

        # Files to generate must be added second
        self.add_file_to_backup(
            os.path.join("src", "battle_setup.c"),
            file_key="BATTLE_SETUP_C",
        )

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.StartersDataExtractor)

    @override
    def parse_to_c_code(self):
        """
        Parses the Pokemon data into C code.

        This method calls the base class's `parse_to_c_code` method and then updates the starter choose C file
        and the battle setup C file.
        """
        super().parse_to_c_code()
        self.__update_starter_choose_c_file()
        self.__update_battle_setup_c_file()

    def __update_starter_choose_c_file(self):
        """
        Updates the FIELD_SPECIALS_C file with new starter data.

        Reads the existing FIELD_SPECIALS_C file, finds the starter array, and replaces it with the new data.
        The new data is obtained from the 'data' attribute of the class.
        """
        try:
            reader = ReadSourceFile(
                self.project_info, self.get_file_path("FIELD_SPECIALS_C", True)
            )
        except FileNotFoundError:
            reader = ReadSourceFile(
                self.project_info, self.get_file_path("FIELD_SPECIALS_C")
            )
        with reader as f:
            content = f.read()
        pattern = re.compile(
            r"(const u16 sStarter(?:Mon|Species)\[\]\s*=\s*{)(.*?)(};)", re.S
        )
        m = pattern.search(content)
        if m:
            starter_entries = list(self.data)
            starter_lines = "\n".join(
                f"    {s['species']}," if i < len(starter_entries) - 1 else f"    {s['species']}"
                for i, s in enumerate(starter_entries)
            )
            replacement = f"{m.group(1)}\n{starter_lines}\n{m.group(3)}"
            content = content[: m.start()] + replacement + content[m.end() :]
        with WriteSourceFile(
            self.project_info, self.get_file_path("FIELD_SPECIALS_C")
        ) as f:
            f.write(content)

    def __update_battle_setup_c_file(self):
        """
        Updates the battle setup C file by modifying specific lines of code.
        This method reads the existing file, makes the necessary modifications,
        and writes the updated lines back to the file.
        """
        battle_setup_lines = []
        try:
            reader = ReadSourceFile(
                self.project_info, self.get_file_path("BATTLE_SETUP_C", True)
            )
        except FileNotFoundError:
            reader = ReadSourceFile(
                self.project_info, self.get_file_path("BATTLE_SETUP_C")
            )
        with reader as f:
            inside_give_starter_function = False
            for line in f:
                if line.strip() == "static void CB2_GiveStarter(void)":
                    inside_give_starter_function = True
                    battle_setup_lines.append(line)
                    continue
                if inside_give_starter_function:
                    if line.startswith("{"):
                        battle_setup_lines.append(line)
                        continue
                    if line.startswith("}"):
                        inside_give_starter_function = False
                        battle_setup_lines.append(line)
                        continue
                    elif "u16 starterMon" in line:
                        if any(starter["ability_num"] != -1 for starter in self.data):
                            line += "\n    u16 abilityNum;\n"
                        battle_setup_lines.append(line)
                    elif "ScriptGiveMon(starterMon" in line:
                        line = self.__generate_switch_case_code()
                        battle_setup_lines.append(line)
                    else:
                        battle_setup_lines.append(line)
                if not inside_give_starter_function:
                    battle_setup_lines.append(line)

        with WriteSourceFile(
            self.project_info, self.get_file_path("BATTLE_SETUP_C")
        ) as f:
            f.writelines(battle_setup_lines)

    def __generate_switch_case_code(self):
        """
        Generates the switch case code for assigning starter Pokémon based on the value of gSpecialVar_Result.

        Returns:
            str: The generated switch case code.
        """
        switch_case_code = "    switch(gSpecialVar_Result)\n    {\n"
        for i, starter in enumerate(self.data):
            switch_case_code += (
                f'        case {i}: // {starter["species"]}\n'
                f'            ScriptGiveMon(starterMon, {starter["level"]}, {starter["item"]}, 0, 0, 0);\n'
            )
            if starter["custom_move"] != "MOVE_NONE":
                switch_case_code += f'            GiveMoveToMon(&gPlayerParty[0], {starter["custom_move"]});\n'
            if starter["ability_num"] != -1:
                switch_case_code += (
                    f'            abilityNum = {starter["ability_num"]};\n'
                )
                switch_case_code += f"            SetMonData(&gPlayerParty[0], MON_DATA_ABILITY_NUM, &abilityNum);\n"
            switch_case_code += f"            break;\n"
        switch_case_code += "    }\n"
        return switch_case_code


class PokemonTrainers(pokemon_data.PokemonTrainers):
    """Data class for trainer definitions."""

    # Paths relative to repo root
    _OPPONENTS_H    = os.path.join("include", "constants", "opponents.h")
    _PARTIES_H      = os.path.join("src", "data", "trainer_parties.h")

    def __init__(self, project_info, parent=None):
        super().__init__(project_info, parent)

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("src", "data", "trainers.h"),
            file_key="TRAINERS_H",
        )
        # Note: opponents.h and trainer_parties.h are updated as a best-effort
        # rename-sync step and are NOT added to the mandatory backup list so that
        # projects lacking those paths don't fail the pre-flight source check.

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.TrainersDataExtractor)

    def missing_required_sources(self) -> list[str]:
        # Only TRAINERS_H is required; the rename-sync files are optional.
        import os as _os
        root = self.local_util.repo_root()
        trainers_h = self.FILES.get("TRAINERS_H", {}).get("original")
        if not trainers_h:
            return []
        abs_path = _os.path.join(root, _os.path.normpath(trainers_h))
        return [] if _os.path.isfile(abs_path) else [abs_path]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _repo_root(self) -> str:
        return LocalUtil(self.project_info).repo_root()

    def _full(self, rel: str) -> str:
        return os.path.join(self._repo_root(), rel)

    def _build_trainer_const_rename_map(self) -> dict[str, str]:
        """Return {old_const: new_const} for any trainer constants that were
        renamed in the JSON relative to opponents.h."""
        opponents_path = self._full(self._OPPONENTS_H)
        if not os.path.isfile(opponents_path):
            return {}

        pat = re.compile(r'^#define\s+(TRAINER_\w+)\s+\d+')
        defined_order: list[str] = []
        defined_set: set[str] = set()
        try:
            with open(opponents_path, encoding="utf-8") as f:
                for line in f:
                    m = pat.match(line)
                    if m:
                        name = m.group(1)
                        defined_order.append(name)
                        defined_set.add(name)
        except Exception:
            return {}

        json_set = set(self.data.keys())
        orphaned = [n for n in defined_order if n not in json_set]    # old names
        new_names = [n for n in self.data   if n not in defined_set]  # new names

        if len(orphaned) != len(new_names):
            # Mismatch – only rename what we can safely pair up
            count = min(len(orphaned), len(new_names))
            orphaned  = orphaned[:count]
            new_names = new_names[:count]

        return dict(zip(orphaned, new_names))

    def _build_party_symbol_rename_map(self) -> dict[str, str]:
        """Return {old_symbol: new_symbol} for any party symbols renamed in
        the JSON .party fields relative to declarations in trainer_parties.h."""
        parties_path = self._full(self._PARTIES_H)
        if not os.path.isfile(parties_path):
            return {}

        decl_pat = re.compile(r'static const struct \w+ (s\w+)\s*\[\]')
        declared_order: list[str] = []
        declared_set:  set[str]  = set()
        try:
            with open(parties_path, encoding="utf-8") as f:
                for line in f:
                    m = decl_pat.search(line)
                    if m:
                        sym = m.group(1)
                        declared_order.append(sym)
                        declared_set.add(sym)
        except Exception:
            return {}

        # Extract referenced party symbols from JSON .party values
        ref_pat = re.compile(r'\b(s[A-Z]\w+)\b')
        referenced_order: list[str] = []
        referenced_set:   set[str]  = set()
        for info in self.data.values():
            party_val = str(info.get("party", ""))
            m = ref_pat.search(party_val)
            if m:
                sym = m.group(1)
                referenced_order.append(sym)
                referenced_set.add(sym)

        orphaned = [s for s in declared_order if s not in referenced_set]
        new_syms = [s for s in referenced_order if s not in declared_set]

        if len(orphaned) != len(new_syms):
            count = min(len(orphaned), len(new_syms))
            orphaned = orphaned[:count]
            new_syms = new_syms[:count]

        return dict(zip(orphaned, new_syms))

    @staticmethod
    def _apply_word_renames(text: str, rename_map: dict[str, str]) -> str:
        """Replace all whole-word occurrences of each key with its value."""
        for old, new in rename_map.items():
            text = re.sub(r'\b' + re.escape(old) + r'\b', new, text)
        return text

    @override
    def parse_to_c_code(self):
        super().parse_to_c_code()

        # 1. Write the trainer struct file
        with WriteSourceFile(self.project_info, self.get_file_path("TRAINERS_H")) as f:
            f.write("const struct Trainer gTrainers[] = {\n")
            for const, info in self.data.items():
                f.write(f"    [{const}] = {{\n")
                for key, val in info.items():
                    # Skip empty values — writing ".field = ," is invalid C
                    # and breaks the build.  The compiler zero-initialises the
                    # field instead, which is always safe for unused trainers.
                    if str(val).strip():
                        f.write(f"        .{key} = {val},\n")
                f.write("    },\n")
            f.write("};\n")

        # 2. Sync renamed trainer constants → opponents.h
        const_renames = self._build_trainer_const_rename_map()
        if const_renames:
            opponents_path = self._full(self._OPPONENTS_H)
            try:
                with open(opponents_path, encoding="utf-8") as f:
                    text = f.read()
                text = self._apply_word_renames(text, const_renames)
                with open(opponents_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                print(f"Updated {self._OPPONENTS_H}: renamed {len(const_renames)} trainer constant(s)")
            except Exception as e:
                print(f"Warning: could not update {self._OPPONENTS_H}: {e}")

        # 3. Sync renamed party symbols → trainer_parties.h
        party_renames = self._build_party_symbol_rename_map()
        if party_renames:
            parties_path = self._full(self._PARTIES_H)
            try:
                with open(parties_path, encoding="utf-8") as f:
                    text = f.read()
                text = self._apply_word_renames(text, party_renames)
                with open(parties_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                print(f"Updated {self._PARTIES_H}: renamed {len(party_renames)} party symbol(s)")
            except Exception as e:
                print(f"Warning: could not update {self._PARTIES_H}: {e}")


class PokemonMoves(pokemon_data.PokemonMoves):
    _HEADER_CANDIDATES: dict[str, tuple[str, ...]] = {
        "BATTLE_MOVES_H": ("src/data/battle_moves.h", "data/battle_moves.h"),
        "MOVE_DESCS_C": ("src/move_descriptions.c", "data/move_descriptions.c"),
        "LVL_PTRS_H": ("src/data/pokemon/level_up_learnset_pointers.h", "src/data/level_up_learnset_pointers.h", "data/pokemon/level_up_learnset_pointers.h", "data/level_up_learnset_pointers.h"),
        "LVL_SETS_H": ("src/data/pokemon/level_up_learnsets.h", "src/data/level_up_learnsets.h", "data/pokemon/level_up_learnsets.h", "data/level_up_learnsets.h"),
        "TMHM_SETS_H": ("src/data/pokemon/tmhm_learnsets.h", "src/data/tmhm_learnsets.h", "data/pokemon/tmhm_learnsets.h", "data/tmhm_learnsets.h"),
        "TUTOR_SETS_H": ("src/data/pokemon/tutor_learnsets.h", "src/data/tutor_learnsets.h", "data/pokemon/tutor_learnsets.h", "data/tutor_learnsets.h"),
        "EGG_MOVES_H": ("src/data/pokemon/egg_moves.h", "src/data/egg_moves.h", "data/pokemon/egg_moves.h", "data/egg_moves.h"),
    }
    _HEADER_SEARCH_NAMES: dict[str, str] = {
        "BATTLE_MOVES_H": "battle_moves.h",
        "MOVE_DESCS_C": "move_descriptions.c",
        "LVL_PTRS_H": "level_up_learnset_pointers.h",
        "LVL_SETS_H": "level_up_learnsets.h",
        "TMHM_SETS_H": "tmhm_learnsets.h",
        "TUTOR_SETS_H": "tutor_learnsets.h",
        "EGG_MOVES_H": "egg_moves.h",
    }
    def __init__(self, project_info, parent=None):
        # Call super().__init__ before adding backup or generated files
        super().__init__(project_info, parent)
        self._repo_root = LocalUtil(project_info).repo_root()
        self._reported_missing_headers: set[str] = set()

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("src", "data", "battle_moves.h"),
            file_key="BATTLE_MOVES_H",
        )
        self.add_file_to_backup(
            os.path.join("src", "move_descriptions.c"),
            file_key="MOVE_DESCS_C",
        )
        # Learnset sources (FireRed)
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "level_up_learnset_pointers.h"),
            file_key="LVL_PTRS_H",
        )
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "level_up_learnsets.h"),
            file_key="LVL_SETS_H",
        )
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "tmhm_learnsets.h"),
            file_key="TMHM_SETS_H",
        )
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "tutor_learnsets.h"),
            file_key="TUTOR_SETS_H",
        )
        self.add_file_to_backup(
            os.path.join("src", "data", "pokemon", "egg_moves.h"),
            file_key="EGG_MOVES_H",
        )

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.MovesDataExtractor)
        self._resolve_header_paths()

        # Seed move descriptions from the C source file into JSON if not yet populated.
        # This ensures descriptions survive save/reload even if the user never opens
        # the Moves tab.
        if not self.data.get("move_descriptions"):
            self._seed_move_descriptions()

    def _resolve_header_paths(self) -> None:
        root = self._repo_root
        root_path = Path(root)
        for key, candidates in self._HEADER_CANDIDATES.items():
            entry = self.FILES.get(key)
            if not entry:
                continue
            current = os.path.normpath(entry["original"])
            search = [current, *(os.path.normpath(c) for c in candidates)]
            resolved: str | None = None
            for candidate in search:
                candidate_path = Path(candidate) if os.path.isabs(candidate) else root_path / candidate
                if candidate_path.exists():
                    try:
                        resolved = os.path.relpath(candidate_path, root_path)
                    except ValueError:
                        resolved = str(candidate_path)
                    break
            if resolved is None:
                search_name = self._HEADER_SEARCH_NAMES.get(key)
                if search_name:
                    for hit in root_path.rglob(search_name):
                        hit_str = str(hit).replace("\\", "/")
                        if "/temp/" in hit_str or "/cache/" in hit_str:
                            continue
                        try:
                            resolved = os.path.relpath(hit, root_path)
                        except ValueError:
                            resolved = str(hit)
                        break
            if resolved:
                resolved_norm = os.path.normpath(resolved)
                if resolved_norm != current:
                    self._set_file_path(key, resolved_norm)
                    display_res = resolved_norm.replace("\\", "/")
                    try:
                        logger.info("Resolved %s to %s (was %s)", key, display_res, current.replace("\\", "/"))
                    except Exception:
                        logger.info("Resolved %s to %s", key, display_res)
            else:
                first = search[0] if search else key
                self._log_missing_header(os.path.normpath(first))

    def _set_file_path(self, file_key: str, rel_path: str) -> None:
        entry = self.FILES.get(file_key)
        if not entry:
            return
        normalized = os.path.normpath(rel_path)
        entry["original"] = normalized
        from app_info import get_cache_dir
        cache = get_cache_dir(self.project_info.get("dir", ""))
        entry["backup"] = os.path.normpath(os.path.join(cache, "backups", normalized))

    def _seed_move_descriptions(self) -> None:
        """Parse move_descriptions.c and populate self.data['move_descriptions'].

        Called once during init if the JSON cache is empty, so descriptions
        survive save/reload even if the user never opens the Moves tab.
        """
        try:
            import re
            descs_path = os.path.join(self._repo_root, "src", "move_descriptions.c")
            if not os.path.isfile(descs_path):
                return
            with open(descs_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            result: dict[str, str] = {}
            head_pat = re.compile(r"gMoveDescription_([A-Za-z0-9]+)\s*\[\]\s*=\s*_\(", re.S)
            pos = 0
            while True:
                m = head_pat.search(text, pos)
                if not m:
                    break
                suffix = m.group(1)
                const = "MOVE_" + re.sub(r"([A-Z])", r"_\1", suffix).upper().lstrip("_")
                start = m.end()
                end = text.find(");", start)
                if end == -1:
                    break
                parts = re.findall(r'"((?:[^\\"]|\\.)*)"', text[start:end])
                raw = "".join(parts)
                desc = raw.replace('\\"', '"').replace("\\\\", "\\")
                result[const] = desc
                pos = end + 2
            if result:
                self.data["move_descriptions"] = result
        except Exception:
            pass

    def _header_for_key(self, file_key: str) -> tuple[str, bool]:
        try:
            rel_path = self.get_file_path(file_key)
        except KeyError:
            return file_key, False
        if os.path.isabs(rel_path):
            abs_path = rel_path
        else:
            abs_path = os.path.normpath(os.path.join(self._repo_root, rel_path))
        return rel_path, os.path.exists(abs_path)

    def _log_missing_header(self, rel_path: str) -> None:
        key = os.path.normpath(rel_path)
        if key in self._reported_missing_headers:
            return
        self._reported_missing_headers.add(key)
        logger.warning("Skipping writeback for %s because the file does not exist in the project", key)


    @override
    def missing_required_sources(self) -> list[str]:
        # PokemonMoves handles missing learnset headers gracefully (logs and skips),
        # so it never blocks the top-level pre-flight check.
        return []

    def save(self):
        if isinstance(self.data, dict):
            species_moves = self.data.get("species_moves")
            order = getattr(self.parent, "_fr_order_learnset", None) if getattr(self, "parent", None) else None
            if order and isinstance(species_moves, dict):
                self.data["species_moves"] = {sp: order(entries or []) for sp, entries in species_moves.items()}
        super().save()

    @override
    def parse_to_c_code(self):
        super().parse_to_c_code()
        self._resolve_header_paths()

        rel_path, file_exists = self._header_for_key("BATTLE_MOVES_H")
        if file_exists:
            with WriteSourceFile(self.project_info, rel_path) as f:
                f.write("const struct BattleMove gBattleMoves[MOVES_COUNT] =\n{\n")
                move_items = list(self.data.get("moves", {}).items())
                for idx, (move, info) in enumerate(move_items):
                    f.write(f"    [{move}] =\n    {{\n")
                    for key, val in info.items():
                        if key in ("id", "description", "name", "animation"):
                            continue
                        f.write(f"        .{key} = {val},\n")
                    f.write("    },\n")
                    # Blank line between entries, but not after the last one
                    if idx < len(move_items) - 1:
                        f.write("\n")
                f.write("};\n")
        else:
            self._log_missing_header(rel_path)

        # Update in-game move names in move_names.h (in-place patching)
        names_h = os.path.join(
            self.local_util.repo_root(), "src", "data", "text", "move_names.h"
        )
        if os.path.isfile(names_h):
            try:
                with open(names_h, "r", encoding="utf-8") as fh:
                    names_text = fh.read()
                changed = False
                for move, info in self.data.get("moves", {}).items():
                    new_name = info.get("name")
                    if not new_name:
                        continue
                    import re as _re
                    pat = _re.compile(
                        r'(\[' + _re.escape(move) + r'\]\s*=\s*_\(")[^"]*(")'
                    )
                    updated = pat.sub(lambda m: m.group(1) + new_name + m.group(2), names_text)
                    if updated != names_text:
                        names_text = updated
                        changed = True
                if changed:
                    with open(names_h, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(names_text)
            except Exception:
                pass

        # Patch move descriptions in-place.  For existing moves, update the
        # string inside gMoveDescription_Xxx[] = _("...");.  For new moves
        # that don't have a variable yet, add the variable + pointer entry.
        desc_edits = self.data.get("move_descriptions", {})
        if desc_edits:
            descs_c = os.path.join(self.local_util.repo_root(), "src", "move_descriptions.c")
            if os.path.isfile(descs_c):
                try:
                    import re as _re
                    with open(descs_c, "r", encoding="utf-8") as fh:
                        desc_text = fh.read()
                    desc_changed = False
                    new_consts = []   # (move_const, c_var, c_desc) for moves missing from file
                    for move, new_desc in desc_edits.items():
                        if not new_desc and new_desc != "":
                            continue
                        suffix = move.replace("MOVE_", "") if move.startswith("MOVE_") else move
                        var_name = "".join(part.capitalize() for part in suffix.split("_"))
                        c_var = f"gMoveDescription_{var_name}"
                        pat = _re.compile(
                            r"(" + _re.escape(c_var)
                            + r"\s*\[\]\s*=\s*_\()\"(?:[^\\\"]|\\.)*\"(\s*\)\s*;)",
                            _re.S,
                        )
                        c_desc = new_desc.replace('"', '\\"')
                        updated = pat.sub(lambda m: m.group(1) + '"' + c_desc + '"' + m.group(2), desc_text)
                        if updated != desc_text:
                            desc_text = updated
                            desc_changed = True
                        elif c_var not in desc_text:
                            # Variable doesn't exist — new move, needs to be added
                            new_consts.append((move, c_var, c_desc))

                    # Add new description variables + pointer entries
                    if new_consts:
                        # Find the pointer table to insert variable declarations before it
                        ptr_m = _re.search(r"const\s+u8\s+\*\s*(?:const\s+)?gMoveDescription\w*\s*\[", desc_text)
                        if ptr_m:
                            insert_pos = ptr_m.start()
                            var_block = "\n".join(
                                f'const u8 {cv}[] = _("{cd}");'
                                for _, cv, cd in new_consts
                            ) + "\n\n"
                            desc_text = desc_text[:insert_pos] + var_block + desc_text[insert_pos:]
                            desc_changed = True

                            # Add pointer entries before the closing };
                            ptr_m2 = _re.search(r"const\s+u8\s+\*\s*(?:const\s+)?gMoveDescription\w*\s*\[", desc_text)
                            if ptr_m2:
                                close_idx = desc_text.find("};", ptr_m2.end())
                                if close_idx >= 0:
                                    before = desc_text[:close_idx].rstrip()
                                    if before and before[-1] != ',':
                                        before += ','
                                    before += '\n'
                                    ptr_block = "\n".join(
                                        f"    [{mc} - 1] = {cv},"
                                        for mc, cv, _ in new_consts
                                    ) + "\n"
                                    desc_text = before + ptr_block + desc_text[close_idx:]

                    if desc_changed:
                        with open(descs_c, "w", encoding="utf-8", newline="\n") as fh:
                            fh.write(desc_text)
                except Exception:
                    pass

        # Persist species learnsets to C headers (level-up, TM/HM, tutor, egg).
        # Skip when direct writers (_write_moves_headers) already handled these.
        if getattr(self, "_skip_parse_to_c", False):
            return
        species_moves = self.data.get("species_moves") or {}
        order = getattr(self.parent, "_fr_order_learnset", None) if getattr(self, "parent", None) else None
        if order:
            species_moves = {sp: order(entries or []) for sp, entries in species_moves.items()}
        if not species_moves:
            return

        def _method(entry: dict) -> str:
            return str(entry.get("method") or "").upper()

        # Helpers
        def _camel(base: str) -> str:
            parts = base.lower().split('_')
            return ''.join(p.capitalize() for p in parts if p)

        def _species_base(spec: str) -> str:
            return spec[len("SPECIES_") :] if spec.startswith("SPECIES_") else spec

        # Update level-up arrays and pointers
        try:
            import re
            # Read current files
            try:
                with ReadSourceFile(self.project_info, self.get_file_path("LVL_SETS_H", True)) as fh:
                    lvl_text = fh.read()
            except Exception:
                lvl_text = ""
            try:
                with ReadSourceFile(self.project_info, self.get_file_path("LVL_PTRS_H", True)) as fh:
                    ptr_text = fh.read()
            except Exception:
                ptr_text = ""

            if lvl_text and ptr_text:
                # Map species -> symbol from pointers
                ptr_pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(s\w+LevelUpLearnset)")
                sp_to_sym = {m.group(1): m.group(2) for m in ptr_pat.finditer(ptr_text)}
                # Replace arrays for species that have LEVEL entries
                for sp, entries in species_moves.items():
                    lvl_entries = [e for e in entries if _method(e) == "LEVEL"]
                    if not lvl_entries:
                        continue
                    sym = sp_to_sym.get(sp)
                    if not sym:
                        base = _species_base(sp)
                        sym = f"s{_camel(base)}LevelUpLearnset"
                        # Ensure pointer references the symbol
                        def _rep_ptr(m):
                            return f"[{m.group(1)}] = {sym}" if m.group(1) == sp else m.group(0)
                        ptr_text = re.sub(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(\w+)", _rep_ptr, ptr_text)
                    # Build new body
                    body_lines = []
                    for ent in sorted(lvl_entries, key=lambda d: int(d.get("value") or 0)):
                        try:
                            lv = int(ent.get("value") or 0)
                        except Exception:
                            lv = 0
                        mv = ent.get("move") or "MOVE_NONE"
                        body_lines.append(f"    LEVEL_UP_MOVE({lv}, {mv}),")
                    body_lines.append("    LEVEL_UP_END")
                    new_block = (
                        f"static const struct LevelUpMove {sym}[] = {{\n" + "\n".join(body_lines) + "\n};"
                    )
                    # Replace block for symbol
                    arr_pat = re.compile(rf"static\\s+const\\s+u16\\s+{re.escape(sym)}\\[\\]\\s*=\\s*\{{.*?\}};", re.S)
                    if arr_pat.search(lvl_text):
                        lvl_text = arr_pat.sub(new_block, lvl_text)
                    else:
                        # Append near the end if symbol not present
                        lvl_text = lvl_text.rstrip() + "\n\n" + new_block + "\n"

                # Write back
                rel_lvl, lvl_exists = self._header_for_key("LVL_SETS_H")
                rel_ptr, ptr_exists = self._header_for_key("LVL_PTRS_H")
                if lvl_exists:
                    with WriteSourceFile(self.project_info, rel_lvl) as fh:
                        fh.write(lvl_text)
                else:
                    self._log_missing_header(rel_lvl)
                if ptr_exists:
                    with WriteSourceFile(self.project_info, rel_ptr) as fh:
                        fh.write(ptr_text)
                else:
                    self._log_missing_header(rel_ptr)
        except Exception:
            pass

        # Update TM/HM learnsets
        try:
            import re
            try:
                with ReadSourceFile(self.project_info, self.get_file_path("TMHM_SETS_H", True)) as fh:
                    tm_text = fh.read()
            except Exception:
                tm_text = ""
            if tm_text:
                def _rebuild_tm_expr(sp: str) -> str | None:
                    entries = [e for e in species_moves.get(sp, []) if _method(e) in ("TM", "HM")]
                    if not entries:
                        return None
                    tokens = []
                    for e in entries:
                        kind = str(e.get("value") or "").strip().upper()  # e.g., TM06 or HM01
                        mv = str(e.get("move") or "MOVE_NONE")
                        base = mv[len("MOVE_"):] if mv.startswith("MOVE_") else mv
                        if kind:
                            tokens.append(f"TMHM({kind}_{base})")
                    if not tokens:
                        return None
                    return "TMHM_LEARNSET(" + "\n                                        | ".join(sorted(tokens)) + ")"

                def _tm_sub(m):
                    sp = m.group(1)
                    expr = _rebuild_tm_expr(sp)
                    return f"[{sp}]        = {expr}," if expr else m.group(0)

                tm_text = re.sub(
                    r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*TMHM_LEARNSET\((?:.|\n)*?\)\,",
                    _tm_sub,
                    tm_text,
                )
                rel_tm, tm_exists = self._header_for_key("TMHM_SETS_H")
                if tm_exists:
                    with WriteSourceFile(self.project_info, rel_tm) as fh:
                        fh.write(tm_text)
                else:
                    self._log_missing_header(rel_tm)
        except Exception:
            pass

        # Update tutor learnsets (bitmask rendered as OR of TUTOR(MOVE_*))
        try:
            import re
            try:
                with ReadSourceFile(self.project_info, self.get_file_path("TUTOR_SETS_H", True)) as fh:
                    tut_text = fh.read()
            except Exception:
                tut_text = ""
            if tut_text:
                def _rebuild_tutor_line(sp: str) -> str | None:
                    entries = [e for e in species_moves.get(sp, []) if _method(e) == "TUTOR"]
                    if not entries:
                        return None
                    tokens = [f"TUTOR({e.get('move')})" for e in entries if e.get('move')]
                    if not tokens:
                        return None
                    return " "+"\n                         | ".join(sorted(tokens))

                def _tut_sub(m):
                    sp = m.group(1)
                    repl = _rebuild_tutor_line(sp)
                    return f"[{sp}] ={repl}," if repl else m.group(0)

                tut_text = re.sub(
                    r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*[^\n]+\,",
                    _tut_sub,
                    tut_text,
                )
                rel_tutor, tutor_exists = self._header_for_key("TUTOR_SETS_H")
                if tutor_exists:
                    with WriteSourceFile(self.project_info, rel_tutor) as fh:
                        fh.write(tut_text)
                else:
                    self._log_missing_header(rel_tutor)
        except Exception:
            pass

        # Updating egg moves can be added similarly; for now we leave them unchanged
        # Update egg moves blocks: egg_moves(SPECIES_BASE, MOVE_A, MOVE_B, ...)
        try:
            import re
            try:
                with ReadSourceFile(self.project_info, self.get_file_path("EGG_MOVES_H", True)) as fh:
                    egg_text = fh.read()
            except Exception:
                egg_text = ""
            if egg_text:
                # Build mapping base -> moves
                egg_map: dict[str, list[str]] = {}
                for sp, entries in species_moves.items():
                    base = sp[len("SPECIES_") :] if sp.startswith("SPECIES_") else sp
                    ml = [e.get("move") for e in entries if _method(e) == "EGG" and e.get("move")]
                    if ml:
                        egg_map[base] = sorted(set(ml))

                for base, mv_list in egg_map.items():
                    # Construct replacement block with vanilla indentation
                    lines = [f"egg_moves({base},"]
                    for mv in mv_list:
                        lines.append(f"              {mv},")
                    # Remove trailing comma from last move
                    if len(lines) > 1:
                        lines[-1] = lines[-1].rstrip(',')
                    lines.append(")")
                    new_block = "\n".join(lines)
                    # Replace existing species block if present
                    pat = re.compile(rf"egg_moves\(\s*{re.escape(base)}\s*,[\s\S]*?\)")
                    if pat.search(egg_text):
                        egg_text = pat.sub(new_block, egg_text)
                    else:
                        # Insert before EGG_MOVES_TERMINATOR or closing brace
                        idx = egg_text.rfind("EGG_MOVES_TERMINATOR")
                        if idx == -1:
                            idx = egg_text.rfind("};")
                        if idx == -1:
                            egg_text = egg_text.rstrip() + "\n" + new_block + ",\n"
                        else:
                            egg_text = egg_text[:idx] + new_block + ",\n" + egg_text[idx:]

                rel_egg, egg_exists = self._header_for_key("EGG_MOVES_H")
                if egg_exists:
                    with WriteSourceFile(self.project_info, rel_egg) as fh:
                        fh.write(egg_text)
                else:
                    self._log_missing_header(rel_egg)
        except Exception:
            pass

    def plan_writebacks(self) -> dict[str, list[str]]:
        """Return a preview mapping of headers to species that would be updated.

        Keys are relative header paths; values are a list of species constants.
        """
        self._resolve_header_paths()
        result: dict[str, set[str]] = {}
        missing: set[str] = set()
        sm = self.data.get("species_moves") or {}

        def _maybe_collect(file_key: str, species: str) -> None:
            rel_path, exists = self._header_for_key(file_key)
            rel_norm = os.path.normpath(rel_path)
            rel_display = rel_norm.replace('\\', '/')
            if exists:
                result.setdefault(rel_display, set()).add(species)
            else:
                missing.add(rel_display)

        for sp, entries in sm.items():
            kinds = {e.get("method") for e in entries}
            if "LEVEL" in kinds:
                _maybe_collect("LVL_SETS_H", sp)
                _maybe_collect("LVL_PTRS_H", sp)
            if ("TM" in kinds) or ("HM" in kinds):
                _maybe_collect("TMHM_SETS_H", sp)
            if "TUTOR" in kinds:
                _maybe_collect("TUTOR_SETS_H", sp)
            if "EGG" in kinds:
                _maybe_collect("EGG_MOVES_H", sp)

        for rel_path in sorted(missing):
            self._log_missing_header(rel_path)

        return {k: sorted(v) for k, v in result.items()}


class Pokedex(pokemon_data.Pokedex):
    def __init__(self, project_info, parent=None):
        # Initialise base class first so project_info is available
        super().__init__(project_info, parent)

        # Files to back up must be added before parsing occurs
        self.add_file_to_backup(
            os.path.join("include", "constants", "pokedex.h"),
            file_key="POKEDEX_H",
        )

        # Instantiate your corresponding extractor class
        self.instantiate_extractor(pee.PokedexDataExtractor)

    @override
    def parse_to_c_code(self):
        super().parse_to_c_code()

        # Update species dex numbers to match current Pokédex order
        species_data = None
        if getattr(self.parent, "data", None):
            species_data = self.parent.data.get("species_data")
        if species_data:
            for i, entry in enumerate(self.data.get("national_dex", []), start=1):
                sp = entry.get("species")
                if sp in species_data.data:
                    species_data.data[sp]["dex_num"] = i
                    species_data.data[sp]["dex_constant"] = entry.get("dex_constant")
                entry["dex_num"] = i
            # Persist updated species dex numbers
            species_data.save()

        # In-place patch: replace only the NATIONAL_DEX enum body in include/constants/pokedex.h
        rel_path = self.get_file_path("POKEDEX_H")
        try:
            with ReadSourceFile(self.project_info, rel_path) as rf:
                content = rf.read()
        except Exception as e:
            raise RuntimeError(f"Failed to read pokedex.h: {e}")

        lines = content.splitlines(keepends=True)

        # Heuristic to find the National Dex enum block
        start_idx = -1
        end_idx = -1
        # Prefer comment marker if present
        anchor = None
        for i, ln in enumerate(lines):
            if "National Pokedex order" in ln:
                anchor = i
                break
        search_from = anchor + 1 if anchor is not None else 0
        # Find the next line with 'enum' and '{'
        for i in range(search_from, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith("enum") and "{" in stripped:
                start_idx = i
                break
        if start_idx == -1:
            raise RuntimeError("Could not locate NATIONAL_DEX enum start in pokedex.h; aborting to preserve file")

        # Walk forward to find the matching closing '};' for this enum
        depth = 0
        found_open = False
        for j in range(start_idx, len(lines)):
            # Count braces on this line
            depth += lines[j].count("{")
            if depth > 0:
                found_open = True
            depth -= lines[j].count("}")
            # Closing line contains '};' when depth returns to 0
            if found_open and depth == 0:
                end_idx = j
                break
        if end_idx == -1 or end_idx <= start_idx:
            raise RuntimeError("Could not locate NATIONAL_DEX enum end in pokedex.h; aborting to preserve file")

        # Determine indentation inside enum (default 4 spaces)
        indent = "    "
        for k in range(start_idx + 1, min(end_idx, start_idx + 10)):
            body_ln = lines[k]
            if body_ln.strip():
                indent = body_ln[: len(body_ln) - len(body_ln.lstrip())]
                break

        # Capture comment lines from the old enum body so they can be
        # re-inserted at the correct positions in the regenerated body.
        # Map: NATIONAL_DEX_* constant → list of comment lines that precede it.
        old_body_lines = lines[start_idx + 1 : end_idx]
        comments_before: dict[str, list[str]] = {}
        pending_comments: list[str] = []
        for obl in old_body_lines:
            stripped = obl.strip()
            if stripped.startswith("//"):
                pending_comments.append(obl)
            elif stripped.startswith("NATIONAL_DEX_"):
                # Extract the constant name (strip trailing comma)
                cname = stripped.rstrip(",").strip()
                if pending_comments:
                    comments_before[cname] = list(pending_comments)
                    pending_comments.clear()

        # Build new body preserving simple style: one constant per line with trailing comma
        new_body = []
        # Re-insert any comments that preceded NATIONAL_DEX_NONE
        for cl in comments_before.get("NATIONAL_DEX_NONE", []):
            new_body.append(cl if cl.endswith("\n") else cl + "\n")
        new_body.append(f"{indent}NATIONAL_DEX_NONE,\n")
        for entry in self.data.get("national_dex", []):
            const = entry.get("dex_constant")
            if not const:
                continue
            # Re-insert any comments that preceded this constant
            for cl in comments_before.get(const, []):
                new_body.append(cl if cl.endswith("\n") else cl + "\n")
            new_body.append(f"{indent}{const},\n")

        # Assemble new file content
        # Keep everything up to and including the 'enum {' line
        pre = lines[: start_idx + 1]
        post = lines[end_idx:]
        # Ensure closing line '};' remains intact at the start of 'post'
        if not post or not post[0].strip().endswith("};"):
            # The end line should be included in post already; enforce it
            post = [lines[end_idx]] + lines[end_idx + 1 :]
        patched = pre + new_body + post
        new_content = "".join(patched)

        # Only write back if content changed
        if new_content != content:
            with WriteSourceFile(self.project_info, rel_path) as wf:
                wf.write(new_content)
        # Done: Hoenn enum, defines, guards, and comments remain untouched.

class PokemonDataManager(pokemon_data.PokemonDataManager):
    """
    A class that manages the data for Pokemon.

    This class holds instances of each of the data classes for Pokemon and other info
    in the game. This is how the UI interfaces with the plugin's data classes.
    """

    SOURCE_PREFIX = ""

    def _fr_order_learnset(self, entries):
        """Return a list of learnset entries sorted like the vanilla headers."""
        if not entries:
            return []
        def sort_key(pair):
            idx, entry = pair
            method = str(entry.get('method') or '').upper()
            priority = {'LEVEL': 0, 'TM': 1, 'HM': 2, 'TUTOR': 3, 'EGG': 4}
            base = priority.get(method, 5)
            if method == 'LEVEL':
                try:
                    lvl = int(entry.get('value') or 0)
                except Exception:
                    lvl = 0
                return (base, lvl, idx)
            if method in {'TM', 'HM'}:
                code = str(entry.get('value') or '').upper()
                match = re.match(r'(?:TM|HM)(\d+)', code)
                num = int(match.group(1)) if match else 999
                return (base, num, idx)
            return (base, str(entry.get('move') or ''), idx)
        return [entry for _, entry in sorted(enumerate(entries), key=sort_key)]

    def __init__(self, project_info, logger=None):
        # Must call the parent class' __init__ method before adding your data classes
        super().__init__(project_info, logger=logger)

        # Add your data classes
        self.add_species_data_class(SpeciesData)
        self.add_species_graphics_class(SpeciesGraphics)
        self.add_pokemon_abilities_class(PokemonAbilities)
        self.add_pokemon_items_class(PokemonItems)
        self.add_pokemon_constants_class(PokemonConstants)
        self.add_pokemon_evolutions_class(PokemonEvolutions)
        self.add_pokemon_starters_class(PokemonStarters)
        self.add_pokemon_trainers_class(PokemonTrainers)
        self.add_pokemon_moves_class(PokemonMoves)
        self.add_pokedex_class(Pokedex)
        self.refactor_service = RefactorService(project_info)

        # In-memory overlays (no writes on load)
        self._fr_species_moves_overlay: dict[str, list] = {}
        self._fr_move_desc_overlay: dict[str, str] = {}
        self._fr_move_desc_ready: bool = False

    # --- FireRed-specific overlays to avoid load-time writes ---
    def _fr_read_header_lines(self, *parts: str) -> list[str]:
        try:
            with ReadSourceFile(self.project_info, os.path.join(*parts)) as f:
                return f.readlines()
        except Exception:
            return []

    def _fr_get_species_learnset(self, species: str) -> list:
        if species in self._fr_species_moves_overlay:
            return self._fr_species_moves_overlay[species]
        try:
            import re
            root_parts = ("src", "data", "pokemon")
            out: list[dict] = []
            def _ensure(move, method, value):
                out.append({"move": move, "method": method, "value": value})
            # Level-up
            ptr = self._fr_read_header_lines(*root_parts, "level_up_learnset_pointers.h")
            lvl = self._fr_read_header_lines(*root_parts, "level_up_learnsets.h")
            if ptr and lvl:
                lvl_text = "\n".join(lvl)
                ptr_pat = re.compile(r"\[\s*(SPECIES_[A-Z0-9_]+)\s*\]\s*=\s*(s\w+LevelUpLearnset)")
                arr_pat = re.compile(r"static\s+const\s+(?:struct\s+LevelUpMove|u16)\s+(s\w+LevelUpLearnset)\[\]\s*=\s*\{(.*?)\};", re.S)
                move_pat = re.compile(
                    r"LEVEL_UP_MOVE\(\s*(\d+)\s*,\s*(MOVE_[A-Z0-9_]+)\s*\)"
                )
                arrays = {m.group(1): m.group(2) for m in arr_pat.finditer(lvl_text)}
                sym = None
                for m in ptr_pat.finditer("\n".join(ptr)):
                    if m.group(1) == species:
                        sym = m.group(2); break
                if sym and sym in arrays:
                    body = arrays[sym]
                    for mv in move_pat.finditer(body):
                        try:
                            level = int(mv.group(1))
                        except Exception:
                            continue
                        _ensure(mv.group(2), "LEVEL", level)
            # TM/HM
            tm = self._fr_read_header_lines(*root_parts, "tmhm_learnsets.h")
            if tm:
                sp_pat = re.compile(rf"\[\s*{re.escape(species)}\s*\]\s*=\s*TMHM_LEARNSET\((.*?)\)", re.S)
                tok_pat = re.compile(r"TMHM\((TM\d+_[A-Z0-9_]+|HM\d+_[A-Z0-9_]+)\)")
                blob = "\n".join(tm)
                m = sp_pat.search(blob)
                if m:
                    for tok in tok_pat.findall(m.group(1)):
                        kind, rest = tok.split('_', 1)
                        _ensure(f"MOVE_{rest}", "TM" if kind.startswith("TM") else "HM", kind)
            # Tutor
            tut = self._fr_read_header_lines(*root_parts, "tutor_learnsets.h")
            if tut:
                sp_pat = re.compile(rf"\[\s*{re.escape(species)}\s*\]\s*=\s*([^\n]+?)\,")
                mv_pat = re.compile(r"TUTOR\((MOVE_[A-Z0-9_]+)\)")
                blob = "\n".join(tut)
                m = sp_pat.search(blob)
                if m:
                    for mv in mv_pat.findall(m.group(1)):
                        _ensure(mv, "TUTOR", "")
            # Egg
            egg = self._fr_read_header_lines(*root_parts, "egg_moves.h")
            if egg:
                base = species[len("SPECIES_") :] if species.startswith("SPECIES_") else species
                eg_pat = re.compile(rf"egg_moves\(\s*{re.escape(base)}\s*,(.*?)\)", re.S)
                blob = "\n".join(egg)
                m = eg_pat.search(blob)
                if m:
                    for tok in m.group(1).split(','):
                        mv = tok.strip()
                        if mv.startswith("MOVE_"):
                            _ensure(mv, "EGG", "")
            ordered = self._fr_order_learnset(out)
            self._fr_species_moves_overlay[species] = ordered
            return ordered
        except Exception:
            return []

    def _fr_build_move_desc_map(self) -> None:
        if self._fr_move_desc_ready:
            return
        try:
            import re
            text = "\n".join(self._fr_read_header_lines("src", "move_descriptions.c"))
            # Match entries like: [MOVE_POUND] = _("Text with \\n+            # newlines");
            # Capture escaped sequences inside the C string.
            pat = re.compile(r"\[\s*(MOVE_[A-Z0-9_]+)\s*\]\s*=\s*_\(\s*\"((?:[^\\\"]|\\.)*)\"\s*\)\s*;", re.S)
            for m in pat.finditer(text):
                key = m.group(1)
                raw = m.group(2)
                # Unescape common sequences like \n
                # Keep \n literal so editors see line breaks tokens exactly as in source
                desc = raw.replace("\\\"", '"').replace("\\\\", "\\")
                self._fr_move_desc_overlay[key] = desc
        except Exception:
            self._fr_move_desc_overlay = {}
        self._fr_move_desc_ready = True

    def _fr_get_move_description(self, move: str) -> str:
        if not self._fr_move_desc_ready:
            try:
                self._fr_build_move_desc_map2()
            except Exception:
                self._fr_build_move_desc_map()
        return self._fr_move_desc_overlay.get(move, "")

    def _fr_build_move_desc_map2(self) -> None:
        if self._fr_move_desc_ready:
            return
        import re
        text = "\n".join(self._fr_read_header_lines("src", "move_descriptions.c"))
        # FireRed uses variables like gMoveDescription_Pound[] = _("...");
        head_pat = re.compile(r"gMoveDescription_([A-Za-z0-9]+)\s*\[\]\s*=\s*_\(", re.S)
        pos = 0
        while True:
            m = head_pat.search(text, pos)
            if not m:
                break
            suffix = m.group(1)
            # Convert CamelCase (KarateChop) to MOVE_KARATE_CHOP
            const = "MOVE_" + re.sub(r"([A-Z])", r"_\1", suffix).upper().lstrip('_')
            start = m.end()
            end = text.find(");", start)
            if end == -1:
                break
            block = text[start:end]
            parts = re.findall(r"\"((?:[^\\\"]|\\.)*)\"", block)
            raw = "".join(parts)
            # Keep \n literal tokens for editing precision
            desc = raw.replace("\\\"", '"').replace("\\\\", "\\")
            self._fr_move_desc_overlay[const] = desc
            pos = end + 2
        self._fr_move_desc_ready = True

    # Override: species moves without touching files on load
    def get_species_moves(self, species: str) -> list:
        pm = self.data.get("pokemon_moves")
        if pm and pm.data:
            sm = pm.data.get("species_moves") or {}
            if species in sm:
                return sm.get(species, [])
        return self._fr_get_species_learnset(species)

    # Override: move description without touching files on load
    def get_move_description(self, move: str) -> str:
        # Prefer JSON cache
        pm = self.data.get("pokemon_moves")
        if pm and pm.data:
            md = pm.data.get("move_descriptions", {})
            if move in md:
                return md.get(move) or ""
        return self._fr_get_move_description(move)

    # Override: moves map; augment with description for UI without writing files
    def get_pokemon_moves(self) -> dict:
        pm = self.data.get("pokemon_moves")
        base = pm.data.get("moves", {}) if pm and pm.data else {}
        # Build description map once
        if not self._fr_move_desc_ready:
            try:
                self._fr_build_move_desc_map2()
            except Exception:
                self._fr_build_move_desc_map()
        # Shallow copy to avoid mutating backing JSON in memory
        out = {}
        for mv, info in base.items():
            try:
                d = dict(info) if isinstance(info, dict) else {}
            except Exception:
                d = {}
            # Only attach if not present
            if "description" not in d:
                d["description"] = self._fr_move_desc_overlay.get(mv, "")
            out[mv] = d
        return out

    def _format_graphics_constant(self, species: str, key: str) -> str | None:
        """Return the expected graphics constant for ``species`` and ``key``.

        The FireRed graphics files consistently use names like
        ``gMonFrontPic_Bulbasaur``. When ``species.json`` lacks explicit
        mappings this helper derives the constant from the species name and
        requested key. The result is only returned when a matching entry exists
        in ``species_graphics.json``.
        """

        prefix_map = {
            "frontPic": "gMonFrontPic_",
            "backPic": "gMonBackPic_",
            "iconSprite": "gMonIcon_",
            "footprint": "gMonFootprint_",
        }

        sg = self.data.get("species_graphics")
        if not sg or not sg.data:
            return None

        prefix = prefix_map.get(key)
        if not prefix:
            return None

        base = species
        if base.startswith("SPECIES_"):
            base = base[len("SPECIES_") :]

        name = "".join(part.capitalize() for part in base.lower().split("_"))
        const = prefix + name
        if const in sg.data:
            return const
        return None

    @override
    def get_species_image(
        self, species: str, key: str, index: int = -1, form: str | None = None
    ):
        image_name = self.get_species_info(species, key, form)
        sg = self.data.get("species_graphics")
        if (not sg or not sg.data) and image_name is None:
            return None
        if image_name is None or not isinstance(image_name, str) or image_name not in getattr(sg, "data", {}):
            image_name = self._format_graphics_constant(form or species, key)
        if not image_name:
            return None
        return sg.get_image(image_name, index) if sg else None

    @override
    def get_species_image_path(
        self, species: str, key: str, form: str | None = None
    ) -> str | None:
        """Return an absolute path (forward slashes) for the requested image.

        Order: mapping -> derived constant -> conventional layout fallback.
        """

        def _norm(p: str) -> str:
            p = os.path.normpath(p)
            return p.replace(os.sep, "/") if os.sep == "\\" else p

        def _readable(abs_path: str, log_name: str) -> str | None:
            ap = _norm(abs_path)
            if not os.path.isfile(ap):
                logging.warning("Image file for %s not found: %s", log_name, ap)
                return None
            from PyQt6.QtGui import QImageReader
            if not QImageReader(ap).canRead():
                logging.warning("Failed to load image file %s for %s", ap, log_name)
                return None
            logging.debug("Final image path for %s: %s", log_name, ap)
            return ap

        image_name = self.get_species_info(species, key, form)
        sg = self.data.get("species_graphics")

        if image_name is None or not isinstance(image_name, str) or image_name not in getattr(sg, "data", {}):
            image_name = self._format_graphics_constant(form or species, key)

        if isinstance(sg, object) and getattr(sg, "data", None) and image_name in sg.data:
            image_url = sg.data[image_name]["png"]
            prefix = self.project_info.get("source_prefix", self.SOURCE_PREFIX)
            mapped = os.path.join(self.project_info["dir"], prefix, image_url) if prefix else os.path.join(self.project_info["dir"], image_url)
            ok = _readable(mapped, image_name)
            if ok:
                return ok

        key_to_file = {
            "frontPic": "front.png",
            "backPic": "back.png",
            "iconSprite": "icon.png",
            "footprint": "footprint.png",
        }
        filename = key_to_file.get(key)
        base = (form or species) or ""
        if base.startswith("SPECIES_"):
            base = base[len("SPECIES_") :]
        species_slug = base.lower()
        if filename and species_slug:
            fallback = os.path.join(self.project_info["dir"], "graphics", "pokemon", species_slug, filename)
            ok = _readable(fallback, f"{species_slug}/{filename}")
            if ok:
                return ok

        return None

    @override
    def get_species_shiny_image_path(
        self, species: str, key: str, form: str | None = None
    ) -> str | None:
        """Generate shiny front sprite by applying shiny.pal to base front.png."""
        if key != "frontPic":
            return None
        base_path = self.get_species_image_path(species, key, form=form)
        if not base_path:
            return None
        def _norm(p: str) -> str:
            p = os.path.normpath(p)
            return p.replace(os.sep, "/") if os.sep == "\\" else p
        from PyQt6.QtGui import QImage, QColor
        base_dir = os.path.dirname(base_path)
        normal_pal = os.path.join(base_dir, "normal.pal")
        shiny_pal = os.path.join(base_dir, "shiny.pal")
        if not (os.path.isfile(normal_pal) and os.path.isfile(shiny_pal)):
            return None
        def _load_pal(path: str) -> list[tuple[int, int, int]] | None:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            except OSError:
                return None
            colors: list[tuple[int, int, int]] = []
            if lines and lines[0].upper().startswith("JASC-PAL"):
                try:
                    count = int(lines[2])
                    body = lines[3:3+count]
                except Exception:
                    body = lines[3:]
                for ln in body:
                    parts = [p for p in ln.replace(",", " ").split() if p]
                    if len(parts) >= 3:
                        try:
                            colors.append((int(parts[0]), int(parts[1]), int(parts[2])))
                        except ValueError:
                            continue
            else:
                for ln in lines:
                    parts = [p for p in ln.replace(",", " ").split() if p]
                    if len(parts) >= 3:
                        try:
                            colors.append((int(parts[0]), int(parts[1]), int(parts[2])))
                        except ValueError:
                            continue
            return colors or None
        normal = _load_pal(normal_pal)
        shiny = _load_pal(shiny_pal)
        if not normal or not shiny:
            return None
        n = min(len(normal), len(shiny))
        normal = normal[:n]
        shiny = shiny[:n]
        img = QImage(base_path)
        if img.isNull():
            return None
        repl = {normal[i]: shiny[i] for i in range(n)}
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        w, h = img.width(), img.height()
        for y in range(h):
            for x in range(w):
                c = img.pixelColor(x, y)
                key_rgb = (c.red(), c.green(), c.blue())
                new_rgb = repl.get(key_rgb)
                if new_rgb is not None:
                    img.setPixelColor(x, y, QColor(new_rgb[0], new_rgb[1], new_rgb[2], c.alpha()))
        slug = species
        if slug.startswith("SPECIES_"):
            slug = slug[len("SPECIES_"):]
        slug = "".join(part.lower() for part in slug.split("_"))
        from app_info import get_cache_dir
        out_dir = os.path.join(get_cache_dir(self.project_info.get("dir", "")), "shiny", slug)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            return None
        out_path = os.path.join(out_dir, "front_shiny.png")
        if not img.save(out_path, "PNG"):
            return None
        return _norm(out_path)
      


