from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cost_average_out.config import ConfigError, load_config

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


@app.command()
def plan(config: str = "config.yaml") -> None:
    """Plan the next sell cycle without submitting exchange orders."""
    typer.echo(f"plan is not implemented yet: {config}")


@app.command()
def run_once(config: str = "config.yaml") -> None:
    """Run one timer-friendly execution cycle."""
    typer.echo(f"run-once is not implemented yet: {config}")
