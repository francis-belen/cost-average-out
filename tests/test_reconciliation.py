from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from cost_average_out.config import AppConfig
from cost_average_out.exchange import Balance, Fill, Market, Order, OrderStatus, Ticker
from cost_average_out.ledger import Ledger, PlannedOrderInput
from cost_average_out.reconciliation import reconcile
from tests.test_config import valid_config_data

NOW = datetime(2030, 1, 8, 12, tzinfo=UTC)


class FakeExchangeAdapter:
    name = "kraken"

    def __init__(self) -> None:
        self.balances: Sequence[Balance] = [
            Balance("BTC", Decimal("0.5"), Decimal("0.75")),
            Balance("EUR", Decimal("100"), Decimal("100")),
        ]
        self.markets: Sequence[Market] = [
            Market(
                "BTC/EUR",
                True,
                True,
                Decimal("0.0001"),
                Decimal("5"),
                Decimal("0.00000001"),
            ),
            Market(
                "ETH/EUR",
                True,
                True,
                Decimal("0.001"),
                Decimal("5"),
                Decimal("0.00000001"),
            ),
        ]
        self.open_orders: Sequence[Order] = []
        self.recent_orders: Sequence[Order] = []
        self.fills: Sequence[Fill] = []
        self.tickers: Sequence[Ticker] = [
            Ticker("BTC/EUR", Decimal("50000"), Decimal("50050"), NOW),
            Ticker("ETH/EUR", Decimal("2500"), Decimal("2502"), NOW),
        ]

    def fetch_balances(self) -> Sequence[Balance]:
        return self.balances

    def fetch_markets(self, symbols: Sequence[str]) -> Sequence[Market]:
        assert list(symbols) == ["BTC/EUR", "ETH/EUR"]
        return self.markets

    def fetch_tickers(self, symbols: Sequence[str]) -> Sequence[Ticker]:
        return self.tickers

    def submit_market_sell_order(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
    ) -> Order:
        order = Order(
            exchange_order_id=f"order-{len(self.recent_orders) + 1}",
            client_order_id=client_order_id,
            symbol=symbol,
            status=OrderStatus.OPEN,
            amount=quantity,
            filled=Decimal("0"),
            timestamp=NOW,
            raw={"id": f"order-{len(self.recent_orders) + 1}"},
        )
        self.recent_orders = [*self.recent_orders, order]
        return order

    def fetch_open_orders(self, symbols: Sequence[str]) -> Sequence[Order]:
        return self.open_orders

    def fetch_recent_orders(
        self,
        symbols: Sequence[str],
        since: datetime,
    ) -> Sequence[Order]:
        assert since < NOW
        return self.recent_orders

    def fetch_recent_fills(
        self,
        symbols: Sequence[str],
        since: datetime,
    ) -> Sequence[Fill]:
        assert since < NOW
        return self.fills


def config() -> AppConfig:
    return AppConfig.model_validate(valid_config_data())


def initialized_ledger(tmp_path: Path) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    return ledger


def planned_ledger(tmp_path: Path) -> Ledger:
    ledger = initialized_ledger(tmp_path)
    ledger.create_cycle_with_orders(
        "cycle-1",
        NOW,
        [PlannedOrderInput("BTC/EUR", Decimal("0.01"), Decimal("500"))],
    )
    ledger.register_exchange_order(
        "cycle-1",
        "BTC/EUR",
        "kraken",
        "cao-cycle-1-btc",
    )
    return ledger


def order(status: OrderStatus) -> Order:
    return Order(
        exchange_order_id="order-1",
        client_order_id="cao-cycle-1-btc",
        symbol="BTC/EUR",
        status=status,
        amount=Decimal("0.01"),
        filled=Decimal("0") if status is OrderStatus.OPEN else Decimal("0.01"),
        timestamp=NOW,
        raw={"id": "order-1", "status": status.value},
    )


def scalar(path: Path, query: str) -> object:
    with sqlite3.connect(path) as connection:
        row = connection.execute(query).fetchone()
        assert row is not None
        return row[0]


def test_reconcile_records_available_and_total_balances(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)
    adapter = FakeExchangeAdapter()

    result = reconcile(config(), ledger, adapter, now=NOW)

    assert result.balance_count == 2
    assert result.market_count == 2
    assert result.execution_blocked is False
    with sqlite3.connect(ledger.path) as connection:
        btc = connection.execute(
            """
            SELECT available, total, source FROM balances
            WHERE exchange = 'kraken' AND asset = 'BTC'
            """
        ).fetchone()
    assert btc == ("0.5", "0.75", "reconciliation")


def test_open_app_created_order_blocks_execution(tmp_path: Path) -> None:
    ledger = planned_ledger(tmp_path)
    adapter = FakeExchangeAdapter()
    adapter.open_orders = [order(OrderStatus.OPEN)]

    result = reconcile(config(), ledger, adapter, now=NOW)

    assert result.unresolved_app_order_count == 1
    assert result.execution_blocked is True
    assert (
        scalar(
            ledger.path,
            "SELECT status FROM exchange_orders WHERE client_order_id = "
            "'cao-cycle-1-btc'",
        )
        == "open"
    )


def test_closed_order_and_fill_are_reconciled_idempotently(tmp_path: Path) -> None:
    ledger = planned_ledger(tmp_path)
    adapter = FakeExchangeAdapter()
    adapter.recent_orders = [order(OrderStatus.FILLED)]
    adapter.fills = [
        Fill(
            exchange_fill_id="fill-1",
            exchange_order_id="order-1",
            symbol="BTC/EUR",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            fee=Decimal("1.25"),
            fee_currency="EUR",
            filled_at=NOW,
            raw={"id": "fill-1"},
        )
    ]

    first = reconcile(config(), ledger, adapter, now=NOW)
    second = reconcile(config(), ledger, adapter, now=NOW)

    assert first.matched_order_count == 1
    assert first.recorded_fill_count == 1
    assert first.execution_blocked is False
    assert second.recorded_fill_count == 0
    assert scalar(ledger.path, "SELECT COUNT(*) FROM fills") == 1
    assert (
        scalar(
            ledger.path,
            "SELECT status FROM exchange_orders WHERE exchange_order_id = 'order-1'",
        )
        == "filled"
    )


def test_orphan_remote_app_order_still_blocks_execution(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)
    adapter = FakeExchangeAdapter()
    adapter.open_orders = [order(OrderStatus.OPEN)]

    result = reconcile(config(), ledger, adapter, now=NOW)

    assert result.matched_order_count == 0
    assert result.execution_blocked is True


def test_non_app_open_order_does_not_block_execution(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)
    adapter = FakeExchangeAdapter()
    external = order(OrderStatus.OPEN)
    adapter.open_orders = [
        Order(
            exchange_order_id=external.exchange_order_id,
            client_order_id="manual-order",
            symbol=external.symbol,
            status=external.status,
            amount=external.amount,
            filled=external.filled,
            timestamp=external.timestamp,
            raw=external.raw,
        )
    ]

    result = reconcile(config(), ledger, adapter, now=NOW)

    assert result.open_order_count == 1
    assert result.execution_blocked is False
