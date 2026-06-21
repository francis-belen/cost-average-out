from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cost_average_out.cli import app

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_help_lists_validate_config_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "validate-config" in result.stdout

    assert "schedule-status" in result.stdout

def test_example_config_is_valid_offline() -> None:
    result = runner.invoke(
        app,
        [
            "validate-config",
            "--config",
            str(PROJECT_ROOT / "config.example.yaml"),
        ],
    )

    assert result.exit_code == 0
    assert "Configuration valid" in result.stdout
    assert "offline validation" in result.stdout
    assert "live trading disabled" in result.stdout


def test_missing_config_is_reported() -> None:
    result = runner.invoke(
        app,
        ["validate-config", "--config", "does-not-exist.yaml"],
    )

    assert result.exit_code == 1
    assert "Configuration invalid" in result.stderr


def test_invalid_config_is_reported(tmp_path: Path) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text("config_version: 1\n", encoding="utf-8")

    result = runner.invoke(app, ["validate-config", "--config", str(config)])

    assert result.exit_code == 1
    assert "Configuration invalid" in result.stderr


def test_schedule_status_reports_not_due() -> None:
    result = runner.invoke(
        app,
        [
            "schedule-status",
            "--config",
            str(PROJECT_ROOT / "config.example.yaml"),
            "--at",
            "2029-12-31T22:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert "Status: not_due" in result.stdout
    assert "Scheduled at: 2030-01-01T00:00:00+01:00" in result.stdout
    assert "Cycle ID: cao-2030-01-01-" in result.stdout
    assert "Manual approval required: no" in result.stdout


def test_schedule_status_reports_missed_manual_approval() -> None:
    result = runner.invoke(
        app,
        [
            "schedule-status",
            "--config",
            str(PROJECT_ROOT / "config.example.yaml"),
            "--at",
            "2030-01-02T12:00:00+01:00",
        ],
    )

    assert result.exit_code == 0
    assert "Status: missed" in result.stdout
    assert "Manual approval required: yes" in result.stdout


def test_schedule_status_rejects_naive_at_value() -> None:
    result = runner.invoke(
        app,
        [
            "schedule-status",
            "--config",
            str(PROJECT_ROOT / "config.example.yaml"),
            "--at",
            "2030-01-01T12:00:00",
        ],
    )

    assert result.exit_code == 1
    assert "must include a UTC offset" in result.stderr
