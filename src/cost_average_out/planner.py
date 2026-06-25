"""Sell planning and safety checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from cost_average_out.config import AppConfig, PercentageBasis
from cost_average_out.exchange import Balance, Market, Ticker


class PlanItemStatus(StrEnum):
    PLANNED = "planned"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SellPlanItem:
    symbol: str
    base_asset: str
    quantity: Decimal
    estimated_quote_value: Decimal
    status: PlanItemStatus
    reason: str | None = None


@dataclass(frozen=True)
class SellPlan:
    observed_at: datetime
    items: tuple[SellPlanItem, ...]

    @property
    def planned_items(self) -> tuple[SellPlanItem, ...]:
        return tuple(
            item for item in self.items if item.status is PlanItemStatus.PLANNED
        )

    @property
    def has_blocks(self) -> bool:
        return any(item.status is PlanItemStatus.BLOCKED for item in self.items)


def build_sell_plan(
    config: AppConfig,
    balances: list[Balance],
    markets: list[Market],
    tickers: list[Ticker],
    *,
    initial_balances: list[Balance] | None = None,
    now: datetime | None = None,
) -> SellPlan:
    """Compute one conservative sell plan from exchange state."""

    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    balance_by_asset = {balance.asset.upper(): balance for balance in balances}
    initial_balance_by_asset = {
        balance.asset.upper(): balance for balance in initial_balances or []
    }
    market_by_symbol = {market.symbol.upper(): market for market in markets}
    ticker_by_symbol = {ticker.symbol.upper(): ticker for ticker in tickers}

    items: list[SellPlanItem] = []
    for symbol in config.symbols:
        normalized_symbol = symbol.upper()
        base_asset = normalized_symbol.split("/", maxsplit=1)[0]
        current_balance = balance_by_asset.get(base_asset)
        market = market_by_symbol.get(normalized_symbol)
        ticker = ticker_by_symbol.get(normalized_symbol)

        basis_balance = current_balance
        if config.cost_average_out.percentage_basis is PercentageBasis.INITIAL_SNAPSHOT:
            basis_balance = initial_balance_by_asset.get(base_asset)
            if basis_balance is None:
                items.append(
                    _item(
                        normalized_symbol,
                        base_asset,
                        Decimal("0"),
                        Decimal("0"),
                        PlanItemStatus.BLOCKED,
                        "missing initial balance snapshot",
                    )
                )
                continue

        if current_balance is None or basis_balance is None:
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    Decimal("0"),
                    Decimal("0"),
                    PlanItemStatus.SKIPPED,
                    "no available balance",
                )
            )
            continue
        if market is None:
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    Decimal("0"),
                    Decimal("0"),
                    PlanItemStatus.BLOCKED,
                    "missing market metadata",
                )
            )
            continue
        if ticker is None or ticker.bid is None or ticker.ask is None:
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    Decimal("0"),
                    Decimal("0"),
                    PlanItemStatus.BLOCKED,
                    "missing bid/ask quote",
                )
            )
            continue
        if ticker.bid <= 0 or ticker.ask <= 0 or ticker.ask < ticker.bid:
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    Decimal("0"),
                    Decimal("0"),
                    PlanItemStatus.BLOCKED,
                    "invalid bid/ask quote",
                )
            )
            continue

        mid_price = (ticker.bid + ticker.ask) / Decimal("2")
        spread_bps = (ticker.ask - ticker.bid) / mid_price * Decimal("10000")
        if spread_bps > Decimal(str(config.safety.max_spread_bps)):
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    Decimal("0"),
                    Decimal("0"),
                    PlanItemStatus.BLOCKED,
                    f"spread exceeds max_spread_bps: {spread_bps.normalize()}",
                )
            )
            continue

        basis_quantity = basis_balance.available
        quantity = basis_quantity * Decimal(str(config.cost_average_out.percentage))
        if quantity <= 0 or current_balance.available <= 0:
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    Decimal("0"),
                    Decimal("0"),
                    PlanItemStatus.SKIPPED,
                    "no available balance",
                )
            )
            continue
        if quantity > current_balance.available:
            quantity = current_balance.available

        estimated_value = quantity * ticker.bid
        if market.minimum_amount is not None and quantity < market.minimum_amount:
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    quantity,
                    estimated_value,
                    PlanItemStatus.SKIPPED,
                    "below exchange minimum amount",
                )
            )
            continue
        if market.minimum_cost is not None and estimated_value < market.minimum_cost:
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    quantity,
                    estimated_value,
                    PlanItemStatus.SKIPPED,
                    "below exchange minimum notional",
                )
            )
            continue
        if estimated_value > Decimal(str(config.safety.max_sell_value_per_cycle)):
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    quantity,
                    estimated_value,
                    PlanItemStatus.BLOCKED,
                    "above max sell value per cycle",
                )
            )
            continue

        remaining_quantity = current_balance.available - quantity
        remaining_value = remaining_quantity * ticker.bid
        if remaining_value < Decimal(str(config.safety.min_remaining_value_per_symbol)):
            items.append(
                _item(
                    normalized_symbol,
                    base_asset,
                    quantity,
                    estimated_value,
                    PlanItemStatus.BLOCKED,
                    "remaining value below configured minimum",
                )
            )
            continue

        items.append(
            _item(
                normalized_symbol,
                base_asset,
                quantity,
                estimated_value,
                PlanItemStatus.PLANNED,
                None,
            )
        )

    return SellPlan(observed_at=observed_at, items=tuple(items))


def _item(
    symbol: str,
    base_asset: str,
    quantity: Decimal,
    estimated_quote_value: Decimal,
    status: PlanItemStatus,
    reason: str | None,
) -> SellPlanItem:
    return SellPlanItem(
        symbol=symbol,
        base_asset=base_asset,
        quantity=quantity.normalize(),
        estimated_quote_value=estimated_quote_value.normalize(),
        status=status,
        reason=reason,
    )
