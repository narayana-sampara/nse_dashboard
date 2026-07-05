from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from nse_dashboard.domain.market_data import DataSourceError

IST = ZoneInfo("Asia/Kolkata")


class YahooFinanceAdapter:
    """Yahoo implementation of the provider-neutral market-data contract."""

    name = "Yahoo Finance"

    def __init__(self, opener: Any = urlopen) -> None:
        self._opener = opener

    def history(self, symbol: str, period: str) -> pd.DataFrame:
        try:
            frame = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        except Exception as exc:
            raise DataSourceError(f"Yahoo Finance download failed for {symbol}") from exc
        normalized = self._single_frame(frame, symbol)
        if normalized.empty:
            raise DataSourceError(f"Yahoo Finance returned no data for {symbol}")
        return normalized

    def market_history(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
        try:
            raw = yf.download(
                symbols,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            raise DataSourceError("Yahoo Finance market download failed") from exc
        if raw.empty:
            raise DataSourceError("Yahoo Finance returned no market data")
        return {symbol: self._ticker_frame(raw, symbol) for symbol in symbols}

    def quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        fields = ",".join(
            [
                "symbol",
                "shortName",
                "longName",
                "currency",
                "marketState",
                "regularMarketPrice",
                "regularMarketChange",
                "regularMarketChangePercent",
                "regularMarketTime",
                "regularMarketPreviousClose",
                "regularMarketDayHigh",
                "regularMarketDayLow",
            ]
        )
        query = urlencode({"symbols": ",".join(symbols), "fields": fields})
        request = Request(
            f"https://query1.finance.yahoo.com/v8/finance/quote?{query}",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=10) as response:
                if getattr(response, "status", 200) >= 400:
                    raise DataSourceError(
                        f"Yahoo Finance returned {response.status} for quotes"
                    )
                payload = json.loads(response.read().decode("utf-8"))
        except DataSourceError:
            return self._quotes_from_download(symbols)
        except Exception as exc:
            try:
                return self._quotes_from_download(symbols)
            except DataSourceError as fallback_exc:
                raise DataSourceError("Yahoo Finance quote download failed") from fallback_exc

        quotes = payload.get("quoteResponse", {}).get("result", [])
        prices: dict[str, dict[str, Any]] = {}
        for quote in quotes:
            parsed = self._quote_payload(quote)
            if parsed is not None:
                prices[parsed["symbol"]] = parsed
        if not prices:
            return self._quotes_from_download(symbols)
        return prices

    def _quotes_from_download(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        try:
            intraday = yf.download(
                symbols,
                period="1d",
                interval="1m",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
            daily = yf.download(
                symbols,
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            raise DataSourceError("Yahoo Finance fallback quote download failed") from exc

        prices: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            quote = self._quote_from_frames(
                symbol,
                self._ticker_frame(intraday, symbol),
                self._ticker_frame(daily, symbol),
            )
            if quote is not None:
                prices[symbol] = quote
        if not prices:
            raise DataSourceError("Yahoo Finance returned no quote data")
        return prices

    @staticmethod
    def _quote_from_frames(
        symbol: str, intraday: pd.DataFrame, daily: pd.DataFrame
    ) -> dict[str, Any] | None:
        intraday = _clean_ohlcv(intraday)
        daily = _clean_ohlcv(daily)
        source = intraday if not intraday.empty else daily
        if source.empty or "Close" not in source:
            return None

        latest = source.iloc[-1]
        price = _number(latest.get("Close"))
        if price is None:
            return None

        previous_close = None
        if len(daily) >= 2 and "Close" in daily:
            previous_close = _number(daily["Close"].iloc[-2])
        elif len(source) >= 2 and "Close" in source:
            previous_close = _number(source["Close"].iloc[-2])

        change = price - previous_close if previous_close else 0.0
        change_pct = change / previous_close * 100 if previous_close else 0.0
        market_time = _index_time(source.index[-1])
        return {
            "symbol": symbol,
            "name": symbol,
            "currency": "INR",
            "price": price,
            "close": price,
            "change": round(change, 4),
            "change_pct": round(change_pct, 4),
            "previous_close": previous_close,
            "day_high": _number(source["High"].max()) if "High" in source else None,
            "day_low": _number(source["Low"].min()) if "Low" in source else None,
            "as_of": _display_time(market_time),
            "market_time": market_time.isoformat() if market_time else None,
            "market_state": "LATEST",
            "price_basis": "LATEST",
        }

    @staticmethod
    def _ticker_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if isinstance(raw.columns, pd.MultiIndex):
            if symbol in raw.columns.get_level_values(0):
                return raw[symbol].dropna(how="all")
            if symbol in raw.columns.get_level_values(-1):
                return raw.xs(symbol, level=-1, axis=1).dropna(how="all")
            return pd.DataFrame()
        return raw.dropna(how="all")

    @staticmethod
    def _single_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if isinstance(frame.columns, pd.MultiIndex):
            if symbol in frame.columns.get_level_values(-1):
                frame = frame.xs(symbol, level=-1, axis=1)
            else:
                frame = frame.copy()
                frame.columns = frame.columns.get_level_values(0)
        return frame.dropna(how="all")

    @staticmethod
    def _quote_payload(quote: dict[str, Any]) -> dict[str, Any] | None:
        symbol = str(quote.get("symbol") or "").upper()
        price = _number(quote.get("regularMarketPrice"))
        if not symbol or price is None:
            return None
        market_state = str(quote.get("marketState") or "UNKNOWN")
        market_epoch = _number(quote.get("regularMarketTime"))
        market_time = (
            datetime.fromtimestamp(market_epoch, timezone.utc)
            if market_epoch is not None
            else None
        )
        return {
            "symbol": symbol,
            "name": str(quote.get("shortName") or quote.get("longName") or symbol),
            "currency": str(quote.get("currency") or "INR"),
            "price": price,
            "close": price,
            "change": _number(quote.get("regularMarketChange")) or 0.0,
            "change_pct": _number(quote.get("regularMarketChangePercent")) or 0.0,
            "previous_close": _number(quote.get("regularMarketPreviousClose")),
            "day_high": _number(quote.get("regularMarketDayHigh")),
            "day_low": _number(quote.get("regularMarketDayLow")),
            "as_of": _display_time(market_time),
            "market_time": market_time.isoformat() if market_time else None,
            "market_state": market_state,
            "price_basis": _price_basis(market_state),
        }


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(IST).strftime("%d %b, %I:%M %p")


def _price_basis(market_state: str) -> str:
    if market_state in {"CLOSED", "POST", "POSTPOST"}:
        return "TODAY_CLOSE"
    if market_state == "REGULAR":
        return "INTRADAY"
    return "LATEST"


def _clean_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    columns = [column for column in ("Open", "High", "Low", "Close", "Volume") if column in frame]
    if not columns:
        return pd.DataFrame()
    cleaned = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    return cleaned.dropna(subset=["Close"]).sort_index()


def _index_time(value: Any) -> datetime | None:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(IST)
    return timestamp.to_pydatetime().astimezone(timezone.utc)
