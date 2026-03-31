from typing_extensions import override
from plugin_abstract.plugin_info import PorySuitePlugin
from plugins.pokefirered.pokemon_data import PokemonDataManager


class FireRedPlugin(PorySuitePlugin):
    """Plugin for the official FireRed base."""

    @staticmethod
    @override
    def create_data_manager(project_info: dict, logger=None) -> PokemonDataManager:
        """Create the plugin's PokemonDataManager."""
        return PokemonDataManager(project_info, logger=logger)
