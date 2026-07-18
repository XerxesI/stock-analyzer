"""Recommendation Sandbox (MVP 2).

Turns the frozen Model 2 daily ranking into deterministic, auditable daily
BUY/HOLD/SELL/SKIP recommendations and simulated virtual positions. See
docs/02_mvp/MVP_2_Recommendation_Sandbox_Specification.md for the full specification.

This package never modifies Model 2 (scripts/train_swing_20_logistic_baseline.py) or
re-opens the Locked Test (docs/09_experiments/EXP-003_SWING20_Locked_Test.md).
"""
