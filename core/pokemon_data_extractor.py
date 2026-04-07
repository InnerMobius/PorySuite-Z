import os
import re
import json
from collections import OrderedDict

# Updated for the FireRed repository layout.
# Paths now reference files directly under include/ and src/.

from core.pokemon_data_extractor_base import PokemonDataExtractor
from .utils import preprocess_c_file


def _find_abilities_header(root: str) -> str | None:
    """Return the path to the header file defining all abilities."""

    preferred = os.path.join(root, "include", "constants", "abilities.h")
    if os.path.isfile(preferred):
        return preferred

    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            if not fname.endswith(".h"):
                continue
            path = os.path.join(dirpath, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if "#define ABILITY_" in line:
                            return path
            except OSError:
                continue
    return None


def _order_learnset_entries(entries):
    """Return learnset entries sorted like FireRed headers."""
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


def _clean_line(line: str) -> str:
    """Strip inline comments and whitespace."""
    line = re.sub(r"/\*.*?\*/", "", line)
    line = line.split("//")[0]
    return line.strip()


def _load_json(path: str, source_headers: list[str] | None = None) -> dict | list | None:
    """Return JSON data if file exists, is valid, and is not stale.

    If *source_headers* is given, the cache is considered stale when any
    of those files has a modification time newer than the JSON file.
    """
    abs_path = os.path.abspath(path)
    print(f"Reading {abs_path}")
    try:
        if source_headers:
            json_mtime = os.path.getmtime(path)
            for hdr in source_headers:
                if os.path.isfile(hdr) and os.path.getmtime(hdr) > json_mtime:
                    print(f"Cache stale: {os.path.basename(hdr)} is newer than {os.path.basename(path)}, re-extracting")
                    return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if data:
                return data
    except (FileNotFoundError, json.decoder.JSONDecodeError):
        pass
    return None


def _write_json(path: str, data: dict | list, min_entries: int = 1) -> bool:
    """Write JSON data and return ``True`` on success.

    ``min_entries`` defines the smallest acceptable number of entries. If
    ``data`` is empty or contains fewer than this amount, a warning is logged
    and ``False`` is returned so callers can abort loading when parsing
    produced no results.
    """

    if not data or len(data) < min_entries:
        print(
            f"Warning: refusing to write fewer than {min_entries} entries to {os.path.abspath(path)}"
        )
        return False

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"Wrote {os.path.abspath(path)}")
    return True


def _read_header(util, *parts: str) -> list[str]:
    """Return preprocessed lines from a header or ``[]`` on failure."""

    try:
        root = util.repo_root()
    except Exception:
        root = util.repo_root()

    candidates = [os.path.join(root, *parts)]
    for path in candidates:
        abs_path = os.path.abspath(path)
        if not os.path.isfile(path):
            print(f"Missing header: {abs_path}")
            continue

        print(f"Reading {abs_path}")
        try:
            lines = preprocess_c_file(path, util.project_info)
            if lines:
                return lines
            print(f"Preprocess produced no output for {abs_path}; using raw file")
        except Exception as e:
            print(f"Preprocess failed for {abs_path}: {e}")
            print("Fallback parser engaged: parsing without header resolution.")

        try:
            with open(path, encoding="utf-8") as f:
                return f.readlines()
        except OSError as e:
            print(f"Unreadable header: {abs_path} ({e})")
    return []


def parse_species_names(root: str) -> dict[str, str]:
    """Return a mapping of species constants to display names."""
    path = os.path.join(root, "src", "data", "text", "species_names.h")
    if not os.path.isfile(path):
        print(f"Missing header: {os.path.abspath(path)}")
        return {}
    print(f"Reading {os.path.abspath(path)}")
    pattern = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*_\(\"([^\"]+)\"\)")
    names: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = pattern.search(ln)
            if m:
                names[m.group(1)] = m.group(2)
    return names


def _parse_species_count(root: str) -> int:
    """Return ``NUM_SPECIES`` from ``constants/species.h`` or ``0`` on failure."""
    path = os.path.join(root, "include", "constants", "species.h")
    if not os.path.isfile(path):
        print(f"Missing header: {os.path.abspath(path)}")
        return 0
    print(f"Reading {os.path.abspath(path)}")
    defines: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.split("//")[0].strip()
            m = re.match(r"#define\s+(\w+)\s+(\S+)", ln)
            if m:
                defines[m.group(1)] = m.group(2)
    count = defines.get("NUM_SPECIES") or defines.get("SPECIES_COUNT")
    if not count:
        return 0

    def resolve(val: str) -> int | None:
        if val.isdigit():
            return int(val)
        if val in defines:
            return resolve(defines[val])
        m = re.match(r"\((\w+)\s*\+\s*(\d+)\)", val)
        if m:
            base = resolve(m.group(1))
            if base is not None:
                return base + int(m.group(2))
        return None

    result = resolve(count)
    return result if result is not None else 0


def parse_pokedex_texts(util) -> dict[str, str]:
    """Return a mapping of Pokédex text constants to their strings."""
    root = util.repo_root()
    files = [
        os.path.join(root, "src", "data", "pokemon", "pokedex_text_lg.h"),
        os.path.join(root, "src", "data", "pokemon", "pokedex_text_fr.h"),
    ]
    texts: dict[str, str] = {}
    pattern = re.compile(
        r"const\s+u8\s+(g\w+)\s*\[\]\s*=\s*_\(\s*((?:\".*?\"\s*)+)\);",
        re.S,
    )
    for path in files:
        if not os.path.isfile(path):
            continue
        print(f"Reading {os.path.abspath(path)}")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        for const, body in pattern.findall(content):
            lines = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', body, re.S)
            text = "".join(lines)
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            text = text.replace("\\n", "\n")
            texts[const] = text
    return texts


def parse_pokedex_entries(util) -> dict[str, dict]:
    """Parse ``pokedex_entries.h`` and return a mapping of species constants."""
    root = util.repo_root()
    lines = _read_header(
        util,
        "src",
        "data",
        "pokemon",
        "pokedex_entries.h",
    )
    if not lines:
        print("Missing file or empty read for pokedex entries")
    # collect valid species constants from species.h
    species_path = os.path.join(root, "include", "constants", "species.h")
    species_consts = set()
    if os.path.isfile(species_path):
        print(f"Reading {os.path.abspath(species_path)}")
        with open(species_path, encoding="utf-8") as f:
            for ln in f:
                m = re.match(r"#define\s+(SPECIES_[A-Z0-9_]+)\s+(\d+)", ln)
                if m:
                    species_consts.add(m.group(1))

    text_strings = parse_pokedex_texts(util)

    entries: dict[str, dict] = {}
    current: str | None = None
    i = 0
    while i < len(lines):
        ln = _clean_line(lines[i])
        i += 1
        if not ln:
            continue
        m = re.match(r"\[(NATIONAL_DEX_[A-Z0-9_]+)\]\s*=", ln)
        if m:
            dex_const = m.group(1)
            rest = ln[m.end():].strip()
            while not rest and i < len(lines):
                rest = _clean_line(lines[i])
                i += 1
            if not rest.startswith("{"):
                current = None
                continue
            species_const = "SPECIES_" + dex_const[len("NATIONAL_DEX_"):]
            if species_const not in species_consts:
                current = None
                continue
            current = species_const
            entries[current] = {"dex_constant": dex_const}
            if rest != "{":
                ln = rest
            else:
                continue

        if current is None:
            continue

        if ln.startswith("}"):
            current = None
            continue

        if ln.startswith("."):
            entry = ln
            while not entry.rstrip().endswith(",") and i < len(lines):
                extra = _clean_line(lines[i])
                i += 1
                entry += " " + extra

            kv = re.match(r"\.(\w+)\s*=\s*(.*),", entry)
            if kv:
                key = kv.group(1)
                val = kv.group(2).strip()
                if key == "categoryName":
                    mcat = re.match(r'_\("(.*)"\)', val)
                    if mcat:
                        val = mcat.group(1)
                elif key in {
                    "height",
                    "weight",
                    "pokemonScale",
                    "pokemonOffset",
                    "trainerScale",
                    "trainerOffset",
                }:
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                if key in {"description", "unusedDescription"}:
                    text = text_strings.get(val)
                    if text is not None:
                        entries[current][f"{key}Text"] = text
                entries[current][key] = val

    return entries




