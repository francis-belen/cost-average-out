from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from cost_average_out.config import ConfigError, load_config
from cost_average_out.cycle import (
    LiveExecutionBlockedError,
    PlanPreview,
    dry_run_once,
    live_run_once,
    preview_plan,
)
from cost_average_out.exchange import (
    ExchangeError,
    ExchangeTimeoutError,
    create_exchange_adapter,
)
from cost_average_out.ledger import BalanceInput, Ledger, LedgerError
from cost_average_out.notifications import (
    NotificationError,
    NotificationKind,
    NotificationMessage,
    NotificationProvider,
    create_notification_provider,
)
from cost_average_out.planner import SellPlanItem
from cost_average_out.portfolio import backfill_prices, default_since, portfolio_history
from cost_average_out.reconciliation import reconcile as reconcile_exchange
from cost_average_out.scheduling import evaluate_cycle

app = typer.Typer(help="Cost Average Out CLI.")


@app.command()
def validate_config(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
) -> None:
    """Validate configuration without submitting exchange orders."""
    try:
        validated = load_config(config)
    except ConfigError as exc:
        typer.echo(f"Configuration invalid: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    trading_state = "enabled" if validated.safety.live_trading_enabled else "disabled"
    typer.echo(
        f"Configuration valid: {config} "
        f"(offline validation; live trading {trading_state})."
    )
    typer.echo(
        f"Exchange: {validated.exchange}; quote: {validated.quote_currency}; "
        f"timezone: {validated.timezone}; symbols: {', '.join(validated.symbols)}"
    )


@app.command("schedule-status")
def schedule_status(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
    at: Annotated[
        str | None,
        typer.Option(
            "--at",
            help="Evaluate at an ISO-8601 instant; defaults to the current time.",
        ),
    ] = None,
) -> None:
    """Show the current schedule decision without external side effects."""
    try:
        validated = load_config(config)
        now = _parse_instant(at) if at is not None else datetime.now(UTC)
        evaluation = evaluate_cycle(validated, now)
    except (ConfigError, ValueError) as exc:
        typer.echo(f"Schedule evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Status: {evaluation.status.value}")
    typer.echo(f"Scheduled at: {evaluation.scheduled_at.isoformat()}")
    typer.echo(f"Cycle ID: {evaluation.cycle_id}")
    typer.echo(
        "Manual approval required: "
        f"{'yes' if evaluation.requires_manual_approval else 'no'}"
    )


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--at must include a UTC offset or Z suffix")
    return parsed


@app.command("init-ledger")
def init_ledger(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
) -> None:
    """Create or migrate the configured SQLite ledger."""
    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        ledger.migrate()
    except (ConfigError, LedgerError, OSError) as exc:
        typer.echo(f"Ledger initialization failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Ledger ready: {ledger.path}")


@app.command()
def status(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
) -> None:
    """Show read-only application status from the SQLite ledger."""
    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        summary = ledger.summary()
        evaluation = evaluate_cycle(validated, datetime.now(UTC))
    except (ConfigError, LedgerError, OSError, ValueError) as exc:
        typer.echo(f"Status unavailable: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Ledger: {ledger.path}")
    typer.echo(f"Cycles: {sum(summary.cycle_counts.values())}")
    for cycle_status, count in sorted(summary.cycle_counts.items()):
        typer.echo(f"  {cycle_status}: {count}")
    typer.echo(f"Planned orders: {summary.planned_order_count}")
    typer.echo(f"Unresolved exchange orders: {summary.unresolved_exchange_order_count}")
    typer.echo(f"Schedule status: {evaluation.status.value}")
    typer.echo(f"Next/relevant scheduled at: {evaluation.scheduled_at.isoformat()}")
    typer.echo(f"Next/relevant cycle ID: {evaluation.cycle_id}")
    typer.echo(
        "Manual approval required: "
        f"{'yes' if evaluation.requires_manual_approval else 'no'}"
    )
    if summary.unresolved_exchange_order_count:
        typer.echo("Warning: unresolved app-created exchange orders block execution")
    if summary.last_cycle_id is None:
        typer.echo("Last cycle: none")
    else:
        typer.echo(
            f"Last cycle: {summary.last_cycle_id} "
            f"({summary.last_cycle_status}, {summary.last_cycle_scheduled_at})"
        )


@app.command("snapshot-balances")
def snapshot_balances(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
) -> None:
    """Store the initial exchange balance snapshot used by planning."""
    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        ledger.migrate()
        adapter = create_exchange_adapter(validated.exchange)
        markets = adapter.fetch_markets(validated.symbols)
        balances = adapter.fetch_balances()
        count = ledger.record_balances(
            adapter.name,
            datetime.now(UTC),
            [
                BalanceInput(
                    asset=balance.asset,
                    available=balance.available,
                    total=balance.total,
                )
                for balance in balances
            ],
            "initial_snapshot",
        )
    except (ConfigError, ExchangeError, LedgerError, OSError, ValueError) as exc:
        typer.echo(f"Balance snapshot failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Initial balance snapshot recorded: {count} balances")
    typer.echo(f"Markets validated: {len(markets)}")


@app.command()
def plan(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
    at: Annotated[
        str | None,
        typer.Option(
            "--at",
            help="Price-plan timestamp for deterministic tests; defaults to now.",
        ),
    ] = None,
) -> None:
    """Plan the next sell cycle without submitting exchange orders."""
    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        ledger.summary()
        adapter = create_exchange_adapter(validated.exchange)
        preview = preview_plan(
            validated,
            ledger,
            adapter,
            now=_parse_instant(at) if at is not None else datetime.now(UTC),
        )
    except (ConfigError, ExchangeError, LedgerError, OSError, ValueError) as exc:
        typer.echo(f"Plan failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _echo_plan_preview(preview)


@app.command("run-once")
def run_once(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Evaluate and preview a cycle without submitting exchange orders.",
        ),
    ] = False,
    live: Annotated[
        bool,
        typer.Option(
            "--live",
            help="Submit live exchange orders if all safety gates pass.",
        ),
    ] = False,
    confirm_first_live_sell: Annotated[
        bool,
        typer.Option(
            "--confirm-first-live-sell",
            help="Confirm the first live sell when config requires it.",
        ),
    ] = False,
    persist_simulation: Annotated[
        bool,
        typer.Option(
            "--persist-simulation",
            help="Persist the dry-run cycle and planned-order simulation locally.",
        ),
    ] = False,
    at: Annotated[
        str | None,
        typer.Option(
            "--at",
            help="Evaluate at an ISO-8601 instant; defaults to the current time.",
        ),
    ] = None,
) -> None:
    """Run one timer-friendly execution cycle."""
    if dry_run == live:
        typer.echo("Choose exactly one of --dry-run or --live.", err=True)
        raise typer.Exit(code=1)
    if live and persist_simulation:
        typer.echo("--persist-simulation is only valid with --dry-run.", err=True)
        raise typer.Exit(code=1)

    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        ledger.summary()
        notifier = create_notification_provider(validated.notifications)
        adapter = create_exchange_adapter(validated.exchange)
        evaluated_at = _parse_instant(at) if at is not None else datetime.now(UTC)
        if dry_run:
            result = dry_run_once(
                validated,
                ledger,
                adapter,
                now=evaluated_at,
                persist_simulation=persist_simulation,
            )
            _send_notification(
                notifier,
                NotificationMessage(
                    kind=_dry_run_notification_kind(result.notification_preview),
                    title="Cost Average Out dry run",
                    body=result.notification_preview,
                ),
            )
        else:
            live_result = live_run_once(
                validated,
                ledger,
                adapter,
                now=evaluated_at,
                confirm_first_live_sell=confirm_first_live_sell,
            )
            _send_notification(
                notifier,
                NotificationMessage(
                    kind=(
                        NotificationKind.BLOCK
                        if live_result.reconciliation.execution_blocked
                        else NotificationKind.SUCCESS
                    ),
                    title="Cost Average Out live run",
                    body=live_result.notification_preview,
                ),
            )
    except LiveExecutionBlockedError as exc:
        _notify_failure_if_configured(config, "Cost Average Out live blocked", str(exc))
        typer.echo(f"Live execution blocked: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ExchangeTimeoutError as exc:
        _notify_failure_if_configured(config, "Cost Average Out live timeout", str(exc))
        typer.echo(f"Live execution timeout: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except NotificationError as exc:
        typer.echo(f"Notification configuration failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (ConfigError, ExchangeError, LedgerError, OSError, ValueError) as exc:
        label = "Dry run" if dry_run else "Live run"
        _notify_failure_if_configured(
            config,
            f"Cost Average Out {label.lower()} failed",
            str(exc),
        )
        typer.echo(f"{label} failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if dry_run:
        typer.echo(f"Dry run cycle status: {result.evaluation.status.value}")
        typer.echo(f"Scheduled at: {result.evaluation.scheduled_at.isoformat()}")
        typer.echo(f"Cycle ID: {result.evaluation.cycle_id}")
        if result.persisted_cycle_id is not None:
            typer.echo(f"Simulation persisted: {result.persisted_cycle_id}")
        if result.preview is not None:
            _echo_plan_preview(result.preview)
        typer.echo(f"Notification preview: {result.notification_preview}")
    else:
        typer.echo(f"Live cycle status: {live_result.evaluation.status.value}")
        typer.echo(f"Scheduled at: {live_result.evaluation.scheduled_at.isoformat()}")
        typer.echo(f"Cycle ID: {live_result.evaluation.cycle_id}")
        typer.echo(f"Submitted orders: {live_result.submitted_order_count}")
        typer.echo(
            "Post-submit reconciliation blocked: "
            f"{'yes' if live_result.reconciliation.execution_blocked else 'no'}"
        )
        typer.echo(f"Notification preview: {live_result.notification_preview}")


def _dry_run_notification_kind(summary: str) -> NotificationKind:
    if "blocked" in summary:
        return NotificationKind.BLOCK
    if "no cycle is due" in summary or "skipped/blocked" in summary:
        return NotificationKind.SKIP
    return NotificationKind.SUCCESS


def _notify_failure_if_configured(
    config_path: Path,
    title: str,
    body: str,
) -> None:
    try:
        validated = load_config(config_path)
        notifier = create_notification_provider(validated.notifications)
        _send_notification(
            notifier,
            NotificationMessage(
                kind=NotificationKind.FAILURE,
                title=title,
                body=body,
            ),
        )
    except (ConfigError, NotificationError, OSError):
        return


def _send_notification(
    provider: NotificationProvider,
    message: NotificationMessage,
) -> None:
    try:
        provider.send(message)
    except NotificationError as exc:
        typer.echo(f"Notification failed: {exc}", err=True)


def _echo_plan_preview(preview: PlanPreview) -> None:
    typer.echo(f"Observed at: {preview.sell_plan.observed_at.isoformat()}")
    typer.echo(f"Execution blocked: {'yes' if preview.execution_blocked else 'no'}")
    for reason in preview.block_reasons:
        typer.echo(f"Block reason: {reason}")
    typer.echo("Sell plan:")
    for item in preview.sell_plan.items:
        typer.echo(_format_plan_item(item))


def _format_plan_item(item: SellPlanItem) -> str:
    reason = f"; reason={item.reason}" if item.reason is not None else ""
    return (
        f"  {item.symbol}: status={item.status.value}; "
        f"quantity={_format_decimal(item.quantity)}; "
        f"estimated_value={_format_decimal(item.estimated_quote_value)}"
        f"{reason}"
    )


def _format_decimal(value: object) -> str:
    return format(value, "f")


@app.command("backfill-prices")
def backfill_prices_command(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", dir_okay=False),
    ] = Path("config.yaml"),
    days: Annotated[
        int,
        typer.Option("--days", min=1, help="Number of recent days to backfill."),
    ] = 90,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Optional exchange candle limit."),
    ] = None,
) -> None:
    """Backfill daily OHLCV candles into the local price cache."""
    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        ledger.migrate()
        adapter = create_exchange_adapter(validated.exchange)
        result = backfill_prices(
            validated,
            ledger,
            adapter,
            since=default_since(days),
            limit=limit,
        )
    except (ConfigError, ExchangeError, LedgerError, OSError, ValueError) as exc:
        typer.echo(f"Price backfill failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Candles fetched: {result.fetched_count}")
    typer.echo(f"Candles stored: {result.stored_count}")


@app.command("portfolio-history")
def portfolio_history_command(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", dir_okay=False),
    ] = Path("config.yaml"),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write JSON to this path."),
    ] = None,
) -> None:
    """Export chart-ready portfolio history JSON from cached candles."""
    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        ledger.summary()
        rows = portfolio_history(validated, ledger, exchange=validated.exchange)
    except (ConfigError, LedgerError, OSError, ValueError) as exc:
        typer.echo(f"Portfolio history failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = json.dumps(rows, indent=2, sort_keys=True)
    if output is None:
        typer.echo(payload)
    else:
        output.write_text(payload + "\n", encoding="utf-8")
        typer.echo(f"Portfolio history written: {output}")


@app.command()
def reconcile(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to the YAML configuration file.",
            dir_okay=False,
        ),
    ] = Path("config.yaml"),
    lookback_days: Annotated[
        int,
        typer.Option(
            "--lookback-days",
            min=1,
            help="Number of days of closed orders and fills to fetch.",
        ),
    ] = 7,
) -> None:
    """Reconcile read-only exchange state into the local ledger."""
    try:
        validated = load_config(config)
        ledger = Ledger(validated.database_path.expanduser())
        ledger.summary()
        adapter = create_exchange_adapter(validated.exchange)
        result = reconcile_exchange(
            validated,
            ledger,
            adapter,
            lookback=timedelta(days=lookback_days),
        )
    except (ConfigError, ExchangeError, LedgerError, OSError, ValueError) as exc:
        typer.echo(f"Reconciliation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Balances recorded: {result.balance_count}")
    typer.echo(f"Markets validated: {result.market_count}")
    typer.echo(f"Open orders: {result.open_order_count}")
    typer.echo(f"Recent orders: {result.recent_order_count}")
    typer.echo(f"Recent fills: {result.recent_fill_count}")
    typer.echo(f"Unresolved app orders: {result.unresolved_app_order_count}")
    typer.echo(f"Execution blocked: {'yes' if result.execution_blocked else 'no'}")
