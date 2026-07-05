from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateScanRequest(BaseModel):
    target_pct: float = Field(default=5.0, gt=0, le=50)
    stop_loss_pct: float = Field(default=2.0, gt=0, le=50)
    holding_days: int = Field(default=5, ge=1, le=20)
    probability_threshold: float = Field(default=65.0, ge=0, le=100)
    max_candidates: int = Field(default=20, ge=1, le=100)
    initial_capital: float = Field(default=10_000.0, gt=0)
    min_avg_volume: float = Field(default=0.0, ge=0)
    min_avg_turnover: float = Field(default=10_000_000.0, ge=0)


class BacktestRequest(BaseModel):
    start_date: str
    end_date: str
    initial_capital: float = Field(default=10_000.0, gt=0)
    target_pct: float = Field(default=5.0, gt=0, le=50)
    stop_loss_pct: float = Field(default=2.0, gt=0, le=50)
    holding_days: int = Field(default=5, ge=1, le=20)
    probability_threshold: float = Field(default=65.0, ge=0, le=100)
    max_trades: int = Field(default=200, ge=1, le=2000)
    cost_assumption_bps: float = Field(default=30.0, ge=0)
    slippage_bps: float = Field(default=10.0, ge=0)
    diversify: bool = False
    max_concurrent_trades: int = Field(default=5, ge=1, le=50)


class ProjectionRequest(BaseModel):
    initial_capital: float = Field(default=10_000.0, gt=0)
    target_pct: float = Field(default=5.0, gt=0, le=50)
    stop_loss_pct: float = Field(default=2.0, gt=0, le=50)
    number_of_trades: int = Field(default=200, ge=1, le=5000)
    expected_win_rate: float = Field(default=70.0, ge=0, le=100)
    cost_per_trade_pct: float = Field(default=0.3, ge=0, le=10)


class StartPaperTradeRequest(BaseModel):
    symbol: str
    signal_id: str | None = None
    entry_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    stop_loss_price: float = Field(gt=0)
    capital_before: float = Field(default=10_000.0, gt=0)


class ClosePaperTradeRequest(BaseModel):
    exit_price: float = Field(gt=0)
    exit_reason: str = "manual_close"