class SpeciesDataExtractor(PokemonDataExtractor):
    """
    Extracts Pokémon species data using the FireRed layout.
    """

    def __init__(
        self,
        project_info: dict,
        data_file: str = None,
        files: dict = None,
        rebuild_on_type_mismatch: bool | None = None,
    ):
        super().__init__(project_info, data_file, files)
        self._species_header_lines: list[str] | None = None
        if rebuild_on_type_mismatch is None:
            env = os.getenv("PORYSUITE_REBUILD_ON_TYPE_MISMATCH")
            rebuild_on_type_mismatch = bool(env and env != "0")
        self.rebuild_on_type_mismatch = rebuild_on_type_mismatch

    def reset_cache(self) -> None:
        """Clear the cached species_info.h lines so the next extraction re-reads from disk."""
        self._species_header_lines = None

    def _get_species_header_lines(self) -> list[str]:
        """Return cached ``species_info.h`` lines."""
        if self._species_header_lines is None:
            self._species_header_lines = _read_header(self.docker_util, self.HEADER_FILE)
        return self._species_header_lines

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        def parse_types(val):
            return [t.strip() for t in val.strip("{}").split(",")]

        def parse_gender_ratio(val):
            if isinstance(val, int):
                return val

            # Strip optional numeric casts like ``(u8)``
            val = re.sub(r'^\([^\)]+\)\s*', '', str(val).strip())

            # ``PERCENT_FEMALE(x)`` or ``min(254, ((x * 255) / 100))``
            pf = re.search(r'PERCENT_FEMALE\(\s*([0-9.]+)\s*\)', val)
            if pf:
                return int(round(float(pf.group(1)) * 255 / 100))

            mn = re.search(
                r'min\s*\(\s*254\s*,\s*\(\s*\(\s*([0-9.]+)\s*\*\s*255\s*\)\s*/\s*100\s*\)\s*\)',
                val,
            )
            if mn:
                return int(round(float(mn.group(1)) * 255 / 100))

            if val == "MON_MALE":
                return 0
            if val == "MON_FEMALE":
                return 254
            if val == "MON_GENDERLESS":
                return 255

            return None

        def parse_egg_groups(val):
            return [eg.strip() for eg in val.strip("{}").split(",")]

        def parse_abilities(val):
            return [a.strip() for a in val.strip("{}").split(",")]

        def parse_species_name(val):
            return re.sub(r'_\("(.*)"\)', r'\1', val)

        def parse_category_name(val):
            return re.sub(r'_\("(.*)"\)', r'\1', val)

        def parse_description(val):
            return val.replace("\\n", "\n")

        def parse_flags(val):
            return [f.strip() for f in val.split(" | ")]

        def parse_pic_size(val):
            match = re.match(r'MON_COORDS_SIZE\((.*), (.*)\)', val)
            return match.groups()

        def parse_evolutions(val):
            if isinstance(val, str):
                evos = re.sub(r'\(const struct Evolution\[\]\)\s*\{\s*\{(.*)\},\s*\}', r'\1', val).strip()
                evos = evos.split("}, {")
                result = []
                for evo in evos:
                    evo = evo.strip().split(", ")
                    method = evo[0].strip()
                    if len(evo) == 3:
                        try:
                            param = int(evo[1].strip())
                        except ValueError:
                            param = evo[1].strip()
                        target_species = evo[2].strip()
                        evo_dict = {
                            "method": method,
                            "param": param,
                            "targetSpecies": target_species,
                        }
                    else:
                        if method == "EVOLUTIONS_END":
                            continue
                        evo_dict = {
                            "method": evo[0].strip(),
                            "param": None,
                            "targetSpecies": None,
                        }
                    result.append(evo_dict)
                return result
            return val

        def parse_conditionals(val):
            # Check if the value is a list or tuple
            if isinstance(val, list):
                value_list = val
            elif isinstance(val, tuple):
                value_list = list(val)
            else:
                value_list = [val]

            # Iterate through each value in the list
            for i in range(len(value_list)):
                # Check if the value is a string
                if not isinstance(value_list[i], str):
                    continue

                # Use regex to match conditional expressions
                matches = re.findall(r'(?:\b|\()\s*(.+?)\s+(.+?)\s+(.+?)\s+(\?)\s+(.+?)\s+(:)\s+(.+?)(?:\)|$)',
                                     value_list[i])

                # If there is a single match, extract the parameters
                if len(matches) == 1:
                    match = matches[0]
                    param1, condition, param2, _, true_value, _, false_value = match
                    to_add = 0
                    to_subtract = 0

                    # Check if there is a value to add or subtract
                    if value_list[i].split(" ")[-2] == "+":
                        to_add = int(value_list[i].split(" ")[-1])
                    elif value_list[i].split(" ")[-2] == "-":
                        to_subtract = int(value_list[i].split(" ")[-1])

                    # Create a dictionary of parameters
                    parameters = {
                        "param1": param1,
                        "condition": condition,
                        "param2": param2,
                        "true_value": true_value,
                        "false_value": false_value,
                        "to_add": to_add,
                        "to_subtract": to_subtract
                    }

                    # Update the value with the parameters
                    if isinstance(val, str):
                        val = parameters
                    else:
                        value_list[i] = parameters

            # Return the updated value
            if isinstance(val, list) or isinstance(val, tuple):
                return value_list
            return val

        # Dictionary of parsers
        parsers = {
            "types": parse_types,
            "genderRatio": parse_gender_ratio,
            "friendship": lambda val: 70 if val == "STANDARD_FRIENDSHIP" else val,
            "eggGroups": parse_egg_groups,
            "abilities": parse_abilities,
            "speciesName": parse_species_name,
            "categoryName": parse_category_name,
            "description": parse_description,
            "flags": parse_flags,
            "frontPicSize": parse_pic_size,
            "backPicSize": parse_pic_size,
            "frontPicSizeFemale": parse_pic_size,
            "backPicSizeFemale": parse_pic_size,
            "evolutions": parse_evolutions,
        }

        try:
            value = int(value)
        except ValueError:
            pass
        if key in parsers:
            value = parse_conditionals(parsers[key](value))

        return key, value

    HEADER_FILE = os.path.join("src", "data", "pokemon", "species_info.h")

    def _parse_gender_ratio(self, species: str) -> int | None:
        """Return the gender ratio for ``species`` directly from the header."""
        lines = self._get_species_header_lines()
        if not lines:
            return None
        current = None
        pattern = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=")
        for ln in lines:
            ln = _clean_line(ln)
            m = pattern.match(ln)
            if m:
                current = m.group(1)
                continue
            if current == species and ".genderRatio" in ln:
                m2 = re.search(r"\.genderRatio\s*=\s*([^,]+)", ln)
                if m2:
                    _, val = self.parse_value_by_key("genderRatio", m2.group(1))
                    return val
        return None

    def _parse_header_data(self) -> dict:
        """Return species data parsed directly from ``species_info.h``."""
        lines = self._get_species_header_lines()
        if not lines:
            print("Missing file or empty read for species header")
        macro_defs: dict[str, dict] = {}
        species = {}
        current = None

        # Parse macro definitions like OLD_UNOWN_SPECIES_INFO
        i = 0
        while i < len(lines):
            raw = lines[i].rstrip()
            m = re.match(r"#define\s+(\w+)\s*\\", raw)
            if not m:
                i += 1
                continue
            macro_name = m.group(1)
            i += 1
            body_lines = []
            while i < len(lines):
                part = lines[i].rstrip()
                i += 1
                if part.endswith("\\"):
                    part = part[:-1]
                body_lines.append(_clean_line(part))
                if "}" in part:
                    break
            fields: dict[str, any] = {}
            for ln in body_lines:
                for kv in re.finditer(r"\.(\w+)\s*=\s*(\{[^}]*\}|[^,]+)", ln):
                    k, v = self.parse_value_by_key(kv.group(1), kv.group(2).strip())
                    fields[k] = v
            if fields:
                macro_defs[macro_name] = fields

        i = 0

        i = 0
        while i < len(lines):
            ln = _clean_line(lines[i])
            i += 1
            if not ln:                       # skip blanks / pure comments
                continue

            # start of a species entry
            m = re.match(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=", ln)
            if m:
                current_name = m.group(1)
                rest = ln[m.end():].strip()
                # advance through blank/comment lines to find opening brace
                while not rest and i < len(lines):
                    rest = _clean_line(lines[i])
                    i += 1
                macro_match = rest.rstrip(',')
                if macro_match in macro_defs:
                    species[current_name] = {"species_info": dict(macro_defs[macro_match]), "forms": {}}
                    current = None
                    continue
                if not rest.startswith("{"):
                    current = None
                    continue
                current = current_name
                species[current] = {"species_info": {}, "forms": {}}
                # consume anything after '{' on the same line
                if rest != "{":
                    ln = rest
                else:
                    continue

            if current is None:              # not inside a struct – keep scanning
                continue

            # closing brace ends the struct
            if ln.startswith("}"):
                current = None
                continue

            # key‑value line (may spill onto multiple lines)
            if ln.startswith("."):
                entry = ln
                while not entry.rstrip().endswith(",") and i < len(lines):
                    extra = _clean_line(lines[i])
                    i += 1
                    entry += " " + extra

                for kv in re.finditer(r"\.(\w+)\s*=\s*(\{[^}]*\}|[^,]+)", entry):
                    k, v = self.parse_value_by_key(kv.group(1), kv.group(2).strip())
                    species[current]["species_info"][k] = v

        # Parse evolutions.h for evolution data
        evo_lines = _read_header(
            self.docker_util,
            "src",
            "data",
            "pokemon",
            "evolution.h",
        )
        i = 0
        while i < len(evo_lines):
            ln = _clean_line(evo_lines[i])
            i += 1
            if not ln or ln.startswith("const struct Evolution"):
                continue

            m = re.match(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(.*)", ln)
            if not m:
                continue
            const = m.group(1)
            rest = m.group(2)
            while rest.count("{") > rest.count("}") and i < len(evo_lines):
                rest += " " + _clean_line(evo_lines[i])
                i += 1
            rest = rest.rstrip(",").strip()
            if rest and rest != "{}":
                value = rest.strip("{}")
                _, evos = self.parse_value_by_key("evolutions", value)
            else:
                evos = []
            species.setdefault(const, {"species_info": {}, "forms": {}})
            species[const]["species_info"]["evolutions"] = evos

        # Fallback: if no species were parsed (e.g., macro expansion not detected),
        # synthesize minimal entries from species_names.h so downstream tools have
        # a stable set. Also include SPECIES_EGG when present in headers.
        if not species:
            try:
                names = parse_species_names(self.docker_util.repo_root())
                if names:
                    defaults = {"baseHP": 1, "types": ["TYPE_NORMAL", "TYPE_NONE"], "abilities": ["ABILITY_NONE", "ABILITY_NONE", "ABILITY_NONE"], "evolutions": []}
                    for const in names.keys():
                        species[const] = {"species_info": dict(defaults), "forms": {}}
                    # Add EGG if defined in headers (best-effort)
                    egg_const = "SPECIES_EGG"
                    if egg_const not in species:
                        species[egg_const] = {"species_info": dict(defaults), "forms": {}}
            except Exception:
                pass

        # Load species names and validate count
        names = parse_species_names(self.docker_util.repo_root())
        expected_count = _parse_species_count(self.docker_util.repo_root())
        if expected_count and len(names) != expected_count:
            msg = (
                f"Warning: expected {expected_count} species names but parsed {len(names)}."
            )
            print(msg)
            self.messages.append(msg)
            expected_count = len(names)

        for const, info in species.items():
            if const in names:
                info["species_info"]["speciesName"] = names[const]

        # Preserve species names for quick lookup
        for const, info in species.items():
            if const in names:
                info["name"] = names[const]
                for form in info.get("forms", {}).values():
                    form["name"] = names[const]

        required = {"baseHP", "types", "abilities"}
        defaults = {
            "baseHP": 1,
            "types": ["TYPE_NORMAL", "TYPE_NONE"],
            "abilities": ["ABILITY_NONE", "ABILITY_NONE", "ABILITY_NONE"],
        }
        valid = {}
        defaulted = 0
        for name, info in species.items():
            si = info.get("species_info", {})
            missing = [f for f in required if f not in si or si[f] is None]
            if missing:
                for f in missing:
                    val = defaults[f]
                    si[f] = list(val) if isinstance(val, list) else val
                self.messages.append(f"Defaulted {name}: missing {', '.join(missing)}")
                defaulted += 1

            if "evolutions" not in si or si["evolutions"] is None:
                si["evolutions"] = []

            # Ensure types has two entries
            if isinstance(si.get("types"), list):
                while len(si["types"]) < len(defaults["types"]):
                    si["types"].append(defaults["types"][len(si["types"])])

            # Ensure abilities has three entries
            if isinstance(si.get("abilities"), list):
                while len(si["abilities"]) < len(defaults["abilities"]):
                    si["abilities"].append(defaults["abilities"][len(si["abilities"])])

            valid[name] = info

        # Merge Pokédex information so each species has a number and constant
        dex_ext = PokedexDataExtractor(self.project_info, "pokedex.json")
        dex_data = dex_ext.extract_data() or {}
        dex_entries = dex_data.get("national_dex", [])

        for entry in dex_entries:
            sp = entry.get("species")
            if sp in valid:
                valid[sp]["dex_num"] = entry.get("dex_num")
                valid[sp]["dex_constant"] = entry.get("dex_constant")

        missing_from_dex = []
        for name in valid:
            if "dex_num" not in valid[name]:
                missing_from_dex.append(name)
                valid[name]["dex_num"] = None
                valid[name]["dex_constant"] = None
        for sp in missing_from_dex:
            print(f"Warning: {sp} missing from Pokédex list")

        # Merge detailed Pokédex info from pokedex_entries.h
        dex_details = parse_pokedex_entries(self.docker_util)
        for sp, info in dex_details.items():
            if sp in valid:
                valid[sp]["pokedex"] = info

        # Mirror categoryName and description from pokedex into species_info
        # so that parse_to_c_code can write them back to headers and the UI
        # sees them as "owned" values rather than fallback-only values.
        for sp, sp_data in valid.items():
            si = sp_data.get("species_info")
            pdex = sp_data.get("pokedex")
            if si is None or pdex is None:
                continue
            if not si.get("categoryName") and pdex.get("categoryName"):
                si["categoryName"] = pdex["categoryName"]
            if not si.get("description") and pdex.get("descriptionText"):
                si["description"] = pdex["descriptionText"]

        if expected_count and len(valid) != expected_count + 1:
            msg = (
                f"Warning: expected {expected_count + 1} species but parsed {len(valid)}."
            )
            print(msg)
            self.messages.append(msg)
            expected_count = len(valid) - 1

        print(f"Loaded {len(valid)} species [OK] (defaulted {defaulted})")
        return valid

    def extract_data(self) -> dict:
        self.messages = []
        json_path = os.path.join(self.docker_util.repo_root(), "src", "data", self.DATA_FILE)
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")

        root = self.docker_util.repo_root()
        source_headers = [
            os.path.join(root, self.HEADER_FILE),
            os.path.join(root, "src", "data", "pokemon", "evolution.h"),
        ]
        data = _load_json(json_path, source_headers=source_headers)
        if data is not None:
            header = self._parse_header_data()
            fixed_ratios = 0
            fixed_types = 0
            for sp, info in data.items():
                si = info.get("species_info", {})
                header_si = header.get(sp, {}).get("species_info", {})

                cached_ratio = si.get("genderRatio")
                header_ratio = header_si.get("genderRatio")
                if header_ratio is None:
                    header_ratio = self._parse_gender_ratio(sp)
                if header_ratio is not None and header_ratio != cached_ratio:
                    si["genderRatio"] = header_ratio
                    fixed_ratios += 1

                cached_types = si.get("types") or []
                header_types = header_si.get("types")
                if header_types and (
                    cached_types != header_types
                    or any(t == "TYPE_NONE" for t in cached_types)
                ):
                    si["types"] = header_types
                    fixed_types += 1

            if fixed_types and self.rebuild_on_type_mismatch:
                print("Rebuilding species caches due to type mismatches…")
                _write_json(json_path, header, len(header))
                print(f"Loaded {len(header)} species [OK]")
                return header

            if fixed_ratios or fixed_types:
                _write_json(json_path, data, len(data))
                if fixed_ratios:
                    print(f"Fixed {fixed_ratios} gender ratios")
                if fixed_types:
                    print(f"Fixed {fixed_types} type mismatches")
            print(f"Loaded {len(data)} species [OK]")
            return data

        data = self._parse_header_data()
        min_count = len(data) if data else 1

        # Preserve user-edited fields (categoryName, description, speciesName)
        # that live in the JSON but may not exist in species_info.h yet.
        # The header extractor can't read these if parse_to_c_code hasn't
        # inserted them, so we carry them over from the stale JSON cache.
        _PRESERVE_KEYS = ("categoryName", "description", "speciesName")
        try:
            with open(json_path, encoding="utf-8") as _jf:
                stale_json = json.load(_jf)
            if isinstance(stale_json, dict):
                for sp, sp_data in stale_json.items():
                    if sp not in data:
                        continue
                    stale_si = sp_data.get("species_info", {})
                    fresh_si = data[sp].get("species_info", {})
                    for k in _PRESERVE_KEYS:
                        stale_val = stale_si.get(k)
                        fresh_val = fresh_si.get(k)
                        # Only carry over if stale JSON has a value and header doesn't
                        if stale_val and not fresh_val:
                            fresh_si[k] = stale_val
        except Exception:
            pass

        if not _write_json(json_path, data, min_count):
            print("Aborting species load.")
            return data
        for msg in self.messages:
            print(msg)
        return data


class SpeciesGraphicsDataExtractor(PokemonDataExtractor):
    """
    A class used to extract species graphics data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> dict:
        self.messages = []
        json_path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")

        header = os.path.join(
            self.docker_util.repo_root(), "src", "data", "graphics", "pokemon.h"
        )
        data = _load_json(json_path, source_headers=[header])
        if data is not None:
            print(f"Loaded {len(data)} species graphics [OK]")
            return data

        # Fallback: parse graphics header for sprite paths
        if not os.path.isfile(header):
            print("Missing file or empty read for graphics header")
            return {}

        pattern = re.compile(
            r"g[A-Za-z0-9_]+\[\]\s*=\s*INCBIN_U\w+\(\"([^\"]+)\"\)" 
        )
        graphics: dict[str, dict] = {}
        with open(header, encoding="utf-8") as f:
            for ln in f:
                m = pattern.search(ln)
                if not m:
                    continue
                path = m.group(1)
                png = (
                    path.replace(".4bpp.lz", ".png")
                    .replace(".4bpp", ".png")
                    .replace(".1bpp", ".png")
                    .replace(".gbapal.lz", ".pal")
                )
                const = re.search(r"\b(\w+)\[\]", ln)
                if const:
                    graphics[const.group(1)] = {"png": png}

        if not _write_json(json_path, graphics, 1):
            print("Aborting species graphics load.")
            return graphics

        print(f"Loaded {len(graphics)} species graphics [OK]")
        return graphics


class AbilitiesDataExtractor(PokemonDataExtractor):
    """
    A class used to extract abilities data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> dict:
        self.messages = []
        json_path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")

        # Search for a header defining abilities
        header = _find_abilities_header(self.docker_util.repo_root())
        source_headers = [header] if header else []
        data = _load_json(json_path, source_headers=source_headers)
        if data is not None:
            # Enrich with display names + descriptions from text file
            self._enrich_abilities(data)
            print(f"Loaded {len(data)} abilities [OK]")
            return data
        if not header:
            print("Failed to locate abilities header for rebuild")
            return {}

        with open(header, encoding="utf-8") as f:
            lines = f.readlines()
        abilities = {}
        pattern = re.compile(r"^\s*#define\s+(ABILITY_[A-Z0-9_]+)\s+(\d+)")
        for ln in lines:
            m = pattern.match(ln)
            if m:
                const, ident = m.groups()
                abilities[const] = {"name": const[len("ABILITY_"):], "id": int(ident)}

        # Enrich with display names + descriptions from text file
        self._enrich_abilities(abilities)

        if not _write_json(json_path, abilities, 1):
            print("Aborting abilities load.")
            return abilities

        print(f"Loaded {len(abilities)} abilities [OK]")
        return abilities

    def _enrich_abilities(self, abilities: dict) -> None:
        """Parse display names and descriptions from src/data/text/abilities.h."""
        root = self.docker_util.repo_root()
        text_path = os.path.join(root, "src", "data", "text", "abilities.h")
        if not os.path.isfile(text_path):
            return
        try:
            with open(text_path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return

        # Parse display names:  [ABILITY_XXX] = _("Display Name"),
        name_rx = re.compile(
            r'\[(ABILITY_[A-Z0-9_]+)\]\s*=\s*_\("([^"]*)"\)',
        )
        # Parse descriptions:  static const u8 sXxxDescription[] = _("...");
        desc_var_rx = re.compile(
            r'static\s+const\s+u8\s+(s\w+Description)\[\]\s*=\s*_\("([^"]*)"\)\s*;'
        )
        # Parse pointer table:  [ABILITY_XXX] = sXxxDescription,
        desc_ptr_rx = re.compile(
            r'\[(ABILITY_[A-Z0-9_]+)\]\s*=\s*(s\w+Description)\s*,'
        )

        # Build variable→text mapping from description strings
        desc_vars: dict[str, str] = {}
        for m in desc_var_rx.finditer(content):
            desc_vars[m.group(1)] = m.group(2)

        # Map ABILITY_* constant → description via pointer table
        desc_map: dict[str, str] = {}
        for m in desc_ptr_rx.finditer(content):
            const, var = m.group(1), m.group(2)
            if var in desc_vars:
                desc_map[const] = desc_vars[var]

        # Find where the gAbilityNames array starts so we only match
        # names inside it (not descriptions above)
        names_start = content.find("gAbilityNames")

        for m in name_rx.finditer(content, names_start if names_start >= 0 else 0):
            const, display_name = m.group(1), m.group(2)
            if const in abilities:
                abilities[const]["display_name"] = display_name

        for const, desc in desc_map.items():
            if const in abilities:
                abilities[const]["description"] = desc


class ItemsDataExtractor(PokemonDataExtractor):
    """Extract item data for FireRed projects."""

    HEADER_CANDIDATES = [
        os.path.join("src", "data", "items.h"),
        os.path.join("src", "data", "graphics", "items.h"),
    ]

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> dict | None:
        """Load items from JSON or rebuild them from a header file."""

        self.messages = []
        self.items_order = []

        json_path = self.get_data_file_path()
        root = self.docker_util.repo_root()
        source_headers = [
            os.path.join(root, c) for c in self._candidate_headers()
        ]
        data = _load_json(json_path, source_headers=source_headers)
        if isinstance(data, dict) and data:
            print(f"Loaded {len(data)} items [OK]")
            self.items_order = list(data.keys())
            return data

        items, order, header_used = self._parse_items_header()
        if not items:
            header_rel = header_used or self.HEADER_CANDIDATES[0]
            header_path = os.path.join(self.docker_util.repo_root(), header_rel)
            print(
                f"Warning: {os.path.abspath(header_path)} missing or unreadable; no item entries found; no item data loaded"
            )
            return None

        payload = dict(items)
        if _write_json(json_path, payload, min_entries=len(payload)):
            origin = os.path.normpath(header_used or self.HEADER_CANDIDATES[0])
            print(f"Rebuilt {len(payload)} items from {origin}")
        self.items_order = order
        return payload

    def _candidate_headers(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        file_entry = self.FILES.get("ITEMS_H") if isinstance(getattr(self, "FILES", None), dict) else None
        if isinstance(file_entry, dict):
            original = file_entry.get("original")
            if original:
                norm = os.path.normpath(original)
                ordered.append(norm)
                seen.add(norm)
        for rel in self.HEADER_CANDIDATES:
            norm = os.path.normpath(rel)
            if norm not in seen:
                ordered.append(norm)
                seen.add(norm)
        return ordered

    @staticmethod
    def _extract_english(name_expr: str) -> str:
        name_expr = name_expr.strip()
        if name_expr.startswith('_("') and name_expr.endswith('")'):
            return name_expr[3:-2]
        if name_expr.startswith('"') and name_expr.endswith('"'):
            return name_expr[1:-1]
        return name_expr

    def _parse_items_header(
        self,
    ) -> tuple[OrderedDict[str, dict], list[str], str | None]:
        for rel in self._candidate_headers():
            lines = _read_header(self.docker_util, rel)
            if not lines:
                continue

            text = "\n".join(lines)
            items = OrderedDict()
            order: list[str] = []
            start_pattern = re.compile(r"\[(ITEM_[A-Z0-9_]+)\]\s*=\s*\{", re.M)

            pos = 0
            length = len(text)
            while True:
                match = start_pattern.search(text, pos)
                if not match:
                    break
                const = match.group(1)
                body_start = match.end()
                brace_depth = 1
                idx = body_start
                in_string = False
                in_char = False
                in_line_comment = False
                in_block_comment = False

                while idx < length and brace_depth > 0:
                    ch = text[idx]
                    if in_line_comment:
                        if ch == "\n":
                            in_line_comment = False
                        idx += 1
                        continue
                    if in_block_comment:
                        if ch == "*" and idx + 1 < length and text[idx + 1] == "/":
                            in_block_comment = False
                            idx += 2
                        else:
                            idx += 1
                        continue
                    if in_string:
                        if ch == "\\" and idx + 1 < length:
                            idx += 2
                            continue
                        if ch == '"':
                            in_string = False
                        idx += 1
                        continue
                    if in_char:
                        if ch == "\\" and idx + 1 < length:
                            idx += 2
                            continue
                        if ch == "'":
                            in_char = False
                        idx += 1
                        continue
                    if ch == "/" and idx + 1 < length:
                        nxt = text[idx + 1]
                        if nxt == "/":
                            in_line_comment = True
                            idx += 2
                            continue
                        if nxt == "*":
                            in_block_comment = True
                            idx += 2
                            continue
                    if ch == '"':
                        in_string = True
                        idx += 1
                        continue
                    if ch == "'":
                        in_char = True
                        idx += 1
                        continue
                    if ch == "{":
                        brace_depth += 1
                    elif ch == "}":
                        brace_depth -= 1
                    idx += 1

                if brace_depth != 0:
                    pos = match.end()
                    continue

                block = text[body_start: idx - 1]
                pos = idx

                fields: dict[str, str] = {}
                current_key: str | None = None
                fragments: list[str] = []

                for raw in block.splitlines():
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    if stripped.startswith('.') and '=' in stripped:
                        if current_key is not None:
                            value = ''.join(fragments).strip().rstrip(',') if fragments else ''
                            fields[current_key] = value
                            fragments.clear()
                        key, rest = stripped.split('=', 1)
                        current_key = key.strip().lstrip('.')
                        fragments.append(rest.strip())
                    elif current_key is not None:
                        fragments.append(' ' + stripped)

                if current_key is not None:
                    value = ''.join(fragments).strip().rstrip(',') if fragments else ''
                    fields[current_key] = value

                items[const] = fields if fields else {}
                order.append(const)

            if items:
                self.header_used = rel
                return items, order, rel

        return OrderedDict(), [], None
      
      
class TrainersDataExtractor(PokemonDataExtractor):
    """Extract trainer data from FireRed’s src/data/trainers.h."""

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    HEADER_FILE = os.path.join("src", "data", "trainers.h")

    def extract_data(self) -> dict:
        self.messages = []
        # 1. try cached JSON
        root = self.docker_util.repo_root()
        json_path = os.path.join(root, "src", "data", self.DATA_FILE)
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")
        header_abs = os.path.join(root, self.HEADER_FILE)
        data = _load_json(json_path, source_headers=[header_abs])
        if data is not None:
            return data

        # 2. parse the header
        lines = _read_header(self.docker_util, self.HEADER_FILE)
        trainers: dict[str, dict] = {}
        current: str | None = None

        i = 0
        while i < len(lines):
            ln = _clean_line(lines[i])
            i += 1
            if not ln:
                continue

            # start of a trainer definition
            m = re.match(r"\[(TRAINER_[A-Z0-9_]+)\]\s*=\s*{", ln)
            if m:
                current = m.group(1)
                trainers[current] = {}
                continue

            if current is None:              # not inside a struct
                continue

            # end of struct
            if ln.startswith("}"):
                current = None
                awaiting_brace = False
                continue

            # key/value (may span multiple lines)
            if ln.startswith("."):
                entry = ln
                while not entry.rstrip().endswith(",") and i < len(lines):
                    extra = _clean_line(lines[i])
                    i += 1
                    entry += " " + extra

                kv = re.match(r"\.(\w+)\s*=\s*(.*),", entry)
                if kv:
                    k, v = self.parse_value_by_key(kv.group(1), kv.group(2).strip())
                    trainers[current][k] = v

        # 3. cache and return
        if not _write_json(json_path, trainers, 1):
            print("Aborting trainers load.")
            return trainers
        for msg in self.messages:
            print(msg)
        print(f"Loaded {len(trainers)} trainers [OK]")
        return trainers


class PokemonConstantsExtractor(PokemonDataExtractor):
    """
    A class used to extract game constants from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    @staticmethod
    def __parse_data(name: str, value, description: str) -> dict:
        return {
            "name": name,
            "value": value,
            "description": description
        }

    def should_extract(self) -> bool:
        """Return ``True`` when ``constants.json`` should be rebuilt."""
        if super().should_extract():
            return True

        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f) or {}
            missing_keys = any(
                key not in data
                for key in ("types", "evolution_types", "egg_groups", "growth_rates")
            )
            return missing_keys
        except (FileNotFoundError, json.JSONDecodeError):
            return True

    def extract_data(self) -> dict:
        self.messages = []
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        header = os.path.join(
            self.docker_util.repo_root(), "include", "constants", "pokemon.h"
        )
        data = _load_json(path, source_headers=[header]) or {}
        if data.get("types") and data.get("evolution_types"):
            print(f"Loaded {len(data['types'])} types [OK]")
            print(
                f"Loaded {len(data['evolution_types'])} evolution types [OK]"
            )
            return data
        if not os.path.isfile(header):
            print(f"Missing header: {os.path.abspath(header)}")
            return data

        print(f"Reading {os.path.abspath(header)}")
        type_pattern = re.compile(r"^#define\s+(TYPE_[A-Z0-9_]+)\s+(\d+)")
        evo_pattern = re.compile(r"^#define\s+(EVO_[A-Z0-9_]+)\s+(\d+)")
        egg_pattern = re.compile(r"^#define\s+(EGG_GROUP_[A-Z0-9_]+)\s+(\d+)")
        growth_pattern = re.compile(r"^#define\s+(GROWTH_[A-Z0-9_]+)\s+(\d+)")
        types: dict[str, dict] = {}
        evolutions: dict[str, dict] = {}
        egg_groups: dict[str, dict] = {}
        growth_rates: dict[str, dict] = {}
        with open(header, encoding="utf-8") as f:
            for ln in f:
                ln = _clean_line(ln)
                m = type_pattern.match(ln)
                if m:
                    const, ident = m.groups()
                    name = const[len("TYPE_"):].title().replace("_", " ")
                    types[const] = {"name": name, "value": int(ident)}
                m = evo_pattern.match(ln)
                if m:
                    const, ident = m.groups()
                    name = const[len("EVO_"):].title().replace("_", " ")
                    evolutions[const] = {"name": name, "value": int(ident)}
                m = egg_pattern.match(ln)
                if m:
                    const, ident = m.groups()
                    name = const[len("EGG_GROUP_"):].title().replace("_", " ")
                    egg_groups[const] = {"name": name, "value": int(ident)}
                m = growth_pattern.match(ln)
                if m:
                    const, ident = m.groups()
                    name = const[len("GROWTH_"):].title().replace("_", " ")
                    growth_rates[const] = {"name": name, "value": int(ident)}
        if types or evolutions or egg_groups or growth_rates:
            if types:
                data["types"] = types
            if evolutions:
                data["evolution_types"] = evolutions
            if egg_groups:
                data["egg_groups"] = egg_groups
            if growth_rates:
                data["growth_rates"] = growth_rates
            _write_json(path, data, 1)
            if types:
                print(f"Loaded {len(types)} types [OK]")
            if evolutions:
                print(f"Loaded {len(evolutions)} evolution types [OK]")
            if egg_groups:
                print(f"Loaded {len(egg_groups)} egg groups [OK]")
            if growth_rates:
                print(f"Loaded {len(growth_rates)} growth rates [OK]")
        return data


class StartersDataExtractor(PokemonDataExtractor):
    """
    A class used to extract starter Pokémon data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    HEADER_FILE = os.path.join("src", "field_specials.c")

    def extract_data(self) -> list | None:
        self.messages = []
        json_path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")
        root = self.docker_util.repo_root()
        source_headers = [
            os.path.join(root, self.HEADER_FILE),
            os.path.join(root, "src", "battle_setup.c"),
        ]
        data = _load_json(json_path, source_headers=source_headers)
        if data is not None:
            print(f"Loaded {len(data)} starters [OK]")
            return data

        lines = _read_header(self.docker_util, self.HEADER_FILE)
        starters: list[dict] = []

        file_text = "\n".join(_clean_line(ln) for ln in lines)
        pattern = re.compile(
            r"(?:s?Starter(?:Mons|Species))\s*\[\s*\]\s*=\s*{\s*([^}]*)\s*}\s*;\s*(?://.*)?",
            re.S,
        )
        match = pattern.search(file_text)
        species_list: list[str] = []
        if match:
            species_text = match.group(1)
            species_list = re.findall(r"SPECIES_[A-Z0-9_]+", species_text)

        for sp in species_list:
            starters.append(
                {
                    "species": sp,
                    "level": 5,
                    "item": "ITEM_NONE",
                    "custom_move": "MOVE_NONE",
                    "ability_num": -1,
                }
            )

        battle_lines = _read_header(self.docker_util, "src", "battle_setup.c")
        battle_text = "\n".join(_clean_line(ln) for ln in battle_lines)
        func = re.search(r"CB2_GiveStarter\(void\)(.*?)}", battle_text, re.S)
        if func:
            body = func.group(1)
            cases = re.findall(r"case\s+\d+\s*:\s*(.*?)break\s*;", body, re.S)
            for idx, block in enumerate(cases):
                if idx >= len(starters):
                    break
                m = re.search(
                    r"ScriptGiveMon\s*\(\s*starterMon\s*,\s*(\d+)\s*,\s*([A-Za-z0-9_]+)",
                    block,
                )
                if m:
                    starters[idx]["level"] = int(m.group(1))
                    starters[idx]["item"] = m.group(2)
                m2 = re.search(r"abilityNum\s*=\s*([A-Za-z0-9_]+)", block)
                if m2:
                    try:
                        starters[idx]["ability_num"] = int(m2.group(1))
                    except ValueError:
                        starters[idx]["ability_num"] = m2.group(1)

                m3 = re.search(
                    r"GiveMoveToMon\s*\([^,]*,\s*([A-Za-z0-9_]+)\s*\)",
                    block,
                )
                if m3:
                    starters[idx]["custom_move"] = m3.group(1)

        if len(starters) < 3:
            msg = (
                f"Warning: expected at least 3 starters but parsed {len(starters)}."
            )
            print(msg)
            self.messages.append(msg)

        if not _write_json(json_path, starters, 1):
            print("Aborting starters load.")
            return starters
        print(f"Loaded {len(starters)} starters [OK]")
        return starters


class MovesDataExtractor(PokemonDataExtractor):
    """
    A class used to extract moves data from the source files.
    """

    def __init__(self, project_info: dict, data_file: str = None, files: dict = None):
        super().__init__(project_info, data_file, files)
        self.moves_data = {
            "moves": {},
            "move_descriptions": {},
            "constants": {}
        }

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        if key in {"power", "accuracy", "pp", "secondaryEffectChance", "priority", "id"}:
            try:
                value = int(self.__parse_macro(value))
            except Exception:
                pass
        return key, value

    def __parse_macro(self, val):
        if type(val) is int:
            return val
        val = re.sub(r'\((.*)\)', r'\1', val)
        values = val.split(" ")
        if len(values) == 3:
            if values[1] == "+":
                value1 = values[0]
                value2 = values[2]
                try:
                    val = int(self.__parse_macro(value1)) + int(self.__parse_macro(value2))
                except ValueError:
                    val = -1
        elif len(values) == 1:
            try:
                while val in self.moves_data["constants"] or val in self.moves_data["moves"]:
                    if val in self.moves_data["constants"]:
                        val = self.moves_data["constants"][val]
                    elif val in self.moves_data["moves"]:
                        val = self.moves_data["moves"][val]["id"]
                val = int(val)
            except ValueError:
                val = self.__parse_macro(val)
        return val

    HEADER_MOVES = os.path.join("src", "data", "battle_moves.h")
    HEADER_DESCS = os.path.join("src", "move_descriptions.c")
    HEADER_NAMES = os.path.join("src", "data", "text", "move_names.h")

    def extract_data(self) -> dict | None:
        self.messages = []
        root = self.docker_util.repo_root()
        json_path = os.path.join(root, "src", "data", self.DATA_FILE)
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")
        source_headers = [
            os.path.join(root, self.HEADER_MOVES),
            os.path.join(root, self.HEADER_DESCS),
            os.path.join(root, self.HEADER_NAMES),
        ]
        data = _load_json(json_path, source_headers=source_headers)
        if data is not None:
            # Normalize numeric fields to ints when loaded from JSON caches
            try:
                moves = data.get("moves") or {}
                for info in moves.values():
                    if not isinstance(info, dict):
                        continue
                    for key in ("power", "accuracy", "pp", "secondaryEffectChance", "priority", "id"):
                        val = info.get(key)
                        if isinstance(val, str):
                            try:
                                info[key] = int(val)
                            except ValueError:
                                pass
            except Exception:
                pass
            species_moves = data.get("species_moves") if isinstance(data, dict) else None
            rebuild = False
            if not isinstance(species_moves, dict) or not species_moves:
                rebuild = True
            else:
                for entries in species_moves.values():
                    if not isinstance(entries, list):
                        rebuild = True
                        break
            if not rebuild:
                return data
            print("Rebuilding species move caches from headers (missing or invalid species_moves)")

        moves = {"moves": {}, "move_descriptions": {}, "constants": {}}
        lines = _read_header(self.docker_util, self.HEADER_MOVES)
        current = None
        awaiting_brace = False
        i = 0
        skipped = 0
        while i < len(lines):
            ln = _clean_line(lines[i])
            i += 1
            if not ln:
                continue

            # start of a move struct
            if current is None:
                m = re.match(r"\[(MOVE_[A-Z0-9_]+)\]\s*=", ln)
                if m:
                    current = m.group(1)
                    moves["moves"][current] = {"id": len(moves["moves"]) + 1}
                    awaiting_brace = "{" not in ln
                continue

            if awaiting_brace:
                if ln.startswith("{"):
                    awaiting_brace = False
                continue

            # end of struct
            if ln.startswith("}"):
                current = None
                awaiting_brace = False
                continue

            # key / value line – may span multiple lines
            if ln.startswith("."):
                entry = ln
                while not entry.rstrip().endswith(",") and i < len(lines):
                    extra = _clean_line(lines[i])
                    i += 1
                    entry += " " + extra

                kv = re.match(r"\.(\w+)\s*=\s*(.*),", entry)
                if kv:
                    k, v = self.parse_value_by_key(kv.group(1), kv.group(2).strip())
                    moves["moves"][current][k] = v

        desc_lines = _read_header(self.docker_util, self.HEADER_DESCS)
        pattern = re.compile(r"\[(MOVE_[A-Z0-9_]+)\]\s*=\s*_(?:\(\"(.*)\"\))?")
        for ln in desc_lines:
            d = pattern.search(ln)
            if d:
                moves["move_descriptions"][d.group(1)] = (d.group(2) or "").replace("\\n", "\n")

        # Extract in-game move names from move_names.h
        name_lines = _read_header(self.docker_util, self.HEADER_NAMES)
        name_pat = re.compile(r"\[(MOVE_[A-Z0-9_]+)\]\s*=\s*_\(\"([^\"]*)\"\)")
        for ln in name_lines:
            nm = name_pat.search(ln)
            if nm and nm.group(1) in moves["moves"]:
                moves["moves"][nm.group(1)]["name"] = nm.group(2)

        valid_moves = {}
        for name, info in moves["moves"].items():
            if len(info) > 1:
                valid_moves[name] = info
            else:
                skipped += 1
                self.messages.append(f"Skipped {name}: invalid move struct")
        moves["moves"] = valid_moves

        if len(valid_moves) < 1:
            msg = (
                f"Warning: expected at least 1 move but parsed {len(valid_moves)}."
            )
            print(msg)
            self.messages.append(msg)

        # Build species learnsets (level-up, TM/HM, tutor, egg) when headers exist
        species_moves: dict[str, list] = {}
        def _ensure(sp: str):
            species_moves.setdefault(sp, [])
        try:
            # Level-up
            ptr_lines = _read_header(self.docker_util, "src", "data", "pokemon", "level_up_learnset_pointers.h")
            lvl_lines = _read_header(self.docker_util, "src", "data", "pokemon", "level_up_learnsets.h")
            if ptr_lines and lvl_lines:
                lvl_text = "\n".join(lvl_lines)
                ptr_pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(s\w+LevelUpLearnset)")
                arr_pat = re.compile(r"static\s+const\s+u16\s+(s\w+LevelUpLearnset)\[\]\s*=\s*\{(.*?)\};", re.S)
                move_pat = re.compile(
                    r"LEVEL_UP_MOVE\(\s*(\d+)\s*,\s*(MOVE_[A-Z0-9_]+)\s*\)"
                )
                arrays = {m.group(1): m.group(2) for m in arr_pat.finditer(lvl_text)}
                for m in ptr_pat.finditer("\n".join(ptr_lines)):
                    sp = m.group(1)
                    sym = m.group(2)
                    body = arrays.get(sym)
                    if not body:
                        continue
                    for mv in move_pat.finditer(body):
                        try:
                            lvl = int(mv.group(1))
                        except Exception:
                            continue
                        move_const = mv.group(2)
                        _ensure(sp)
                        species_moves[sp].append({"move": move_const, "method": "LEVEL", "value": lvl})
        except Exception:
            pass
        try:
            # TM/HM
            tm_lines = _read_header(self.docker_util, "src", "data", "pokemon", "tmhm_learnsets.h")
            if tm_lines:
                sp_pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*TMHM_LEARNSET\((.*?)\)\s*\,", re.S)
                tok_pat = re.compile(r"TMHM\((TM\d+_[A-Z0-9_]+|HM\d+_[A-Z0-9_]+)\)")
                blob = "\n".join(tm_lines)
                for sp_m in sp_pat.finditer(blob):
                    sp = sp_m.group(1)
                    expr = sp_m.group(2)
                    for tok in tok_pat.findall(expr):
                        kind, rest = tok.split('_', 1)
                        move_const = f"MOVE_{rest}"
                        method = "TM" if kind.startswith("TM") else "HM"
                        _ensure(sp)
                        species_moves[sp].append({"move": move_const, "method": method, "value": kind})
        except Exception:
            pass
        try:
            # Tutor
            tutor_lines = _read_header(self.docker_util, "src", "data", "pokemon", "tutor_learnsets.h")
            if tutor_lines:
                sp_pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*((?:.|\n)*?)\,", re.S)
                mv_pat = re.compile(r"TUTOR\((MOVE_[A-Z0-9_]+)\)")
                blob = "\n".join(tutor_lines)
                for sp_m in sp_pat.finditer(blob):
                    sp = sp_m.group(1)
                    expr = sp_m.group(2)
                    for mv in mv_pat.findall(expr):
                        _ensure(sp)
                        species_moves[sp].append({"move": mv, "method": "TUTOR", "value": ""})
        except Exception:
            pass
        try:
            # Egg moves
            egg_lines = _read_header(self.docker_util, "src", "data", "pokemon", "egg_moves.h")
            if egg_lines:
                eg_pat = re.compile(r"egg_moves\(([^\)]+)\)")
                blob = "\n".join(egg_lines)
                for m in eg_pat.finditer(blob):
                    parts = [p.strip() for p in m.group(1).split(',') if p.strip()]
                    if not parts:
                        continue
                    sp = f"SPECIES_{parts[0]}"
                    for mv in parts[1:]:
                        if mv.startswith("MOVE_"):
                            _ensure(sp)
                            species_moves[sp].append({"move": mv, "method": "EGG", "value": ""})
        except Exception:
            pass
        if species_moves:
            moves["species_moves"] = {sp: _order_learnset_entries(entries) for sp, entries in species_moves.items()}

        if not _write_json(json_path, moves, 1):
            print("Aborting moves load.")
            return moves
        for msg in self.messages:
            print(msg)
        print(f"Loaded {len(valid_moves)} moves [OK] (skipped {skipped})")
        return moves


class PokedexDataExtractor(PokemonDataExtractor):
    """
    A class used to extract pokedex data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    HEADER_FILE = os.path.join("include", "constants", "pokedex.h")

    def extract_data(self) -> dict | None:
        self.messages = []
        json_path = os.path.join(self.docker_util.repo_root(), "src", "data", self.DATA_FILE)
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")
        header_abs = os.path.join(self.docker_util.repo_root(), self.HEADER_FILE)
        data = _load_json(json_path, source_headers=[header_abs])
        if data is not None:
            print(f"Loaded {len(data['national_dex'])} pokedex entries [OK]")
            return data

        lines = _read_header(self.docker_util, self.HEADER_FILE)
        if not lines:
            print("Missing file or empty read for pokedex header")

        dex_entries = []
        counting = False
        dex_num = 0
        skipped = 0
        for ln in lines:
            clean = _clean_line(ln)
            if not counting and re.search(r"\benum\b", clean):
                counting = True
                continue
            if counting:
                if "};" in clean:
                    break
                m = re.search(r"(NATIONAL_DEX_[A-Z0-9_]+)", clean)
                if not m:
                    if clean:
                        skipped += 1
                        self.messages.append(
                            f"Skipped dex entry {clean}: invalid format"
                        )
                    continue
                const = m.group(1)
                if const == "NATIONAL_DEX_NONE":
                    continue
                dex_num += 1
                species = "SPECIES_" + const[len("NATIONAL_DEX_"):]
                dex_entries.append(
                    {
                        "dex_num": dex_num,
                        "species": species,
                        "dex_constant": const,
                    }
                )

        valid = [d for d in dex_entries if d.get("dex_num") is not None]

        # Merge detailed pokedex information for each species
        details = parse_pokedex_entries(self.docker_util)
        text_strings = parse_pokedex_texts(self.docker_util)
        for entry in valid:
            info = details.get(entry["species"], {})
            entry.update(info)
            const = entry.get("description")
            if const and "descriptionText" not in entry:
                text = text_strings.get(const)
                if text is not None:
                    entry["descriptionText"] = text

        # Build regional dex: use Kanto Dex count if defined, else default to 151
        kanto_count = 151
        try:
            for ln in lines:
                clean = _clean_line(ln)
                m = re.match(r"#define\s+KANTO_DEX_COUNT\s+(NATIONAL_DEX_[A-Z0-9_]+)", clean)
                if m:
                    cutoff = m.group(1)
                    # Find index of cutoff in valid list
                    for i, entry in enumerate(valid, start=1):
                        if entry.get("dex_constant") == cutoff:
                            kanto_count = i
                            break
                    break
        except Exception:
            pass
        regional = [{"dex_constant": entry.get("dex_constant")} for entry in valid[:kanto_count]]

        data = {"national_dex": valid, "regional_dex": regional}

        if len(valid) < 1:
            print(
                f"Error: expected at least 1 pokedex entry but parsed {len(valid)}. Aborting pokedex load."
            )
            return data
        if not _write_json(json_path, data, 1):
            print("Aborting pokedex load.")
            return data
        for msg in self.messages:
            print(msg)
        print(f"Loaded {len(valid)} pokedex entries [OK] (skipped {skipped})")
        return data


class PokemonEvolutionsExtractor(PokemonDataExtractor):
    """Extract evolution data from ``evolution.h``."""

    HEADER_FILE = os.path.join("src", "data", "pokemon", "evolution.h")

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        # Reuse SpeciesDataExtractor's evolution parser
        helper = SpeciesDataExtractor(self.project_info)
        return helper.parse_value_by_key(key, value)

    def extract_data(self) -> dict | None:
        self.messages = []
        json_path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")
        header_abs = os.path.join(self.docker_util.repo_root(), self.HEADER_FILE)
        data = _load_json(json_path, source_headers=[header_abs])
        if data is not None:
            print(f"Loaded {len(data)} evolution lists [OK]")
            return data

        lines = _read_header(self.docker_util, self.HEADER_FILE)
        if not lines:
            print("Missing file or empty read for evolution header")

        evolutions: dict[str, list] = {}
        i = 0
        while i < len(lines):
            ln = _clean_line(lines[i])
            i += 1
            if not ln or ln.startswith("const struct Evolution"):
                continue

            m = re.match(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(.*)", ln)
            if not m:
                continue
            const = m.group(1)
            rest = m.group(2)
            while rest.count("{") > rest.count("}") and i < len(lines):
                rest += " " + _clean_line(lines[i])
                i += 1
            rest = rest.rstrip(",").strip()
            if rest and rest != "{}":
                value = rest.strip("{}")
                _, evos = self.parse_value_by_key("evolutions", value)
            else:
                evos = []
            evolutions[const] = evos

        if not _write_json(json_path, evolutions, 1):
            print("Aborting evolutions load.")
            return evolutions
        print(f"Loaded {len(evolutions)} evolution lists [OK]")
        return evolutions
