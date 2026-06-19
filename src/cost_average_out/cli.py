from __future__ import annotations

import typer

app = typer.Typer(help="Cost Average Out CLI.")


@app.command()
def validate_config(config: str = "config.yaml") -> None:
    """Validate configuration without submitting exchange orders."""
    typer.echo(f"validate-config is not implemented yet: {config}")


@app.command()
def plan(config: str = "config.yaml") -> None:
    """Plan the next sell cycle without submitting exchange orders."""
    typer.echo(f"plan is not implemented yet: {config}")


@app.command()
def run_once(config: str = "config.yaml") -> None:
    """Run one timer-friendly execution cycle."""
    typer.echo(f"run-once is not implemented yet: {config}")
