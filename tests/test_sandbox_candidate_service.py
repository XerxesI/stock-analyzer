from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

import stock_analyzer.sandbox.application.candidate_service as candidate_service_module
from stock_analyzer.sandbox.application.candidate_service import (
    CandidateService,
    MISSING_MARKET_DATA,
    compute_max_entry_price,
)
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import VirtualPosition
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

CONFIG = SandboxConfig()


# --------------------------------------------------------------- entry price


def test_max_entry_price_close_cap_binds_when_lower():
    # 2% of 100 = 102. ATR cap: 100 + 0.25*20 = 105. Close cap is lower -> binds.
    result = compute_max_entry_price(signal_close=100.0, atr14=20.0, config=CONFIG)
    assert result == pytest.approx(102.0)


def test_max_entry_price_atr_cap_binds_when_lower():
    # 2% of 100 = 102. ATR cap: 100 + 0.25*4 = 101. ATR cap is lower -> binds.
    result = compute_max_entry_price(signal_close=100.0, atr14=4.0, config=CONFIG)
    assert result == pytest.approx(101.0)


def test_max_entry_price_missing_atr_returns_none():
    assert compute_max_entry_price(signal_close=100.0, atr14=None, config=CONFIG) is None
    assert compute_max_entry_price(signal_close=100.0, atr14=float("nan"), config=CONFIG) is None


def test_max_entry_price_invalid_close_returns_none():
    assert compute_max_entry_price(signal_close=None, atr14=1.0, config=CONFIG) is None
    assert compute_max_entry_price(signal_close=0.0, atr14=1.0, config=CONFIG) is None
    assert compute_max_entry_price(signal_close=-5.0, atr14=1.0, config=CONFIG) is None


def test_max_entry_price_rounds_to_four_decimals():
    # Large ATR so the close-percentage cap is unambiguously the lower (binding) term.
    result = compute_max_entry_price(signal_close=33.333333, atr14=100.0, config=CONFIG)
    assert result == round(result, 4)
    assert result == pytest.approx(33.333333 * 1.02, abs=1e-4)


# --------------------------------------------------------------- candidate lifecycle


