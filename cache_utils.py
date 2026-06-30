"""Thread-safe TTL cache utility with lightweight hit/miss metrics."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Callable, Generic, TypeVar


K = TypeVar("K")
V = TypeVar("V")


@dataclass
class _CacheEntry(Generic[V]):
    value: V
    expires_at: float


class TTLCache(Generic[K, V]):
    """Simple in-memory TTL cache with bounded size and basic metrics."""

    def __init__(self, maxsize: int, default_ttl_seconds: float, name: str) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive.")
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be positive.")
        self._maxsize = maxsize
        self._default_ttl_seconds = default_ttl_seconds
        self._name = name
        self._store: dict[K, _CacheEntry[V]] = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def _purge_expired_locked(self, now: float) -> None:
        expired_keys = [key for key, entry in self._store.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._store.pop(key, None)

    def get(self, key: K) -> V | None:
        """Get cached value or None when absent/expired."""

        now = monotonic()
        with self._lock:
            self._purge_expired_locked(now)
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._hits += 1
            return entry.value

    def set(self, key: K, value: V, ttl_seconds: float | None = None) -> V:
        """Store a cache value and return it."""

        ttl = self._default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive.")
        now = monotonic()
        expires_at = now + ttl
        with self._lock:
            self._purge_expired_locked(now)
            if key not in self._store and len(self._store) >= self._maxsize:
                oldest_key = next(iter(self._store))
                self._store.pop(oldest_key, None)
            self._store[key] = _CacheEntry(value=value, expires_at=expires_at)
        return value

    def get_or_set(self, key: K, factory: Callable[[], V], ttl_seconds: float | None = None) -> V:
        """Get cached value or build and cache a new one."""

        existing = self.get(key)
        if existing is not None:
            return existing
        value = factory()
        return self.set(key, value, ttl_seconds=ttl_seconds)

    def snapshot(self) -> dict[str, float | int | str]:
        """Return current cache statistics."""

        now = monotonic()
        with self._lock:
            self._purge_expired_locked(now)
            hits = self._hits
            misses = self._misses
            size = len(self._store)
        total = hits + misses
        hit_rate = (hits / total) if total else 0.0
        return {
            "name": self._name,
            "size": size,
            "maxsize": self._maxsize,
            "ttl_seconds": self._default_ttl_seconds,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hit_rate, 4),
        }
