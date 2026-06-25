from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cost_average_out.config import AppConfig
from cost_average_out.exchange import Balance, Market, Ticker
from cost_average_out.planner import PlanItemStatus, SellPlanItem, build_sell_plan
from tests.test_config import valid_config_data

NOW = datetime(2030, 1, 8, 12, tzinfo=UTC)


def config(overrides: dict[str, Any] | None = None) -> AppConfig:
    data = deepcopy(valid_config_data())
    if overrides:
        for section, values in overrides.items():
            if isinstance(values, dict) and isinstance(data.get(section), dict):
                data[section].update(values)
            else:
                data[section] = values
    return AppConfig.model_validate(data)


def balances() -> list[Balance]:
    return [
        Balance("BTC", Decimal("0.5"), Decimal("0.75")),
        Balance("ETH", Decimal("2"), Decimal("3")),
        Balance("DOGE", Decimal("10000"), Decimal("10000")),
    ]


def markets() -> list[Market]:
    return [
        Market("BTC/EUR", True, True, Decimal("0.0001"), Decimal("5"), None),
        Market("ETH/EUR", True, True, Decimal("0.001"), Decimal("5"), None),
        Market("DOGE/EUR", True, True, Decimal("1"), Decimal("5"), None),
    ]


def tickers() -> list[Ticker]:
    return [
        Ticker("BTC/EUR", Decimal("50000"), Decimal("50050"), NOW),
        Ticker("ETH/EUR", Decimal("2500"), Decimal("2501"), NOW),
        Ticker("DOGE/EUR", Decimal("0.1"), Decimal("0.101"), NOW),
    ]


def item_by_symbol(plan_symbol: str, items: tuple[SellPlanItem, ...]) -> SellPlanItem:
    return next(item for item in items if item.symbol == plan_symbol)


def test_one_percent_sell_plan_uses_available_balance_not_total() -> None:
    plan = build_sell_plan(config(), balances(), markets(), tickers(), now=NOW)

    btc = item_by_symbol("BTC/EUR", plan.items)

    assert btc.status is PlanItemStatus.PLANNED
    assert btc.quantity == Decimal("0.005")
    assert btc.estimated_quote_value == Decimal("250")


def test_plan_never_includes_symbols_outside_allowlist() -> None:
    plan = build_sell_plan(config(), balances(), markets(), tickers(), now=NOW)

    assert {item.symbol for item in plan.items} == {"BTC/EUR", "ETH/EUR"}


def test_plan_skips_orders_below_exchange_minimum() -> None:
    low_value_tickers = [Ticker("BTC/EUR", Decimal("100"), Decimal("100.1"), NOW)]

    plan = build_sell_plan(
        config({"symbols": ["BTC/EUR"]}),
        balances(),
        markets(),
        low_value_tickers,
        now=NOW,
    )

    item = plan.items[0]
    assert item.status is PlanItemStatus.SKIPPED
    assert item.reason == "below exchange minimum notional"


def test_plan_blocks_orders_above_max_sell_value() -> None:
    plan = build_sell_plan(
        config({"symbols": ["BTC/EUR"], "safety": {"max_sell_value_per_cycle": 100}}),
        balances(),
        markets(),
        tickers(),
        now=NOW,
    )

    item = plan.items[0]
    assert item.status is PlanItemStatus.BLOCKED
    assert item.reason == "above max sell value per cycle"


def test_plan_blocks_when_remaining_value_is_too_low() -> None:
    plan = build_sell_plan(
        config(
            {
                "symbols": ["BTC/EUR"],
                "safety": {"min_remaining_value_per_symbol": 30000},
            }
        ),
        balances(),
        markets(),
        tickers(),
        now=NOW,
    )

    item = plan.items[0]
    assert item.status is PlanItemStatus.BLOCKED
    assert item.reason == "remaining value below configured minimum"


def test_plan_blocks_wide_spreads() -> None:
    wide_tickers = [Ticker("BTC/EUR", Decimal("50000"), Decimal("51000"), NOW)]

    plan = build_sell_plan(
        config({"symbols": ["BTC/EUR"]}),
        balances(),
        markets(),
        wide_tickers,
        now=NOW,
    )

    item = plan.items[0]
    assert item.status is PlanItemStatus.BLOCKED
    assert item.reason is not None
    assert item.reason.startswith("spread exceeds max_spread_bps")


def test_initial_snapshot_basis_uses_snapshot_but_caps_to_current_available() -> None:
    cfg = config(
        {
            "symbols": ["BTC/EUR"],
            "cost_average_out": {"percentage_basis": "initial_snapshot"},
            "safety": {"min_remaining_value_per_symbol": 0},
        }
    )
    initial = [Balance("BTC", Decimal("1"), Decimal("1"))]

    plan = build_sell_plan(
        cfg,
        [Balance("BTC", Decimal("0.006"), Decimal("0.006"))],
        markets(),
        tickers(),
        initial_balances=initial,
        now=NOW,
    )

    item = plan.items[0]
    assert item.status is PlanItemStatus.PLANNED
    assert item.quantity == Decimal("0.006")
