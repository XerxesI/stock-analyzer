"""Freeze-validation gate -- Revision 5, Section 12 item 9 / Section 13, Stage 10.

Stage 10 is the last mandatory stop before any REAL EXP-005 comparison run
(checklist: "Stop and report here before running any real experiment"). No variant
(B or D) may execute until `validate_freeze` confirms the Experiment Manifest
(Stage 9) is fully populated -- Section 13: "The real EXP-005 comparison run
refuses to start unless every Stage 9 manifest field ... is present and populated
-- a missing/empty field raises before any variant executes."

This module performs no side effects of its own and constructs nothing -- it only
inspects an already-built `ExperimentManifest` and raises `FreezeValidationError`,
naming exactly which field(s) are missing, before the caller is allowed to proceed
to actually building/running any variant. It never repairs, defaults, or fills in a
missing field.

`spy_benchmark_snapshot_id` is deliberately excluded from the required-field check:
Section 5/29 state it is contextual only and never gates the primary Variant B vs.
D comparison -- a manifest with every OTHER field populated is a complete, valid
freeze regardless of whether the one-time SPY pull has happened yet.

**No diagnostic definition may change after real Variant B results become visible**
(Section 12 item 9's closing sentence) is a process discipline this module cannot
mechanically enforce by itself -- there is no "later manifest" to compare against
until after a real run exists. It is honored by: freezing the manifest here, before
Stage 10 proceeds, and never regenerating or hand-editing it afterward.
"""

from __future__ import annotations

from stock_analyzer.sandbox.exp005.manifest import ExperimentManifest

_REQUIRED_FIELDS = (
    "experiment_id",
    "code_commit_sha",
    "model_version",
    "universe_hash",
    "ohlc_hash",
    "signal_hash",
    "eligibility_hash",
    "feature_hash",
    "calendar_version",
    "feature_snapshot_id",
    "swing20_snapshot_id",
    "portfolio_configuration_hash",
)


class FreezeValidationError(RuntimeError):
    """Raised when the Experiment Manifest is not fully populated -- Section 13:
    "a missing/empty field raises before any variant executes." """


def missing_manifest_fields(manifest: ExperimentManifest) -> list[str]:
    """Pure inspection -- every field name that is missing/empty/invalid, or an
    empty list if the manifest is complete. Exposed separately from
    `validate_freeze` so a caller can report the specific gap(s) without having to
    parse an exception message."""

    missing = [field for field in _REQUIRED_FIELDS if not getattr(manifest, field)]
    if manifest.schema_version <= 0:
        missing.append("schema_version")
    if manifest.decision_audit_schema_version <= 0:
        missing.append("decision_audit_schema_version")
    if manifest.calendar_session_count <= 0:
        missing.append("calendar_session_count")
    if not manifest.control_seed_list:
        missing.append("control_seed_list")
    if not manifest.feasibility_criteria:
        missing.append("feasibility_criteria")
    if not manifest.diagnostic_definitions:
        missing.append("diagnostic_definitions")
    return missing


def validate_freeze(manifest: ExperimentManifest) -> None:
    """Raises FreezeValidationError if the manifest is incomplete. Returns None
    (no side effect) if it is complete -- the caller is then, and only then,
    permitted to proceed to Stage 10's real Variant B / Variant D runs."""

    missing = missing_manifest_fields(manifest)
    if missing:
        raise FreezeValidationError(
            f"Experiment Manifest is incomplete -- missing/empty field(s): {missing}. "
            "No EXP-005 variant may execute until every manifest field is present and "
            "populated (Section 13). Regenerate the manifest (exp005/manifest.py) with "
            "the missing value(s) supplied, then re-validate."
        )
