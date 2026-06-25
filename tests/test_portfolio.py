from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from cost_average_out.config import AppConfig
from cost_average_out.ledger import BalanceInput, Ledger
from cost_average_out.portfolio import backfill_prices, portfolio_history
from tests.test_config import valid_config_data
from tests.test_reconciliation import FakeExchangeAdapter

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def config() -> AppConfig:
    data = valid_config_data()
    data["symbols"] = ["BTC/EUR"]
    return AppConfig.model_validate(data)


def initialized_ledger(tmp_path: Path) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    return ledger


def test_backfill_prices_records_candles(tmp_path: Path) -> None:
    ledger = initialized_ledger(tmp_path)

    result = backfill_prices(
        config(),
        ledger,
        FakeExchangeAdapter(),
        since=NOW,
        limit=1,
    )

    assert result.fetched_count == 1
    assert len(ledger.price_candles("kraken", ["BTC/EUR"])) == 1


def test_portfolio_history_exports_chart_ready_rows(tmp_path: Path) -> None:
    cfg = config()
    ledger = initialized_ledger(tmp_path)
    ledger.record_balances(
        "kraken",
        NOW,
        [BalanceInput("BTC", Decimal("0.5"), Decimal("0.5"))],
        "reconciliation",
    )
    backfill_prices(cfg, ledger, FakeExchangeAdapter(), since=NOW, limit=1)

    rows = portfolio_history(cfg, ledger, exchange="kraken")

    assert rows == [
        {
            "date": "2030-01-08",
            "quote_currency": "EUR",
            "total_value": "5.5",
            "assets": {
                "BTC": {
                    "quantity": "0.5",
                    "close": "11",
                    "value": "5.5",
                    "price_status": "cached",
                }
            },
            "missing_prices": [],
            "valuation_status": "complete",
        }
    ]
