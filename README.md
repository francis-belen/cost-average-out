# Cost Average Out

Self-hosted recurring crypto sell automation for gradual, controlled portfolio
distribution.

## Status

MVP implementation in progress. The CLI can validate config, initialize a local
ledger, reconcile read-only exchange state, snapshot balances, plan sells, run
dry-run cycles, and execute guarded live cycles when explicitly enabled.

## Warning

This project can submit real exchange orders when live trading is enabled and
`run-once --live` is used. It is not financial advice, tax advice, or a managed
service. Use dry-run mode first and review every configuration value before
enabling live trading.

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
cost-average-out schedule-status --config config.yaml
cost-average-out init-ledger --config config.yaml
cost-average-out status --config config.yaml
cost-average-out reconcile --config config.yaml
cost-average-out snapshot-balances --config config.yaml
cost-average-out plan --config config.yaml
cost-average-out run-once --dry-run --config config.yaml
cost-average-out backfill-prices --config config.yaml --days 90
cost-average-out portfolio-history --config config.yaml --output history.json
```

Config validation is offline: it parses and validates the YAML file without
loading credentials, contacting an exchange, or submitting orders.

`snapshot-balances`, `plan`, and `run-once --dry-run` are read-only against the
exchange. `plan` prints the next sell quantities and safety decisions.
`run-once --dry-run` evaluates the timer cycle and can optionally persist a local
simulation with `--persist-simulation`.

Live execution is available only through `run-once --live` and is blocked unless
`live_trading_enabled` is true, the kill switch is false, and any configured
first-live-sell confirmation is supplied.

Live trading should remain disabled until configuration, exchange permissions,
and reconciliation behavior are verified.

## MVP Validation Checklist

Before tagging or enabling live trading, run the safe release-gate workflow:

```bash
cost-average-out validate-config --config config.yaml
cost-average-out status --config config.yaml
cost-average-out reconcile --config config.yaml
cost-average-out snapshot-balances --config config.yaml
cost-average-out plan --config config.yaml
cost-average-out run-once --dry-run --config config.yaml
cost-average-out backfill-prices --config config.yaml --days 7
cost-average-out portfolio-history --config config.yaml --output history.json
```

Expected results: config is valid, unresolved exchange orders are zero, read-only
exchange calls succeed, dry-run submits no orders, and portfolio history exports
valid JSON. Do not run `run-once --live` until dry-run behavior, API permissions,
and configured sell sizes have been reviewed intentionally.

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
- [systemd service example](./docs/cost-average-out.service.example)
- [systemd timer example](./docs/cost-average-out.timer.example)
