from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from pytest import MonkeyPatch

from cost_average_out.exchange import (
    ExchangeConfigurationError,
    KrakenExchangeAdapter,
    OrderStatus,
    UnsupportedSymbolError,
    create_exchange_adapter,
)


class FakeCcxtClient:
    def __init__(self) -> None:
        self.markets: Mapping[str, Mapping[str, Any]] = {}
        self.balance: Mapping[str, Any] = {
            "free": {"BTC": 0.5, "EUR": 100},
            "total": {"BTC": 0.75, "EUR": 100},
        }
        self.open_orders: dict[str, Sequence[Mapping[str, Any]]] = {}
        self.closed_orders: dict[str, Sequence[Mapping[str, Any]]] = {}
        self.trades: dict[str, Sequence[Mapping[str, Any]]] = {}
        self.tickers: dict[str, Mapping[str, Any]] = {}

    def load_markets(self) -> Mapping[str, Mapping[str, Any]]:
        return self.markets

    def fetch_balance(self) -> Mapping[str, Any]:
        return self.balance

    def fetch_ticker(
        self,
        symbol: str,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        return self.tickers[symbol]

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        assert symbol is not None
        return self.open_orders.get(symbol, [])

    def fetch_closed_orders(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        assert symbol is not None
        return self.closed_orders.get(symbol, [])

    def fetch_my_trades(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        assert symbol is not None
        return self.trades.get(symbol, [])


def market() -> Mapping[str, Any]:
    return {
        "active": True,
        "spot": True,
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5}},
        "precision": {"amount": 0.00000001},
    }


def test_fetches_available_and_total_balances() -> None:
    adapter = KrakenExchangeAdapter(FakeCcxtClient())

    balances = {balance.asset: balance for balance in adapter.fetch_balances()}

    assert str(balances["BTC"].available) == "0.5"
    assert str(balances["BTC"].total) == "0.75"
    assert str(balances["EUR"].available) == "100"


def test_fetch_tickers_normalizes_bid_ask() -> None:
    client = FakeCcxtClient()
    client.tickers["BTC/EUR"] = {
        "symbol": "BTC/EUR",
        "bid": 50000,
        "ask": 50050,
        "timestamp": 1_893_456_000_000,
    }
    adapter = KrakenExchangeAdapter(client)

    ticker = adapter.fetch_tickers(["BTC/EUR"])[0]

    assert ticker.symbol == "BTC/EUR"
    assert str(ticker.bid) == "50000"
    assert str(ticker.ask) == "50050"
    assert ticker.timestamp == datetime(2030, 1, 1, 0, tzinfo=UTC)


def test_fetch_markets_rejects_unavailable_symbols() -> None:
    client = FakeCcxtClient()
    client.markets = {"BTC/EUR": market()}
    adapter = KrakenExchangeAdapter(client)

    with pytest.raises(UnsupportedSymbolError, match="ETH/EUR"):
        adapter.fetch_markets(["BTC/EUR", "ETH/EUR"])


def test_normalizes_partial_open_order_and_fill() -> None:
    client = FakeCcxtClient()
    client.open_orders["BTC/EUR"] = [
        {
            "id": "order-1",
            "clientOrderId": "cao-cycle-1-btc",
            "symbol": "BTC/EUR",
            "status": "open",
            "amount": 1,
            "filled": 0.25,
            "timestamp": 1_893_456_000_000,
        }
    ]
    client.trades["BTC/EUR"] = [
        {
            "id": "fill-1",
            "order": "order-1",
            "symbol": "BTC/EUR",
            "amount": 0.25,
            "price": 50_000,
            "timestamp": 1_893_456_000_000,
            "fee": {"cost": 2.5, "currency": "EUR"},
        }
    ]
    adapter = KrakenExchangeAdapter(client)

    orders = adapter.fetch_open_orders(["BTC/EUR"])
    timestamp = orders[0].timestamp
    assert timestamp is not None
    fills = adapter.fetch_recent_fills(
        ["BTC/EUR"],
        timestamp,
    )

    assert orders[0].status is OrderStatus.PARTIAL
    assert orders[0].is_app_created is True
    assert str(fills[0].quantity) == "0.25"
    assert fills[0].fee_currency == "EUR"


def test_deduplicates_orders_returned_for_multiple_symbols() -> None:
    client = FakeCcxtClient()
    duplicate = {
        "id": "order-1",
        "status": "closed",
        "amount": 1,
        "filled": 1,
    }
    client.closed_orders = {"BTC/EUR": [duplicate], "ETH/EUR": [duplicate]}
    adapter = KrakenExchangeAdapter(client)

    orders = adapter.fetch_recent_orders(
        ["BTC/EUR", "ETH/EUR"],
        datetime.now(UTC),
    )

    assert len(orders) == 1


def test_factory_requires_credentials(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("COST_AVERAGE_OUT_EXCHANGE_API_KEY", raising=False)
    monkeypatch.delenv("COST_AVERAGE_OUT_EXCHANGE_API_SECRET", raising=False)

    with pytest.raises(ExchangeConfigurationError, match="credentials are required"):
        create_exchange_adapter("kraken")


def test_factory_rejects_unsupported_exchange() -> None:
    with pytest.raises(ExchangeConfigurationError, match="unsupported exchange"):
        create_exchange_adapter("unsupported")
