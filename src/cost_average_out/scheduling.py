"""Pure, timezone-aware recurring-cycle scheduling."""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from cost_average_out.config import AppConfig, Interval, MissedCyclePolicy


class CycleStatus(StrEnum):
    NOT_DUE = "not_due"
    DUE = "due"
    MISSED = "missed"


@dataclass(frozen=True)
class CycleEvaluation:
    status: CycleStatus
    scheduled_at: datetime
    cycle_id: str
    requires_manual_approval: bool


def evaluate_cycle(config: AppConfig, now: datetime) -> CycleEvaluation:
    """Evaluate the current cycle without reading or writing external state."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    timezone = ZoneInfo(config.timezone)
    local_date = now.astimezone(timezone).date()
    scheduled_date = _relevant_schedule_date(config, local_date)
    scheduled_at = datetime.combine(
        scheduled_date,
        datetime.min.time(),
        tzinfo=timezone,
    )

    if local_date < scheduled_date:
        status = CycleStatus.NOT_DUE
    elif local_date == scheduled_date:
        status = CycleStatus.DUE
    else:
        status = CycleStatus.MISSED

    requires_manual_approval = (
        status is CycleStatus.MISSED
        and config.safety.missed_cycle_policy
        is MissedCyclePolicy.REQUIRE_MANUAL_APPROVAL
    )
    return CycleEvaluation(
        status=status,
        scheduled_at=scheduled_at,
        cycle_id=cycle_id(config, scheduled_at),
        requires_manual_approval=requires_manual_approval,
    )


def cycle_id(config: AppConfig, scheduled_at: datetime) -> str:
    """Return an ID stable across repeated evaluations of the same cycle."""

    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise ValueError("scheduled_at must be timezone-aware")

    settings = config.cost_average_out
    identity = {
        "config_version": config.config_version,
        "exchange": config.exchange,
        "quote_currency": config.quote_currency,
        "timezone": config.timezone,
        "start_date": settings.start_date.isoformat(),
        "end_date": settings.end_date.isoformat() if settings.end_date else None,
        "interval": settings.interval.value,
        "percentage": settings.percentage,
        "percentage_basis": settings.percentage_basis.value,
        "symbols": sorted(config.symbols),
        "scheduled_at": scheduled_at.astimezone(UTC).isoformat(),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return f"cao-{scheduled_at.date().isoformat()}-{digest}"


def _relevant_schedule_date(config: AppConfig, local_date: date) -> date:
    settings = config.cost_average_out
    start = settings.start_date
    end = settings.end_date

    if local_date < start:
        return start

    effective_date = min(local_date, end) if end is not None else local_date
    if settings.interval is Interval.DAILY:
        return effective_date
    if settings.interval is Interval.WEEKLY:
        elapsed_days = (effective_date - start).days
        return start + timedelta(days=(elapsed_days // 7) * 7)
    return _latest_monthly_date(start, effective_date)


def _latest_monthly_date(start: date, current: date) -> date:
    month_offset = (current.year - start.year) * 12 + current.month - start.month
    candidate = _add_months(start, month_offset)
    if candidate > current:
        candidate = _add_months(start, month_offset - 1)
    return candidate


def _add_months(start: date, months: int) -> date:
    absolute_month = start.year * 12 + start.month - 1 + months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
