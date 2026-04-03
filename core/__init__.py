"""
core — PorySuite data layer for pokefirered projects.

Replaces the old plugin_abstract + plugins/pokefirered system.
Only pokefirered is supported; the plugin discovery mechanism is gone.
"""

from core.pokemon_data import PokemonDataManager


# Plugin metadata (previously loaded from plugin_info.json)
PLUGIN_IDENTIFIER = "com.porysuite.firered"
PLUGIN_VERSION = "0.1.4"
PROJECT_BASE_REPO = "https://github.com/pret/pokefirered.git"
PROJECT_BASE_BRANCH = "master"


def create_data_manager(project_info: dict, logger=None) -> PokemonDataManager:
    """Create the FireRed PokemonDataManager.

    Direct replacement for the old plugin.create_data_manager() call.
    """
    return PokemonDataManager(project_info, logger=logger)


def plugin_info() -> dict:
    """Return plugin metadata as a dict (same format the old pluginmanager used)."""
    return {
        "name": "FireRed",
        "description": "Uses the official pokefirered decompilation",
        "author": "Te_On",
        "version": PLUGIN_VERSION,
        "identifier": PLUGIN_IDENTIFIER,
        "rom_base": "firered",
        "project_base_repo": PROJECT_BASE_REPO,
        "project_base_branch": PROJECT_BASE_BRANCH,
        "dependencies": [],
        "readme": "",
    }
