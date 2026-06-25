"""Dry-run cycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cost_average_out.config import AppConfig, PercentageBasis
from cost_average_out.exchange import Balance, ExchangeAdapter
from cost_average_out.ledger import (
    Ledger,
    PlannedOrderInput,
    PlannedOrderState,
)
from cost_average_out.planner import (
    PlanItemStatus,
    SellPlan,
    SellPlanItem,
    build_sell_plan,
)
from cost_average_out.scheduling import CycleEvaluation, CycleStatus, evaluate_cycle


@dataclass(frozen=True)
class PlanPreview:
    sell_plan: SellPlan
    execution_blocked: bool
    block_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DryRunResult:
    evaluation: CycleEvaluation
    preview: PlanPreview | None
    persisted_cycle_id: str | None
    notification_preview: str


def preview_plan(
    config: AppConfig,
    ledger: Ledger,
    adapter: ExchangeAdapter,
    *,
    now: datetime | None = None,
) -> PlanPreview:
    """Build a read-only sell-plan preview."""

    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    markets = adapter.fetch_markets(config.symbols)
    balances = adapter.fetch_balances()
    tickers = adapter.fetch_tickers(config.symbols)
    initial_balances: list[Balance] | None = None
    if config.cost_average_out.percentage_basis is PercentageBasis.INITIAL_SNAPSHOT:
        initial_balances = [
            Balance(record.asset, record.available, record.total)
            for record in ledger.latest_balances(
                adapter.name,
                source="initial_snapshot",
            ).values()
        ]

    sell_plan = build_sell_plan(
        config,
        list(balances),
        list(markets),
        list(tickers),
        initial_balances=initial_balances,
        now=observed_at,
    )
    unresolved_orders = ledger.summary().unresolved_exchange_order_count
    block_reasons = [
        item.reason
        for item in sell_plan.items
        if item.status is PlanItemStatus.BLOCKED and item.reason is not None
    ]
    if unresolved_orders:
        block_reasons.append("unresolved app-created exchange orders")
    return PlanPreview(
        sell_plan=sell_plan,
        execution_blocked=bool(block_reasons),
        block_reasons=tuple(block_reasons),
    )


def dry_run_once(
    config: AppConfig,
    ledger: Ledger,
    adapter: ExchangeAdapter,
    *,
    now: datetime | None = None,
    persist_simulation: bool = False,
) -> DryRunResult:
    """Evaluate one timer cycle without submitting exchange orders."""

    evaluated_at = now or datetime.now(UTC)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    evaluation = evaluate_cycle(config, evaluated_at)
    if evaluation.status is CycleStatus.NOT_DUE:
        return DryRunResult(
            evaluation=evaluation,
            preview=None,
            persisted_cycle_id=None,
            notification_preview="Dry run: no cycle is due.",
        )
    if evaluation.requires_manual_approval:
        return DryRunResult(
            evaluation=evaluation,
            preview=None,
            persisted_cycle_id=None,
            notification_preview="Dry run: missed cycle requires manual approval.",
        )

    preview = preview_plan(config, ledger, adapter, now=evaluated_at)
    persisted_cycle_id = None
    if persist_simulation:
        ledger.create_cycle_with_orders(
            evaluation.cycle_id,
            evaluation.scheduled_at,
            [_planned_order_input(item) for item in preview.sell_plan.items],
        )
        persisted_cycle_id = evaluation.cycle_id

    status = "blocked" if preview.execution_blocked else "ready"
    planned_count = len(preview.sell_plan.planned_items)
    notification_preview = (
        f"Dry run: {status}; {planned_count} planned sell order(s); "
        f"{len(preview.sell_plan.items) - planned_count} skipped/blocked item(s)."
    )
    return DryRunResult(
        evaluation=evaluation,
        preview=preview,
        persisted_cycle_id=persisted_cycle_id,
        notification_preview=notification_preview,
    )


def _planned_order_input(item: SellPlanItem) -> PlannedOrderInput:
    status = {
        PlanItemStatus.PLANNED: PlannedOrderState.PLANNED,
        PlanItemStatus.SKIPPED: PlannedOrderState.SKIPPED,
        PlanItemStatus.BLOCKED: PlannedOrderState.BLOCKED,
    }[item.status]
    return PlannedOrderInput(
        symbol=item.symbol,
        quantity=item.quantity,
        estimated_quote_value=item.estimated_quote_value,
        status=status,
        reason=item.reason,
    )
