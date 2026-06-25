"""Price backfill and portfolio-history export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from cost_average_out.config import AppConfig
from cost_average_out.exchange import ExchangeAdapter
from cost_average_out.ledger import CandleInput, Ledger, PriceCandle


@dataclass(frozen=True)
class PriceBackfillResult:
    fetched_count: int
    stored_count: int


def backfill_prices(
    config: AppConfig,
    ledger: Ledger,
    adapter: ExchangeAdapter,
    *,
    since: datetime,
    limit: int | None = None,
    timeframe: str = "1d",
) -> PriceBackfillResult:
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("since must be timezone-aware")
    candles: list[CandleInput] = []
    for symbol in config.symbols:
        rows = adapter.fetch_ohlcv(symbol, timeframe, since, limit)
        candles.extend(
            CandleInput(
                exchange=adapter.name,
                symbol=row.symbol,
                timeframe=row.timeframe,
                opened_at=row.opened_at,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in rows
        )
    stored = ledger.record_price_candles(candles)
    return PriceBackfillResult(fetched_count=len(candles), stored_count=stored)


def portfolio_history(
    config: AppConfig,
    ledger: Ledger,
    *,
    exchange: str,
    timeframe: str = "1d",
) -> list[dict[str, Any]]:
    balances = ledger.latest_balances(exchange)
    candles = ledger.price_candles(exchange, config.symbols, timeframe)
    candles_by_day: dict[str, list[PriceCandle]] = {}
    for cached_candle in candles:
        day = cached_candle.opened_at.astimezone(UTC).date().isoformat()
        candles_by_day.setdefault(day, []).append(cached_candle)

    rows: list[dict[str, Any]] = []
    for day in sorted(candles_by_day):
        total = Decimal("0")
        assets: dict[str, dict[str, str]] = {}
        missing: list[str] = []
        for symbol in config.symbols:
            base = symbol.split("/", maxsplit=1)[0]
            balance = balances.get(base)
            candle: PriceCandle | None = next(
                (item for item in candles_by_day[day] if item.symbol == symbol),
                None,
            )
            if balance is None or candle is None:
                missing.append(symbol)
                continue
            value = balance.total * candle.close
            total += value
            assets[base] = {
                "quantity": _decimal_string(balance.total),
                "close": _decimal_string(candle.close),
                "value": _decimal_string(value),
                "price_status": "cached",
            }
        rows.append(
            {
                "date": day,
                "quote_currency": config.quote_currency,
                "total_value": _decimal_string(total),
                "assets": assets,
                "missing_prices": missing,
                "valuation_status": "complete" if not missing else "partial",
            }
        )
    return rows


def default_since(days: int) -> datetime:
    if days <= 0:
        raise ValueError("days must be positive")
    return datetime.now(UTC) - timedelta(days=days)


def _decimal_string(value: Decimal) -> str:
    return format(value.normalize(), "f")
