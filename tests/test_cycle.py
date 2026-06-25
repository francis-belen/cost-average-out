from __future__ import annotations

import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cost_average_out.config import AppConfig
from cost_average_out.cycle import LiveExecutionBlockedError, live_run_once
from cost_average_out.exchange import ExchangeTimeoutError, Order, OrderStatus
from cost_average_out.ledger import (
    CycleState,
    Ledger,
    LedgerConflictError,
    PlannedOrderInput,
)
from cost_average_out.scheduling import evaluate_cycle
from tests.test_config import valid_config_data
from tests.test_reconciliation import FakeExchangeAdapter

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def config(
    *,
    live_enabled: bool = True,
    kill_switch: bool = False,
    require_confirmation: bool = False,
) -> AppConfig:
    data = deepcopy(valid_config_data())
    data["safety"]["live_trading_enabled"] = live_enabled
    data["safety"]["kill_switch"] = kill_switch
    data["safety"]["require_first_live_sell_confirmation"] = require_confirmation
    return AppConfig.model_validate(data)


def initialized_ledger(tmp_path: Path) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    return ledger


def scalar(path: Path, query: str) -> object:
    with sqlite3.connect(path) as connection:
        row = connection.execute(query).fetchone()
        assert row is not None
        return row[0]


class FilledExchangeAdapter(FakeExchangeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.submitted_client_ids: list[str] = []

    def submit_market_sell_order(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
    ) -> Order:
        self.submitted_client_ids.append(client_order_id)
        order = Order(
            exchange_order_id="filled-order-1",
            client_order_id=client_order_id,
            symbol=symbol,
            status=OrderStatus.FILLED,
            amount=quantity,
            filled=quantity,
            timestamp=NOW,
            raw={"id": "filled-order-1", "status": "closed"},
        )
        self.recent_orders = [order]
        return order


class TimeoutExchangeAdapter(FakeExchangeAdapter):
    def submit_market_sell_order(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
    ) -> Order:
        raise ExchangeTimeoutError("simulated timeout")


def test_live_execution_requires_config_opt_in(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    with pytest.raises(LiveExecutionBlockedError, match="live_trading_enabled"):
        live_run_once(
            config(live_enabled=False),
            ledger,
            FakeExchangeAdapter(),
            now=NOW,
        )


def test_live_execution_respects_kill_switch(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    with pytest.raises(LiveExecutionBlockedError, match="kill_switch"):
        live_run_once(
            config(kill_switch=True),
            ledger,
            FakeExchangeAdapter(),
            now=NOW,
        )


def test_first_live_sell_requires_confirmation_when_configured(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    with pytest.raises(LiveExecutionBlockedError, match="first live sell"):
        live_run_once(
            config(require_confirmation=True),
            ledger,
            FakeExchangeAdapter(),
            now=NOW,
        )


def test_live_execution_submits_and_persists_exchange_order(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)
    adapter = FilledExchangeAdapter()

    result = live_run_once(config(), ledger, adapter, now=NOW)

    assert result.submitted_order_count == 1
    assert len(adapter.submitted_client_ids) == 1
    assert scalar(ledger.path, "SELECT COUNT(*) FROM exchange_orders") == 1
    assert scalar(ledger.path, "SELECT status FROM exchange_orders") == "filled"
    assert scalar(ledger.path, "SELECT status FROM cycles") == "completed"


def test_submission_timeout_records_unknown_state(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    with pytest.raises(ExchangeTimeoutError):
        live_run_once(config(), ledger, TimeoutExchangeAdapter(), now=NOW)

    assert scalar(ledger.path, "SELECT status FROM cycles") == (
        CycleState.UNKNOWN_REQUIRES_RECONCILIATION.value
    )
    assert scalar(ledger.path, "SELECT status FROM exchange_orders") == (
        "unknown_requires_reconciliation"
    )


def test_rerunning_completed_cycle_does_not_submit_again(tmp_path: Path) -> None:
    cfg = config()
    ledger = initialized_ledger(tmp_path)
    evaluation = evaluate_cycle(cfg, NOW)
    ledger.create_cycle(
        evaluation.cycle_id,
        evaluation.scheduled_at,
        CycleState.COMPLETED,
    )
    adapter = FilledExchangeAdapter()

    with pytest.raises(LedgerConflictError):
        live_run_once(cfg, ledger, adapter, now=NOW)

    assert adapter.submitted_client_ids == []


def test_unresolved_partial_order_blocks_before_submit(tmp_path: Path) -> None:
    cfg = config()
    ledger = initialized_ledger(tmp_path)
    ledger.create_cycle_with_orders(
        "previous-cycle",
        NOW,
        [PlannedOrderInput("BTC/EUR", Decimal("0.01"), Decimal("500"))],
    )
    ledger.register_exchange_order(
        "previous-cycle",
        "BTC/EUR",
        "kraken",
        "cao-previous-cycle-btc",
        status="partial",
        exchange_order_id="partial-order-1",
    )
    adapter = FilledExchangeAdapter()

    with pytest.raises(LiveExecutionBlockedError, match="unresolved"):
        live_run_once(cfg, ledger, adapter, now=NOW)

    assert adapter.submitted_client_ids == []
