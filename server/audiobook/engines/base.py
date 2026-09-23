from __future__ import annotations

from pathlib import Path

import numpy as np


# Instruction templates from AuK's docs/COOKBOOK.md (1.1 Zero-shot TTS, 1.2 Instruct TTS).
INSTRUCT_TEMPLATE = {
    "en": 'Generate speech based on the following description: "{description}". The content to speak is: "{text}".',
    "zh": '请基于下面的描述: "{description}",生成语音内容"{text}".',
}
ZERO_SHOT_TEMPLATE = 'Say the following with the same voice: "{text}"'


class Engine:
    """A speech engine. Output is mono float32 at `sample_rate`, exactly `seconds` long (AuK
    generates to the requested duration, so the caller controls the speaking rate)."""

    name = "base"
    is_demo = False
    sample_rate = 24000
    # "described": voices are text descriptions turned into a sample that every chunk clones (AuK).
    # "preset": voices are fixed speakers of the model, used by id (Kokoro).
    voice_mode = "described"
    custom_voices = True  # can the user describe a new voice?
    languages = frozenset({"en", "zh"})  # book languages this engine narrates well

    @property
    def label(self) -> str:
        return self.name

    def load(self) -> None:
        raise NotImplementedError

    def design_voice(self, description: str, text: str, language: str, seconds: float, seed: int) -> np.ndarray:
        """Instruct TTS: invent a voice from a description (used once per voice to make its sample)."""
        raise NotImplementedError

    def speak(self, reference_wav: Path, text: str, seconds: float, seed: int) -> np.ndarray:
        """Zero-shot TTS: say `text` in the voice of `reference_wav` (used for every chunk of a book)."""
        raise NotImplementedError

    def speak_preset(self, voice_key: str, text: str, language: str, speed: float) -> np.ndarray:
        """Preset engines: say `text` with the fixed voice `voice_key` at natural length."""
        raise NotImplementedError
