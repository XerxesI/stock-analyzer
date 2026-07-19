"""Frozen-artifact lineage verification -- Stage 10 closure (P1 review).

Shared by both `FrozenSwing20MarketDataProvider` (which only needs `prices`
verified) and `exp005/manifest.py`'s manifest builder (which needs every artifact
plus the feature dataset's own semantic hash) -- so the two never independently
re-derive, and risk disagreeing about, what counts as a verified feature-snapshot
<-> SWING_20-snapshot relationship.

`verify_frozen_lineage` never trusts a manifest's own claim about a file's hash
without recomputing it: it re-hashes every one of the 5 SWING_20 artifact files
(universe/prices/labels/eligibility/failures) from disk, and re-hashes the
feature dataset's actual row content via the SAME mechanism
`stock_analyzer.datasets.swing_20.features.build_lineage` already uses (a pandas
content hash, not a raw-file-bytes hash -- two files with identical logical
content but different parquet encoding would otherwise hash differently and be
wrongly flagged as mismatched). Raises `FrozenArtifactVerificationError` on any
disagreement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_SWING20_ARTIFACT_FILES = ("universe", "prices", "labels", "eligibility", "failures")


class FrozenArtifactVerificationError(RuntimeError):
    """Raised when a frozen artifact's actual file/content does not match its
    manifest-recorded hash, or when a feature snapshot's manifest does not agree
    with the SWING_20 snapshot it claims to be built from."""


def sha256_of_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_of_dataframe(df: pd.DataFrame) -> str:
    """The project's canonical semantic feature-dataset hash -- matches
    stock_analyzer.datasets.swing_20.features.build_lineage exactly."""

    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class VerifiedLineage:
    """Every field here has already been physically confirmed against the actual
    files on disk -- never merely read from a manifest and trusted."""

    feature_snapshot_dir: Path
    feature_snapshot_id: str
    feature_dataset_hash: str
    swing20_snapshot_dir: Path
    swing20_snapshot_id: str
    artifact_hashes: dict[str, str]  # universe/prices/labels/eligibility/failures
    prices_df: pd.DataFrame


def verify_frozen_lineage(feature_snapshot_dir: str | Path) -> VerifiedLineage:
    """Loads the feature snapshot's manifest, follows it to the SWING_20 snapshot
    it claims to be built from, and physically re-verifies every hash involved:

    1. the feature manifest's `source_swing20_snapshot_id` must equal the SWING_20
       manifest's own `dataset_version`;
    2. the feature manifest's recorded `source_swing20_artifact_hashes` must equal
       the SWING_20 manifest's own `artifact_hashes`, key for key;
    3. every one of the 5 SWING_20 artifact files must hash (SHA-256 of the file's
       bytes) to its manifest-recorded value;
    4. the feature dataset's own semantic content hash (sha256_of_dataframe) must
       equal the feature manifest's recorded `feature_dataset_hash`.

    Raises FrozenArtifactVerificationError on the first mismatch found. Never
    repairs or re-labels a mismatched artifact.
    """

    feature_dir = Path(feature_snapshot_dir)
    feature_manifest = _read_json(feature_dir / "manifest.json")

    swing20_dir_raw = feature_manifest.get("source_swing20_snapshot_dir")
    if not swing20_dir_raw:
        raise FrozenArtifactVerificationError(
            f"feature snapshot manifest at {feature_dir} has no source_swing20_snapshot_dir."
        )
    swing20_dir = Path(swing20_dir_raw)
    swing20_manifest = _read_json(swing20_dir / "manifest.json")

    claimed_id = feature_manifest.get("source_swing20_snapshot_id")
    actual_id = swing20_manifest.get("dataset_version")
    if claimed_id != actual_id:
        raise FrozenArtifactVerificationError(
            f"feature snapshot {feature_dir} claims source_swing20_snapshot_id={claimed_id!r}, "
            f"but {swing20_dir}/manifest.json's own dataset_version is {actual_id!r}."
        )

    claimed_hashes = feature_manifest.get("source_swing20_artifact_hashes") or {}
    actual_manifest_hashes = swing20_manifest.get("artifact_hashes") or {}
    if claimed_hashes != actual_manifest_hashes:
        raise FrozenArtifactVerificationError(
            f"feature snapshot {feature_dir}'s recorded source_swing20_artifact_hashes do not "
            f"match {swing20_dir}/manifest.json's own artifact_hashes -- "
            f"claimed={claimed_hashes}, actual={actual_manifest_hashes}."
        )

    verified_hashes: dict[str, str] = {}
    for artifact_name in _SWING20_ARTIFACT_FILES:
        relative_path = (swing20_manifest.get("artifacts") or {}).get(artifact_name)
        if not relative_path:
            raise FrozenArtifactVerificationError(
                f"{swing20_dir}/manifest.json has no {artifact_name!r} entry under 'artifacts'."
            )
        file_path = swing20_dir / Path(relative_path).name
        recorded_hash = actual_manifest_hashes.get(artifact_name)
        actual_hash = sha256_of_file(file_path)
        if actual_hash != recorded_hash:
            raise FrozenArtifactVerificationError(
                f"{file_path} does not match its manifest-recorded hash -- "
                f"expected {recorded_hash}, got {actual_hash}."
            )
        verified_hashes[artifact_name] = actual_hash

    features_path = feature_dir / "features.parquet"
    features_df = pd.read_parquet(features_path)
    actual_feature_hash = sha256_of_dataframe(features_df)
    recorded_feature_hash = feature_manifest.get("feature_dataset_hash")
    if actual_feature_hash != recorded_feature_hash:
        raise FrozenArtifactVerificationError(
            f"{features_path}'s actual semantic content hash does not match its manifest-"
            f"recorded feature_dataset_hash -- expected {recorded_feature_hash}, "
            f"got {actual_feature_hash}."
        )

    prices_df = pd.read_parquet(swing20_dir / "prices.parquet")

    return VerifiedLineage(
        feature_snapshot_dir=feature_dir,
        feature_snapshot_id=feature_manifest.get("dataset_version"),
        feature_dataset_hash=actual_feature_hash,
        swing20_snapshot_dir=swing20_dir,
        swing20_snapshot_id=actual_id,
        artifact_hashes=verified_hashes,
        prices_df=prices_df,
    )
