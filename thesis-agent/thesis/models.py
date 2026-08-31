from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Side(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class ThesisStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    FLATTENED = "flattened"
    REJECTED = "rejected"


class SpreadLeg(BaseModel):
    symbol: str
    side: str  # buy | sell
    position_intent: str
    ratio_qty: int = 1


class Structure(BaseModel):
    kind: str = "debit_vertical"
    underlying: str
    long_symbol: str
    short_symbol: str | None = None
    expiration: str
    long_strike: float
    short_strike: float | None = None
    dte: int
    debit_limit: float
    qty: int
    max_loss_usd: float
    avg_dollar_volume_20d: float | None = None
    min_avg_dollar_volume: float | None = None
    max_option_bid_ask_pct: float | None = None
    long_bid_ask_pct: float | None = None
    short_bid_ask_pct: float | None = None
    legs: list[SpreadLeg]


class OrderSnapshot(BaseModel):
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_id: str
    client_order_id: str = ""
    status: str
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    filled_at: datetime | None = None
    canceled_at: datetime | None = None
    expired_at: datetime | None = None
    failed_at: datetime | None = None
    qty: float = 0.0
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    limit_price: float | None = None
    legs: list[dict[str, Any]] = Field(default_factory=list)


class PositionSnapshot(BaseModel):
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    side: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    cost_basis: float
    unrealized_pl: float
    unrealized_pl_pct: float


class PerformanceSnapshot(BaseModel):
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "Alpaca paper account"
    starting_equity: float
    equity: float
    last_equity: float
    cash: float
    buying_power: float
    options_buying_power: float
    total_pl: float
    total_return_pct: float
    realized_pl: float
    unrealized_pl: float
    fees: float
    reconciliation_delta: float
    fill_count: int


class MonitoringSnapshot(BaseModel):
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    attribution_status: str = "unverified"
    entry_status: str
    position_status: str
    open_leg_count: int = 0
    expected_leg_count: int = 0
    market_value: float = 0.0
    unrealized_pl: float = 0.0
    exit_status: str
    exit_reason: str


class Thesis(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    underlying: str
    side: Side
    regime: str
    setup: str
    invalidation: str
    horizon: str
    expected_move_pct: float
    iv_note: str
    conviction: float  # 0..1
    status: ThesisStatus = ThesisStatus.DRAFT
    structure: Structure | None = None
    order_id: str | None = None
    mcp_trace: list[dict[str, Any]] = Field(default_factory=list)
    cli_commands: list[str] = Field(default_factory=list)
    notes: str = ""
    decision: str = ""  # no_trade | rejected | submitted | blocked
    gates: list[dict[str, Any]] = Field(default_factory=list)
    tool_path: str = ""  # mcp | legacy cli | none
    snapshots: list[dict[str, Any]] = Field(default_factory=list)
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)
    order_status: str = ""
    order_submitted_at: datetime | None = None
    order_filled_at: datetime | None = None
    order_filled_qty: float = 0.0
    order_filled_avg_price: float | None = None
    monitoring: MonitoringSnapshot | None = None
    exit_order_id: str | None = None
    exit_status: str = "not_applicable"
    exit_reason: str = ""
    exit_at: datetime | None = None
