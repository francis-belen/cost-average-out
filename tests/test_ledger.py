from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cost_average_out.ledger import (
    CycleState,
    Ledger,
    LedgerConflictError,
    LedgerNotInitializedError,
    PlannedOrderInput,
)

SCHEDULED_AT = datetime(2030, 1, 1, tzinfo=UTC)


def table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }


def row_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_migration_is_repeatable_and_creates_required_tables(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    ledger = Ledger(database)

    ledger.migrate()
    ledger.migrate()

    assert {
        "schema_migrations",
        "app_metadata",
        "cycles",
        "planned_orders",
        "exchange_orders",
        "fills",
        "ledger_events",
        "balances",
    }.issubset(table_names(database))
    assert row_count(database, "schema_migrations") == 1


def test_summary_requires_initialized_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "missing.sqlite3")

    with pytest.raises(LedgerNotInitializedError):
        ledger.summary()


def test_completed_cycle_cannot_be_duplicated(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    ledger.create_cycle("cycle-1", SCHEDULED_AT, CycleState.COMPLETED)

    with pytest.raises(LedgerConflictError, match="cycle already exists"):
        ledger.create_cycle("cycle-1", SCHEDULED_AT, CycleState.COMPLETED)

    summary = ledger.summary()
    assert summary.cycle_counts == {"completed": 1}


def test_planned_order_is_unique_per_cycle_and_symbol(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    duplicate_orders = [
        PlannedOrderInput("BTC/EUR", Decimal("0.01"), Decimal("500")),
        PlannedOrderInput("BTC/EUR", Decimal("0.02"), Decimal("1000")),
    ]

    with pytest.raises(LedgerConflictError):
        ledger.create_cycle_with_orders("cycle-1", SCHEDULED_AT, duplicate_orders)

    assert row_count(ledger.path, "cycles") == 0
    assert row_count(ledger.path, "planned_orders") == 0
    assert row_count(ledger.path, "ledger_events") == 0


def test_cycle_and_order_plan_commit_atomically(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    orders = [
        PlannedOrderInput("BTC/EUR", Decimal("0.01"), Decimal("500")),
        PlannedOrderInput("ETH/EUR", Decimal("0.5"), Decimal("750")),
    ]

    ledger.create_cycle_with_orders("cycle-1", SCHEDULED_AT, orders)

    summary = ledger.summary()
    assert summary.cycle_counts == {"planned": 1}
    assert summary.planned_order_count == 2
    assert row_count(ledger.path, "ledger_events") == 1


@pytest.mark.parametrize(
    "state",
    [
        CycleState.SKIPPED,
        CycleState.BLOCKED,
        CycleState.PARTIAL,
        CycleState.COMPLETED,
    ],
)
def test_ledger_represents_terminal_and_partial_cycle_states(
    tmp_path: Path,
    state: CycleState,
) -> None:
    ledger = Ledger(tmp_path / f"{state.value}.sqlite3")
    ledger.migrate()

    ledger.create_cycle(f"cycle-{state.value}", SCHEDULED_AT, state)

    assert ledger.summary().cycle_counts == {state.value: 1}


def test_status_transition_appends_audit_event(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()
    ledger.create_cycle("cycle-1", SCHEDULED_AT)

    ledger.set_cycle_status("cycle-1", CycleState.BLOCKED)

    summary = ledger.summary()
    assert summary.cycle_counts == {"blocked": 1}
    assert summary.last_cycle_id == "cycle-1"
    assert summary.last_cycle_status == "blocked"
    assert row_count(ledger.path, "ledger_events") == 1


def test_foreign_keys_are_enabled(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    ledger.migrate()

    with sqlite3.connect(ledger.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO planned_orders(
                    cycle_id, symbol, quantity, estimated_quote_value,
                    status, created_at, updated_at
                ) VALUES(999, 'BTC/EUR', '1', '1', 'planned', 'now', 'now')
                """
            )
