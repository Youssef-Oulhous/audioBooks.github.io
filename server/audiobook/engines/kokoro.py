"""Kokoro voices through Pipecat's KokoroTTSService (the TTS used by Pipecat voice agents).

Runs on a normal CPU: no GPU, no API key. The model (~330 MB) is downloaded by Pipecat on
first use into ~/.cache/pipecat/kokoro-onnx. Voices are fixed speakers (af_heart, bm_george,
ff_siwis, ...), so every chunk of a book is read by exactly the same voice.

Pipecat services normally live inside a real-time pipeline. For batch audiobook rendering we
drive the service directly: `run_tts()` is its synthesis entry point, and settings changes go
through `_update_settings()`, the same path a `TTSUpdateSettingsFrame` takes in a pipeline.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path

import numpy as np

from ..config import Settings
from .base import Engine


SAMPLE_RATE = 24000

# book language -> Pipecat Language value (Pipecat maps these to espeak-ng locales for Kokoro)
_LANGUAGES = {"en": "en", "en-gb": "en-GB", "fr": "fr", "es": "es", "it": "it", "pt": "pt-BR", "zh": "zh", "hi": "hi", "ja": "ja"}


class KokoroEngine(Engine):
    name = "kokoro"
    voice_mode = "preset"
    custom_voices = False
    languages = frozenset({"en", "fr", "es", "it", "pt", "zh", "hi", "ja"})
    sample_rate = SAMPLE_RATE

    def __init__(self, settings: Settings):
        self.settings = settings
        self.tts = None

    @property
    def label(self) -> str:
        where = "on a GPU" if getattr(self, "device", "cpu") == "cuda" else "on this computer's CPU"
        return f"Kokoro voices (Pipecat) {where}"

    def load(self) -> None:
        try:
            from pipecat.services.kokoro.tts import KokoroTTSService
        except ImportError as exc:
            raise RuntimeError('Pipecat Kokoro isn\'t installed: pip install "pipecat-ai[kokoro]"') from exc
        model = os.environ.get("KOKORO_MODEL_PATH")
        voices = os.environ.get("KOKORO_VOICES_PATH")
        self._settings_cls = KokoroTTSService.Settings
        self._has_speed = "speed" in {f.name for f in dataclasses.fields(KokoroTTSService.Settings)}
        self.tts = KokoroTTSService(
            model_path=model if model and Path(model).exists() else None,
            voices_path=voices if voices and Path(voices).exists() else None,
            settings=KokoroTTSService.Settings(voice="af_heart"),
            sample_rate=SAMPLE_RATE,
        )
        # Pipecat sets the output rate in setup() when a pipeline starts; we synthesize without one.
        self.tts._sample_rate = SAMPLE_RATE
        self.device = "cpu"
        if os.environ.get("KOKORO_DEVICE", "auto") != "cpu":
            self._try_gpu(model, voices)

    def _try_gpu(self, model_path: str | None, voices_path: str | None) -> None:
        """Run the same Kokoro model on a GPU when one is available (10-50x faster than a laptop CPU).

        Pipecat builds a CPU session; we swap in a CUDA one and keep everything else of its service."""
        try:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro

            if "CUDAExecutionProvider" not in ort.get_available_providers():
                return
            cache = Path(os.path.expanduser("~/.cache/pipecat/kokoro-onnx"))
            model = Path(model_path) if model_path else cache / "kokoro-v1.0.onnx"
            voices = Path(voices_path) if voices_path else cache / "voices-v1.0.bin"
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(str(model), options, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            self.tts._kokoro = Kokoro.from_session(session, str(voices))
            self.device = "cuda"
        except Exception as exc:  # any trouble: stay on the CPU session Pipecat made
            import logging

            logging.getLogger("audiobook.engine").warning("Kokoro GPU unavailable, using the CPU: %s", exc)

    def voices_available(self) -> set[str]:
        return set(self.tts._kokoro.get_voices()) if self.tts else set()

    def speak_preset(self, voice_key: str, text: str, language: str, speed: float) -> np.ndarray:
        """Say `text` with a fixed Kokoro voice. Natural length; `speed` 0.5-2.0."""
        from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame
        from pipecat.transcriptions.language import Language

        # British voices (bf_*, bm_*) read English with the British phoneme set
        lang = "en-gb" if language == "en" and voice_key[:2] in ("bf", "bm") else language
        fields = {"voice": voice_key, "language": Language(_LANGUAGES.get(lang, "en"))}
        if self._has_speed:  # Pipecat >= 1.x exposes Kokoro's speed; older releases always read at 1.0
            fields["speed"] = float(speed)
        delta = self._settings_cls(**fields)

        async def synthesize() -> bytes:
            await self.tts._update_settings(delta)
            pcm = bytearray()
            async for frame in self.tts.run_tts(text, "audiobook"):
                if isinstance(frame, ErrorFrame):
                    raise RuntimeError(f"Kokoro failed: {frame.error}")
                if isinstance(frame, TTSAudioRawFrame):
                    pcm += frame.audio
            return bytes(pcm)

        pcm = asyncio.run(synthesize())
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32767.0
