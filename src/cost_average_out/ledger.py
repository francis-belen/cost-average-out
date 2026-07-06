"""SQLite-backed application ledger and migrations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, cast


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
class BalanceInput:
    asset: str
    available: Decimal
    total: Decimal


@dataclass(frozen=True)
class ReconciledOrderInput:
    exchange_order_id: str
    client_order_id: str | None
    status: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ReconciledFillInput:
    exchange_fill_id: str
    exchange_order_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal | None
    fee_currency: str | None
    filled_at: datetime
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ReconciliationWriteResult:
    balance_count: int
    matched_order_count: int
    recorded_fill_count: int


@dataclass(frozen=True)
class BalanceRecord:
    asset: str
    available: Decimal
    total: Decimal
    observed_at: datetime
    source: str


@dataclass(frozen=True)
class CandleInput:
    exchange: str
    symbol: str
    timeframe: str
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class PriceCandle:
    exchange: str
    symbol: str
    timeframe: str
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class ActivationInput:
    activated_at: datetime
    activation_type: str
    activation_reason: str
    reference_symbol: str | None = None
    reference_price: Decimal | None = None
    threshold: Decimal | None = None


@dataclass(frozen=True)
class ActivationRecord:
    activated: bool
    activated_at: datetime | None
    activation_type: str | None
    activation_reason: str | None
    reference_symbol: str | None
    reference_price: Decimal | None
    threshold: Decimal | None


@dataclass(frozen=True)
class LedgerSummary:
    cycle_counts: dict[str, int]
    last_cycle_id: str | None
    last_cycle_status: str | None
    last_cycle_scheduled_at: str | None
    planned_order_count: int
    unresolved_exchange_order_count: int


_MIGRATION_2 = (
    """
    CREATE TABLE price_candles (
        id INTEGER PRIMARY KEY,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        open TEXT NOT NULL,
        high TEXT NOT NULL,
        low TEXT NOT NULL,
        close TEXT NOT NULL,
        volume TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(exchange, symbol, timeframe, opened_at)
    )
    """,
    (
        "CREATE INDEX idx_price_candles_lookup "
        "ON price_candles(symbol, timeframe, opened_at)"
    ),
)


_MIGRATION_3 = (
    """
    CREATE TABLE activation_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        activated INTEGER NOT NULL CHECK (activated IN (0, 1)),
        activated_at TEXT,
        activation_type TEXT,
        activation_reason TEXT,
        reference_symbol TEXT,
        reference_price TEXT,
        threshold TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)


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
            if 2 not in applied:
                for statement in _MIGRATION_2:
                    connection.execute(statement)
                now = _utc_now()
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(2, ?)",
                    (now,),
                )
                connection.execute(
                    """
                    INSERT INTO app_metadata(key, value, updated_at)
                    VALUES('schema_version', '2', ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (now,),
                )
            if 3 not in applied:
                for statement in _MIGRATION_3:
                    connection.execute(statement)
                now = _utc_now()
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(3, ?)",
                    (now,),
                )
                connection.execute(
                    """
                    INSERT INTO app_metadata(key, value, updated_at)
                    VALUES('schema_version', '3', ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
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

    def update_planned_order_status(
        self,
        cycle_id: str,
        symbol: str,
        status: PlannedOrderState,
        reason: str | None = None,
    ) -> None:
        """Update a planned order state by public cycle ID and symbol."""

        now = _utc_now()
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE planned_orders
                SET status = ?, reason = COALESCE(?, reason), updated_at = ?
                WHERE symbol = ?
                  AND cycle_id = (SELECT id FROM cycles WHERE cycle_id = ?)
                """,
                (status.value, reason, now, symbol, cycle_id),
            )
            if cursor.rowcount != 1:
                raise LedgerError(
                    f"planned order not found for cycle {cycle_id}: {symbol}"
                )

    def register_exchange_order(
        self,
        cycle_id: str,
        symbol: str,
        exchange: str,
        client_order_id: str,
        status: str = "submitting",
        exchange_order_id: str | None = None,
        raw: Mapping[str, Any] | None = None,
    ) -> int:
        """Register an app-created order for later reconciliation."""

        now = _utc_now()
        try:
            with self._connection() as connection, connection:
                planned_order = connection.execute(
                    """
                    SELECT planned_orders.id
                    FROM planned_orders
                    JOIN cycles ON cycles.id = planned_orders.cycle_id
                    WHERE cycles.cycle_id = ? AND planned_orders.symbol = ?
                    """,
                    (cycle_id, symbol),
                ).fetchone()
                if planned_order is None:
                    raise LedgerError(
                        f"planned order not found for cycle {cycle_id}: {symbol}"
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO exchange_orders(
                        planned_order_id, exchange, exchange_order_id,
                        client_order_id, status, raw_json, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(planned_order[0]),
                        exchange,
                        exchange_order_id,
                        client_order_id,
                        status,
                        json.dumps(raw, sort_keys=True, default=str)
                        if raw is not None
                        else None,
                        now,
                        now,
                    ),
                )
                row_id = cursor.lastrowid
                assert row_id is not None
                return row_id
        except sqlite3.IntegrityError as exc:
            raise LedgerConflictError(
                f"exchange order already exists: {client_order_id}"
            ) from exc

    def record_balances(
        self,
        exchange: str,
        observed_at: datetime,
        balances: Sequence[BalanceInput],
        source: str,
    ) -> int:
        """Persist a point-in-time balance snapshot."""

        self._require_aware(observed_at)
        if not source.strip():
            raise ValueError("source must be non-empty")
        observed = observed_at.astimezone(UTC).isoformat()
        with self._connection() as connection, connection:
            self._record_balances(connection, exchange, observed, balances, source)
            connection.execute(
                """
                INSERT INTO ledger_events(
                    cycle_id, event_type, payload_json, created_at
                ) VALUES(NULL, 'balances_recorded', ?, ?)
                """,
                (
                    json.dumps(
                        {
                            "balance_count": len(balances),
                            "exchange": exchange,
                            "source": source,
                        },
                        sort_keys=True,
                    ),
                    observed,
                ),
            )
        return len(balances)

    def latest_balances(
        self,
        exchange: str,
        *,
        source: str | None = None,
    ) -> dict[str, BalanceRecord]:
        """Return the latest balance row per asset for an exchange/source."""

        if not self.path.is_file():
            raise LedgerNotInitializedError(f"ledger does not exist: {self.path}")
        source_filter = "AND source = ?" if source is not None else ""
        parameters: tuple[str, ...] = (
            (exchange,) if source is None else (exchange, source)
        )
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT b.asset, b.available, b.total, b.observed_at, b.source
                    FROM balances b
                    JOIN (
                        SELECT asset, MAX(observed_at) AS observed_at
                        FROM balances
                        WHERE exchange = ? {source_filter}
                        GROUP BY asset
                    ) latest
                        ON latest.asset = b.asset
                       AND latest.observed_at = b.observed_at
                    WHERE b.exchange = ? {source_filter}
                    """,
                    (*parameters, *parameters),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise LedgerNotInitializedError("ledger migrations are incomplete") from exc

        return {
            str(row[0]): BalanceRecord(
                asset=str(row[0]),
                available=Decimal(str(row[1])),
                total=Decimal(str(row[2])),
                observed_at=datetime.fromisoformat(str(row[3])),
                source=str(row[4]),
            )
            for row in rows
        }

    def record_reconciliation(
        self,
        exchange: str,
        observed_at: datetime,
        balances: Sequence[BalanceInput],
        orders: Sequence[ReconciledOrderInput],
        fills: Sequence[ReconciledFillInput],
    ) -> ReconciliationWriteResult:
        """Atomically persist balances and reconcile known app-created orders."""

        self._require_aware(observed_at)
        observed = observed_at.astimezone(UTC).isoformat()
        matched_order_ids: set[int] = set()
        recorded_fill_count = 0
        with self._connection() as connection, connection:
            self._record_balances(
                connection, exchange, observed, balances, "reconciliation"
            )

            for order in orders:
                local_order = self._find_exchange_order(
                    connection,
                    exchange,
                    order.exchange_order_id,
                    order.client_order_id,
                )
                if local_order is None:
                    continue
                local_order_id = int(local_order[0])
                connection.execute(
                    """
                    UPDATE exchange_orders
                    SET exchange_order_id = ?, status = ?, raw_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        order.exchange_order_id,
                        order.status,
                        json.dumps(order.raw, sort_keys=True, default=str),
                        observed,
                        local_order_id,
                    ),
                )
                matched_order_ids.add(local_order_id)

            for fill in fills:
                local_order = connection.execute(
                    """
                    SELECT id FROM exchange_orders
                    WHERE exchange = ? AND exchange_order_id = ?
                    """,
                    (exchange, fill.exchange_order_id),
                ).fetchone()
                if local_order is None:
                    continue
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO fills(
                        exchange_order_id, exchange_fill_id, symbol, quantity,
                        price, fee, fee_currency, filled_at, raw_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(local_order[0]),
                        fill.exchange_fill_id,
                        fill.symbol,
                        str(fill.quantity),
                        str(fill.price),
                        str(fill.fee) if fill.fee is not None else None,
                        fill.fee_currency,
                        fill.filled_at.astimezone(UTC).isoformat(),
                        json.dumps(fill.raw, sort_keys=True, default=str),
                    ),
                )
                recorded_fill_count += max(cursor.rowcount, 0)

            connection.execute(
                """
                INSERT INTO ledger_events(
                    cycle_id, event_type, payload_json, created_at
                ) VALUES(NULL, 'reconciliation_completed', ?, ?)
                """,
                (
                    json.dumps(
                        {
                            "balance_count": len(balances),
                            "matched_order_count": len(matched_order_ids),
                            "recorded_fill_count": recorded_fill_count,
                        },
                        sort_keys=True,
                    ),
                    observed,
                ),
            )

        return ReconciliationWriteResult(
            balance_count=len(balances),
            matched_order_count=len(matched_order_ids),
            recorded_fill_count=recorded_fill_count,
        )

    def record_price_candles(self, candles: Sequence[CandleInput]) -> int:
        """Insert or update cached OHLCV candles."""

        now = _utc_now()
        written = 0
        with self._connection() as connection, connection:
            for candle in candles:
                self._require_aware(candle.opened_at)
                cursor = connection.execute(
                    """
                    INSERT INTO price_candles(
                        exchange, symbol, timeframe, opened_at, open, high, low,
                        close, volume, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(exchange, symbol, timeframe, opened_at) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        updated_at = excluded.updated_at
                    """,
                    (
                        candle.exchange,
                        candle.symbol,
                        candle.timeframe,
                        candle.opened_at.astimezone(UTC).isoformat(),
                        str(candle.open),
                        str(candle.high),
                        str(candle.low),
                        str(candle.close),
                        str(candle.volume),
                        now,
                        now,
                    ),
                )
                written += max(cursor.rowcount, 0)
        return written

    def price_candles(
        self,
        exchange: str,
        symbols: Sequence[str],
        timeframe: str = "1d",
    ) -> list[PriceCandle]:
        """Read cached candles for symbols in chronological order."""

        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT exchange, symbol, timeframe, opened_at, open, high,
                           low, close, volume
                    FROM price_candles
                    WHERE exchange = ?
                      AND timeframe = ?
                      AND symbol IN ({placeholders})
                    ORDER BY opened_at, symbol
                    """,
                    (exchange, timeframe, *symbols),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise LedgerNotInitializedError("ledger migrations are incomplete") from exc
        return [
            PriceCandle(
                exchange=str(row[0]),
                symbol=str(row[1]),
                timeframe=str(row[2]),
                opened_at=datetime.fromisoformat(str(row[3])),
                open=Decimal(str(row[4])),
                high=Decimal(str(row[5])),
                low=Decimal(str(row[6])),
                close=Decimal(str(row[7])),
                volume=Decimal(str(row[8])),
            )
            for row in rows
        ]

    def has_unresolved_exchange_orders(self) -> bool:
        """Return whether local app-created orders require resolution."""

        return self.summary().unresolved_exchange_order_count > 0

    @staticmethod
    def _record_balances(
        connection: sqlite3.Connection,
        exchange: str,
        observed: str,
        balances: Sequence[BalanceInput],
        source: str,
    ) -> None:
        for balance in balances:
            connection.execute(
                """
                INSERT INTO balances(
                    exchange, asset, available, total, observed_at, source
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, asset, observed_at) DO UPDATE SET
                    available = excluded.available,
                    total = excluded.total,
                    source = excluded.source
                """,
                (
                    exchange,
                    balance.asset,
                    str(balance.available),
                    str(balance.total),
                    observed,
                    source,
                ),
            )

    @staticmethod
    def _find_exchange_order(
        connection: sqlite3.Connection,
        exchange: str,
        exchange_order_id: str,
        client_order_id: str | None,
    ) -> sqlite3.Row | tuple[Any, ...] | None:
        if client_order_id:
            row = connection.execute(
                """
                SELECT id FROM exchange_orders
                WHERE exchange = ? AND (
                    exchange_order_id = ? OR client_order_id = ?
                )
                """,
                (exchange, exchange_order_id, client_order_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT id FROM exchange_orders
                WHERE exchange = ? AND exchange_order_id = ?
                """,
                (exchange, exchange_order_id),
            ).fetchone()
        return cast(tuple[Any, ...] | None, row)

    def activation_state(self) -> ActivationRecord:
        """Return persisted one-time activation state, defaulting to inactive."""

        if not self.path.is_file():
            raise LedgerNotInitializedError(f"ledger does not exist: {self.path}")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT activated, activated_at, activation_type, activation_reason,
                           reference_symbol, reference_price, threshold
                    FROM activation_state
                    WHERE id = 1
                    """
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise LedgerNotInitializedError("ledger migrations are incomplete") from exc

        if row is None:
            return ActivationRecord(False, None, None, None, None, None, None)
        activated_at = datetime.fromisoformat(str(row[1])) if row[1] else None
        return ActivationRecord(
            activated=bool(row[0]),
            activated_at=activated_at,
            activation_type=str(row[2]) if row[2] is not None else None,
            activation_reason=str(row[3]) if row[3] is not None else None,
            reference_symbol=str(row[4]) if row[4] is not None else None,
            reference_price=Decimal(str(row[5])) if row[5] is not None else None,
            threshold=Decimal(str(row[6])) if row[6] is not None else None,
        )

    def record_activation(self, activation: ActivationInput) -> ActivationRecord:
        """Persist first activation exactly once and return the stored row."""

        self._require_aware(activation.activated_at)
        now = _utc_now()
        with self._connection() as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO activation_state(
                    id, activated, activated_at, activation_type, activation_reason,
                    reference_symbol, reference_price, threshold, created_at, updated_at
                ) VALUES(1, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activation.activated_at.astimezone(UTC).isoformat(),
                    activation.activation_type,
                    activation.activation_reason,
                    activation.reference_symbol,
                    str(activation.reference_price)
                    if activation.reference_price is not None
                    else None,
                    str(activation.threshold)
                    if activation.threshold is not None
                    else None,
                    now,
                    now,
                ),
            )
        return self.activation_state()

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
