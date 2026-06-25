"""Read-only exchange adapter contracts and Kraken implementation."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, TypeVar, cast

import ccxt  # type: ignore[import-untyped]

APP_CLIENT_ORDER_PREFIX = "cao-"

T = TypeVar("T")


class ExchangeError(RuntimeError):
    """Base error for normalized exchange failures."""


class ExchangeConfigurationError(ExchangeError):
    """Raised when exchange credentials or adapter selection are invalid."""


class UnsupportedSymbolError(ExchangeError):
    """Raised when configured symbols are unavailable for spot trading."""


class OrderStatus(StrEnum):
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    FAILED = "failed"
    UNKNOWN_REQUIRES_RECONCILIATION = "unknown_requires_reconciliation"


@dataclass(frozen=True)
class Balance:
    asset: str
    available: Decimal
    total: Decimal


@dataclass(frozen=True)
class Market:
    symbol: str
    active: bool
    spot: bool
    minimum_amount: Decimal | None
    minimum_cost: Decimal | None
    amount_precision: Decimal | None


@dataclass(frozen=True)
class Ticker:
    symbol: str
    bid: Decimal | None
    ask: Decimal | None
    timestamp: datetime | None


@dataclass(frozen=True)
class Order:
    exchange_order_id: str
    client_order_id: str | None
    symbol: str
    status: OrderStatus
    amount: Decimal
    filled: Decimal
    timestamp: datetime | None
    raw: Mapping[str, Any]

    @property
    def is_app_created(self) -> bool:
        return bool(
            self.client_order_id
            and self.client_order_id.startswith(APP_CLIENT_ORDER_PREFIX)
        )


@dataclass(frozen=True)
class Fill:
    exchange_fill_id: str
    exchange_order_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal | None
    fee_currency: str | None
    filled_at: datetime
    raw: Mapping[str, Any]


class ExchangeAdapter(Protocol):
    name: str

    def fetch_balances(self) -> Sequence[Balance]: ...

    def fetch_markets(self, symbols: Sequence[str]) -> Sequence[Market]: ...

    def fetch_tickers(self, symbols: Sequence[str]) -> Sequence[Ticker]: ...

    def fetch_open_orders(self, symbols: Sequence[str]) -> Sequence[Order]: ...

    def fetch_recent_orders(
        self,
        symbols: Sequence[str],
        since: datetime,
    ) -> Sequence[Order]: ...

    def fetch_recent_fills(
        self,
        symbols: Sequence[str],
        since: datetime,
    ) -> Sequence[Fill]: ...


class CcxtClient(Protocol):
    markets: Mapping[str, Mapping[str, Any]]

    def load_markets(self) -> Mapping[str, Mapping[str, Any]]: ...

    def fetch_balance(self) -> Mapping[str, Any]: ...

    def fetch_ticker(
        self,
        symbol: str,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]: ...

    def fetch_closed_orders(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]: ...

    def fetch_my_trades(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]: ...


class KrakenExchangeAdapter:
    name = "kraken"

    def __init__(self, client: CcxtClient) -> None:
        self._client = client

    def fetch_balances(self) -> Sequence[Balance]:
        try:
            raw = self._client.fetch_balance()
        except Exception as exc:
            raise ExchangeError(f"Kraken balance fetch failed: {exc}") from exc

        free = _mapping(raw.get("free"))
        total = _mapping(raw.get("total"))
        assets = sorted(set(free) | set(total))
        return [
            Balance(
                asset=asset.upper(),
                available=_decimal(free.get(asset)),
                total=_decimal(total.get(asset)),
            )
            for asset in assets
            if _decimal(total.get(asset)) != 0 or _decimal(free.get(asset)) != 0
        ]

    def fetch_markets(self, symbols: Sequence[str]) -> Sequence[Market]:
        try:
            loaded = self._client.load_markets()
        except Exception as exc:
            raise ExchangeError(f"Kraken market fetch failed: {exc}") from exc

        missing = [symbol for symbol in symbols if symbol not in loaded]
        if missing:
            raise UnsupportedSymbolError(
                "symbols unavailable on Kraken: " + ", ".join(missing)
            )

        markets: list[Market] = []
        invalid: list[str] = []
        for symbol in symbols:
            raw = loaded[symbol]
            spot = bool(raw.get("spot", raw.get("type") == "spot"))
            active = raw.get("active") is not False
            if not spot or not active:
                invalid.append(symbol)
                continue
            limits = _mapping(raw.get("limits"))
            amount_limits = _mapping(limits.get("amount"))
            cost_limits = _mapping(limits.get("cost"))
            precision = _mapping(raw.get("precision"))
            markets.append(
                Market(
                    symbol=symbol,
                    active=active,
                    spot=spot,
                    minimum_amount=_optional_decimal(amount_limits.get("min")),
                    minimum_cost=_optional_decimal(cost_limits.get("min")),
                    amount_precision=_optional_decimal(precision.get("amount")),
                )
            )
        if invalid:
            raise UnsupportedSymbolError(
                "symbols are inactive or not spot markets on Kraken: "
                + ", ".join(invalid)
            )
        return markets

    def fetch_tickers(self, symbols: Sequence[str]) -> Sequence[Ticker]:
        tickers: list[Ticker] = []
        try:
            for symbol in symbols:
                tickers.append(
                    _normalize_ticker(self._client.fetch_ticker(symbol), symbol)
                )
        except Exception as exc:
            raise ExchangeError(f"Kraken ticker fetch failed: {exc}") from exc
        return tickers

    def fetch_open_orders(self, symbols: Sequence[str]) -> Sequence[Order]:
        return self._fetch_orders("open", symbols, None)

    def fetch_recent_orders(
        self,
        symbols: Sequence[str],
        since: datetime,
    ) -> Sequence[Order]:
        _require_aware(since)
        return self._fetch_orders("closed", symbols, _milliseconds(since))

    def fetch_recent_fills(
        self,
        symbols: Sequence[str],
        since: datetime,
    ) -> Sequence[Fill]:
        _require_aware(since)
        fills: list[Fill] = []
        try:
            for symbol in symbols:
                rows = self._client.fetch_my_trades(symbol, _milliseconds(since))
                fills.extend(_normalize_fill(row, symbol) for row in rows)
        except Exception as exc:
            raise ExchangeError(f"Kraken fill fetch failed: {exc}") from exc
        return _deduplicate(fills, key=lambda fill: fill.exchange_fill_id)

    def _fetch_orders(
        self,
        kind: str,
        symbols: Sequence[str],
        since: int | None,
    ) -> Sequence[Order]:
        orders: list[Order] = []
        try:
            for symbol in symbols:
                if kind == "open":
                    rows = self._client.fetch_open_orders(symbol)
                else:
                    rows = self._client.fetch_closed_orders(symbol, since)
                orders.extend(_normalize_order(row, symbol) for row in rows)
        except Exception as exc:
            raise ExchangeError(f"Kraken {kind} order fetch failed: {exc}") from exc
        return _deduplicate(orders, key=lambda order: order.exchange_order_id)


def create_exchange_adapter(exchange: str) -> ExchangeAdapter:
    """Create the configured production adapter using environment credentials."""

    if exchange != "kraken":
        raise ExchangeConfigurationError(f"unsupported exchange: {exchange}")
    api_key = os.getenv("COST_AVERAGE_OUT_EXCHANGE_API_KEY", "").strip()
    secret = os.getenv("COST_AVERAGE_OUT_EXCHANGE_API_SECRET", "").strip()
    if not api_key or not secret:
        raise ExchangeConfigurationError(
            "Kraken credentials are required in "
            "COST_AVERAGE_OUT_EXCHANGE_API_KEY and "
            "COST_AVERAGE_OUT_EXCHANGE_API_SECRET"
        )
    client = ccxt.kraken({"apiKey": api_key, "secret": secret, "enableRateLimit": True})
    return KrakenExchangeAdapter(cast(CcxtClient, client))


def _normalize_order(raw: Mapping[str, Any], fallback_symbol: str) -> Order:
    status = str(raw.get("status") or "").lower()
    amount = _decimal(raw.get("amount"))
    filled = _decimal(raw.get("filled"))
    normalized_status = {
        "open": OrderStatus.PARTIAL if filled > 0 else OrderStatus.OPEN,
        "closed": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED,
        "cancelled": OrderStatus.CANCELED,
        "rejected": OrderStatus.FAILED,
        "expired": OrderStatus.FAILED,
    }.get(status, OrderStatus.UNKNOWN_REQUIRES_RECONCILIATION)
    exchange_order_id = str(raw.get("id") or "").strip()
    if not exchange_order_id:
        raise ExchangeError("Kraken returned an order without an ID")
    client_order_id = raw.get("clientOrderId")
    return Order(
        exchange_order_id=exchange_order_id,
        client_order_id=str(client_order_id) if client_order_id else None,
        symbol=str(raw.get("symbol") or fallback_symbol),
        status=normalized_status,
        amount=amount,
        filled=filled,
        timestamp=_optional_datetime(raw.get("timestamp")),
        raw=dict(raw),
    )


def _normalize_fill(raw: Mapping[str, Any], fallback_symbol: str) -> Fill:
    fill_id = str(raw.get("id") or "").strip()
    order_id = str(raw.get("order") or "").strip()
    timestamp = _optional_datetime(raw.get("timestamp"))
    if not fill_id or not order_id or timestamp is None:
        raise ExchangeError("Kraken returned an incomplete fill")
    fee_data = _mapping(raw.get("fee"))
    return Fill(
        exchange_fill_id=fill_id,
        exchange_order_id=order_id,
        symbol=str(raw.get("symbol") or fallback_symbol),
        quantity=_decimal(raw.get("amount")),
        price=_decimal(raw.get("price")),
        fee=_optional_decimal(fee_data.get("cost")),
        fee_currency=(
            str(fee_data["currency"]).upper() if fee_data.get("currency") else None
        ),
        filled_at=timestamp,
        raw=dict(raw),
    )


def _normalize_ticker(raw: Mapping[str, Any], fallback_symbol: str) -> Ticker:
    return Ticker(
        symbol=str(raw.get("symbol") or fallback_symbol),
        bid=_optional_decimal(raw.get("bid")),
        ask=_optional_decimal(raw.get("ask")),
        timestamp=_optional_datetime(raw.get("timestamp")),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)


def _milliseconds(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("since must be timezone-aware")


def _deduplicate(items: Sequence[T], key: Callable[[T], str]) -> list[T]:
    unique: dict[str, T] = {}
    for item in items:
        unique[str(key(item))] = item
    return list(unique.values())
