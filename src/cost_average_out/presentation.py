"""CLI presentation helpers.

This module renders existing domain result objects only. It does not evaluate
schedules, inspect exchange state, or make safety decisions.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cost_average_out.activation import ActivationEvaluation
from cost_average_out.config import AppConfig
from cost_average_out.cycle import DryRunResult, LiveRunResult, PlanPreview
from cost_average_out.ledger import LedgerSummary
from cost_average_out.planner import SellPlanItem
from cost_average_out.reconciliation import ReconciliationResult
from cost_average_out.scheduling import CycleEvaluation


@dataclass(frozen=True)
class KeyValue:
    label: str
    value: str


class OutputFormat(StrEnum):
    RICH = "rich"
    JSON = "json"


JSON_SCHEMA_VERSION = 1


class CliPresenter:
    """Render command snapshots as Rich output only when stdout is a terminal."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    @property
    def rich_enabled(self) -> bool:
        return self.console.is_terminal

    def json(self, payload: dict[str, Any]) -> None:
        versioned_payload = {"schema_version": JSON_SCHEMA_VERSION, **payload}
        self.console.file.write(
            json.dumps(versioned_payload, sort_keys=True, separators=(",", ":")) + "\n"
        )

    def schedule_status_payload(
        self,
        config: AppConfig,
        evaluation: CycleEvaluation | None,
        activation: ActivationEvaluation,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command": "schedule-status",
            "activation": _activation_payload(activation),
            "exchange": config.exchange,
            "kill_switch": config.safety.kill_switch,
            "live_trading_enabled": config.safety.live_trading_enabled,
            "schedule_interval": config.cost_average_out.interval.value,
            "status": "inactive" if evaluation is None else evaluation.status.value,
        }
        if evaluation is not None:
            payload.update(
                {
                    "cycle_id": evaluation.cycle_id,
                    "manual_approval_required": evaluation.requires_manual_approval,
                    "scheduled_at": evaluation.scheduled_at.isoformat(),
                }
            )
        return payload

    def schedule_status(
        self,
        config: AppConfig,
        evaluation: CycleEvaluation | None,
        activation: ActivationEvaluation,
    ) -> None:
        rows = [
            KeyValue("Activation status", activation.status.value),
            KeyValue("Activation type", activation.activation_type),
            KeyValue("Activation reason", activation.activation_reason or "none"),
            KeyValue(
                "Activated at",
                activation.activated_at.isoformat()
                if activation.activated_at
                else "none",
            ),
            KeyValue("Price trigger status", activation.price_trigger_status.value),
            KeyValue("Schedule", config.cost_average_out.interval.value),
            KeyValue("Exchange", config.exchange),
            KeyValue(
                "Live Trading", enabled_disabled(config.safety.live_trading_enabled)
            ),
            KeyValue("Kill Switch", on_off(config.safety.kill_switch)),
        ]
        if evaluation is None:
            rows.insert(0, KeyValue("Status", "inactive"))
            rows.append(KeyValue("Message", activation.message))
        else:
            rows.insert(0, KeyValue("Status", evaluation.status.value))
            rows.extend(
                [
                    KeyValue("Scheduled at", evaluation.scheduled_at.isoformat()),
                    KeyValue("Cycle ID", evaluation.cycle_id),
                    KeyValue(
                        "Manual approval required",
                        yes_no(evaluation.requires_manual_approval),
                    ),
                ]
            )
        self._summary("Schedule Status", rows)

    def status(
        self,
        config: AppConfig,
        ledger_path: Path,
        summary: LedgerSummary,
        evaluation: CycleEvaluation | None,
        activation: ActivationEvaluation,
        config_path: Path = Path("config.yaml"),
    ) -> None:
        self._summary(
            "Application Status",
            [
                KeyValue("Ledger", str(ledger_path)),
                KeyValue("Exchange", config.exchange),
                KeyValue("Quote currency", config.quote_currency),
                KeyValue("Timezone", config.timezone),
                KeyValue("Symbols", ", ".join(config.symbols)),
                KeyValue(
                    "Live Trading",
                    enabled_disabled(config.safety.live_trading_enabled),
                ),
                KeyValue("Kill Switch", on_off(config.safety.kill_switch)),
                KeyValue("Activation status", activation.status.value),
                KeyValue("Activation type", activation.activation_type),
                KeyValue("Activation reason", activation.activation_reason or "none"),
                KeyValue(
                    "Activated at",
                    activation.activated_at.isoformat()
                    if activation.activated_at
                    else "none",
                ),
                KeyValue("Price trigger status", activation.price_trigger_status.value),
                KeyValue(
                    "Schedule status",
                    "inactive" if evaluation is None else evaluation.status.value,
                ),
                KeyValue(
                    "Next/relevant scheduled at",
                    "none"
                    if evaluation is None
                    else evaluation.scheduled_at.isoformat(),
                ),
                KeyValue(
                    "Next/relevant cycle ID",
                    "none" if evaluation is None else evaluation.cycle_id,
                ),
                KeyValue(
                    "Manual approval required",
                    "no"
                    if evaluation is None
                    else yes_no(evaluation.requires_manual_approval),
                ),
                KeyValue("Cycles", str(sum(summary.cycle_counts.values()))),
                KeyValue("Planned orders", str(summary.planned_order_count)),
                KeyValue(
                    "Unresolved exchange orders",
                    str(summary.unresolved_exchange_order_count),
                ),
                KeyValue(
                    "Ledger Status",
                    "blocked" if summary.unresolved_exchange_order_count else "ready",
                ),
            ],
        )
        if summary.cycle_counts:
            self._summary(
                "Cycle Counts",
                [
                    KeyValue(cycle_status, str(count))
                    for cycle_status, count in sorted(summary.cycle_counts.items())
                ],
            )
        else:
            self._line("Cycle Counts: none")
        if summary.unresolved_exchange_order_count:
            self._blocked(
                "Unresolved application-created exchange orders exist.",
                _reconcile_command(config_path),
            )
            self._line(
                "Warning: unresolved app-created exchange orders block execution"
            )
        if summary.last_cycle_id is None:
            self._line("Last cycle: none")
        else:
            self._line(
                "Last cycle: "
                f"{summary.last_cycle_id} "
                f"({summary.last_cycle_status}, {summary.last_cycle_scheduled_at})"
            )

    def status_payload(
        self,
        config: AppConfig,
        ledger_path: Path,
        summary: LedgerSummary,
        evaluation: CycleEvaluation | None,
        activation: ActivationEvaluation,
        config_path: Path = Path("config.yaml"),
    ) -> dict[str, Any]:
        unresolved = summary.unresolved_exchange_order_count
        payload: dict[str, Any] = {
            "command": "status",
            "config": _public_config_payload(config),
            "activation": _activation_payload(activation),
            "ledger": {
                "cycle_counts": dict(sorted(summary.cycle_counts.items())),
                "last_cycle": {
                    "cycle_id": summary.last_cycle_id,
                    "scheduled_at": summary.last_cycle_scheduled_at,
                    "status": summary.last_cycle_status,
                },
                "path": str(ledger_path),
                "planned_orders": summary.planned_order_count,
                "status": "blocked" if unresolved else "ready",
                "total_cycles": sum(summary.cycle_counts.values()),
                "unresolved_exchange_orders": unresolved,
            },
            "schedule": _cycle_evaluation_payload(evaluation)
            if evaluation is not None
            else None,
        }
        if unresolved:
            payload["blocked"] = _blocked_payload(
                "Unresolved application-created exchange orders exist.",
                _reconcile_command(config_path),
            )
        return payload

    def snapshot_balances(
        self,
        config: AppConfig,
        *,
        balance_count: int,
        market_count: int,
    ) -> None:
        self._summary(
            "Balance Snapshot",
            [
                KeyValue(
                    "Initial balance snapshot recorded",
                    f"{balance_count} balances",
                ),
                KeyValue("Markets validated", str(market_count)),
                KeyValue("Exchange", config.exchange),
                KeyValue(
                    "Live Trading",
                    enabled_disabled(config.safety.live_trading_enabled),
                ),
                KeyValue("Kill Switch", on_off(config.safety.kill_switch)),
            ],
        )

    def plan_preview(
        self,
        config: AppConfig,
        preview: PlanPreview,
        config_path: Path = Path("config.yaml"),
    ) -> None:
        planned = len(preview.sell_plan.planned_items)
        total = len(preview.sell_plan.items)
        self._summary(
            "Sell Plan",
            [
                KeyValue(
                    "Activation status",
                    preview.activation.status.value
                    if preview.activation
                    else "unknown",
                ),
                KeyValue(
                    "Activation type",
                    preview.activation.activation_type
                    if preview.activation
                    else "unknown",
                ),
                KeyValue(
                    "Activation reason",
                    (preview.activation.activation_reason or "none")
                    if preview.activation
                    else "none",
                ),
                KeyValue(
                    "Price trigger status",
                    preview.activation.price_trigger_status.value
                    if preview.activation
                    else "unknown",
                ),
                KeyValue("Observed at", preview.sell_plan.observed_at.isoformat()),
                KeyValue("Execution blocked", yes_no(preview.execution_blocked)),
                KeyValue("Planned orders", str(planned)),
                KeyValue("Skipped/blocked", str(total - planned)),
                KeyValue("Exchange", config.exchange),
                KeyValue(
                    "Live Trading",
                    enabled_disabled(config.safety.live_trading_enabled),
                ),
                KeyValue("Kill Switch", on_off(config.safety.kill_switch)),
            ],
        )
        for reason in preview.block_reasons:
            self._line(f"Block reason: {reason}")
        if preview.execution_blocked:
            self._blocked(
                "; ".join(preview.block_reasons) or "Sell plan has blocked items.",
                _reconcile_command(config_path),
            )
        self._plan_items(preview.sell_plan.items)

    def plan_preview_payload(
        self,
        config: AppConfig,
        preview: PlanPreview,
        config_path: Path = Path("config.yaml"),
    ) -> dict[str, Any]:
        planned = len(preview.sell_plan.planned_items)
        total = len(preview.sell_plan.items)
        payload: dict[str, Any] = {
            "activation": _activation_payload(preview.activation)
            if preview.activation
            else None,
            "block_reasons": list(preview.block_reasons),
            "command": "plan",
            "config": _public_config_payload(config),
            "execution_blocked": preview.execution_blocked,
            "observed_at": preview.sell_plan.observed_at.isoformat(),
            "planned_orders": planned,
            "sell_plan": [_plan_item_payload(item) for item in preview.sell_plan.items],
            "skipped_or_blocked": total - planned,
        }
        if preview.execution_blocked:
            payload["blocked"] = _blocked_payload(
                "; ".join(preview.block_reasons) or "Sell plan has blocked items.",
                _reconcile_command(config_path),
            )
        return payload

    def dry_run(self, config: AppConfig, result: DryRunResult) -> None:
        rows = [
            KeyValue("Activation status", result.activation.status.value),
            KeyValue("Activation type", result.activation.activation_type),
            KeyValue(
                "Activation reason", result.activation.activation_reason or "none"
            ),
            KeyValue(
                "Activated at",
                result.activation.activated_at.isoformat()
                if result.activation.activated_at
                else "none",
            ),
            KeyValue(
                "Price trigger status", result.activation.price_trigger_status.value
            ),
            KeyValue(
                "Dry run cycle status",
                "inactive"
                if result.evaluation is None
                else result.evaluation.status.value,
            ),
            KeyValue(
                "Scheduled at",
                "none"
                if result.evaluation is None
                else result.evaluation.scheduled_at.isoformat(),
            ),
            KeyValue(
                "Cycle ID",
                "none" if result.evaluation is None else result.evaluation.cycle_id,
            ),
            KeyValue(
                "Manual approval required",
                "no"
                if result.evaluation is None
                else yes_no(result.evaluation.requires_manual_approval),
            ),
            KeyValue("Exchange", config.exchange),
            KeyValue(
                "Live Trading",
                enabled_disabled(config.safety.live_trading_enabled),
            ),
            KeyValue("Kill Switch", on_off(config.safety.kill_switch)),
        ]
        if result.persisted_cycle_id is not None:
            rows.append(KeyValue("Simulation persisted", result.persisted_cycle_id))
        self._summary("Dry Run", rows)
        if result.preview is not None:
            self.plan_preview(config, result.preview)
        self._line(f"Notification preview: {result.notification_preview}")

    def dry_run_payload(
        self,
        config: AppConfig,
        result: DryRunResult,
    ) -> dict[str, Any]:
        return {
            "command": "run-once",
            "mode": "dry-run",
            "activation": _activation_payload(result.activation),
            "config": _public_config_payload(config),
            "notification_preview": result.notification_preview,
            "persisted_cycle_id": result.persisted_cycle_id,
            "schedule": _cycle_evaluation_payload(result.evaluation)
            if result.evaluation is not None
            else None,
            "sell_plan": [
                _plan_item_payload(item)
                for item in (result.preview.sell_plan.items if result.preview else ())
            ],
        }

    def live_run_payload(
        self,
        config: AppConfig,
        result: LiveRunResult,
    ) -> dict[str, Any]:
        return {
            "command": "run-once",
            "mode": "live",
            "activation": _activation_payload(result.activation),
            "config": _public_config_payload(config),
            "notification_preview": result.notification_preview,
            "schedule": _cycle_evaluation_payload(result.evaluation),
            "submitted_orders": result.submitted_order_count,
        }

    def live_run(self, config: AppConfig, result: LiveRunResult) -> None:
        self._summary(
            "Live Run Summary",
            [
                KeyValue("Activation status", result.activation.status.value),
                KeyValue("Activation type", result.activation.activation_type),
                KeyValue(
                    "Activation reason", result.activation.activation_reason or "none"
                ),
                KeyValue("Live cycle status", result.evaluation.status.value),
                KeyValue("Scheduled at", result.evaluation.scheduled_at.isoformat()),
                KeyValue("Cycle ID", result.evaluation.cycle_id),
                KeyValue("Submitted orders", str(result.submitted_order_count)),
                KeyValue(
                    "Post-submit reconciliation blocked",
                    yes_no(result.reconciliation.execution_blocked),
                ),
                KeyValue("Open Orders", str(result.reconciliation.open_order_count)),
                KeyValue(
                    "Unresolved app orders",
                    str(result.reconciliation.unresolved_app_order_count),
                ),
                KeyValue("Exchange", config.exchange),
                KeyValue(
                    "Live Trading",
                    enabled_disabled(config.safety.live_trading_enabled),
                ),
                KeyValue("Kill Switch", on_off(config.safety.kill_switch)),
            ],
        )
        self._line(f"Notification preview: {result.notification_preview}")

    def reconciliation(
        self,
        config: AppConfig,
        result: ReconciliationResult,
        config_path: Path = Path("config.yaml"),
    ) -> None:
        self._summary(
            "Reconciliation",
            [
                KeyValue("Observed at", result.observed_at.isoformat()),
                KeyValue("Balances recorded", str(result.balance_count)),
                KeyValue("Markets validated", str(result.market_count)),
                KeyValue("Open orders", str(result.open_order_count)),
                KeyValue("Recent orders", str(result.recent_order_count)),
                KeyValue("Recent fills", str(result.recent_fill_count)),
                KeyValue("Matched app orders", str(result.matched_order_count)),
                KeyValue("Recorded fills", str(result.recorded_fill_count)),
                KeyValue(
                    "Unresolved app orders",
                    str(result.unresolved_app_order_count),
                ),
                KeyValue("Execution blocked", yes_no(result.execution_blocked)),
                KeyValue("Exchange", config.exchange),
                KeyValue(
                    "Live Trading",
                    enabled_disabled(config.safety.live_trading_enabled),
                ),
                KeyValue("Kill Switch", on_off(config.safety.kill_switch)),
            ],
        )
        if result.execution_blocked:
            self._blocked(
                "Unresolved application-created exchange orders exist.",
                _reconcile_command(config_path),
            )

    def reconciliation_payload(
        self,
        config: AppConfig,
        result: ReconciliationResult,
        config_path: Path = Path("config.yaml"),
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "balances_recorded": result.balance_count,
            "command": "reconcile",
            "config": _public_config_payload(config),
            "execution_blocked": result.execution_blocked,
            "markets_validated": result.market_count,
            "matched_app_orders": result.matched_order_count,
            "observed_at": result.observed_at.isoformat(),
            "open_orders": result.open_order_count,
            "recent_fills": result.recent_fill_count,
            "recent_orders": result.recent_order_count,
            "recorded_fills": result.recorded_fill_count,
            "unresolved_app_orders": result.unresolved_app_order_count,
        }
        if result.execution_blocked:
            payload["blocked"] = _blocked_payload(
                "Unresolved application-created exchange orders exist.",
                _reconcile_command(config_path),
            )
        return payload

    def _summary(self, title: str, rows: Iterable[KeyValue]) -> None:
        items = list(rows)
        if not self.rich_enabled:
            for item in items:
                self._line(f"{item.label}: {item.value}")
            return

        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()
        for item in items:
            table.add_row(item.label, item.value)
        self.console.print(Panel(table, title=title, box=box.ROUNDED, expand=False))

    def _plan_items(self, items: Iterable[SellPlanItem]) -> None:
        plan_items = list(items)
        if not self.rich_enabled:
            self._line("Sell plan:")
            for item in plan_items:
                self._line(format_plan_item_plain(item))
            return

        table = Table(title="Sell Plan Items", box=box.SIMPLE_HEAVY)
        table.add_column("Symbol")
        table.add_column("Status")
        table.add_column("Quantity", justify="right")
        table.add_column("Estimated Value", justify="right")
        table.add_column("Reason")
        for item in plan_items:
            table.add_row(
                item.symbol,
                str(item.status.value),
                format_decimal(item.quantity),
                format_decimal(item.estimated_quote_value),
                item.reason or "",
            )
        self.console.print(table)

    def _line(self, value: str) -> None:
        self.console.print(value, highlight=False)

    def _blocked(self, reason: str, recommended_command: str) -> None:
        self._line("Blocked")
        self._line(f"Reason: {reason}")
        self.console.file.write(f"Recommended action: {recommended_command}\n")


