# Cost Average Out

Self-hosted recurring crypto sell automation for gradual, controlled portfolio
distribution.

## Status

Pre-MVP project scaffold. The initial goal is a conservative CLI app that can
sell a configured percentage of selected exchange balances on a fixed schedule.

## Warning

This project can submit real exchange orders once live trading is implemented and
enabled. It is not financial advice, tax advice, or a managed service. Use dry-run
mode first and review every configuration value before enabling live trading.

## MVP Scope

- One exchange at a time.
- Spot sells only.
- Selected symbols only.
- Fixed interval.
- Fixed percentage.
- SQLite local ledger.
- Dry-run mode.
- Reconciliation before live orders.
- Timer-friendly `run-once` command.

## Quick Start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
cp config.example.yaml config.yaml
cost-average-out validate-config --config config.yaml
```

Config validation is offline: it parses and validates the YAML file without
loading credentials, contacting an exchange, or submitting orders. The `plan`
and live execution workflows are not implemented yet.

Live trading should remain disabled until configuration, exchange permissions,
and reconciliation behavior are verified.

## Development Checks

```bash
pytest
ruff check .
mypy
```

## Repository Policy

This repository is public for transparency, personal use, and portfolio purposes.
External contributions are not currently accepted. See [CONTRIBUTING.md](./CONTRIBUTING.md).

## Documentation

- [Implementation Plan](./docs/implementation_plan.md)
- [Config](./docs/config.md)
- [Ledger](./docs/ledger.md)
- [Safety](./docs/safety.md)
- [Operations](./docs/operations.md)
- [Exchange Adapters](./docs/exchange-adapters.md)