class FakePredictionAdapter:
    """Matches Model2PredictionAdapter's public interface without loading the real
    (gitignored, multi-hundred-MB) frozen training feature dataset."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.model_version = "fake-model-for-tests"
        self.fit_params = {
            "adv_edges": np.array([-np.inf, 15.0, 17.0, 19.0, 21.0, np.inf]),
            "adv_labels": ["adv_q1", "adv_q2", "adv_q3", "adv_q4", "adv_q5"],
        }

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        return pd.Series([self._scores.get(sym, 0.0) for sym in features_df.index], index=features_df.index)


class FakeUniverseProvider:
    def __init__(self, frames_by_date: dict[date, pd.DataFrame]) -> None:
        self._frames = frames_by_date

    def features_for_date(self, as_of_date: date) -> pd.DataFrame:
        return self._frames.get(as_of_date, pd.DataFrame()).copy()


def _universe_frame(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "adv20": [20_000_000.0] * len(symbols),
            "rvol_20": [1.0] * len(symbols),
            "rsi_14": [50.0] * len(symbols),
            "spy_trend": ["Bull"] * len(symbols),
            "spy_volatility_bucket": ["Normal"] * len(symbols),
        },
        index=pd.Index(symbols, name="symbol"),
    )


def _synthetic_prices(as_of_date: date, days: int = 30, close: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp(as_of_date), periods=days)
    closes = [close + i * 0.1 for i in range(days)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * days,
        },
        index=dates,
    )


@pytest.fixture
def repo() -> SandboxRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return SandboxRepository(conn)


def _make_service(repo: SandboxRepository, as_of: date, symbols: list[str], scores: dict[str, float], monkeypatch, missing_symbols: set[str] | None = None) -> CandidateService:
    missing_symbols = missing_symbols or set()

    def fake_fetch_as_of(symbol: str, fetch_date: date, period: str = "2y") -> pd.DataFrame:
        if symbol in missing_symbols:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return _synthetic_prices(fetch_date)

    monkeypatch.setattr(candidate_service_module, "fetch_as_of", fake_fetch_as_of)

    universe = FakeUniverseProvider({as_of: _universe_frame(symbols)})
    adapter = FakePredictionAdapter(scores)
    return CandidateService(repo, adapter, universe, CONFIG)


def test_shadow_top10_retained_even_when_fewer_actionable(repo: SandboxRepository, monkeypatch):
    as_of = date(2026, 6, 15)
    symbols = [f"SYM{i}" for i in range(15)]
    scores = {sym: float(len(symbols) - i) for i, sym in enumerate(symbols)}  # SYM0 highest
    service = _make_service(repo, as_of, symbols, scores, monkeypatch)

    result = service.generate_candidates(as_of)

    assert len(result.shadow_top10) == 10
    assert [c.symbol for c in result.shadow_top10] == symbols[:10]
    assert len(result.actionable) == CONFIG.max_actionable_candidates


def test_only_top_n_actionable_selected_in_rank_order(repo: SandboxRepository, monkeypatch):
    as_of = date(2026, 6, 15)
    symbols = [f"SYM{i}" for i in range(10)]
    scores = {sym: float(len(symbols) - i) for i, sym in enumerate(symbols)}
    service = _make_service(repo, as_of, symbols, scores, monkeypatch)

    result = service.generate_candidates(as_of)

    assert [c.symbol for c in result.actionable] == ["SYM0", "SYM1", "SYM2"]
    assert len(result.entry_orders) == 3


def test_already_open_symbol_is_excluded_from_actionable(repo: SandboxRepository, monkeypatch):
    as_of = date(2026, 6, 15)
    symbols = [f"SYM{i}" for i in range(5)]
    scores = {sym: float(len(symbols) - i) for i, sym in enumerate(symbols)}

    # SYM0 (rank 1) already has an OPEN position from a prior day -- must be skipped,
    # letting SYM1-3 fill the top-3 actionable slots instead. Real FK parent rows
    # (a prior candidate + order) are created first, exactly as production code would.
    prior_candidate = RankedCandidate(
        candidate_id=RankedCandidate.make_id(date(2026, 6, 9), "SYM0"),
        run_id=SandboxRun.make_id(date(2026, 6, 9), "generate-candidates"),
        as_of_date=date(2026, 6, 9),
        symbol="SYM0",
        daily_rank=1,
        model_score=0.9,
        signal_close=50.0,
        atr14=1.0,
        max_entry_price=51.0,
        shadow_top10=True,
        actionable=True,
        exclusion_reason=None,
        adv_quintile="adv_q1",
        market_regime="Bull_Normal",
    )
    repo.create_run(
        SandboxRun(
            run_id=prior_candidate.run_id,
            as_of_date=date(2026, 6, 9),
            command="generate-candidates",
            started_at=datetime.now(timezone.utc),
            configuration_hash="prior-config",
        )
    )
    repo.insert_ranked_candidate(prior_candidate)
    prior_order = EntryOrder(
        order_id=EntryOrder.make_id(prior_candidate.candidate_id),
        candidate_id=prior_candidate.candidate_id,
        symbol="SYM0",
        signal_date=date(2026, 6, 9),
        created_date=date(2026, 6, 9),
        valid_until=date(2026, 6, 11),
        max_entry_price=51.0,
        status="FILLED",
    )
    repo.create_entry_order(prior_order)
    repo.create_position(
        VirtualPosition(
            position_id=VirtualPosition.make_id("SYM0", date(2026, 6, 10)),
            symbol="SYM0",
            candidate_id=prior_candidate.candidate_id,
            order_id=prior_order.order_id,
            signal_date=date(2026, 6, 9),
            entry_date=date(2026, 6, 10),
            entry_price=50.0,
            quantity=20.0,
            initial_rank=1,
            initial_model_score=0.9,
            signal_close=50.0,
            max_entry_price=51.0,
            initial_adv_quintile="adv_q1",
            initial_market_regime="Bull_Normal",
            target_price=60.0,
            planned_time_exit_date=date(2026, 7, 8),
        )
    )
    service = _make_service(repo, as_of, symbols, scores, monkeypatch)

    result = service.generate_candidates(as_of)

    assert "SYM0" not in [c.symbol for c in result.actionable]
    assert [c.symbol for c in result.actionable] == ["SYM1", "SYM2", "SYM3"]


def test_missing_market_data_excludes_symbol_with_reason(repo: SandboxRepository, monkeypatch):
    as_of = date(2026, 6, 15)
    symbols = [f"SYM{i}" for i in range(5)]
    scores = {sym: float(len(symbols) - i) for i, sym in enumerate(symbols)}
    service = _make_service(repo, as_of, symbols, scores, monkeypatch, missing_symbols={"SYM0"})

    result = service.generate_candidates(as_of)

    sym0 = next(c for c in result.shadow_top10 if c.symbol == "SYM0")
    assert sym0.actionable is False
    assert sym0.exclusion_reason == MISSING_MARKET_DATA
    assert sym0.shadow_top10 is True  # still retained in the shadow set
    assert "SYM0" not in [c.symbol for c in result.actionable]


def test_actionable_candidate_creates_pending_entry_order(repo: SandboxRepository, monkeypatch):
    as_of = date(2026, 6, 15)
    symbols = ["SYM0", "SYM1"]
    scores = {"SYM0": 1.0, "SYM1": 0.5}
    service = _make_service(repo, as_of, symbols, scores, monkeypatch)

    result = service.generate_candidates(as_of)

    assert len(result.entry_orders) == 2
    for order in result.entry_orders:
        assert order.status == "PENDING"
        assert order.signal_date == as_of
        assert order.valid_until > as_of


# --------------------------------------------------------------- frozen model protection


def test_frozen_model2_feature_list_matches_current_make_design_matrix():
    """Regression guard: if scripts/train_swing_20_logistic_baseline.py's Model 2
    design matrix ever changes, this must fail loudly rather than silently score
    with a different model than the one EXP-003 Locked-Test-validated. Uses a small
    synthetic frame so this test needs no large/gitignored data file."""

    from scripts.train_swing_20_logistic_baseline import fit_on_train, make_design_matrix
    from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import FROZEN_MODEL2_FEATURE_LIST

    rng = np.random.default_rng(0)
    n = 200
    dates = pd.bdate_range("2024-01-02", periods=n)
    df = pd.DataFrame(
        {
            "date": dates,
            "adv20": rng.uniform(1e6, 5e7, size=n),
            "rvol_20": rng.uniform(0.3, 2.0, size=n),
            "rsi_14": rng.uniform(20, 80, size=n),
            "spy_trend": rng.choice(["Bull", "Bear"], size=n),
            "spy_volatility_bucket": rng.choice(["Normal", "High"], size=n),
            "target_20pct_20d": rng.integers(0, 2, size=n).astype(bool),
        }
    )
    fit = fit_on_train(df)
    X = make_design_matrix(df, fit, "model2")

    assert tuple(X.columns) == FROZEN_MODEL2_FEATURE_LIST
