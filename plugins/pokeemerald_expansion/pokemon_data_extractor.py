import os
import re
import json

# Updated for the FireRed repository layout.
# Paths now reference files directly under include/ and src/.

from plugin_abstract.pokemon_data_extractor import PokemonDataExtractor


def _find_abilities_header(root: str) -> str | None:
    """Return the path to the first header file defining abilities."""

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


def _clean_line(line: str) -> str:
    """Strip inline comments and whitespace."""
    line = re.sub(r"/\*.*?\*/", "", line)
    line = line.split("//")[0]
    return line.strip()


def _load_json(path: str) -> dict | list | None:
    """Return JSON data if file exists and is valid."""
    abs_path = os.path.abspath(path)
    print(f"Reading {abs_path}")
    try:
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
    and ``False`` is returned so callers can abort loading when parsing produced
    no results.
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


class SpeciesDataExtractor(PokemonDataExtractor):
    """
    Extracts Pokémon species data using the FireRed layout.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        def parse_types(val):
            return [t.strip() for t in val.strip("{}").split(",")]

        def parse_gender_ratio(val):
            if isinstance(val, int):
                return val
            elif "min" in val:
                match = re.match(r'min\(254, \(\((.*) \* 255\) \/ 100\)\)', val)
                return int(round((float(match.group(1)) * 255) / 100))
            elif val == "MON_MALE":
                return 0
            elif val == "MON_FEMALE":
                return 254
            elif val == "MON_GENDERLESS":
                return 255

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

    def extract_data(self) -> dict | None:
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(path):
            print(f"Missing file: {os.path.abspath(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            return None

        if not data:
            print(
                f"Error: {os.path.abspath(path)} contained no item entries; aborting items load"
            )
            return None

        return data


class SpeciesGraphicsDataExtractor(PokemonDataExtractor):
    """
    A class used to extract species graphics data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> dict | None:
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(path):
            print(f"Missing file: {os.path.abspath(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            return None

        if not data:
            print(
                f"Error: {os.path.abspath(path)} contained no item entries; aborting items load"
            )
            return None

        return data


class AbilitiesDataExtractor(PokemonDataExtractor):
    """
    A class used to extract abilities data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> dict:
        json_path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(json_path):
            print(f"Missing file: {os.path.abspath(json_path)}")

        data = _load_json(json_path)
        if data is not None:
            print(f"Loaded {len(data)} abilities [OK]")
            return data

        header = _find_abilities_header(self.docker_util.repo_root())
        if not header:
            print("Failed to locate abilities header for rebuild")
            return {}

        abilities = {}
        pattern = re.compile(r"^\s*#define\s+(ABILITY_[A-Z0-9_]+)\s+(\d+)")
        with open(header, encoding="utf-8") as f:
            for ln in f:
                m = pattern.match(_clean_line(ln))
                if m:
                    const, ident = m.groups()
                    abilities[const] = {
                        "name": const[len("ABILITY_"):],
                        "id": int(ident),
                    }

        if not _write_json(json_path, abilities, 1):
            print("Aborting abilities load.")
            return abilities

        print(f"Loaded {len(abilities)} abilities [OK]")
        return abilities


class ItemsDataExtractor(PokemonDataExtractor):
    """
    A class used to extract items data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> dict | None:
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(path):
            print(f"Missing file: {os.path.abspath(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            return None

        if not data:
            print(
                f"Error: {os.path.abspath(path)} contained no item entries; aborting items load"
            )
            return None

        return data


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

    def extract_data(self) -> dict:
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(path):
            print(f"Missing file: {os.path.abspath(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            return {}


class StartersDataExtractor(PokemonDataExtractor):
    """
    A class used to extract starter Pokémon data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> list | None:
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(path):
            print(f"Missing file: {os.path.abspath(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            return []


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

    def extract_data(self) -> dict | None:
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(path):
            print(f"Missing file: {os.path.abspath(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            return {}


class PokedexDataExtractor(PokemonDataExtractor):
    """
    A class used to extract pokedex data from the source files.
    """

    def parse_value_by_key(self, key: str, value: str) -> tuple:
        return key, value

    def extract_data(self) -> dict | None:
        path = os.path.join(
            self.docker_util.repo_root(), "src", "data", self.DATA_FILE
        )
        if not os.path.isfile(path):
            print(f"Missing file: {os.path.abspath(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            return {}
