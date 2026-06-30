"""JSON-backed metrics persistence helpers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any


LOGGER = logging.getLogger(__name__)
_STORE_LOCK = Lock()
_STORE_PATH = Path(os.getenv("STOCK_ANALYZER_METRICS_FILE", "runtime_metrics.json"))


def metrics_store_path() -> str:
    """Return the absolute path used for persisted metrics."""

    return str(_STORE_PATH.resolve())


def _read_store_locked() -> dict[str, Any]:
    if not _STORE_PATH.exists():
        return {}
    try:
        with _STORE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Failed to read metrics store '%s': %s", _STORE_PATH, exc)
        return {}
    if not isinstance(payload, dict):
        LOGGER.warning("Metrics store '%s' has invalid shape; expected object.", _STORE_PATH)
        return {}
    return payload


def _write_store_locked(payload: dict[str, Any]) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = _STORE_PATH.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        temp_path.replace(_STORE_PATH)
    except OSError as exc:
        LOGGER.warning("Failed to persist metrics store '%s': %s", _STORE_PATH, exc)


def load_metrics_section(section: str, defaults: dict[str, float | int]) -> dict[str, float | int]:
    """Load a persisted section and merge it with numeric defaults."""

    with _STORE_LOCK:
        payload = _read_store_locked()
    section_data = payload.get(section, {})
    result = dict(defaults)
    if isinstance(section_data, dict):
        for key, value in section_data.items():
            if key in result and isinstance(value, (int, float)):
                result[key] = value
    return result


def persist_metrics_section(section: str, data: dict[str, float | int]) -> None:
    """Persist a numeric metrics section."""

    with _STORE_LOCK:
        payload = _read_store_locked()
        payload[section] = dict(data)
        _write_store_locked(payload)
