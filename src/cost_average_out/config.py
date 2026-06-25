"""Configuration loading and offline validation."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class ConfigError(ValueError):
    """Raised when a configuration file cannot be loaded or validated."""


class StrictModel(BaseModel):
    """Base model that rejects misspelled or unsupported settings."""

    model_config = ConfigDict(extra="forbid")


class Interval(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class PercentageBasis(StrEnum):
    CURRENT_BALANCE = "current_balance"
    INITIAL_SNAPSHOT = "initial_snapshot"


class MissedCyclePolicy(StrEnum):
    REQUIRE_MANUAL_APPROVAL = "require_manual_approval"


class CostAverageOutSettings(StrictModel):
    start_date: date
    end_date: date | None = None
    interval: Interval
    percentage: float = Field(gt=0, le=1)
    percentage_basis: PercentageBasis

    @model_validator(mode="after")
    def end_date_is_not_before_start_date(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class SafetySettings(StrictModel):
    live_trading_enabled: bool
    kill_switch: bool
    require_first_live_sell_confirmation: bool
    missed_cycle_policy: MissedCyclePolicy
    max_sell_value_per_cycle: float = Field(gt=0)
    min_remaining_value_per_symbol: float = Field(ge=0)
    max_spread_bps: float = Field(gt=0)


class NotificationSettings(StrictModel):
    provider: Literal["none", "webhook"]


class AppConfig(StrictModel):
    config_version: Literal[1]
    exchange: str = Field(min_length=1)
    quote_currency: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    database_path: Path
    cost_average_out: CostAverageOutSettings
    symbols: list[str] = Field(min_length=1)
    safety: SafetySettings
    notifications: NotificationSettings

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {normalized}") from exc
        return normalized

    @model_validator(mode="after")
    def normalize_and_validate_allowlist(self) -> Self:
        normalized = [symbol.strip().upper() for symbol in self.symbols]
        symbol_pattern = re.compile(r"^[A-Z0-9]{2,20}/[A-Z0-9]{2,20}$")
        if any(symbol_pattern.fullmatch(symbol) is None for symbol in normalized):
            raise ValueError("each symbol must use BASE/QUOTE format")
        if len(normalized) != len(set(normalized)):
            raise ValueError("symbols must be unique")

        expected_quote = self.quote_currency.strip().upper()
        mismatches = [
            symbol
            for symbol in normalized
            if symbol.rsplit("/", maxsplit=1)[1] != expected_quote
        ]
        if mismatches:
            raise ValueError(
                "symbol quote currencies must match quote_currency: "
                + ", ".join(mismatches)
            )

        self.exchange = self.exchange.strip().lower()
        self.quote_currency = expected_quote
        self.symbols = normalized
        return self


def load_config(path: Path) -> AppConfig:
    """Load and validate a YAML config without contacting an exchange."""

    try:
        raw_config = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {exc}") from exc

    try:
        data = yaml.safe_load(raw_config)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("config root must be a YAML mapping")

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
