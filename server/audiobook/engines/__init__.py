from __future__ import annotations

from ..config import Settings
from .base import Engine


def create_engine(settings: Settings) -> Engine:
    if settings.engine == "demo":
        from .demo import DemoEngine

        return DemoEngine()
    if settings.engine == "auk":
        from .auk import AukEngine

        return AukEngine(settings)
    if settings.engine == "kokoro":
        from .kokoro import KokoroEngine

        return KokoroEngine(settings)
    raise ValueError(f"AUDIOBOOK_ENGINE must be 'auk', 'kokoro' or 'demo', got {settings.engine!r}")


def engine_traits(name: str) -> dict:
    """What an engine can do, without loading it (used before the model is ready)."""
    if name == "kokoro":
        from .kokoro import KokoroEngine as cls
    elif name == "auk":
        from .auk import AukEngine as cls
    else:
        from .demo import DemoEngine as cls
    return {"voice_mode": cls.voice_mode, "custom_voices": cls.custom_voices, "languages": sorted(cls.languages)}
