"""Read-only exchange reconciliation workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cost_average_out.config import AppConfig
from cost_average_out.exchange import ExchangeAdapter, Order, OrderStatus
from cost_average_out.ledger import (
    BalanceInput,
    Ledger,
    ReconciledFillInput,
    ReconciledOrderInput,
)


@dataclass(frozen=True)
class ReconciliationResult:
    observed_at: datetime
    balance_count: int
    market_count: int
    open_order_count: int
    recent_order_count: int
    recent_fill_count: int
    matched_order_count: int
    recorded_fill_count: int
    unresolved_app_order_count: int
    execution_blocked: bool


def reconcile(
    config: AppConfig,
    ledger: Ledger,
    adapter: ExchangeAdapter,
    *,
    now: datetime | None = None,
    lookback: timedelta = timedelta(days=7),
) -> ReconciliationResult:
    """Fetch account state, validate symbols, and persist a reconciliation."""

    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if lookback <= timedelta(0):
        raise ValueError("lookback must be positive")

    markets = adapter.fetch_markets(config.symbols)
    balances = adapter.fetch_balances()
    open_orders = adapter.fetch_open_orders(config.symbols)
    since = observed_at - lookback
    recent_orders = adapter.fetch_recent_orders(config.symbols, since)
    recent_fills = adapter.fetch_recent_fills(config.symbols, since)

    all_orders = _deduplicate_orders([*open_orders, *recent_orders])
    write_result = ledger.record_reconciliation(
        adapter.name,
        observed_at,
        [
            BalanceInput(
                asset=balance.asset,
                available=balance.available,
                total=balance.total,
            )
            for balance in balances
        ],
        [
            ReconciledOrderInput(
                exchange_order_id=order.exchange_order_id,
                client_order_id=order.client_order_id,
                status=order.status.value,
                raw=order.raw,
            )
            for order in all_orders
        ],
        [
            ReconciledFillInput(
                exchange_fill_id=fill.exchange_fill_id,
                exchange_order_id=fill.exchange_order_id,
                symbol=fill.symbol,
                quantity=fill.quantity,
                price=fill.price,
                fee=fill.fee,
                fee_currency=fill.fee_currency,
                filled_at=fill.filled_at,
                raw=fill.raw,
            )
            for fill in recent_fills
        ],
    )

    unresolved_remote = sum(
        order.is_app_created
        and order.status
        in {
            OrderStatus.OPEN,
            OrderStatus.PARTIAL,
            OrderStatus.UNKNOWN_REQUIRES_RECONCILIATION,
        }
        for order in all_orders
    )
    unresolved_local = ledger.summary().unresolved_exchange_order_count
    unresolved = max(unresolved_remote, unresolved_local)
    return ReconciliationResult(
        observed_at=observed_at,
        balance_count=write_result.balance_count,
        market_count=len(markets),
        open_order_count=len(open_orders),
        recent_order_count=len(recent_orders),
        recent_fill_count=len(recent_fills),
        matched_order_count=write_result.matched_order_count,
        recorded_fill_count=write_result.recorded_fill_count,
        unresolved_app_order_count=unresolved,
        execution_blocked=unresolved > 0,
    )


def _deduplicate_orders(orders: list[Order]) -> list[Order]:
    unique: dict[str, Order] = {}
    for order in orders:
        unique[order.exchange_order_id] = order
    return list(unique.values())
