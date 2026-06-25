from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import StringIO

from rich.console import Console

from cost_average_out.config import AppConfig
from cost_average_out.planner import PlanItemStatus, SellPlanItem
from cost_average_out.presentation import CliPresenter, format_plan_item_plain
from cost_average_out.scheduling import evaluate_cycle
from tests.test_config import valid_config_data


def app_config() -> AppConfig:
    return AppConfig.model_validate(valid_config_data())


def test_plain_plan_item_format_matches_script_friendly_output() -> None:
    item = SellPlanItem(
        symbol="BTC/EUR",
        base_asset="BTC",
        quantity=Decimal("0.005"),
        estimated_quote_value=Decimal("250"),
        status=PlanItemStatus.PLANNED,
    )

    assert (
        format_plan_item_plain(item)
        == "  BTC/EUR: status=planned; quantity=0.005; estimated_value=250"
    )


def test_presenter_plain_schedule_status_includes_safety_snapshot() -> None:
    config = app_config()
    evaluation = evaluate_cycle(
        config,
        datetime.fromisoformat("2029-12-31T22:00:00+00:00"),
    )
    output = StringIO()
    presenter = CliPresenter(Console(file=output, force_terminal=False))

    presenter.schedule_status(config, evaluation)

    assert "Status: not_due" in output.getvalue()
    assert "Exchange: kraken" in output.getvalue()
    assert "Live Trading: disabled" in output.getvalue()
    assert "Kill Switch: off" in output.getvalue()
