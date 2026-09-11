"""Pydantic models for the strategy-as-config contract (plan §5, §6).

Every strategy is DATA: universe → entry_conditions → exit_logic → risk → reentry.
These models are the validator for every JSONB row in `strategy_configs`, both at
seed-generation time and whenever the engine loads a config.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.registry import DETECTOR_NAMES, EXIT_NAMES


class Provenance(StrEnum):
    pdf_canonical = "pdf_canonical"
    interview_supplement = "interview_supplement"
    own = "own"
    derived = "derived"


class Style(StrEnum):
    scalp = "scalp"
    day = "day"
    swing = "swing"


class AutomationMode(StrEnum):
    full_auto = "full_auto"
    semi_auto = "semi_auto"


class StrategyStatus(StrEnum):
    draft = "draft"
    backtesting = "backtesting"
    paper = "paper"
    live = "live"
    retired = "retired"


class Direction(StrEnum):
    long = "long"
    short = "short"
    both = "both"


# ---------- universe ----------

class ScreenerUniverse(BaseModel):
    type: Literal["screener"]
    source: Literal["alpaca", "finviz_elite", "fmp"] = "alpaca"  # alpaca = free assets + SIP daily stats (decision #16)
    params: dict[str, Any]


class WatchlistUniverse(BaseModel):
    type: Literal["watchlist"]
    symbols: list[str] = Field(min_length=1)
    user_editable: bool = True

    @field_validator("symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.upper() for s in v]


Universe = ScreenerUniverse | WatchlistUniverse


# ---------- entry conditions ----------

class Condition(BaseModel):
    """One detector evaluation. `op`/`value` compare the detector output to a threshold.
    `field` selects a sub-output when the detector returns several (e.g. volume_profile.poc).

    `within_bars` makes a condition a SETUP rather than a trigger: it is satisfied if it held on the
    current bar or on any of the previous `within_bars - 1`. Multi-stage patterns need this. S4's quad
    -under-20 and its divergence trigger can never both be true on one bar — the trigger is the 9-3
    crossing UP through 20, which is exactly when the quad stops being under it — so expressing both as
    same-bar gates silently guarantees zero signals forever. It only means anything where bars are
    walked in order (the backtester and the Phase 7 live detector loop); the 08:00 screener evaluates a
    single moment and ignores it.
    """

    detector: str
    params: dict[str, Any] = Field(default_factory=dict)
    field: str | None = None
    op: Literal[">", ">=", "<", "<=", "==", "true", "false", "between", "in"] = "true"
    value: Any = None
    within_bars: int | None = Field(None, ge=1)
    label: str | None = None

    @field_validator("detector")
    @classmethod
    def _known_detector(cls, v: str) -> str:
        if v not in DETECTOR_NAMES:
            raise ValueError(f"unknown detector '{v}' — add it to app/registry.py first")
        return v


class EntryConditions(BaseModel):
    """`all` must pass (hard gates); `soft` contribute to score/ranking; `human_confirm`
    lists what the user checks visually on semi_auto strategies (not code).
    `hermes_gate` names a prompt-template slug that must return pass (e.g. the one-sentence test).
    `stages` describes ordered multi-bar patterns (S4 three-stage divergence) in prose + detectors."""

    all: list[Condition] = Field(default_factory=list)
    soft: list[Condition] = Field(default_factory=list)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    human_confirm: list[str] = Field(default_factory=list)
    hermes_gate: str | None = None
    direction: Direction = Direction.long
    notes: str | None = None


# ---------- exit logic ----------

class ExitStep(BaseModel):
    plugin: str
    params: dict[str, Any] = Field(default_factory=dict)
    when: Literal["premarket", "regular", "always", "after_partial"] = "always"
    label: str | None = None

    @field_validator("plugin")
    @classmethod
    def _known_exit(cls, v: str) -> str:
        if v not in EXIT_NAMES:
            raise ValueError(f"unknown exit plugin '{v}' — add it to app/registry.py first")
        return v


# ---------- risk / reentry ----------

class Risk(BaseModel):
    sizing_rule: Literal["fixed_risk_pct", "fixed_notional", "fixed_shares"] = "fixed_risk_pct"
    risk_per_trade_pct: float = Field(0.5, gt=0, le=5)
    max_concurrent: int = Field(2, ge=1)
    daily_loss_limit_r: float = Field(2.0, gt=0)
    session: Literal["premarket", "regular", "premarket+regular", "swing"] = "regular"
    order_types: list[Literal["limit", "stop_limit"]] = Field(default_factory=lambda: ["limit"])
    spread_ceiling_pct: float | None = None  # None = Guardian global default applies
    notes: str | None = None


class Reentry(BaseModel):
    allowed: bool = False
    max_entries: int = Field(1, ge=1)
    cooldown_bars: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "Reentry":
        if not self.allowed and self.max_entries != 1:
            raise ValueError("max_entries must be 1 when reentry is not allowed")
        return self


# ---------- backtest ----------

class BacktestSpec(BaseModel):
    """How the backtester turns this strategy into trades (plan §13).

    Live, the exact entry price of an S1/S3 trade comes from a Hermes plan. A backtest cannot call
    Hermes and must not pretend to: it would be grading judgment with hindsight. So every backtestable
    strategy declares a DETERMINISTIC entry rule here, and every result reports which rule produced it.
    What the backtest grades is therefore the rule plus the exit chain — never the model.

    A strategy with no `backtest` block is reported "not backtestable" with the reason, never silently
    skipped or given a default that flatters it.
    """

    entry_rule: Literal["signal_bar_close", "next_bar_open", "premarket_high_break",
                        "opening_range_break", "prior_day_high_break"]
    entry_offset_pct: float = Field(0.0, ge=0, le=5)     # trigger this far beyond the level
    entry_window: str = "09:30-15:30"                    # ET; an entry may only trigger inside it
    opening_range_minutes: int = Field(30, ge=1, le=120)
    stop_rule: Literal["pattern_low", "atr_multiple", "percent", "entry_bar_low", "opening_range_opposite"] = "pattern_low"
    stop_atr_multiple: float = Field(1.0, gt=0)
    stop_pct: float = Field(2.0, gt=0)
    stop_lookback_bars: int = Field(3, ge=1)
    target_rule: Literal["r_multiple", "atr_multiple", "none"] = "r_multiple"
    target_r: float = Field(2.0, gt=0)
    target_atr_multiple: float = Field(1.0, gt=0)
    bar_timeframe: Literal["1Min", "5Min", "15Min", "1Hour", "1Day"] = "5Min"
    warmup_bars: int = Field(80, ge=0)                   # bars of context loaded before the window opens
    max_trades_per_day: int = Field(1, ge=1)
    max_symbols_per_day: int = Field(5, ge=1)            # the scanner caps the focus list; so does the replay
    notes: str = ""


# ---------- the strategy ----------

class StrategyConfig(BaseModel):
    code: str = Field(pattern=r"^S\d+$")
    name: str
    creator: str
    provenance: Provenance
    style: Style
    timeframe: str
    automation_mode: AutomationMode
    status: StrategyStatus = StrategyStatus.draft
    instruments: list[str] = Field(default_factory=list)
    frameworks: list[Literal["FA", "FB"]] = Field(default_factory=list)
    universe: Universe = Field(discriminator="type")
    entry_conditions: EntryConditions
    exit_logic: list[ExitStep] = Field(min_length=1)
    risk: Risk
    reentry: Reentry = Field(default_factory=Reentry)
    backtest: BacktestSpec | None = None
    prompt_template_slug: str | None = None
    library_file: str
    notes: str = ""

    @model_validator(mode="after")
    def _premarket_rules(self) -> "StrategyConfig":
        """Plan §3.4 / §12: pre-market sessions may only use limit orders and must carry a synthetic stop."""
        if "premarket" in self.risk.session:
            if self.risk.order_types != ["limit"]:
                raise ValueError(f"{self.code}: pre-market strategies must use limit orders only")
            if not any(e.plugin == "synthetic_premarket_stop" for e in self.exit_logic):
                raise ValueError(f"{self.code}: pre-market strategies must include synthetic_premarket_stop")
            if any(e.plugin == "bracket_oco" and e.when in ("premarket", "always") for e in self.exit_logic):
                raise ValueError(f"{self.code}: bracket_oco cannot be active pre-market (when must be 'regular')")
        return self


# ---------- frameworks, templates, settings ----------

class Framework(BaseModel):
    code: Literal["FA", "FB"]
    name: str
    creator: str
    description: str
    rules: dict[str, Any]
    library_file: str


class PromptTemplate(BaseModel):
    number: int
    slug: str
    name: str
    tier: Literal["local", "hosted"]
    model: str
    version: int = 1
    purpose: str
    system_prompt: str
    user_template: str
    output_schema: dict[str, Any]
    library_file: str
    active: bool = True


class PromotionCriteria(BaseModel):
    """Plan §20 defaults. Win rate is displayed, NEVER a gate."""

    require_positive_expectancy_after_costs: bool = True
    min_profit_factor: float = 1.5
    min_trades_day: int = 100
    min_trades_swing: int = 40
    max_drawdown_pct: float = 15.0
    must_beat_current_config_ab: bool = True
    min_paper_weeks_before_live: int = 4
    win_rate_is_gate: Literal[False] = False
