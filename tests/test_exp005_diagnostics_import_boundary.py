"""Section 26's import-isolation invariant, enforced mechanically -- Stage 11.

None of CandidateService, AdmissionTransactionService/CapacityAdmissionOrchestrator,
EntryService, MonitoringService, ReplayService, or the modules that wire them
together for a real run may import, directly or transitively, from
stock_analyzer.sandbox.exp005.diagnostics. Post-hoc outcomes (Sections 20-25) are
computed strictly AFTER a replay completes; if a decision-time module could reach
into that package, it would become POSSIBLE (even if not actually exercised) for a
post-hoc value to influence a decision -- Section 26 requires this to be
structurally impossible, not merely a code-review convention. This test proves it
via a static import-graph walk, independent of any particular test's import order
or sys.modules caching.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every module that makes -- or atomically persists -- an EXP-005 decision.
DECISION_TIME_MODULES = (
    "stock_analyzer.sandbox.application.candidate_service",
    "stock_analyzer.sandbox.application.entry_service",
    "stock_analyzer.sandbox.application.monitoring_service",
    "stock_analyzer.sandbox.application.replay_service",
    "stock_analyzer.sandbox.application.accounting_seam",
    "stock_analyzer.sandbox.application.market_data_provider",
    "stock_analyzer.sandbox.exp005.application.admission_orchestrator",
    "stock_analyzer.sandbox.exp005.application.variant_runner",
    "stock_analyzer.sandbox.exp005.application.portfolio_accounting_seam",
    "stock_analyzer.sandbox.exp005.application.portfolio_ledger",
    "stock_analyzer.sandbox.exp005.application.replay",
    "stock_analyzer.sandbox.exp005.application.real_run",
)

_DIAGNOSTICS_PREFIX = "stock_analyzer.sandbox.exp005.diagnostics"


def _module_name_to_path(module_name: str) -> Path | None:
    rel = Path(*module_name.split("."))
    as_module_file = REPO_ROOT / rel.with_suffix(".py")
    if as_module_file.exists():
        return as_module_file
    as_package_init = REPO_ROOT / rel / "__init__.py"
    if as_package_init.exists():
        return as_package_init
    return None


def _direct_stock_analyzer_imports(module_name: str) -> set[str]:
    path = _module_name_to_path(module_name)
    if path is None:
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("stock_analyzer"):
                    names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0 and node.module.startswith("stock_analyzer"):
                names.add(node.module)
    return names


def _transitive_stock_analyzer_imports(root_module: str) -> set[str]:
    visited: set[str] = set()
    frontier = [root_module]
    while frontier:
        current = frontier.pop()
        if current in visited:
            continue
        visited.add(current)
        frontier.extend(_direct_stock_analyzer_imports(current) - visited)
    visited.discard(root_module)
    return visited


def test_decision_time_modules_exist_and_are_parseable():
    """Sanity check on the test's own fixture list -- if a module gets renamed,
    this fails loudly instead of the real checks below silently checking nothing
    (a missing file resolves to an empty import set, which would otherwise pass
    trivially and mask the module having moved)."""

    for module_name in DECISION_TIME_MODULES:
        path = _module_name_to_path(module_name)
        assert path is not None, f"{module_name} did not resolve to a file -- has it moved or been renamed?"


def test_no_decision_time_module_directly_imports_diagnostics():
    for module_name in DECISION_TIME_MODULES:
        direct = _direct_stock_analyzer_imports(module_name)
        offending = {m for m in direct if m.startswith(_DIAGNOSTICS_PREFIX)}
        assert offending == set(), f"{module_name} directly imports diagnostics module(s): {offending}"


def test_no_decision_time_module_transitively_imports_diagnostics():
    for module_name in DECISION_TIME_MODULES:
        reachable = _transitive_stock_analyzer_imports(module_name)
        offending = {m for m in reachable if m.startswith(_DIAGNOSTICS_PREFIX)}
        assert offending == set(), (
            f"{module_name} transitively imports diagnostics module(s) {offending} -- violates "
            "Section 26's import-isolation invariant: decision-time code must never be able to "
            "reach post-hoc outcome computations, even indirectly."
        )


def test_diagnostics_package_itself_does_not_import_any_decision_time_module():
    """The converse direction is fine (diagnostics reads decision-time facts from
    the database, not from importing the service code) -- but diagnostics.py
    itself should not import the service classes either, since it only needs
    already-persisted data, never live service behavior."""

    diagnostics_dir = REPO_ROOT / "stock_analyzer" / "sandbox" / "exp005" / "diagnostics"
    decision_time_set = set(DECISION_TIME_MODULES)
    for py_file in diagnostics_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                assert node.module not in decision_time_set, (
                    f"{py_file} imports decision-time module {node.module!r} -- diagnostics must "
                    "only read already-persisted data, never import service/decision code."
                )
