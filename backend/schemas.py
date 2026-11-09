"""
Pydantic schemas for NiftyDebateAI.

Covers market data, agent outputs, judge decisions, paper trades,
and all API response envelopes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator
from time_utils import (
    IST,
    format_ist_display,
    ist_now,
    to_ist,
    to_ist_iso,
)


# ══════════════════════════════════════════════════════════════════════════════
# Market Data
# ══════════════════════════════════════════════════════════════════════════════

class MarketSnapshot(BaseModel):
    """Point-in-time market data fed into the debate agents."""

    spot_price: float = Field(..., description="Current NIFTY spot price")
    ema_20: float = Field(..., description="20-period EMA")
    ema_50: float = Field(..., description="50-period EMA")
    vwap: float = Field(..., description="Volume Weighted Average Price")
    rsi: float = Field(..., description="14-period RSI")
    volume: float = Field(..., description="Current period volume")
    atr: float = Field(..., description="14-period Average True Range")
    vix: float = Field(..., description="India VIX")
    pcr: float = Field(..., description="Put-Call Ratio")
    ce_oi_change: float = Field(..., description="Change in Call OI")
    pe_oi_change: float = Field(..., description="Change in Put OI")
    higher_tf_trend: str = Field(
        ...,
        description="Higher time-frame trend: BULLISH / BEARISH / NEUTRAL",
    )
    timestamp: datetime = Field(
        default_factory=ist_now,
        description="Snapshot timestamp in Indian Standard Time (IST, Asia/Kolkata)",
    )
    atm_strike: str | int | None = Field(default=None, description="Calculated ATM strike")
    nifty_high: float | None = Field(default=None, description="Day high")
    nifty_low: float | None = Field(default=None, description="Day low")
    nifty_open: float | None = Field(default=None, description="Day open")

    @field_validator("timestamp", mode="after")
    @classmethod
    def normalize_snapshot_timestamp(cls, v: datetime) -> datetime:
        return to_ist(v)

    @computed_field
    @property
    def timestamp_iso(self) -> str:
        return to_ist_iso(self.timestamp)

    @computed_field
    @property
    def timestamp_display(self) -> str:
        return format_ist_display(self.timestamp)


class ManualTradeRequest(BaseModel):
    symbol: str | None = Field(default="NIFTY", description="Index symbol: NIFTY or BANKNIFTY")
    strike: int
    option_type: Literal["CE", "PE"]
    lots: int = Field(default=1, ge=1, le=50)
    entry_price: float | None = None
    stop_loss: float | None = None
    target: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# Agent Outputs
# ══════════════════════════════════════════════════════════════════════════════

class BullAgentOutput(BaseModel):
    """Output produced by the Bull (bullish) agent."""

    bias: Literal["BULLISH"] = Field(default="BULLISH")
    confidence: int = Field(..., ge=1, le=10, description="Conviction 1-10")
    recommended_strike: str = Field(..., description="ATM or OTM strike label")
    arguments: list[str] = Field(
        ...,
        min_length=3,
        description="Comprehensive list of bullish arguments covering all technical and derivative angles",
    )
    key_levels: dict[str, Any] = Field(
        default_factory=dict,
        description="Support / resistance / breakout levels",
    )
    structured_arguments: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Categorized arguments across technicals, option chain, Greeks, and price action",
    )

    @computed_field
    @property
    def strike(self) -> str:
        return self.recommended_strike

    @computed_field
    @property
    def confidence_score(self) -> int:
        return self.confidence

    @computed_field
    @property
    def key_arguments(self) -> list[str]:
        return self.arguments


class BearAgentOutput(BaseModel):
    """Output produced by the Bear (bearish) agent."""

    bias: Literal["BEARISH"] = Field(default="BEARISH")
    confidence: int = Field(..., ge=1, le=10, description="Conviction 1-10")
    recommended_strike: str = Field(..., description="ATM or OTM strike label")
    arguments: list[str] = Field(
        ...,
        min_length=3,
        description="Comprehensive list of bearish arguments covering all technical and derivative angles",
    )
    key_levels: dict[str, Any] = Field(
        default_factory=dict,
        description="Support / resistance / breakdown levels",
    )
    structured_arguments: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Categorized arguments across technicals, option chain, Greeks, and price action",
    )

    @computed_field
    @property
    def strike(self) -> str:
        return self.recommended_strike

    @computed_field
    @property
    def confidence_score(self) -> int:
        return self.confidence

    @computed_field
    @property
    def key_arguments(self) -> list[str]:
        return self.arguments


class JudgeDecision(BaseModel):
    """Final decision by the impartial Judge agent."""

    decision: Literal["BUY_CE", "BUY_PE", "NO_TRADE"] = Field(
        ...,
        description="Trade action to take",
    )
    selected_strike: str | None = Field(
        None,
        description="Chosen option strike (None for NO_TRADE)",
    )
    entry_price_estimate: float = Field(
        ...,
        description="Estimated option premium entry price",
    )
    stop_loss: float = Field(..., description="Stop-loss price for the option")
    target: float = Field(..., description="Target price for the option")
    risk_reward_ratio: str = Field(
        ...,
        description="Formatted risk:reward e.g. '1:2.0'",
    )
    winning_argument_summary: str = Field(
        ...,
        description="Summary of the winning side's argument",
    )
    risk_warning: str = Field(
        ...,
        description="Key risk factor for this trade / no-trade",
    )

    @computed_field
    @property
    def verdict(self) -> str:
        return self.decision

    @computed_field
    @property
    def strike(self) -> str | None:
        return self.selected_strike

    @computed_field
    @property
    def recommended_strike(self) -> str | None:
        return self.selected_strike

    @computed_field
    @property
    def entry_price(self) -> float:
        return self.entry_price_estimate

    @computed_field
    @property
    def entry(self) -> float:
        return self.entry_price_estimate

    @computed_field
    @property
    def sl(self) -> float:
        return self.stop_loss

    @computed_field
    @property
    def target_price(self) -> float:
        return self.target

    @computed_field
    @property
    def risk_reward(self) -> str:
        if self.risk_reward_ratio and self.risk_reward_ratio.startswith("1:"):
            return self.risk_reward_ratio[2:]
        return self.risk_reward_ratio

    @computed_field
    @property
    def reasoning(self) -> str:
        return self.winning_argument_summary

    @computed_field
    @property
    def winning_argument(self) -> str:
        return self.winning_argument_summary

    @computed_field
    @property
    def summary(self) -> str:
        return self.winning_argument_summary

    @computed_field
    @property
    def rationale(self) -> str:
        return self.winning_argument_summary


# ══════════════════════════════════════════════════════════════════════════════
# Debate Session (Composite)
# ══════════════════════════════════════════════════════════════════════════════

class DebateSession(BaseModel):
    """Full record of a single debate round."""

    id: int | None = Field(None, description="Database primary key")
    timestamp: datetime = Field(default_factory=ist_now)
    index_symbol: str = Field(default="NIFTY", description="Underlying index: NIFTY or BANKNIFTY")
    market_snapshot: MarketSnapshot
    bull_output: BullAgentOutput
    bear_output: BearAgentOutput
    judge_decision: JudgeDecision

    @field_validator("timestamp", mode="after")
    @classmethod
    def normalize_debate_timestamp(cls, v: datetime) -> datetime:
        return to_ist(v)

    @computed_field
    @property
    def symbol(self) -> str:
        return self.index_symbol

    @computed_field
    @property
    def bull(self) -> BullAgentOutput:
        return self.bull_output

    @computed_field
    @property
    def bear(self) -> BearAgentOutput:
        return self.bear_output

    @computed_field
    @property
    def judge(self) -> JudgeDecision:
        return self.judge_decision

    @computed_field
    @property
    def verdict(self) -> str:
        return self.judge_decision.decision

    @computed_field
    @property
    def spot_price(self) -> float:
        return self.market_snapshot.spot_price

    @computed_field
    @property
    def nifty_spot(self) -> float:
        return self.market_snapshot.spot_price

    @computed_field
    @property
    def created_at(self) -> str:
        return to_ist_iso(self.timestamp)

    @computed_field
    @property
    def timestamp_display(self) -> str:
        return format_ist_display(self.timestamp)


# ══════════════════════════════════════════════════════════════════════════════
# Paper Trade
# ══════════════════════════════════════════════════════════════════════════════

class PaperTrade(BaseModel):
    """Schema for a paper (simulated) trade."""

    id: int | None = None
    debate_id: int
    option_symbol: str
    index_symbol: str = Field(default="NIFTY", description="Underlying index: NIFTY or BANKNIFTY")
    trade_type: str  # BUY_CE / BUY_PE
    entry_price: float
    exit_price: float | None = None
    stop_loss: float
    target: float
    pnl: float | None = None
    status: str = "OPEN"  # OPEN / CLOSED / STOPPED_OUT / TARGET_HIT
    opened_at: datetime = Field(default_factory=ist_now)
    closed_at: datetime | None = None
    quantity: int = 25  # Default to 1 lot of Nifty
    trade_source: str = "AI_DEBATE"

    model_config = ConfigDict(from_attributes=True)

    @field_validator("opened_at", mode="after")
    @classmethod
    def normalize_trade_opened_at(cls, v: datetime) -> datetime:
        return to_ist(v)

    @field_validator("closed_at", mode="after")
    @classmethod
    def normalize_trade_closed_at(cls, v: datetime | None) -> datetime | None:
        return to_ist(v) if v is not None else None

    @computed_field
    @property
    def lots(self) -> int:
        sym = (getattr(self, "index_symbol", None) or self.option_symbol or "").upper()
        lot_size = 15 if "BANK" in sym else 25
        return max(1, self.quantity // lot_size)

    @computed_field
    @property
    def total_amount(self) -> float:
        """Total investment amount: entry_price * quantity."""
        return round(self.entry_price * self.quantity, 2)

    @computed_field
    @property
    def invested_amount(self) -> float:
        return round(self.entry_price * self.quantity, 2)

    @computed_field
    @property
    def current_amount(self) -> float:
        exit_or_entry = self.exit_price if self.exit_price is not None else self.entry_price
        return round(exit_or_entry * self.quantity, 2)

    @computed_field
    @property
    def pnl_percent(self) -> float:
        if self.entry_price > 0 and self.pnl is not None:
            return round((self.pnl / (self.entry_price * self.quantity)) * 100, 2)
        return 0.0

    @computed_field
    @property
    def symbol(self) -> str:
        return self.option_symbol

    @computed_field
    @property
    def trading_symbol(self) -> str:
        return self.option_symbol

    @computed_field
    @property
    def created_at(self) -> str:
        return to_ist_iso(self.opened_at)

    @computed_field
    @property
    def opened_at_display(self) -> str:
        return format_ist_display(self.opened_at)

    @computed_field
    @property
    def closed_at_display(self) -> str | None:
        return format_ist_display(self.closed_at) if self.closed_at else None

    @computed_field
    @property
    def timestamp(self) -> str:
        return self.created_at

    @computed_field
    @property
    def entry_time(self) -> str:
        return self.created_at

    @computed_field
    @property
    def entry(self) -> float:
        return self.entry_price

    @computed_field
    @property
    def exit(self) -> float | None:
        return self.exit_price

    @computed_field
    @property
    def sl(self) -> float:
        return self.stop_loss

    @computed_field
    @property
    def target_price(self) -> float:
        return self.target

    @computed_field
    @property
    def unrealized_pnl(self) -> float | None:
        return self.pnl if self.status == "OPEN" else None

    @computed_field
    @property
    def realized_pnl(self) -> float | None:
        return self.pnl if self.status != "OPEN" else None

    @computed_field
    @property
    def current_pnl(self) -> float | None:
        return self.pnl


# ══════════════════════════════════════════════════════════════════════════════
# API Response Envelopes
# ══════════════════════════════════════════════════════════════════════════════

class StatusResponse(BaseModel):
    """System-level status response."""

    status: str = "ok"
    openalgo_connected: bool = False
    analyzer_mode: bool = False
    spot_price: float | None = None
    scheduler_running: bool = False
    database_ok: bool = False
    market_open: bool = False
    message: str = ""

    @computed_field
    @property
    def sandbox_mode(self) -> bool:
        return self.analyzer_mode

    @computed_field
    @property
    def nifty_spot(self) -> float | None:
        return self.spot_price


class DebateResponse(BaseModel):
    """Wrapper for a single debate result."""

    success: bool = True
    debate: DebateSession
    trade_created: bool = False
    message: str = ""


class DebateHistoryResponse(BaseModel):
    """Paginated list of past debates."""

    total: int
    limit: int
    offset: int
    debates: list[DebateSession]


class TradeListResponse(BaseModel):
    """Wrapper for a list of paper trades."""

    success: bool = True
    trades: list[PaperTrade]
    count: int = 0


class TradeSummaryResponse(BaseModel):
    """Aggregate paper-trading statistics."""

    total_trades: int = 0
    open_trades: int = 0
    closed_trades: int = 0
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0

    @computed_field
    @property
    def wins(self) -> int:
        return self.win_count

    @computed_field
    @property
    def losses(self) -> int:
        return self.loss_count

    @computed_field
    @property
    def net_pnl(self) -> float:
        return self.total_pnl


class SchedulerStatusResponse(BaseModel):
    """Scheduler state."""

    running: bool = False
    is_running: bool = False
    paused: bool = False
    is_paused: bool = False
    state: str = "STOPPED"
    next_run: str | None = None
    next_run_time: str | None = None
    interval_minutes: int = 5
    market_open: bool = False
    message: str = ""

    @field_validator("next_run", "next_run_time", mode="before")
    @classmethod
    def normalize_next_run(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, (datetime, str)):
            return to_ist_iso(v)
        return str(v)

    @computed_field
    @property
    def next_run_display(self) -> str | None:
        if not self.next_run_time:
            return None
        return format_ist_display(self.next_run_time)


# Canonical alias
SchedulerStatus = SchedulerStatusResponse

