"""Checks the AuK adapter against AuK's real AukInfer.generate(), with the neural network stubbed.

Runs only where the `auk` package is installed (e.g. on the GPU server, before downloading weights).
"""

import math
from pathlib import Path

import numpy as np
import pytest

ia = pytest.importorskip("auk.infer.infer_auk")
torch = pytest.importorskip("torch")

from audiobook import audio  # noqa: E402
from audiobook.config import Settings  # noqa: E402
from audiobook.engines.auk import AukEngine  # noqa: E402


@pytest.fixture
def engine(tmp_path, monkeypatch):
    (tmp_path / "AuK-Flash").mkdir()
    (tmp_path / "Qwen2.5-Omni-3B").mkdir()
    (tmp_path / "AuK-Flash" / "auk_flash.safetensors").touch()
    (tmp_path / "AuK-Flash" / "config.yaml").write_text("model:\n  name: AuK-Flash\n")
    calls = []

    def fake_init(self, config_path, ckpt_path, *, device=None, dtype="bf16", qwen_path=None, cpu_offload=False):
        self.device, self.cpu_offload, self.is_flash = "cpu", cpu_offload, True
        self.target_sample_rate, self.downsample_rate, self.latent_dim = 24000, 480, 64

    def fake_run(self, ref_audio, ref_rms, messages, gen_latent_len, **kw):
        calls.append({"content": messages[0]["content"], "ref": tuple(ref_audio.shape), "frames": gen_latent_len, **kw})
        return torch.zeros(1, gen_latent_len * self.downsample_rate)

    monkeypatch.setattr(ia.AukInfer, "__init__", fake_init)
    monkeypatch.setattr(ia.AukInfer, "_run", fake_run)
    monkeypatch.setenv("AUK_CKPT_DIR", str(tmp_path))
    eng = AukEngine(Settings())
    eng.load()
    return eng, calls, tmp_path


def test_voice_design_uses_instruct_template(engine):
    eng, calls, _ = engine
    out = eng.design_voice("A calm narrator.", "Hello there.", "en", 3.0, 7)
    assert out.shape == (math.ceil(3.0 * 50) * 480,)
    text = calls[-1]["content"][0]["text"]
    assert text.startswith('Generate speech based on the following description: "A calm narrator.".')
    assert text.endswith("|<no_prompt_audio>|")  # AuK marks text-only requests itself
    assert calls[-1]["nfe"] == 4 and calls[-1]["cfg_strength"] == 0.0  # Flash recipe


def test_speak_clones_reference(engine):
    eng, calls, tmp = engine
    ref = tmp / "ref.wav"
    audio.write_wav(ref, (0.1 * np.sin(np.arange(24000 * 4) / 20)).astype("float32"), 24000)
    out = eng.speak(Path(ref), "It was a bright cold day.", 2.5, 7)
    assert out.shape == (125 * 480,)
    content = calls[-1]["content"]
    assert content[0]["text"] == 'Say the following with the same voice: "It was a bright cold day."'
    assert content[1] == {"type": "audio", "audio": str(ref)}
    assert calls[-1]["ref"] == (1, 96000)
