from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from nse_dashboard.domain.snapshots import SnapshotRepository
from nse_dashboard.trading.portfolio import PaperPortfolio, PaperPosition, size_position


class PaperPortfolioService:
    strategy_version = "2.0.0"

    def __init__(self, snapshots: SnapshotRepository, starting_equity: float = 1_000_000) -> None:
        self.snapshots = snapshots
        loader = getattr(snapshots, "latest_paper_portfolio", lambda: None)
        stored = loader()
        self.portfolio = self._restore(stored) if stored else PaperPortfolio(
            starting_equity=starting_equity, cash=starting_equity, high_water_mark=starting_equity
        )

    def _restore(self, value: dict[str, Any]) -> PaperPortfolio:
        portfolio = PaperPortfolio(
            starting_equity=float(value["starting_equity"]), cash=float(value["cash"]),
            high_water_mark=float(value["high_water_mark"]),
            realized_pnl=float(value.get("realized_pnl", 0)),
            paused_until_month_review=bool(value.get("paused_until_month_review", False)),
        )
        for raw in value.get("positions", []):
            raw = dict(raw)
            raw["entry_date"] = date.fromisoformat(str(raw["entry_date"])[:10])
            position = PaperPosition(**raw)
            portfolio.positions[position.symbol] = position
        return portfolio

    def _snapshot(self) -> dict[str, Any]:
        summary = self.portfolio.summary()
        result = {
            "strategy_version": self.strategy_version,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "starting_equity": self.portfolio.starting_equity,
            "cash": self.portfolio.cash,
            "high_water_mark": self.portfolio.high_water_mark,
            "realized_pnl": self.portfolio.realized_pnl,
            "paused_until_month_review": self.portfolio.paused_until_month_review,
            "positions": summary["positions"],
            "summary": {key: value for key, value in summary.items() if key != "positions"},
        }
        saver = getattr(self.snapshots, "save_paper_portfolio", None)
        if saver is not None:
            saver(result)
        return result

    def get(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        result = self._snapshot()
        if prices:
            result["summary"] = {
                key: value for key, value in self.portfolio.summary(prices).items() if key != "positions"
            }
        return result

    def open_position(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload["symbol"]).upper()
        if symbol in self.portfolio.positions:
            raise ValueError("A paper position already exists for this symbol; averaging down is disabled")
        summary = self.portfolio.summary()
        if not summary["new_entries_allowed"]:
            raise ValueError("Portfolio drawdown circuit breaker blocks new entries")
        if len(self.portfolio.positions) >= 10:
            raise ValueError("Maximum of 10 concurrent positions reached")
        entry, stop = float(payload["entry_price"]), float(payload["initial_stop"])
        risk_pct = float(payload.get("risk_pct", 0.5)) * float(summary["risk_multiplier"])
        sizing = size_position(
            equity=float(summary["equity"]), entry=entry, stop=stop, risk_pct=risk_pct,
            estimated_cost_bps=float(payload.get("estimated_cost_bps", 25)),
        )
        quantity = int(payload.get("quantity") or sizing["quantity"])
        if quantity <= 0 or quantity * entry > self.portfolio.cash:
            raise ValueError("Position quantity is not affordable")
        sector = str(payload.get("sector", "Unknown"))
        maximum_exposure_pct = min(80.0, float(payload.get("maximum_exposure_pct", 80)))
        if float(summary["market_value"]) + quantity * entry > float(summary["equity"]) * maximum_exposure_pct / 100:
            raise ValueError("Market-regime portfolio exposure limit exceeded")
        sector_value = sum(
            (position.remaining_quantity or 0) * position.entry_price
            for position in self.portfolio.positions.values() if position.sector == sector
        )
        if sector_value + quantity * entry > float(summary["equity"]) * 0.20:
            raise ValueError("Maximum 20% sector exposure exceeded")
        position = PaperPosition(
            symbol=symbol, sector=sector,
            entry_date=date.fromisoformat(str(payload.get("entry_date", date.today().isoformat()))[:10]),
            entry_price=entry, quantity=quantity, initial_stop=stop, current_stop=stop,
            highest_close=entry,
        )
        self.portfolio.cash -= quantity * entry
        self.portfolio.positions[symbol] = position
        return self._snapshot()

    def apply_exit(self, symbol: str, price: float, quantity: int | None = None) -> dict[str, Any]:
        symbol = symbol.upper()
        if symbol not in self.portfolio.positions:
            raise ValueError("Unknown paper position")
        position = self.portfolio.positions[symbol]
        available = int(position.remaining_quantity or 0)
        quantity = available if quantity is None else int(quantity)
        if quantity <= 0 or quantity > available:
            raise ValueError("Exit quantity exceeds the open paper position")
        pnl = (float(price) - position.entry_price) * quantity
        self.portfolio.cash += float(price) * quantity
        self.portfolio.realized_pnl += pnl
        position.realized_pnl += pnl
        position.remaining_quantity = available - quantity
        if position.remaining_quantity == 0:
            position.state = "EXIT"
            del self.portfolio.positions[symbol]
        else:
            position.partial_exit_done = True
            position.state = "PARTIAL_EXIT"
        return self._snapshot()

    def evaluate_position(self, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = symbol.upper()
        if symbol not in self.portfolio.positions:
            raise ValueError("Unknown paper position")
        decision = self.portfolio.positions[symbol].evaluate_close(
            close=float(payload["close"]), atr=float(payload["atr"]),
            supertrend=float(payload["supertrend"]), rsi=float(payload["rsi"]),
            below_supertrend_days=int(payload.get("below_supertrend_days", 0)),
            weekly_below_ema10=bool(payload.get("weekly_below_ema10", False)),
        )
        snapshot = self._snapshot()
        return {"symbol": symbol, "execution": "NEXT_SESSION_OPEN", "decision": decision, "portfolio": snapshot}

    def resume_at_month_review(self) -> dict[str, Any]:
        self.portfolio.paused_until_month_review = False
        return self._snapshot()
