"""Mediated loading boundary for EXP-005's post-hoc decision-quality diagnostics
-- Revision 5, Sections 18-27 and Section 30, Stage 11.

Section 30's formalized contract: the entire diagnostics layer is a pure function

    Result = f(SQLite replay database, prices.parquet, Experiment Manifest)

and nothing else -- no live network access, no hidden global state, no dependency
on wall-clock time or call order, no access to any database or artifact outside
these three named inputs. This module is the ONE place those three inputs are
loaded and verified; every individual diagnostic function (Stages 12-13) receives
an already-built `DiagnosticsContext` from here, never a raw connection, file
path, or manifest object directly -- so "pure function of exactly these three
inputs" is enforced by what's structurally available to call, not merely
documented.

**Import-isolation invariant (Section 26), enforced mechanically, not just by
convention:** this package -- `stock_analyzer.sandbox.exp005.diagnostics` -- must
never be imported, directly or transitively, by `CandidateService`,
`AdmissionTransactionService`/`CapacityAdmissionOrchestrator`, `EntryService`,
`MonitoringService`, `ReplayService`, or any of the modules that wire them
together (`exp005/application/replay.py`, `real_run.py`,
`portfolio_accounting_seam.py`, `portfolio_ledger.py`). Those modules make
decisions; this package only ever reads what already happened, strictly after a
replay has completed. `tests/test_exp005_diagnostics_import_boundary.py` verifies
this by statically walking the import graph, not by code-review discipline alone.

Read-only against an ALREADY-COMPLETED replay database: nothing in this package,
or anything built on top of `DiagnosticsContext`, ever writes to
`portfolio_admissions`/`slot_reservations`/`executions`/`portfolio_equity_snapshots`
or any core sandbox table.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import verify_frozen_lineage
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.manifest import ExperimentManifest
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


class DiagnosticsProvenanceError(RuntimeError):
    """Raised when the frozen prices artifact supplied for diagnostics does not
    match the completed replay's own recorded manifest -- diagnostics must be
    computed against the EXACT same frozen data the replay itself used, never a
    different or unverified snapshot."""


@dataclass(frozen=True)
class DiagnosticsContext:
    """Everything a diagnostic function needs, already loaded and physically
    verified against the manifest -- Section 30's three named inputs, and nothing
    else. `portfolio_repo`/`sandbox_repo` wrap the completed replay's own SQLite
    connection for READ-ONLY access (nothing in the diagnostics package ever
    calls an insert/update method on either)."""

    manifest: ExperimentManifest
    replay_id: str
    prices_df: pd.DataFrame
    portfolio_repo: PortfolioRepository
    sandbox_repo: SandboxRepository


def load_diagnostics_context(
    conn: sqlite3.Connection,
    manifest: ExperimentManifest,
    feature_snapshot_dir: str | Path,
    replay_id: str,
) -> DiagnosticsContext:
    """Loads and re-verifies the frozen prices artifact against
    `manifest.ohlc_hash`/`manifest.feature_snapshot_id` -- never trusts a
    caller-supplied path without re-hashing it, the same discipline
    `real_run.py`'s execution boundary already applies to a real run. Call this
    ONCE per diagnostics pass; every individual diagnostic function takes the
    returned `DiagnosticsContext`, never `conn`/a file path directly.

    `conn` must already be a connection to the COMPLETED replay database this
    `replay_id` was written to -- this function does not open or validate the
    connection itself (Section 26: the decision-time write path and this
    read-only analysis path never share code, only data)."""

    lineage = verify_frozen_lineage(feature_snapshot_dir)
    if lineage.artifact_hashes["prices"] != manifest.ohlc_hash:
        raise DiagnosticsProvenanceError(
            f"the supplied feature snapshot's re-verified prices hash "
            f"({lineage.artifact_hashes['prices']!r}) does not match the manifest's recorded "
            f"ohlc_hash ({manifest.ohlc_hash!r})."
        )
    if lineage.feature_snapshot_id != manifest.feature_snapshot_id:
        raise DiagnosticsProvenanceError(
            f"the supplied feature snapshot ({lineage.feature_snapshot_id!r}) does not match the "
            f"manifest's recorded feature_snapshot_id ({manifest.feature_snapshot_id!r})."
        )

    return DiagnosticsContext(
        manifest=manifest,
        replay_id=replay_id,
        prices_df=lineage.prices_df,
        portfolio_repo=PortfolioRepository(conn),
        sandbox_repo=SandboxRepository(conn),
    )
