from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from cost_average_out.config import AppConfig


def valid_config_data() -> dict[str, Any]:
    return {
        "config_version": 1,
        "exchange": "kraken",
        "quote_currency": "EUR",
        "timezone": "Europe/Amsterdam",
        "database_path": "./data/cost_average_out.sqlite3",
        "activation": {"type": "date", "date": "2030-01-01"},
        "cost_average_out": {
            "start_date": "2030-01-01",
            "end_date": None,
            "interval": "weekly",
            "percentage": 0.01,
            "percentage_basis": "current_balance",
        },
        "symbols": ["BTC/EUR", "ETH/EUR"],
        "safety": {
            "live_trading_enabled": False,
            "kill_switch": False,
            "require_first_live_sell_confirmation": True,
            "missed_cycle_policy": "require_manual_approval",
            "max_sell_value_per_cycle": 500,
            "min_remaining_value_per_symbol": 100,
            "max_spread_bps": 75,
        },
        "notifications": {"provider": "none"},
    }


@pytest.mark.parametrize("percentage", [0, -0.01, 1.01])
def test_invalid_percentage_is_rejected(percentage: float) -> None:
    data = valid_config_data()
    data["cost_average_out"]["percentage"] = percentage

    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_unsupported_missed_cycle_policy_is_rejected() -> None:
    data = valid_config_data()
    data["safety"]["missed_cycle_policy"] = "catch_up_automatically"

    with pytest.raises(ValidationError, match="require_manual_approval"):
        AppConfig.model_validate(data)


def test_unknown_timezone_is_rejected() -> None:
    data = valid_config_data()
    data["timezone"] = "Mars/Olympus_Mons"

    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        AppConfig.model_validate(data)


@pytest.mark.parametrize("symbol", ["BTC-EUR", "BTC/EUR/USD", "B/EUR"])
def test_invalid_symbol_format_is_rejected(symbol: str) -> None:
    data = valid_config_data()
    data["symbols"] = [symbol]

    with pytest.raises(ValidationError, match="BASE/QUOTE"):
        AppConfig.model_validate(data)


def test_normalization_and_unknown_field_rejection() -> None:
    data = deepcopy(valid_config_data())
    data["exchange"] = " KRAKEN "
    data["quote_currency"] = "eur"
    data["symbols"] = [" btc/eur "]
    config = AppConfig.model_validate(data)

    assert config.exchange == "kraken"
    assert config.quote_currency == "EUR"
    assert config.symbols == ["BTC/EUR"]

    data["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        AppConfig.model_validate(data)


def test_webhook_notification_provider_is_supported() -> None:
    data = valid_config_data()
    data["notifications"] = {"provider": "webhook"}

    config = AppConfig.model_validate(data)

    assert config.notifications.provider == "webhook"
