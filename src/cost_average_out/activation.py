"""One-time activation gate evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from cost_average_out.config import ActivationType, AppConfig
from cost_average_out.exchange import ExchangeAdapter, ExchangeError, Ticker
from cost_average_out.ledger import ActivationInput, ActivationRecord, Ledger


class ActivationStatus(StrEnum):
    ACTIVE = "active"
    WAITING = "waiting_for_activation"


class PriceTriggerStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    NOT_CHECKED = "not_checked"
    MET = "met"
    NOT_MET = "not_met"
    FETCH_FAILED = "fetch_failed"


@dataclass(frozen=True)
class ActivationEvaluation:
    status: ActivationStatus
    activation_type: str
    activated_at: datetime | None
    activation_reason: str | None
    reference_symbol: str | None
    reference_price: Decimal | None
    threshold: Decimal | None
    price_trigger_status: PriceTriggerStatus
    message: str

    @property
    def activated(self) -> bool:
        return self.status is ActivationStatus.ACTIVE


def evaluate_activation(
    config: AppConfig,
    ledger: Ledger | None,
    *,
    now: datetime,
    adapter: ExchangeAdapter | None = None,
    persist: bool = False,
) -> ActivationEvaluation:
    """Evaluate the one-time activation gate, optionally persisting first activation."""

    _require_aware(now)
    if ledger is not None:
        stored = ledger.activation_state()
        if stored.activated:
            return _from_record(stored)

    activation = config.activation
    local_date = now.astimezone(ZoneInfo(config.timezone)).date()
    if activation.date is not None and local_date >= activation.date:
        return _activate(
            ledger,
            persist=persist,
            activation_type=activation.type.value,
            activated_at=now,
            reason="date_reached",
            reference_symbol=None,
            reference_price=None,
            threshold=None,
        )

    if activation.type is ActivationType.DATE:
        return ActivationEvaluation(
            status=ActivationStatus.WAITING,
            activation_type=activation.type.value,
            activated_at=None,
            activation_reason=None,
            reference_symbol=None,
            reference_price=None,
            threshold=None,
            price_trigger_status=PriceTriggerStatus.NOT_CONFIGURED,
            message=f"waiting for activation date {activation.date}",
        )

    trigger = activation.price_trigger
    if trigger is None:
        raise ValueError("date_or_price activation requires price_trigger")
    if adapter is None:
        return ActivationEvaluation(
            status=ActivationStatus.WAITING,
            activation_type=activation.type.value,
            activated_at=None,
            activation_reason=None,
            reference_symbol=trigger.reference_symbol,
            reference_price=None,
            threshold=trigger.threshold,
            price_trigger_status=PriceTriggerStatus.NOT_CHECKED,
            message="waiting for activation; price trigger was not checked",
        )

    try:
        ticker = _single_ticker(adapter, trigger.reference_symbol)
        reference_price = _reference_price(ticker)
    except ExchangeError as exc:
        return ActivationEvaluation(
            status=ActivationStatus.WAITING,
            activation_type=activation.type.value,
            activated_at=None,
            activation_reason=None,
            reference_symbol=trigger.reference_symbol,
            reference_price=None,
            threshold=trigger.threshold,
            price_trigger_status=PriceTriggerStatus.FETCH_FAILED,
            message=f"activation price fetch failed: {exc}",
        )

    if reference_price >= trigger.threshold:
        return _activate(
            ledger,
            persist=persist,
            activation_type=activation.type.value,
            activated_at=now,
            reason="price_trigger_reached",
            reference_symbol=trigger.reference_symbol,
            reference_price=reference_price,
            threshold=trigger.threshold,
        )

    return ActivationEvaluation(
        status=ActivationStatus.WAITING,
        activation_type=activation.type.value,
        activated_at=None,
        activation_reason=None,
        reference_symbol=trigger.reference_symbol,
        reference_price=reference_price,
        threshold=trigger.threshold,
        price_trigger_status=PriceTriggerStatus.NOT_MET,
        message="waiting for activation; price trigger not reached",
    )


def _activate(
    ledger: Ledger | None,
    *,
    persist: bool,
    activation_type: str,
    activated_at: datetime,
    reason: str,
    reference_symbol: str | None,
    reference_price: Decimal | None,
    threshold: Decimal | None,
) -> ActivationEvaluation:
    if persist:
        if ledger is None:
            raise ValueError("ledger is required to persist activation")
        record = ledger.record_activation(
            ActivationInput(
                activated_at=activated_at,
                activation_type=activation_type,
                activation_reason=reason,
                reference_symbol=reference_symbol,
                reference_price=reference_price,
                threshold=threshold,
            )
        )
        return _from_record(record)
    return ActivationEvaluation(
        status=ActivationStatus.ACTIVE,
        activation_type=activation_type,
        activated_at=activated_at,
        activation_reason=reason,
        reference_symbol=reference_symbol,
        reference_price=reference_price,
        threshold=threshold,
        price_trigger_status=(
            PriceTriggerStatus.MET
            if reference_symbol is not None
            else PriceTriggerStatus.NOT_CONFIGURED
        ),
        message=f"activation condition met: {reason}",
    )


def _from_record(record: ActivationRecord) -> ActivationEvaluation:
    return ActivationEvaluation(
        status=ActivationStatus.ACTIVE
        if record.activated
        else ActivationStatus.WAITING,
        activation_type=record.activation_type or "unknown",
        activated_at=record.activated_at,
        activation_reason=record.activation_reason,
        reference_symbol=record.reference_symbol,
        reference_price=record.reference_price,
        threshold=record.threshold,
        price_trigger_status=(
            PriceTriggerStatus.MET
            if record.reference_symbol is not None
            else PriceTriggerStatus.NOT_CONFIGURED
        ),
        message=(
            f"activated by {record.activation_reason}"
            if record.activated
            else "waiting for activation"
        ),
    )


def _single_ticker(adapter: ExchangeAdapter, symbol: str) -> Ticker:
    tickers = list(adapter.fetch_tickers([symbol]))
    for ticker in tickers:
        if ticker.symbol.upper() == symbol.upper():
            return ticker
    raise ExchangeError(f"reference ticker unavailable: {symbol}")


def _reference_price(ticker: Ticker) -> Decimal:
    prices = [price for price in (ticker.bid, ticker.ask) if price is not None]
    if not prices:
        raise ExchangeError(f"reference ticker has no bid/ask: {ticker.symbol}")
    price = sum(prices, Decimal("0")) / Decimal(len(prices))
    if price <= 0:
        raise ExchangeError(f"reference ticker is not positive: {ticker.symbol}")
    return price


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
