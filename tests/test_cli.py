from __future__ import annotations

import json
from pathlib import Path

import yaml
from pytest import MonkeyPatch
from typer.testing import CliRunner

from cost_average_out.cli import app
from cost_average_out.notifications import NotificationMessage
from tests.test_config import valid_config_data
from tests.test_reconciliation import FakeExchangeAdapter


class CapturingNotificationProvider:
    def __init__(self) -> None:
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> None:
        self.messages.append(message)

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


def write_config(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "ledger.sqlite3"
    config = tmp_path / "config.yaml"
    data = valid_config_data()
    data["database_path"] = str(database)
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config, database


def test_init_ledger_is_repeatable_and_status_is_ledger_backed(
    tmp_path: Path,
) -> None:
    config, database = write_config(tmp_path)

    first_init = runner.invoke(app, ["init-ledger", "--config", str(config)])
    second_init = runner.invoke(app, ["init-ledger", "--config", str(config)])
    status = runner.invoke(app, ["status", "--config", str(config)])

    assert first_init.exit_code == 0
    assert second_init.exit_code == 0
    assert database.is_file()
    assert f"Ledger ready: {database}" in first_init.stdout
    assert status.exit_code == 0
    assert "Cycles: 0" in status.stdout
    assert "Planned orders: 0" in status.stdout
    assert "Unresolved exchange orders: 0" in status.stdout
    assert "Quote currency: EUR" in status.stdout
    assert "Timezone: Europe/Amsterdam" in status.stdout
    assert "Symbols: BTC/EUR, ETH/EUR" in status.stdout
    assert "Schedule status:" in status.stdout
    assert "Next/relevant cycle ID: cao-" in status.stdout
    assert "Manual approval required:" in status.stdout
    assert "Last cycle: none" in status.stdout


def test_status_reports_uninitialized_ledger(tmp_path: Path) -> None:
    config, database = write_config(tmp_path)

    result = runner.invoke(app, ["status", "--config", str(config)])

    assert result.exit_code == 1
    assert not database.exists()
    assert "Status unavailable: ledger does not exist" in result.stderr


def test_help_lists_ledger_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init-ledger" in result.stdout
    assert "status" in result.stdout


def test_snapshot_balances_command_records_initial_snapshot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    result = runner.invoke(app, ["snapshot-balances", "--config", str(config)])

    assert result.exit_code == 0
    assert "Initial balance snapshot recorded: 2 balances" in result.stdout
    assert "Markets validated: 2" in result.stdout


def test_plan_command_outputs_safety_decisions(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "plan",
            "--config",
            str(config),
            "--at",
            "2030-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert "Execution blocked: no" in result.stdout
    assert "BTC/EUR: status=planned" in result.stdout
    assert "quantity=0.005" in result.stdout
    assert "estimated_value=250" in result.stdout



def test_schedule_status_json_output_is_deterministic() -> None:
    result = runner.invoke(
        app,
        [
            "schedule-status",
            "--config",
            str(PROJECT_ROOT / "config.example.yaml"),
            "--at",
            "2029-12-31T22:00:00Z",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "command": "schedule-status",
        "cycle_id": payload["cycle_id"],
        "exchange": "kraken",
        "kill_switch": False,
        "live_trading_enabled": False,
        "manual_approval_required": False,
        "schedule_interval": "weekly",
        "scheduled_at": "2030-01-01T00:00:00+01:00",
        "status": "not_due",
    }
    assert payload["cycle_id"].startswith("cao-2030-01-01-")


def test_status_json_output(tmp_path: Path) -> None:
    config, database = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        ["status", "--config", str(config), "--output", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "status"
    assert payload["config"]["exchange"] == "kraken"
    assert payload["config"]["symbols"] == ["BTC/EUR", "ETH/EUR"]
    assert payload["ledger"]["path"] == str(database)
    assert payload["ledger"]["planned_orders"] == 0
    assert payload["ledger"]["status"] == "ready"
    assert payload["ledger"]["unresolved_exchange_orders"] == 0
    assert payload["schedule"]["cycle_id"].startswith("cao-")


def test_plan_json_output(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "plan",
            "--config",
            str(config),
            "--at",
            "2030-01-01T00:00:00Z",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "plan"
    assert payload["execution_blocked"] is False
    assert payload["planned_orders"] == 1
    assert payload["sell_plan"][0] == {
        "base_asset": "BTC",
        "estimated_quote_value": "250",
        "quantity": "0.005",
        "reason": None,
        "status": "planned",
        "symbol": "BTC/EUR",
    }


def test_reconcile_json_output(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    result = runner.invoke(
        app,
        ["reconcile", "--config", str(config), "--output", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "reconcile"
    assert payload["balances_recorded"] == 2
    assert payload["markets_validated"] == 2
    assert payload["execution_blocked"] is False
    assert payload["unresolved_app_orders"] == 0


def test_invalid_output_value_is_rejected(tmp_path: Path) -> None:
    config, _ = write_config(tmp_path)

    result = runner.invoke(
        app,
        ["status", "--config", str(config), "--output", "yaml"],
    )

    assert result.exit_code != 0
    assert "json" in result.stderr
    assert "rich" in result.stderr


def test_json_output_has_no_rich_formatting_or_secrets(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setenv("COST_AVERAGE_OUT_EXCHANGE_API_KEY", "secret-api-key")
    monkeypatch.setenv("COST_AVERAGE_OUT_EXCHANGE_API_SECRET", "secret-api-secret")
    monkeypatch.setenv(
        "COST_AVERAGE_OUT_NOTIFICATION_WEBHOOK_URL",
        "https://example.test/secret-webhook",
    )

    result = runner.invoke(
        app,
        ["status", "--config", str(config), "--output", "json"],
    )

    assert result.exit_code == 0
    json.loads(result.stdout)
    assert "\x1b" not in result.stdout
    assert "╭" not in result.stdout
    assert "│" not in result.stdout
    assert "secret-api-key" not in result.stdout
    assert "secret-api-secret" not in result.stdout
    assert "secret-webhook" not in result.stdout

def test_run_once_requires_mode_flag(tmp_path: Path) -> None:
    config, _ = write_config(tmp_path)

    result = runner.invoke(app, ["run-once", "--config", str(config)])

    assert result.exit_code == 1
    assert "Choose exactly one of --dry-run or --live." in result.stderr


def test_run_once_dry_run_writes_no_exchange_orders_by_default(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "run-once",
            "--dry-run",
            "--config",
            str(config),
            "--at",
            "2030-01-01T00:00:00+01:00",
        ],
    )
    status = runner.invoke(app, ["status", "--config", str(config)])

    assert result.exit_code == 0
    assert "Dry run cycle status: due" in result.stdout
    assert "Execution blocked: no" in result.stdout
    assert "Notification preview: Dry run: ready;" in result.stdout
    assert status.exit_code == 0
    assert "Cycles: 0" in status.stdout
    assert "Planned orders: 0" in status.stdout
    assert "Unresolved exchange orders: 0" in status.stdout


def test_run_once_dry_run_sends_notification_preview(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    notifier = CapturingNotificationProvider()
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )
    monkeypatch.setattr(
        "cost_average_out.cli.create_notification_provider",
        lambda settings: notifier,
    )

    result = runner.invoke(
        app,
        [
            "run-once",
            "--dry-run",
            "--config",
            str(config),
            "--at",
            "2030-01-01T00:00:00+01:00",
        ],
    )

    assert result.exit_code == 0
    assert len(notifier.messages) == 1
    assert notifier.messages[0].title == "Cost Average Out dry run"
    assert "Dry run:" in notifier.messages[0].body


def test_run_once_dry_run_can_persist_simulation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "run-once",
            "--dry-run",
            "--persist-simulation",
            "--config",
            str(config),
            "--at",
            "2030-01-01T00:00:00+01:00",
        ],
    )
    status = runner.invoke(app, ["status", "--config", str(config)])

    assert result.exit_code == 0
    assert "Simulation persisted: cao-2030-01-01-" in result.stdout
    assert status.exit_code == 0
    assert "Cycles: 1" in status.stdout
    assert "Planned orders: 2" in status.stdout
    assert "Unresolved exchange orders: 0" in status.stdout


def test_backfill_prices_and_portfolio_history_commands(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    backfill = runner.invoke(
        app,
        ["backfill-prices", "--config", str(config), "--days", "1"],
    )
    history = runner.invoke(app, ["portfolio-history", "--config", str(config)])

    assert backfill.exit_code == 0
    assert "Candles fetched: 2" in backfill.stdout
    assert history.exit_code == 0
    assert "quote_currency" in history.stdout


def test_reconcile_command_uses_read_only_adapter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config, _ = write_config(tmp_path)
    init_result = runner.invoke(app, ["init-ledger", "--config", str(config)])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "cost_average_out.cli.create_exchange_adapter",
        lambda exchange: FakeExchangeAdapter(),
    )

    result = runner.invoke(app, ["reconcile", "--config", str(config)])

    assert result.exit_code == 0
    assert "Balances recorded: 2" in result.stdout
    assert "Markets validated: 2" in result.stdout
    assert "Execution blocked: no" in result.stdout


def test_help_lists_reconcile_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert "reconcile" in result.stdout
    assert "snapshot-balances" in result.stdout

    assert result.exit_code == 0
