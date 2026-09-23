"""Owns the single speech engine: loads it in the background, serialises GPU work, and lets
interactive requests (voice previews) jump ahead of the book renderer."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

from .config import settings
from .engines import create_engine, engine_traits
from .engines.base import Engine


log = logging.getLogger("audiobook.engine")
T = TypeVar("T")


class EngineManager:
    def __init__(self) -> None:
        self._engine: Engine | None = None
        self.state = "idle"  # idle | loading | ready | error
        self.detail: str | None = None
        self._load_lock = threading.Lock()
        self._ready = threading.Event()
        self._gpu = threading.Lock()
        self._priority_waiting = 0
        self._priority_lock = threading.Lock()
        self._no_priority = threading.Event()
        self._no_priority.set()
        # audio seconds produced per wall-clock second, smoothed
        self.realtime_factor: float | None = None

    # ---------------------------------------------------------------- loading

    def start_background_load(self) -> None:
        threading.Thread(target=self._load_safely, name="engine-load", daemon=True).start()

    def _load_safely(self) -> None:
        try:
            self.ensure_loaded()
        except Exception:  # state/detail already recorded
            pass

    def ensure_loaded(self) -> Engine:
        if self._ready.is_set() and self._engine is not None:
            return self._engine
        with self._load_lock:
            if self._ready.is_set() and self._engine is not None:
                return self._engine
            self.state, self.detail = "loading", None
            started = time.time()
            try:
                engine = create_engine(settings)
                log.info("Loading speech engine: %s", engine.label)
                engine.load()
            except Exception as exc:
                self.state, self.detail = "error", f"{type(exc).__name__}: {exc}"
                log.exception("Speech engine failed to load")
                raise
            self._engine = engine
            self.state = "ready"
            self._ready.set()
            log.info("Speech engine ready in %.1fs: %s", time.time() - started, engine.label)
            return engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise RuntimeError("Speech engine is not loaded")
        return self._engine

    # ---------------------------------------------------------------- running

    def run(self, fn: Callable[[], T], priority: bool = False) -> T:
        """Run `fn` with exclusive use of the model. Priority callers are served before the renderer."""
        self.ensure_loaded()
        if priority:
            with self._priority_lock:
                self._priority_waiting += 1
                self._no_priority.clear()
        else:
            self._no_priority.wait()
        try:
            with self._gpu:
                return fn()
        finally:
            if priority:
                with self._priority_lock:
                    self._priority_waiting -= 1
                    if self._priority_waiting == 0:
                        self._no_priority.set()

    def record_speed(self, audio_seconds: float, wall_seconds: float) -> None:
        if wall_seconds <= 0 or audio_seconds <= 0:
            return
        rtf = audio_seconds / wall_seconds
        self.realtime_factor = rtf if self.realtime_factor is None else 0.9 * self.realtime_factor + 0.1 * rtf

    def status(self) -> dict:
        label = self._engine.label if self._engine else create_engine_label()
        return {
            "name": settings.engine,
            "label": label,
            "state": self.state,
            "detail": self.detail,
            "is_demo": settings.engine == "demo",
            "realtime_factor": round(self.realtime_factor, 2) if self.realtime_factor else None,
            **engine_traits(settings.engine),
        }


def create_engine_label() -> str:
    if settings.engine == "demo":
        return "Demo engine (placeholder tones, no AuK model)"
    if settings.engine == "kokoro":
        return "Kokoro voices (Pipecat), loading"
    return ("AuK-Flash" if settings.variant == "flash" else "AuK (Base)") + ", loading"


engine_manager = EngineManager()
