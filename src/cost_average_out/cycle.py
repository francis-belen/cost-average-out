"""Dry-run cycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from cost_average_out.activation import ActivationEvaluation, evaluate_activation
from cost_average_out.config import AppConfig, PercentageBasis
from cost_average_out.exchange import (
    APP_CLIENT_ORDER_PREFIX,
    Balance,
    ExchangeAdapter,
    ExchangeError,
    ExchangeTimeoutError,
    OrderStatus,
)
from cost_average_out.ledger import (
    CycleState,
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
from cost_average_out.reconciliation import ReconciliationResult, reconcile
from cost_average_out.scheduling import CycleEvaluation, CycleStatus, evaluate_cycle


@dataclass(frozen=True)
class PlanPreview:
    sell_plan: SellPlan
    execution_blocked: bool
    block_reasons: tuple[str, ...]
    activation: ActivationEvaluation | None = None


class LiveExecutionBlockedError(RuntimeError):
    """Raised when live execution is unsafe or not explicitly authorized."""


@dataclass(frozen=True)
class LiveRunResult:
    evaluation: CycleEvaluation
    activation: ActivationEvaluation
    submitted_order_count: int
    reconciliation: ReconciliationResult
    notification_preview: str


@dataclass(frozen=True)
class DryRunResult:
    evaluation: CycleEvaluation | None
    activation: ActivationEvaluation
    preview: PlanPreview | None
    persisted_cycle_id: str | None
    notification_preview: str


def preview_plan(
    config: AppConfig,
    ledger: Ledger,
    adapter: ExchangeAdapter,
    *,
    now: datetime | None = None,
    persist_activation: bool = False,
) -> PlanPreview:
    """Build a read-only sell-plan preview."""

    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    activation = evaluate_activation(
        config,
        ledger,
        now=observed_at,
        adapter=adapter,
        persist=persist_activation,
    )
    if not activation.activated:
        return PlanPreview(
            sell_plan=SellPlan(observed_at=observed_at, items=()),
            execution_blocked=True,
            block_reasons=(activation.message,),
            activation=activation,
        )

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
        activation=activation,
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

    preview = preview_plan(config, ledger, adapter, now=evaluated_at)
    activation = preview.activation
    assert activation is not None
    if not activation.activated:
        return DryRunResult(
            evaluation=None,
            activation=activation,
            preview=preview,
            persisted_cycle_id=None,
            notification_preview=f"Dry run: inactive; {activation.message}.",
        )

    evaluation = evaluate_cycle(
        config,
        evaluated_at,
        activation_time=activation.activated_at,
    )
    if evaluation.status is CycleStatus.NOT_DUE:
        return DryRunResult(
            evaluation=evaluation,
            activation=activation,
            preview=None,
            persisted_cycle_id=None,
            notification_preview="Dry run: no cycle is due.",
        )
    if evaluation.requires_manual_approval:
        return DryRunResult(
            evaluation=evaluation,
            activation=activation,
            preview=None,
            persisted_cycle_id=None,
            notification_preview="Dry run: missed cycle requires manual approval.",
        )

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
        activation=activation,
        preview=preview,
        persisted_cycle_id=persisted_cycle_id,
        notification_preview=notification_preview,
    )


def live_run_once(
    config: AppConfig,
    ledger: Ledger,
    adapter: ExchangeAdapter,
    *,
    now: datetime | None = None,
    confirm_first_live_sell: bool = False,
    lookback: timedelta = timedelta(days=7),
) -> LiveRunResult:
    """Execute one guarded live cycle with immediate local persistence."""

    evaluated_at = now or datetime.now(UTC)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    activation = evaluate_activation(
        config,
        ledger,
        now=evaluated_at,
        adapter=adapter,
        persist=True,
    )
    if not activation.activated:
        raise LiveExecutionBlockedError(
            f"program is waiting for activation: {activation.message}"
        )
    if not config.safety.live_trading_enabled:
        raise LiveExecutionBlockedError("live_trading_enabled is false")
    if config.safety.kill_switch:
        raise LiveExecutionBlockedError("kill_switch is true")
    if (
        config.safety.require_first_live_sell_confirmation
        and not confirm_first_live_sell
        and int(ledger.summary().cycle_counts.get(CycleState.COMPLETED.value, 0)) == 0
    ):
        raise LiveExecutionBlockedError("first live sell confirmation is required")

    evaluation = evaluate_cycle(
        config, evaluated_at, activation_time=activation.activated_at
    )
    if evaluation.status is CycleStatus.NOT_DUE:
        raise LiveExecutionBlockedError("no cycle is due")
    if evaluation.requires_manual_approval:
        raise LiveExecutionBlockedError("missed cycle requires manual approval")

    preview = preview_plan(config, ledger, adapter, now=evaluated_at)
    if preview.execution_blocked:
        raise LiveExecutionBlockedError(
            "execution blocked: " + "; ".join(preview.block_reasons)
        )
    planned_items = preview.sell_plan.planned_items
    if not planned_items:
        raise LiveExecutionBlockedError("sell plan contains no executable orders")

    ledger.create_cycle_with_orders(
        evaluation.cycle_id,
        evaluation.scheduled_at,
        [_planned_order_input(item) for item in preview.sell_plan.items],
    )
    ledger.set_cycle_status(evaluation.cycle_id, CycleState.EXECUTING)

    submitted = 0
    for item in planned_items:
        client_order_id = _client_order_id(evaluation.cycle_id, item.symbol)
        try:
            order = adapter.submit_market_sell_order(
                item.symbol,
                item.quantity,
                client_order_id,
            )
        except ExchangeTimeoutError:
            ledger.register_exchange_order(
                evaluation.cycle_id,
                item.symbol,
                adapter.name,
                client_order_id,
                status=CycleState.UNKNOWN_REQUIRES_RECONCILIATION.value,
            )
            ledger.update_planned_order_status(
                evaluation.cycle_id,
                item.symbol,
                PlannedOrderState.FAILED,
                "submission timeout; reconciliation required",
            )
            ledger.set_cycle_status(
                evaluation.cycle_id,
                CycleState.UNKNOWN_REQUIRES_RECONCILIATION,
            )
            raise
        except ExchangeError as exc:
            ledger.update_planned_order_status(
                evaluation.cycle_id,
                item.symbol,
                PlannedOrderState.FAILED,
                f"submission rejected: {exc}",
            )
            ledger.set_cycle_status(evaluation.cycle_id, CycleState.FAILED)
            raise

        ledger.register_exchange_order(
            evaluation.cycle_id,
            item.symbol,
            adapter.name,
            client_order_id,
            status=_exchange_order_status(order.status),
            exchange_order_id=order.exchange_order_id,
            raw=order.raw,
        )
        ledger.update_planned_order_status(
            evaluation.cycle_id,
            item.symbol,
            PlannedOrderState.SUBMITTED,
        )
        submitted += 1

    reconciliation = reconcile(
        config,
        ledger,
        adapter,
        now=evaluated_at,
        lookback=lookback,
    )
    if reconciliation.execution_blocked:
        ledger.set_cycle_status(evaluation.cycle_id, CycleState.PARTIAL)
    else:
        ledger.set_cycle_status(evaluation.cycle_id, CycleState.COMPLETED)

    return LiveRunResult(
        evaluation=evaluation,
        activation=activation,
        submitted_order_count=submitted,
        reconciliation=reconciliation,
        notification_preview=(
            f"Live run: submitted {submitted} order(s); "
            f"recorded {reconciliation.recorded_fill_count} fill(s)."
        ),
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


def _client_order_id(cycle_id: str, symbol: str) -> str:
    digest = sha256(f"{cycle_id}|{symbol}".encode()).hexdigest()[:14]
    return f"{APP_CLIENT_ORDER_PREFIX}{digest}"


def _exchange_order_status(status: OrderStatus) -> str:
    return {
        OrderStatus.OPEN: "open",
        OrderStatus.PARTIAL: "partial",
        OrderStatus.FILLED: "filled",
        OrderStatus.CANCELED: "canceled",
        OrderStatus.FAILED: "failed",
        OrderStatus.UNKNOWN_REQUIRES_RECONCILIATION: "unknown_requires_reconciliation",
    }[status]
