# Changelog

All notable changes to Cost Average Out are documented here.

## 0.9.0 - 2026-07-06

- First stable pre-1.0 release for personal self-hosted operation.
- Added the CLI-first recurring sell workflow: config validation, ledger
  initialization, status inspection, reconciliation, balance snapshots, sell
  planning, dry-run cycles, guarded live cycles, price backfill, and portfolio
  history export.
- Added deterministic JSON output for automation-oriented commands, including
  stable `schema_version` fields and secret-free payloads.
- Added conservative safety controls for live trading, including explicit live
  opt-in, kill switch enforcement, first-live-sell confirmation, symbol
  allowlists, sizing limits, spread checks, and reconciliation-required unknown
  order states.
- Added SQLite ledger migrations, fake-exchange test coverage, operations
  guidance, systemd examples, security guidance, and MIT licensing.
