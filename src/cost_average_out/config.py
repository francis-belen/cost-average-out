"""Configuration loading and offline validation."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(ValueError):
    """Raised when a configuration file cannot be loaded or validated."""


class StrictModel(BaseModel):
    """Base model that rejects misspelled or unsupported settings."""

    model_config = ConfigDict(extra="forbid")


class CostAverageOutSettings(StrictModel):
    start_date: date
    end_date: date | None
    interval: str = Field(min_length=1)
    percentage: float = Field(gt=0, le=1)
    percentage_basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def end_date_is_not_before_start_date(self) -> CostAverageOutSettings:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class SafetySettings(StrictModel):
    live_trading_enabled: bool
    kill_switch: bool
    require_first_live_sell_confirmation: bool
    missed_cycle_policy: str = Field(min_length=1)
    max_sell_value_per_cycle: float = Field(gt=0)
    min_remaining_value_per_symbol: float = Field(ge=0)
    max_spread_bps: float = Field(gt=0)


class NotificationSettings(StrictModel):
    provider: str = Field(min_length=1)


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

    @model_validator(mode="after")
    def symbols_are_unique_and_match_quote_currency(self) -> AppConfig:
        normalized = [symbol.strip().upper() for symbol in self.symbols]
        if any(not symbol or "/" not in symbol for symbol in normalized):
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
        self.timezone = self.timezone.strip()
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