def _public_config_payload(config: AppConfig) -> dict[str, Any]:
    return {
        "exchange": config.exchange,
        "kill_switch": config.safety.kill_switch,
        "live_trading_enabled": config.safety.live_trading_enabled,
        "quote_currency": config.quote_currency,
        "symbols": list(config.symbols),
        "timezone": config.timezone,
    }


def _activation_payload(activation: ActivationEvaluation) -> dict[str, Any]:
    return {
        "activated": activation.activated,
        "activation_status": activation.status.value,
        "activation_type": activation.activation_type,
        "activated_at": activation.activated_at.isoformat()
        if activation.activated_at
        else None,
        "activation_reason": activation.activation_reason,
        "message": activation.message,
        "price_trigger_status": activation.price_trigger_status.value,
        "reference_symbol": activation.reference_symbol,
        "reference_price": str(activation.reference_price)
        if activation.reference_price is not None
        else None,
        "threshold": str(activation.threshold)
        if activation.threshold is not None
        else None,
    }


def _cycle_evaluation_payload(evaluation: CycleEvaluation) -> dict[str, Any]:
    return {
        "cycle_id": evaluation.cycle_id,
        "manual_approval_required": evaluation.requires_manual_approval,
        "scheduled_at": evaluation.scheduled_at.isoformat(),
        "status": evaluation.status.value,
    }


def _plan_item_payload(item: SellPlanItem) -> dict[str, Any]:
    return {
        "base_asset": item.base_asset,
        "estimated_quote_value": format_decimal(item.estimated_quote_value),
        "quantity": format_decimal(item.quantity),
        "reason": item.reason,
        "status": item.status.value,
        "symbol": item.symbol,
    }


def _reconcile_command(config_path: Path) -> str:
    return f"cost-average-out reconcile --config {shlex.quote(str(config_path))}"


def _blocked_payload(reason: str, recommended_command: str) -> dict[str, str]:
    return {
        "reason": reason,
        "recommended_command": recommended_command,
        "status": "blocked",
    }


def format_plan_item_plain(item: SellPlanItem) -> str:
    reason = f"; reason={item.reason}" if item.reason is not None else ""
    return (
        f"  {item.symbol}: status={item.status.value}; "
        f"quantity={format_decimal(item.quantity)}; "
        f"estimated_value={format_decimal(item.estimated_quote_value)}"
        f"{reason}"
    )


def format_decimal(value: object) -> str:
    return format(value, "f")


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def enabled_disabled(value: bool) -> str:
    return "enabled" if value else "disabled"


def on_off(value: bool) -> str:
    return "on" if value else "off"
