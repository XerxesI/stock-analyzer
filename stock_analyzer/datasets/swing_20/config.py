"""Frozen configuration for the SWING_20 dataset audit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseConfig:
    """Eligibility filters for the date-specific SWING_20 universe."""

    minimum_price: float = 5.0
    minimum_adv20: float = 5_000_000.0
    minimum_history_days: int = 250


@dataclass(frozen=True)
class LabelConfig:
    """SWING_20 target definition."""

    target_return: float = 0.20
    horizon_days: int = 20
    fixed_stop: float = -0.08
    atr_stop_multiple: float = 1.0


@dataclass(frozen=True)
class SplitConfig:
    """Temporal split fractions."""

    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    locked_test_fraction: float = 0.20

    def validate(self) -> None:
        total = self.train_fraction + self.validation_fraction + self.locked_test_fraction
        if abs(total - 1.0) > 1e-9:
            raise ValueError("Temporal split fractions must sum to 1.0.")
        if min(self.train_fraction, self.validation_fraction, self.locked_test_fraction) <= 0:
            raise ValueError("Temporal split fractions must all be positive.")


@dataclass(frozen=True)
class Swing20Config:
    """Top-level SWING_20 audit configuration."""

    strategy: str = "SWING_20"
    spec_version: str = "1.0"
    universe: UniverseConfig = UniverseConfig()
    label: LabelConfig = LabelConfig()
    splits: SplitConfig = SplitConfig()

