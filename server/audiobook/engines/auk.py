"""The real thing: Tencent Hunyuan AuK via its own Python API (auk.infer.infer_auk.AukInfer)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..config import Settings
from .base import INSTRUCT_TEMPLATE, ZERO_SHOT_TEMPLATE, Engine


class AukEngine(Engine):
    name = "auk"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.variant = settings.variant.lower()
        if self.variant not in ("flash", "base"):
            raise ValueError(f"AUK_VARIANT must be 'flash' or 'base', got {settings.variant!r}")
        folder, weights = ("AuK-Flash", "auk_flash.safetensors") if self.variant == "flash" else ("AuK", "auk_base.safetensors")
        self.ckpt = settings.ckpt_dir / folder / weights
        self.config = settings.ckpt_dir / folder / "config.yaml"
        self.qwen = settings.ckpt_dir / "Qwen2.5-Omni-3B"
        self.infer = None
        self.device = settings.device

    @property
    def label(self) -> str:
        name = "AuK-Flash" if self.variant == "flash" else "AuK (Base)"
        return f"{name} on {self.device or 'auto'}"

    def load(self) -> None:
        for path, what in ((self.ckpt, "model weights"), (self.config, "config.yaml"), (self.qwen, "Qwen2.5-Omni-3B encoder")):
            if not path.exists():
                raise FileNotFoundError(f"AuK {what} not found at {path}. Run deploy/setup_gpu.sh or set AUK_CKPT_DIR.")
        try:
            from auk.infer.infer_auk import AukInfer
        except ImportError as exc:
            raise RuntimeError("The 'auk' package isn't installed in this Python environment (pip install -e AuK).") from exc

        self.infer = AukInfer(
            str(self.config),
            str(self.ckpt),
            device=self.settings.device,
            dtype=self.settings.dtype,
            qwen_path=str(self.qwen),
            cpu_offload=self.settings.cpu_offload,
        )
        self.device = self.infer.device
        self.sample_rate = int(self.infer.target_sample_rate)

    def _generate(self, content: list, seconds: float, seed: int) -> np.ndarray:
        audio, _ = self.infer.generate(
            [{"role": "user", "content": content}],
            gen_seconds=seconds,
            nfe=self.settings.base_nfe,  # ignored by AuK-Flash, which pins its own 4-step recipe
            cfg_strength=2.0,
            sway_sampling_coef=-1.0,
            seed=seed,
        )
        return audio.squeeze(0).float().cpu().numpy().astype(np.float32)

    def design_voice(self, description: str, text: str, language: str, seconds: float, seed: int) -> np.ndarray:
        template = INSTRUCT_TEMPLATE.get(language, INSTRUCT_TEMPLATE["en"])
        instruction = template.format(description=description, text=text)
        return self._generate([{"type": "text", "text": instruction}], seconds, seed)

    def speak(self, reference_wav: Path, text: str, seconds: float, seed: int) -> np.ndarray:
        instruction = ZERO_SHOT_TEMPLATE.format(text=text)
        content = [{"type": "text", "text": instruction}, {"type": "audio", "audio": str(reference_wav)}]
        return self._generate(content, seconds, seed)
