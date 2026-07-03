# Cost Average Out Implementation Plan

Last updated: 2026-07-04

## Goal

Build a standalone, self-hosted recurring sell app that can gradually distribute
configured crypto holdings into a quote currency such as EUR.

Cost Average Out should work even if the holdings were acquired outside the app.
The first production-quality version should favor conservative behavior over
automation convenience.

## MVP Definition

The MVP is complete when a user can:

- configure one exchange and a symbol allowlist,
- validate config and exchange access,
- create a local SQLite ledger,
- fetch and store an initial balance snapshot,
- dry-run the next recurring sell cycle,
- execute one live recurring sell cycle with explicit live flags,
- reconcile fills and balances after execution,
- receive a cycle summary notification,
- run the app from a `systemd timer`,
- inspect status from the CLI.

## Non-Goals for MVP

- Multi-exchange execution.
- Tax-lot optimization.
- Market timing.
- Automated rebalancing.
- Cross-exchange routing.
- Built-in graphical or interactive UI.
- Selling assets outside the configured allowlist.

## Phase 0: Repo Bootstrap

Deliverables:

- Public repo skeleton.
- `README.md` with project scope and warnings.
- `LICENSE`.
- `SECURITY.md`.
- `.env.example`.
- `config.example.yaml`.
- Basic CLI entrypoint.
- Basic test runner and lint/type-check setup.

Acceptance checks:

- `cost-average-out --help` works.
- `cost-average-out validate-config --config config.example.yaml` works in
  dry-run mode.
- No secrets are committed.

## Phase 1: Config and Scheduling

Deliverables:

- Versioned config schema.
- Symbol allowlist.
- Quote currency setting.
- Timezone-aware interval scheduling.
- Deterministic cycle ID generation.
- Missed-cycle policy handling.

Example config:

```yaml
config_version: 1
exchange: kraken
quote_currency: EUR
timezone: Europe/Amsterdam

cost_average_out:
  start_date: "2030-01-01"
  end_date: null
  interval: weekly
  percentage: 0.01
  percentage_basis: current_balance

symbols:
  - BTC/EUR
  - ETH/EUR

safety:
  live_trading_enabled: false
  kill_switch: false
  require_first_live_sell_confirmation: true
  missed_cycle_policy: require_manual_approval
```

Acceptance checks:

- Invalid percentages are rejected.
- Unsupported missed-cycle policies are rejected.
- Cycle IDs are stable across repeated runs.
- The app can distinguish not-due, due, and missed cycles.

## Phase 2: Ledger

Deliverables:

- SQLite migrations.
- Tables for cycles, planned orders, exchange orders, fills, ledger events,
  balances, and app metadata.
- Transaction boundaries for cycle creation and order planning.
- CLI status view backed by the ledger.

Acceptance checks:

- Migration can run repeatedly.
- Completed cycle cannot be duplicated.
- Planned sell order is unique per cycle and symbol.
- Ledger can represent skipped, blocked, partial, and completed cycles.

## Phase 3: Exchange Adapter Read-Only Mode

Deliverables:

- Exchange adapter interface.
- First concrete exchange implementation.
- Balance fetch.
- Market/symbol metadata fetch.
- Open-order fetch.
- Recent order/fill fetch for configured symbols.
- Read-only `reconcile` command.

Acceptance checks:

- App can fetch available and total balances.
- App rejects symbols unavailable on the exchange.
- App records reconciled balances.
- App blocks execution when open app-created orders are unresolved.

## Phase 4: Sell Planner

Deliverables:

- Initial balance snapshot command.
- Sell sizing for `current_balance`.
- Optional sizing for `initial_snapshot`.
- Min order notional validation.
- Min remaining value validation.
- Max sell value per cycle validation.
- Spread/liquidity guard.

Acceptance checks:

- A 1% sell plan is computed from available balance.
- Plan skips orders below exchange minimum.
- Plan blocks orders above max sell value.
- Plan never includes symbols outside the allowlist.
- Plan uses available balance, not total locked balance.

## Phase 5: Dry-Run Cycle

Deliverables:

- `plan` command.
- `run-once --dry-run` command.
- Notification preview.
- Clear skip/block reasons.

Acceptance checks:

- Dry run writes no live exchange orders.
- Dry run can optionally persist a simulation record.
- Output includes planned sell quantity, estimated value, and safety decisions.

## Phase 6: Live Execution

Deliverables:

- Explicit live execution flag.
- First-live-sell confirmation gate.
- Order submission.
- Immediate persistence of exchange order IDs.
- Post-submit reconciliation.
- Partial fill handling.
- Failure and timeout handling.

Acceptance checks:

- Live execution is impossible while `live_trading_enabled` is false.
- Kill switch blocks all live orders.
- Timeout creates `unknown_requires_reconciliation`, not a blind retry.
- Re-running after a completed cycle does not sell again.
- Re-running after partial fill reconciles before any further action.

## Phase 7: Notifications and Operations

Deliverables:

- Notification provider interface.
- At least one notification provider.
- `systemd` service and timer examples.
- Backup notes for SQLite.
- Operational recovery guide.

Acceptance checks:

- Success, skip, block, and failure notifications are distinguishable.
- `systemd timer` can run `run-once`.
- `status` shows last run, next run, open orders, and warnings.

## Phase 8: Price Cache and Portfolio History

Deliverables:

- Bounded OHLCV cache.
- `backfill-prices` command.
- Portfolio value reconstruction from balances/ledger and daily candles.
- Chart-ready JSON export for external charting tools.

Acceptance checks:

- Candle backfill is incremental.
- Portfolio history exports chart-ready JSON without a full market database.
- Missing price data is visible as estimated or unavailable, not silently hidden.

## Release Gate

Before a v1 public release:

- Unit tests pass.
- Fake-exchange integration tests pass.
- Dry-run workflow is documented.
- Live trading requires explicit opt-in.
- API key permission guidance is documented.
- Recovery from timeout, partial fill, and VM restart is tested.
