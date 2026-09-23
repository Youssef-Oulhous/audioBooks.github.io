"""Runtime settings, all overridable with environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SERVER_DIR.parent


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass
class Settings:
    # "auk" = Tencent AuK on a GPU; "kokoro" = Pipecat's Kokoro voices on any CPU;
    # "demo" = placeholder tones so the app can be tried without any model.
    engine: str = field(default_factory=lambda: _env("AUDIOBOOK_ENGINE", "auk"))
    # Folder that holds AuK/ (auk_base.safetensors), AuK-Flash/ (auk_flash.safetensors) and Qwen2.5-Omni-3B/.
    ckpt_dir: Path = field(default_factory=lambda: Path(_env("AUK_CKPT_DIR", str(PROJECT_DIR.parent / "AuK" / "ckpts"))))
    variant: str = field(default_factory=lambda: _env("AUK_VARIANT", "flash"))  # "flash" (4 steps) | "base" (32 steps)
    device: str | None = field(default_factory=lambda: os.environ.get("AUK_DEVICE") or None)
    dtype: str = field(default_factory=lambda: _env("AUK_DTYPE", "bf16"))
    cpu_offload: bool = field(default_factory=lambda: _env("AUK_CPU_OFFLOAD", "0") == "1")
    base_nfe: int = field(default_factory=lambda: int(_env("AUK_NFE", "32")))

    data_dir: Path = field(default_factory=lambda: Path(_env("AUDIOBOOK_DATA", str(SERVER_DIR / "data"))))
    app_dir: Path = field(default_factory=lambda: Path(_env("AUDIOBOOK_APP_DIR", str(PROJECT_DIR / "app"))))
    token: str = field(default_factory=lambda: _env("AUDIOBOOK_TOKEN", ""))
    preload: bool = field(default_factory=lambda: _env("AUDIOBOOK_PRELOAD", "1") == "1")

    # AuK shares a 30 s budget between the reference voice sample and the generated target.
    max_sequence_seconds: float = 30.0
    max_chunk_seconds: float = field(default_factory=lambda: float(_env("AUDIOBOOK_MAX_CHUNK_SECONDS", "18")))
    aac_bitrate: str = field(default_factory=lambda: _env("AUDIOBOOK_AAC_BITRATE", "64k"))
    mp3_bitrate: str = field(default_factory=lambda: _env("AUDIOBOOK_MP3_BITRATE", "64k"))
    max_upload_mb: int = field(default_factory=lambda: int(_env("AUDIOBOOK_MAX_UPLOAD_MB", "200")))

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"


settings = Settings()
