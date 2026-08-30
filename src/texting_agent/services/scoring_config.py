"""Loads and validates `config/scoring.yaml`.

Validation is not decoration: weights that do not sum to 1.0 or a threshold order
that is inverted would produce scores that look plausible and rank wrongly. The
loader refuses both, at startup, with the field named.
"""

import functools
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from texting_agent.config import settings

SIGNALS = (
    "recency", "purchase_gap", "engagement", "purchase_decline",
    "login_lapse", "cart_abandon", "support",
)


class Weights(BaseModel):
    recency: float = Field(ge=0)
    purchase_gap: float = Field(ge=0)
    engagement: float = Field(ge=0)
    purchase_decline: float = Field(ge=0)
    login_lapse: float = Field(ge=0)
    cart_abandon: float = Field(ge=0)
    support: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self):
        total = sum(getattr(self, s) for s in SIGNALS)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        return self

    @model_validator(mode="after")
    def _trend_share(self):
        """FR-04a: trend signals carry at least 30% of the weight."""
        trend = self.engagement + self.purchase_decline
        if trend < 0.30:
            raise ValueError(f"trend signals must carry >= 0.30 of weight, got {trend}")
        return self

    def __getitem__(self, signal: str) -> float:
        return getattr(self, signal)


class Normalisation(BaseModel):
    inactivity_horizon_days: int = Field(gt=0)
    expected_interval_floor_days: int = Field(gt=0)
    baseline_email_open_rate: float = Field(gt=0, le=1)
    baseline_sms_response_rate: float = Field(gt=0, le=1)
    abandon_cap: int = Field(gt=0)
    support_cap: int = Field(gt=0)


class Thresholds(BaseModel):
    critical: float = Field(gt=0, le=1)
    high: float = Field(gt=0, le=1)
    medium: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _ordered(self):
        if not self.critical > self.high > self.medium:
            raise ValueError("thresholds must satisfy critical > high > medium")
        return self


class ValueConfig(BaseModel):
    vip_pct: float = Field(gt=0, lt=1)
    high_pct: float = Field(gt=0, lt=1)
    standard_pct: float = Field(gt=0, lt=1)
    min_purchasers_for_tiering: int = Field(gt=0)

    @model_validator(mode="after")
    def _bands_leave_room(self):
        total = self.vip_pct + self.high_pct + self.standard_pct
        if total >= 1.0:
            raise ValueError(f"value bands must leave room for LOW_VALUE, got {total}")
        return self


class DataQuality(BaseModel):
    freshness_window_days: int = Field(gt=0)


class ScoringConfig(BaseModel):
    version: int
    weights: Weights
    normalisation: Normalisation
    thresholds: Thresholds
    reason_threshold: float = Field(ge=0, le=1)
    min_signals_required: int = Field(ge=0)
    value: ValueConfig
    data_quality: DataQuality


def load(path: Path | str | None = None) -> ScoringConfig:
    path = path or Path(settings.config_dir) / "scoring.yaml"
    return ScoringConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@functools.cache
def get() -> ScoringConfig:
    """Process-wide config. Tests that vary configuration call `load()` directly."""
    return load()
