import os
from abc import ABC, abstractmethod

from local_env import LocalUtil


def _safe_read_lines(project_info, path):
    """Safely read all lines from a source file.

    If the file does not exist, an error is printed and ``None`` is returned.
    The import of :class:`ReadSourceFile` is delayed to avoid circular
    dependencies with :mod:`plugin_abstract.pokemon_data`.
    """

    from core.pokemon_data_base import ReadSourceFile

    try:
        with ReadSourceFile(project_info, path) as file:
            return file.readlines()
    except FileNotFoundError:
        print(f"Warning: failed to open {path}. File missing")
        return None


class PokemonDataExtractor(ABC):
    """
    Abstract base class for extracting Pokémon data from game files.

    Attributes:
        DATA_FILE (str): The name of the exported JSON file.
        FILES (dict): A dictionary of source files used for extracting data.
        project_info (dict): The project information dictionary.
        project_dir (str): The project directory.
    """

    DATA_FILE: str = None
    # ``FILES`` is initialised per-instance in ``__init__``

    def __init__(self, project_info: dict, data_file: str = None, files: dict = None):
        """
        Initializes a new instance of the PokemonDataExtractor class.
        
        :param project_info: A dictionary containing project information.
        :param data_file: The name of the exported JSON file.
        :param files: A dictionary of files used for extracting data.
        """
        self.project_info = project_info
        self.project_dir = project_info["dir"]
        self.DATA_FILE = data_file
        self.FILES = dict(files) if files else {}
        self.docker_util = LocalUtil(self.project_info)
        # Collect informational messages during extraction
        self.messages: list[str] = []

    def reset_cache(self) -> None:
        """Clear any in-memory caches so the next extract_data() re-reads from disk.

        The base implementation is a no-op.  Subclasses that cache file content
        (e.g. header lines) must override this to nullify those attributes.
        """

    def get_data_file_path(self) -> str:
        """
        Gets the path of the data file.

        :returns: The path of the data file.
        """
        return os.path.join(self.docker_util.repo_root(), "src", "data", self.DATA_FILE)

    def check_json_newer_than_files(self) -> bool:
        """
        Checks if the JSON file is newer than the files it was generated from.

        If any tracked file's modification time cannot be determined,
        ``False`` is returned so extraction proceeds.

        :returns: ``True`` if the JSON file is newer than the files it was generated
            from, ``False`` otherwise.
        """
        json_file = self.get_data_file_path()
        if not os.path.isfile(json_file):
            return False
        json_file_mod_time = os.path.getmtime(json_file)
        for file in self.FILES:
            file_mod_time = self.docker_util.getmtime(
                f"{self.FILES[file]['original']}"
            )
            if file_mod_time is None:
                return False
            if file_mod_time > json_file_mod_time:
                return False
        return True

    def should_extract(self) -> bool:
        """
        Checks if the data can be extracted.

        :returns: True if the data can be extracted, False otherwise.
        """
        return not self.check_json_newer_than_files()

    @abstractmethod
    def extract_data(self) -> dict | None:
        """
        Extracts data from the decompiled Pokémon game files and returns relevant information in a dictionary.
        
        :returns: A dictionary containing Pokémon data, or ``None`` on failure.
        """
        pass

    @abstractmethod
    def parse_value_by_key(self, key: str, value: str) -> tuple:
        """
        Takes a value and parses it based on the key. This method should be overridden in subclasses.

        :param key: The key of the data.
        :param value: The value to parse according to the key.

        :returns: A tuple containing the key and parsed value
        """
        return key, value
