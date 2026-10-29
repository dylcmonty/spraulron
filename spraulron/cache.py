"""Tiny JSON-on-disk cache with TTL and stale-on-error behaviour.

Each cache entry is one file: {"fetched_at": iso8601, "data": ...}. Nothing else is persisted.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class CacheResult:
    key: str
    data: Any
    fetched_at: Optional[datetime]
    from_cache: bool
    stale: bool
    error: Optional[str] = None

    @property
    def age_seconds(self) -> Optional[float]:
        if not self.fetched_at:
            return None
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds()

    def status(self, label: str, ttl_seconds: int) -> dict:
        return {
            "key": self.key,
            "label": label,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "age_seconds": self.age_seconds,
            "ttl_seconds": ttl_seconds,
            "from_cache": self.from_cache,
            "stale": self.stale,
            "error": self.error,
            "count": len(self.data) if isinstance(self.data, list) else None,
        }


class JsonCache:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def path(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
        return self.dir / f"{safe}.json"

    def read(self, key: str) -> Optional[tuple[Any, datetime]]:
        p = self.path(key)
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                blob = json.load(fh)
            return blob["data"], datetime.fromisoformat(blob["fetched_at"])
        except Exception:
            return None

    def write(self, key: str, data: Any) -> datetime:
        now = datetime.now(timezone.utc)
        tmp = self.path(key).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": now.isoformat(), "data": data}, fh)
        tmp.replace(self.path(key))
        return now

    def get_or_fetch(self, key: str, ttl_seconds: int, fetcher: Callable[[], Any], force: bool = False) -> CacheResult:
        with self._lock(key):
            cached = self.read(key)
            if cached and not force:
                data, fetched_at = cached
                age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
                if age < ttl_seconds:
                    return CacheResult(key, data, fetched_at, True, False)
            try:
                data = fetcher()
            except Exception as exc:  # serve stale data if we have any
                if cached:
                    return CacheResult(key, cached[0], cached[1], True, True, f"{type(exc).__name__}: {exc}")
                return CacheResult(key, [] , None, False, True, f"{type(exc).__name__}: {exc}")
            fetched_at = self.write(key, data)
            return CacheResult(key, data, fetched_at, False, False)

    def invalidate(self, key: str) -> None:
        p = self.path(key)
        if p.exists():
            p.unlink()

    def prune(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        n = 0
        for p in self.dir.glob("*.json"):
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        return n

    def listing(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            out.append({"key": p.stem, "bytes": p.stat().st_size,
                        "modified": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()})
        return out
