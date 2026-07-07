"""Freeze the C1 (Support + Bear-Conditional Momentum) combination formula's
parameters, using the ALREADY-SPENT dev data (phase2_retest_obs.csv = train + holdout
combined). This is legitimate: fitting a scaler's parameters on already-used data is
normal practice (like fitting a StandardScaler on a training set), as long as the
FROZEN result is then applied unchanged to genuinely new data (the Locked Test set)
without re-fitting.

Frozen formula (per ChatGPT's Variant A/B, agreed identical):
    z_support  = (support_signal  - mu_support)  / sigma_support
    z_momentum = (momentum_signal - mu_momentum) / sigma_momentum
    is_bear    = 1 if regime in {"Bear_High", "Bear_Normal"} else 0
    C1_score   = z_support + z_momentum * is_bear

Output: artifacts/reports/frozen_c1_params.json - loaded (never recomputed) by
locked_test.py.

Usage:
    python -m stock_analyzer.evaluation.freeze_c1_params
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "phase2_retest_obs.csv"
_FROZEN_PARAMS_PATH = _ARTIFACTS_REPORTS / "frozen_c1_params.json"

PRIMARY_HORIZON = 20  # matches M1/S1/C1 pre-registration


def main() -> None:
    print(f"loading dev data (train+holdout, already spent) from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON]
    print(f"  {len(obs_h)} rows at horizon={PRIMARY_HORIZON}d", flush=True)

    params = {
        "mu_support": float(obs_h["support_signal"].mean()),
        "sigma_support": float(obs_h["support_signal"].std(ddof=0)),
        "mu_momentum": float(obs_h["momentum_signal"].mean()),
        "sigma_momentum": float(obs_h["momentum_signal"].std(ddof=0)),
        "bear_regimes": ["Bear_High", "Bear_Normal"],
        "primary_horizon": PRIMARY_HORIZON,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "source": str(_OBS_PATH),
        "n_observations_used_for_fitting": len(obs_h),
        "note": (
            "These parameters are FROZEN. Do not recompute from Locked Test data. "
            "locked_test.py must load this file as-is."
        ),
    }

    _FROZEN_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_FROZEN_PARAMS_PATH, "w") as f:
        json.dump(params, f, indent=2)

    print(f"\nfrozen parameters saved to {_FROZEN_PARAMS_PATH}:")
    for k, v in params.items():
        print(f"  {k}: {v}")
    print("\nThis file should NOT be regenerated after this point for the current")
    print("Locked Test round. If you re-run this script, you are re-fitting on")
    print("(by then) potentially contaminated data - only do this deliberately,")
    print("e.g. when starting a genuinely new research cycle.")


if __name__ == "__main__":
    main()
