"""SQLite-backed application ledger and migrations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any


class LedgerError(RuntimeError):
    """Base error for ledger operations."""


class LedgerConflictError(LedgerError):
    """Raised when a ledger uniqueness invariant is violated."""


class LedgerNotInitializedError(LedgerError):
    """Raised when a read is attempted before migrations are applied."""


class CycleState(StrEnum):
    PLANNED = "planned"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    EXECUTING = "executing"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN_REQUIRES_RECONCILIATION = "unknown_requires_reconciliation"


class PlannedOrderState(StrEnum):
    PLANNED = "planned"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass(frozen=True)
class PlannedOrderInput:
    symbol: str
    quantity: Decimal
    estimated_quote_value: Decimal
    status: PlannedOrderState = PlannedOrderState.PLANNED
    reason: str | None = None


@dataclass(frozen=True)
class LedgerSummary:
    cycle_counts: dict[str, int]
    last_cycle_id: str | None
    last_cycle_status: str | None
    last_cycle_scheduled_at: str | None
    planned_order_count: int
    unresolved_exchange_order_count: int


_MIGRATION_1 = (
    """
    CREATE TABLE app_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE cycles (
        id INTEGER PRIMARY KEY,
        cycle_id TEXT NOT NULL UNIQUE,
        scheduled_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'planned', 'skipped', 'blocked', 'executing', 'partial',
            'completed', 'failed', 'unknown_requires_reconciliation'
        )),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE planned_orders (
        id INTEGER PRIMARY KEY,
        cycle_id INTEGER NOT NULL REFERENCES cycles(id) ON DELETE RESTRICT,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL DEFAULT 'sell' CHECK (side = 'sell'),
        quantity TEXT NOT NULL,
        estimated_quote_value TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'planned', 'skipped', 'blocked', 'submitted', 'partial',
            'filled', 'canceled', 'failed'
        )),
        reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (cycle_id, symbol)
    )
    """,
    """
    CREATE TABLE exchange_orders (
        id INTEGER PRIMARY KEY,
        planned_order_id INTEGER NOT NULL
            REFERENCES planned_orders(id) ON DELETE RESTRICT,
        exchange TEXT NOT NULL,
        exchange_order_id TEXT,
        client_order_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN (
            'submitting', 'open', 'partial', 'filled', 'canceled', 'failed',
            'unknown_requires_reconciliation'
        )),
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (exchange, exchange_order_id)
    )
    """,
    """
    CREATE TABLE fills (
        id INTEGER PRIMARY KEY,
        exchange_order_id INTEGER NOT NULL
            REFERENCES exchange_orders(id) ON DELETE RESTRICT,
        exchange_fill_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        quantity TEXT NOT NULL,
        price TEXT NOT NULL,
        fee TEXT,
        fee_currency TEXT,
        filled_at TEXT NOT NULL,
        raw_json TEXT,
        UNIQUE (exchange_order_id, exchange_fill_id)
    )
    """,
    """
    CREATE TABLE ledger_events (
        id INTEGER PRIMARY KEY,
        cycle_id INTEGER REFERENCES cycles(id) ON DELETE RESTRICT,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE balances (
        id INTEGER PRIMARY KEY,
        exchange TEXT NOT NULL,
        asset TEXT NOT NULL,
        available TEXT NOT NULL,
        total TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        source TEXT NOT NULL,
        UNIQUE (exchange, asset, observed_at)
    )
    """,
    "CREATE INDEX idx_cycles_scheduled_at ON cycles(scheduled_at)",
    "CREATE INDEX idx_planned_orders_status ON planned_orders(status)",
    "CREATE INDEX idx_exchange_orders_status ON exchange_orders(status)",
    "CREATE INDEX idx_balances_observed_at ON balances(observed_at)",
)


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def migrate(self) -> None:
        """Apply all migrations exactly once and safely allow repeated calls."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            if 1 not in applied:
                for statement in _MIGRATION_1:
                    connection.execute(statement)
                now = _utc_now()
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                    (now,),
                )
                connection.execute(
                    """
                    INSERT INTO app_metadata(key, value, updated_at)
                    VALUES('schema_version', '1', ?)
                    """,
                    (now,),
                )

    def create_cycle(
        self,
        cycle_id: str,
        scheduled_at: datetime,
        status: CycleState = CycleState.PLANNED,
    ) -> int:
        """Create one cycle while enforcing cycle ID uniqueness."""

        self._require_aware(scheduled_at)
        now = _utc_now()
        completed_at = now if status is CycleState.COMPLETED else None
        try:
            with self._connection() as connection, connection:
                cursor = connection.execute(
                    """
                    INSERT INTO cycles(
                        cycle_id, scheduled_at, status, created_at, updated_at,
                        completed_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cycle_id,
                        scheduled_at.astimezone(UTC).isoformat(),
                        status.value,
                        now,
                        now,
                        completed_at,
                    ),
                )
                database_cycle_id = cursor.lastrowid
                assert database_cycle_id is not None
                return database_cycle_id
        except sqlite3.IntegrityError as exc:
            raise LedgerConflictError(f"cycle already exists: {cycle_id}") from exc

    def create_cycle_with_orders(
        self,
        cycle_id: str,
        scheduled_at: datetime,
        orders: Sequence[PlannedOrderInput],
    ) -> int:
        """Atomically create a cycle and all of its planned sell orders."""

        self._require_aware(scheduled_at)
        now = _utc_now()
        try:
            with self._connection() as connection, connection:
                cursor = connection.execute(
                    """
                    INSERT INTO cycles(
                        cycle_id, scheduled_at, status, created_at, updated_at
                    ) VALUES(?, ?, 'planned', ?, ?)
                    """,
                    (
                        cycle_id,
                        scheduled_at.astimezone(UTC).isoformat(),
                        now,
                        now,
                    ),
                )
                database_cycle_id = cursor.lastrowid
                assert database_cycle_id is not None
                for order in orders:
                    connection.execute(
                        """
                        INSERT INTO planned_orders(
                            cycle_id, symbol, quantity, estimated_quote_value,
                            status, reason, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            database_cycle_id,
                            order.symbol,
                            str(order.quantity),
                            str(order.estimated_quote_value),
                            order.status.value,
                            order.reason,
                            now,
                            now,
                        ),
                    )
                self._insert_event(
                    connection,
                    database_cycle_id,
                    "cycle_planned",
                    {"order_count": len(orders)},
                    now,
                )
                return database_cycle_id
        except sqlite3.IntegrityError as exc:
            raise LedgerConflictError(
                "cycle or planned order conflicts with existing ledger state: "
                f"{cycle_id}"
            ) from exc

    def set_cycle_status(self, cycle_id: str, status: CycleState) -> None:
        """Update cycle state and append an audit event in one transaction."""

        now = _utc_now()
        completed_at = now if status is CycleState.COMPLETED else None
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE cycles
                SET status = ?, updated_at = ?, completed_at = ?
                WHERE cycle_id = ?
                """,
                (status.value, now, completed_at, cycle_id),
            )
            if cursor.rowcount != 1:
                raise LedgerError(f"cycle not found: {cycle_id}")
            row = connection.execute(
                "SELECT id FROM cycles WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
            assert row is not None
            self._insert_event(
                connection,
                int(row[0]),
                "cycle_status_changed",
                {"status": status.value},
                now,
            )

    def summary(self) -> LedgerSummary:
        """Return a read-only status summary from the initialized ledger."""

        if not self.path.is_file():
            raise LedgerNotInitializedError(f"ledger does not exist: {self.path}")
        try:
            with self._connection() as connection:
                migration = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 1"
                ).fetchone()
                if migration is None:
                    raise LedgerNotInitializedError("ledger migrations are incomplete")
                counts = {
                    str(row[0]): int(row[1])
                    for row in connection.execute(
                        "SELECT status, COUNT(*) FROM cycles GROUP BY status"
                    ).fetchall()
                }
                last_cycle = connection.execute(
                    """
                    SELECT cycle_id, status, scheduled_at
                    FROM cycles
                    ORDER BY scheduled_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
                planned_order_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM planned_orders"
                    ).fetchone()[0]
                )
                unresolved_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM exchange_orders
                        WHERE status IN (
                            'submitting', 'open', 'partial',
                            'unknown_requires_reconciliation'
                        )
                        """
                    ).fetchone()[0]
                )
        except sqlite3.OperationalError as exc:
            raise LedgerNotInitializedError("ledger migrations are incomplete") from exc

        return LedgerSummary(
            cycle_counts=counts,
            last_cycle_id=str(last_cycle[0]) if last_cycle else None,
            last_cycle_status=str(last_cycle[1]) if last_cycle else None,
            last_cycle_scheduled_at=str(last_cycle[2]) if last_cycle else None,
            planned_order_count=planned_order_count,
            unresolved_exchange_order_count=unresolved_count,
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        cycle_id: int,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO ledger_events(cycle_id, event_type, payload_json, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (cycle_id, event_type, json.dumps(payload, sort_keys=True), created_at),
        )

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
