from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from cost_average_out.config import ConfigError, load_config
from cost_average_out.ledger import Ledger, LedgerError
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

    trading_state = (
        "enabled" if validated.safety.live_trading_enabled else "disabled"
    )
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
    except (ConfigError, LedgerError, OSError) as exc:
        typer.echo(f"Status unavailable: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Ledger: {ledger.path}")
    typer.echo(f"Cycles: {sum(summary.cycle_counts.values())}")
    for cycle_status, count in sorted(summary.cycle_counts.items()):
        typer.echo(f"  {cycle_status}: {count}")
    typer.echo(f"Planned orders: {summary.planned_order_count}")
    typer.echo(
        f"Unresolved exchange orders: {summary.unresolved_exchange_order_count}"
    )
    if summary.last_cycle_id is None:
        typer.echo("Last cycle: none")
    else:
        typer.echo(
            f"Last cycle: {summary.last_cycle_id} "
            f"({summary.last_cycle_status}, {summary.last_cycle_scheduled_at})"
        )

@app.command()
def plan(config: str = "config.yaml") -> None:
    """Plan the next sell cycle without submitting exchange orders."""
    typer.echo(f"plan is not implemented yet: {config}")


@app.command()
def run_once(config: str = "config.yaml") -> None:
    """Run one timer-friendly execution cycle."""
    typer.echo(f"run-once is not implemented yet: {config}")
