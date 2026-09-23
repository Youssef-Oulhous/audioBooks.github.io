"""Placeholder engine for trying the app without a GPU: soft tone bursts, one per word, at the
voice's pitch. It honours the same contract as AuK (exactly `seconds` of audio), so chunking,
pauses, chapter files and the .m4b all behave as they will with the real model."""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

import numpy as np

from .base import Engine


class DemoEngine(Engine):
    name = "demo"
    is_demo = True

    def __init__(self) -> None:
        self.delay = float(os.environ.get("AUDIOBOOK_DEMO_DELAY", "0.05"))

    @property
    def label(self) -> str:
        return "Demo engine (placeholder tones, no AuK model)"

    def load(self) -> None:
        time.sleep(min(self.delay * 10, 1.0))

    def _render(self, text: str, seconds: float, pitch: float, seed: int) -> np.ndarray:
        sr = self.sample_rate
        total = max(1, int(round(seconds * sr)))
        out = np.zeros(total, dtype=np.float32)
        words = re.findall(r"\w+", text) or ["."]
        lead = int(0.08 * sr)
        usable = max(1, total - 2 * lead)
        weights = np.array([len(w) + 2 for w in words], dtype=np.float64)
        spans = (weights / weights.sum() * usable).astype(int)
        rng = np.random.default_rng(seed)
        pos = lead
        for w, span in zip(words, spans):
            n = int(span * 0.78)
            if n > 8:
                t = np.arange(n) / sr
                f = pitch * (1 + 0.08 * rng.standard_normal())
                env = np.sin(np.pi * np.arange(n) / n) ** 2
                out[pos:pos + n] = 0.25 * env * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t))
            pos += span
        time.sleep(self.delay)
        return out

    @staticmethod
    def _pitch_for(key: str) -> float:
        h = int(hashlib.sha1(key.encode()).hexdigest()[:6], 16)
        return 150 + (h % 140)

    def design_voice(self, description: str, text: str, language: str, seconds: float, seed: int) -> np.ndarray:
        return self._render(text, seconds, self._pitch_for(description + str(seed)), seed)

    def speak(self, reference_wav: Path, text: str, seconds: float, seed: int) -> np.ndarray:
        from ..audio import read_wav

        # keep the pitch of the voice sample: count zero crossings over its voiced samples
        ref = read_wav(reference_wav)
        voiced = np.abs(ref) > 0.02
        crossings = np.count_nonzero(np.diff(np.signbit(ref)) & voiced[1:])
        pitch = crossings / 2 / max(1e-3, voiced.sum() / self.sample_rate) if voiced.any() else 180.0
        return self._render(text, seconds, float(np.clip(pitch, 100, 320)), seed)
