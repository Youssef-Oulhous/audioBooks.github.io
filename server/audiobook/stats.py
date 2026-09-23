"""Listening statistics, kept per device and merged by the app.

The app records its own totals (listening time, time in the app, per-book progress) in local
storage and uploads the whole document now and then. Keeping one document per device means a
re-upload never double-counts, and the app merges its own live numbers with the other devices'.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .config import settings


DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BYTES = 2 * 1024 * 1024


class StatsStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()

    @property
    def dir(self) -> Path:
        return settings.data_dir / "stats"

    def all(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not self.dir.exists():
            return out
        with self.lock:
            for path in self.dir.glob("*.json"):
                try:
                    out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
        return out

    def put(self, device_id: str, doc: dict) -> None:
        if not DEVICE_RE.match(device_id):
            raise ValueError("Bad device id.")
        clean = validate(doc)
        raw = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > MAX_BYTES:
            raise ValueError("Stats document too large.")
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            tmp = self.dir / f"{device_id}.json.tmp"
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(self.dir / f"{device_id}.json")


def _num(value, cap: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(cap, number)) if number == number else 0.0  # NaN -> 0


def validate(doc: dict) -> dict:
    """Keep only the fields the app uses, with sane bounds (a day has 86 400 seconds)."""
    if not isinstance(doc, dict):
        raise ValueError("Stats must be a JSON object.")
    days = {}
    for day, entry in (doc.get("days") or {}).items():
        if not (isinstance(day, str) and DAY_RE.match(day) and isinstance(entry, dict)):
            continue
        books = {
            str(k)[:40]: _num(v, 86400)
            for k, v in (entry.get("books") or {}).items()
            if isinstance(k, str)
        } if isinstance(entry.get("books"), dict) else {}
        days[day] = {"listen": _num(entry.get("listen"), 86400), "app": _num(entry.get("app"), 86400), "books": books}
    books = {}
    for book_id, entry in (doc.get("books") or {}).items():
        if not (isinstance(book_id, str) and isinstance(entry, dict)):
            continue
        finished = entry.get("finished_at")
        books[book_id[:40]] = {
            "title": str(entry.get("title") or "")[:300],
            "author": str(entry.get("author") or "")[:300],
            "furthest": _num(entry.get("furthest"), 10**7),
            "total": _num(entry.get("total"), 10**7),
            "finished_at": finished if isinstance(finished, str) and DAY_RE.match(finished) else None,
            "last_played_at": _num(entry.get("last_played_at"), 10**11),
        }
    return {
        "version": 1,
        "days": days,
        "books": books,
        "goal_minutes": int(_num(doc.get("goal_minutes", 30), 600)) or 30,
        "updated_at": _num(doc.get("updated_at"), 10**11),
    }


stats_store = StatsStore()
