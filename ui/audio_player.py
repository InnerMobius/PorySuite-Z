"""Shared audio playback helpers for PorySuite-Z.

Currently only Pokemon cries are supported. Cries ship as .wav files at
``<project>/sound/direct_sound_samples/cries/<slug>.wav``, where ``<slug>``
is the lowercase portion of a ``SPECIES_*`` constant (e.g. ``SPECIES_BULBASAUR``
-> ``bulbasaur.wav``, ``SPECIES_NIDORAN_F`` -> ``nidoran_f.wav``).

Music tracks (``MUS_*``) and sound effects (``SE_*``) are driven by the GBA
music engine from compiled MIDI + voicegroup instrument samples.  Those are
not directly playable on the desktop and therefore have no preview here.
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


class AudioPlayer(QObject):
    """Thin wrapper around QMediaPlayer for one-shot cry/SFX playback."""

    playback_error = pyqtSignal(str)

    _instance: Optional["AudioPlayer"] = None

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)
        self._output.setVolume(0.9)
        self._project_root: Optional[str] = None

    # ------------------------------------------------------------------ API
    @classmethod
    def instance(cls) -> "AudioPlayer":
        if cls._instance is None:
            cls._instance = AudioPlayer()
        return cls._instance

    def set_project_root(self, root: str) -> None:
        self._project_root = root or None

    def stop(self) -> None:
        self._player.stop()

    # -- Cries -----------------------------------------------------------
    def cry_path_for_species(self, species_constant: str) -> Optional[str]:
        """Return the .wav path for ``SPECIES_XXX`` or None if missing."""
        if not self._project_root or not species_constant:
            return None
        if not species_constant.upper().startswith("SPECIES_"):
            slug = species_constant.lower()
        else:
            slug = species_constant[len("SPECIES_"):].lower()
        if not slug or slug in ("none", "egg"):
            return None
        candidate = os.path.join(
            self._project_root,
            "sound", "direct_sound_samples", "cries", f"{slug}.wav",
        )
        if os.path.exists(candidate):
            return candidate
        return None

    def play_cry(self, species_constant: str) -> bool:
        """Play a Pokemon cry.  Returns True if playback started."""
        path = self.cry_path_for_species(species_constant)
        if not path:
            self.playback_error.emit(
                f"No cry file found for {species_constant}."
            )
            return False
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        return True


def get_audio_player() -> AudioPlayer:
    """Convenience accessor for the module-level shared player."""
    return AudioPlayer.instance()
