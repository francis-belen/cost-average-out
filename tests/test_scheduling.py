from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from cost_average_out.config import AppConfig
from cost_average_out.scheduling import CycleStatus, evaluate_cycle
from tests.test_config import valid_config_data

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def make_config(
    *,
    start_date: str = "2030-01-10",
    end_date: str | None = None,
    interval: str = "weekly",
) -> AppConfig:
    data: dict[str, Any] = valid_config_data()
    settings = data["cost_average_out"]
    settings["start_date"] = start_date
    settings["end_date"] = end_date
    settings["interval"] = interval
    return AppConfig.model_validate(data)


def local_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=AMSTERDAM)


def test_cycle_status_distinguishes_not_due_due_and_missed() -> None:
    config = make_config()

    not_due = evaluate_cycle(config, local_datetime("2030-01-09T23:59:00"))
    due = evaluate_cycle(config, local_datetime("2030-01-10T23:59:00"))
    missed = evaluate_cycle(config, local_datetime("2030-01-11T00:00:00"))

    assert not_due.status is CycleStatus.NOT_DUE
    assert due.status is CycleStatus.DUE
    assert missed.status is CycleStatus.MISSED
    assert missed.requires_manual_approval is True


def test_cycle_id_is_stable_for_repeated_runs_and_symbol_order() -> None:
    config = make_config()
    reordered_data = valid_config_data()
    reordered_data["cost_average_out"]["start_date"] = "2030-01-10"
    reordered_data["symbols"] = ["ETH/EUR", "BTC/EUR"]
    reordered = AppConfig.model_validate(reordered_data)
    now = local_datetime("2030-01-10T12:00:00")

    first = evaluate_cycle(config, now)
    second = evaluate_cycle(config, now)
    with_reordered_symbols = evaluate_cycle(reordered, now)

    assert first.cycle_id == second.cycle_id
    assert first.cycle_id == with_reordered_symbols.cycle_id
    assert first.cycle_id.startswith("cao-2030-01-10-")


def test_weekly_cycle_uses_start_date_as_anchor() -> None:
    config = make_config()

    due = evaluate_cycle(config, local_datetime("2030-01-17T08:00:00"))
    missed = evaluate_cycle(config, local_datetime("2030-01-18T08:00:00"))

    assert due.status is CycleStatus.DUE
    assert due.scheduled_at.date().isoformat() == "2030-01-17"
    assert missed.status is CycleStatus.MISSED
    assert missed.scheduled_at.date().isoformat() == "2030-01-17"


def test_monthly_schedule_clamps_to_last_day_of_short_month() -> None:
    config = make_config(start_date="2030-01-31", interval="monthly")

    february = evaluate_cycle(config, local_datetime("2030-02-28T10:00:00"))
    march_before_due = evaluate_cycle(config, local_datetime("2030-03-30T10:00:00"))
    march_due = evaluate_cycle(config, local_datetime("2030-03-31T10:00:00"))

    assert february.status is CycleStatus.DUE
    assert february.scheduled_at.date().isoformat() == "2030-02-28"
    assert march_before_due.status is CycleStatus.MISSED
    assert march_before_due.scheduled_at.date().isoformat() == "2030-02-28"
    assert march_due.status is CycleStatus.DUE


def test_schedule_preserves_local_midnight_across_dst() -> None:
    config = make_config(start_date="2030-03-24", interval="weekly")

    before_dst = evaluate_cycle(config, local_datetime("2030-03-24T12:00:00"))
    after_dst = evaluate_cycle(config, local_datetime("2030-04-07T12:00:00"))

    assert before_dst.scheduled_at.hour == 0
    assert after_dst.scheduled_at.hour == 0
    before_offset = before_dst.scheduled_at.utcoffset()
    after_offset = after_dst.scheduled_at.utcoffset()
    assert before_offset is not None
    assert after_offset is not None
    assert before_offset.total_seconds() == 3600
    assert after_offset.total_seconds() == 7200


def test_end_date_prevents_new_cycles() -> None:
    config = make_config(end_date="2030-01-17")

    evaluation = evaluate_cycle(config, local_datetime("2030-02-01T12:00:00"))

    assert evaluation.status is CycleStatus.MISSED
    assert evaluation.scheduled_at.date().isoformat() == "2030-01-17"


def test_daily_schedule_is_due_each_active_day() -> None:
    config = make_config(interval="daily")

    evaluation = evaluate_cycle(config, local_datetime("2030-01-15T23:59:00"))

    assert evaluation.status is CycleStatus.DUE
    assert evaluation.scheduled_at.date().isoformat() == "2030-01-15"


def test_naive_current_time_is_rejected() -> None:
    config = make_config()

    try:
        evaluate_cycle(config, datetime(2030, 1, 10, 12))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("expected naive datetime to be rejected")
