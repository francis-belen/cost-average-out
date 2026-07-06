from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cost_average_out.activation import evaluate_activation
from cost_average_out.config import AppConfig
from cost_average_out.cycle import (
    LiveExecutionBlockedError,
    dry_run_once,
    live_run_once,
)
from cost_average_out.exchange import Ticker
from cost_average_out.ledger import Ledger
from tests.test_config import valid_config_data
from tests.test_reconciliation import FakeExchangeAdapter


def config_data(**overrides: Any) -> dict[str, Any]:
    data = deepcopy(valid_config_data())
    for key, value in overrides.items():
        data[key] = value
    return data


def app_config(data: dict[str, Any] | None = None) -> AppConfig:
    return AppConfig.model_validate(data or valid_config_data())


def initialized_ledger(tmp_path: Path) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    return ledger


def activation_row_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM activation_state").fetchone()[0]
        )


class PriceAdapter(FakeExchangeAdapter):
    def __init__(self, price: Decimal) -> None:
        super().__init__()
        self.price = price
        self.submitted = 0

    def fetch_tickers(self, symbols: Sequence[str]) -> Sequence[Ticker]:
        if symbols == ["BTC/USDT"]:
            return [Ticker("BTC/USDT", self.price, self.price, None)]
        return list(super().fetch_tickers(symbols))

    def submit_market_sell_order(self, symbol, quantity, client_order_id):  # type: ignore[no-untyped-def]
        self.submitted += 1
        return super().submit_market_sell_order(symbol, quantity, client_order_id)


def date_or_price_data(threshold: str = "120000") -> dict[str, Any]:
    data = valid_config_data()
    data["activation"] = {
        "type": "date_or_price",
        "date": "2030-01-01",
        "price_trigger": {
            "reference_symbol": "BTC/USDT",
            "condition": "above_or_equal",
            "threshold": threshold,
        },
    }
    return data


def test_config_validation_for_date_activation() -> None:
    cfg = app_config()

    assert cfg.activation.type.value == "date"
    assert cfg.activation.date is not None


def test_legacy_start_date_maps_to_date_activation() -> None:
    data = valid_config_data()
    data.pop("activation")

    cfg = app_config(data)

    assert cfg.activation.type.value == "date"
    assert cfg.activation.date == cfg.cost_average_out.start_date


def test_config_validation_for_date_or_price_activation() -> None:
    cfg = app_config(date_or_price_data())

    assert cfg.activation.type.value == "date_or_price"
    assert cfg.activation.price_trigger is not None
    assert cfg.activation.price_trigger.reference_symbol == "BTC/USDT"


@pytest.mark.parametrize("threshold", ["0", "-1"])
def test_invalid_trigger_threshold_is_rejected(threshold: str) -> None:
    with pytest.raises(ValidationError):
        app_config(date_or_price_data(threshold))


def test_invalid_reference_symbol_is_rejected() -> None:
    data = date_or_price_data()
    data["activation"]["price_trigger"]["reference_symbol"] = "BTC-USDT"

    with pytest.raises(ValidationError, match="BASE/QUOTE"):
        app_config(data)


def test_date_activation_not_yet_reached(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    result = evaluate_activation(
        app_config(),
        ledger,
        now=datetime(2029, 12, 31, tzinfo=UTC),
    )

    assert result.activated is False
    assert result.status.value == "waiting_for_activation"
    assert activation_row_count(ledger.path) == 0


def test_date_activation_reached_and_persisted(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    result = evaluate_activation(
        app_config(),
        ledger,
        now=datetime(2030, 1, 1, tzinfo=UTC),
        persist=True,
    )

    assert result.activated is True
    assert result.activation_reason == "date_reached"
    assert ledger.activation_state().activated is True


def test_price_activation_not_yet_reached(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    result = evaluate_activation(
        app_config(date_or_price_data()),
        ledger,
        now=datetime(2029, 12, 1, tzinfo=UTC),
        adapter=PriceAdapter(Decimal("119999")),
    )

    assert result.activated is False
    assert result.price_trigger_status.value == "not_met"
    assert activation_row_count(ledger.path) == 0


def test_price_activation_reached_and_persisted(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    result = evaluate_activation(
        app_config(date_or_price_data()),
        ledger,
        now=datetime(2029, 12, 1, tzinfo=UTC),
        adapter=PriceAdapter(Decimal("120000")),
        persist=True,
    )

    assert result.activated is True
    assert result.activation_reason == "price_trigger_reached"
    assert result.reference_symbol == "BTC/USDT"
    assert result.reference_price == Decimal("120000")


def test_activation_idempotency(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)
    cfg = app_config()

    first = evaluate_activation(
        cfg,
        ledger,
        now=datetime(2030, 1, 1, tzinfo=UTC),
        persist=True,
    )
    second = evaluate_activation(
        cfg,
        ledger,
        now=datetime(2030, 1, 2, tzinfo=UTC),
        persist=True,
    )

    assert first.activated_at == second.activated_at
    assert activation_row_count(ledger.path) == 1


def test_evaluate_activation_does_not_persist_by_default(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    result = evaluate_activation(
        app_config(),
        ledger,
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert result.activated is True
    assert result.activation_reason == "date_reached"
    assert activation_row_count(ledger.path) == 0


def test_live_run_persists_activation_when_condition_is_met(tmp_path: Path) -> None:
    data = valid_config_data()
    data["safety"]["live_trading_enabled"] = True
    data["safety"]["require_first_live_sell_confirmation"] = False
    cfg = app_config(data)
    ledger = initialized_ledger(tmp_path)
    adapter = PriceAdapter(Decimal("50000"))

    result = live_run_once(
        cfg,
        ledger,
        adapter,
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert result.activation.activated is True
    assert ledger.activation_state().activated is True
    assert activation_row_count(ledger.path) == 1


def test_dry_run_does_not_persist_activation_when_condition_is_met(
    tmp_path: Path,
) -> None:
    cfg = app_config()
    ledger = initialized_ledger(tmp_path)

    result = dry_run_once(
        cfg,
        ledger,
        PriceAdapter(Decimal("50000")),
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert result.activation.activated is True
    assert activation_row_count(ledger.path) == 0
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cycles").fetchone()[0] == 0


def test_run_once_live_blocked_before_activation(tmp_path: Path) -> None:
    data = valid_config_data()
    data["safety"]["live_trading_enabled"] = True
    cfg = app_config(data)
    ledger = initialized_ledger(tmp_path)
    adapter = PriceAdapter(Decimal("50000"))

    with pytest.raises(LiveExecutionBlockedError, match="waiting for activation"):
        live_run_once(cfg, ledger, adapter, now=datetime(2029, 12, 1, tzinfo=UTC))

    assert adapter.submitted == 0


def test_run_once_dry_run_does_not_create_sell_orders_before_activation(
    tmp_path: Path,
) -> None:
    cfg = app_config()
    ledger = initialized_ledger(tmp_path)

    result = dry_run_once(
        cfg,
        ledger,
        PriceAdapter(Decimal("50000")),
        now=datetime(2029, 12, 1, tzinfo=UTC),
        persist_simulation=True,
    )

    assert result.activation.activated is False
    assert result.evaluation is None
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cycles").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM planned_orders").fetchone()[0] == 0
        )
