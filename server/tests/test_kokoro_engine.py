"""Pipecat Kokoro engine: real synthesis on the CPU (skipped where pipecat-ai[kokoro] isn't installed)."""

import numpy as np
import pytest

pytest.importorskip("pipecat.services.kokoro.tts")

from audiobook.config import Settings  # noqa: E402
from audiobook.engines.kokoro import KokoroEngine  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    eng = KokoroEngine(Settings())
    eng.load()  # downloads the model on first use (~330 MB)
    return eng


def _seconds(audio: np.ndarray, engine) -> float:
    return len(audio) / engine.sample_rate


def test_voices_used_by_the_app_exist(engine):
    from audiobook.voices import KOKORO_PRESETS

    missing = [v.engine_voice for v in KOKORO_PRESETS if v.engine_voice not in engine.voices_available()]
    assert not missing


def test_speaks_english_and_french(engine):
    en = engine.speak_preset("bm_george", "It was a bright cold day in April.", "en", 1.0)
    fr = engine.speak_preset("ff_siwis", "C'était une belle journée de printemps.", "fr", 1.0)
    for audio in (en, fr):
        assert audio.dtype == np.float32 and 1.0 < _seconds(audio, engine) < 8.0
        assert float(np.sqrt(np.mean(audio**2))) > 0.01  # not silence


def test_pace_changes_length(engine):
    text = "The old house stood at the end of the lane, quiet and patient."
    slow = _seconds(engine.speak_preset("af_heart", text, "en", 0.9), engine)
    fast = _seconds(engine.speak_preset("af_heart", text, "en", 1.1), engine)
    if not engine._has_speed:
        pytest.skip("this Pipecat release reads Kokoro at a fixed speed")
    assert fast < slow * 0.92
