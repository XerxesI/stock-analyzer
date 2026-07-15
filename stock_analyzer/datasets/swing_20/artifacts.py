"""Read/write frozen SWING_20 dataset artifacts."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Literal

import pandas as pd

StorageFormat = Literal["parquet", "csv"]


def write_frame(frame: pd.DataFrame, path: Path, storage_format: StorageFormat) -> Path:
    """Write a DataFrame in the requested frozen artifact format."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if storage_format == "parquet":
        try:
            frame.to_parquet(path, index=False)
        except ImportError as exc:
            raise RuntimeError(
                "Parquet output requires pyarrow or fastparquet. Install project "
                "requirements or run with --format csv for a local diagnostic run."
            ) from exc
    elif storage_format == "csv":
        frame.to_csv(path, index=False)
    else:
        raise ValueError(f"Unsupported storage format: {storage_format}")
    return path


def read_frame(path: Path, storage_format: StorageFormat | None = None) -> pd.DataFrame:
    """Read a frozen artifact DataFrame."""

    resolved_format = storage_format or _format_from_suffix(path)
    if resolved_format == "parquet":
        return pd.read_parquet(path)
    if resolved_format == "csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported storage format: {resolved_format}")


def write_manifest(manifest: dict[str, object], path: Path) -> Path:
    """Write a JSON manifest."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def read_manifest(path: Path) -> dict[str, object]:
    """Read a JSON manifest."""

    return json.loads(path.read_text(encoding="utf-8"))


def artifact_path(output_dir: Path, name: str, storage_format: StorageFormat) -> Path:
    """Build a standard artifact path."""

    suffix = ".parquet" if storage_format == "parquet" else ".csv"
    return output_dir / f"{name}{suffix}"


def file_sha256(path: Path) -> str:
    """Return a SHA-256 hash for a frozen artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_from_suffix(path: Path) -> StorageFormat:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return "parquet"
    if suffix == ".csv":
        return "csv"
    raise ValueError(f"Cannot infer storage format from suffix: {path.suffix}")
