# CLI Reference

Cost Average Out is CLI-first. There is no web dashboard and no interactive TUI.
Each command performs one bounded task, prints a snapshot, and exits.

Use `cost-average-out --help` or `cost-average-out COMMAND --help` for the
canonical option list installed on a host. This page documents what each command
is for, what it can touch, and what an operator should inspect.

## Output Model

Operator-facing commands print safety and status summaries. In an interactive
terminal, output uses restrained Rich tables and panels for alignment. When
stdout is redirected, captured by CI, or written to systemd journals, output
falls back to plain text with stable labels.

Do not automate against color or terminal formatting. For automation, use exit
codes and plain labels such as `Execution blocked`, `Unresolved app orders`,
`Dry run cycle status`, and `Notification preview`.

## Command Summary

| Command | Exchange access | SQLite writes | Live orders | Purpose |
| --- | --- | --- | --- | --- |
| `validate-config` | No | No | No | Validate config syntax and offline safety settings. |
| `schedule-status` | No | No | No | Show whether the configured schedule is due, not due, or missed. |
| `init-ledger` | No | Yes | No | Create or migrate the local SQLite ledger. |
| `status` | No | No | No | Show ledger-backed application status and next/relevant schedule. |
| `reconcile` | Read-only | Yes | No | Fetch exchange state and persist balances, orders, and fills. |
| `snapshot-balances` | Read-only | Yes | No | Store the initial balance snapshot used by initial-snapshot planning. |
| `plan` | Read-only | No | No | Preview sell quantities and safety decisions. |
| `run-once --dry-run` | Read-only when a cycle is due | Optional | No | Evaluate one timer cycle without submitting orders. |
| `run-once --live` | Read/write | Yes | Yes | Execute one guarded live cycle when every safety gate allows it. |
| `backfill-prices` | Read-only | Yes | No | Cache daily OHLCV candles for portfolio history export. |
| `portfolio-history` | No | No | No | Export chart-ready local portfolio history JSON. |

## Commands

### `validate-config`

```bash
cost-average-out validate-config --config config.yaml
```

Validates the YAML config without loading credentials, contacting an exchange,
or submitting orders. Use it after every config edit.

Inspect:

- config path,
- exchange,
- quote currency,
- timezone,
- allowlisted symbols,
- whether live trading is enabled or disabled.

### `schedule-status`

```bash
cost-average-out schedule-status --config config.yaml
cost-average-out schedule-status --config config.yaml --at 2030-01-01T12:00:00+01:00
```

Evaluates the configured recurring schedule without reading the ledger or
contacting an exchange. `--at` is useful for deterministic checks and must
include a UTC offset or `Z`.

Inspect:

- `Status`,
- `Scheduled at`,
- `Cycle ID`,
- `Manual approval required`,
- `Live Trading`,
- `Kill Switch`.

### `init-ledger`

```bash
cost-average-out init-ledger --config config.yaml
```

Creates or migrates the configured SQLite ledger. It does not contact an
exchange. It is safe to run repeatedly.

Inspect:

- `Ledger ready`,
- the resolved SQLite path.

### `status`

```bash
cost-average-out status --config config.yaml
```

Reads the initialized SQLite ledger and prints the current application snapshot.
It does not contact an exchange.

Inspect:

- `Live Trading`,
- `Kill Switch`,
- `Ledger Status`,
- `Cycles`,
- `Planned orders`,
- `Unresolved exchange orders`,
- `Unknown Orders`,
- `Last cycle`,
- next or relevant scheduled cycle.

If unresolved or unknown orders are non-zero, reconcile before any live attempt.

### `reconcile`

```bash
cost-average-out reconcile --config config.yaml
cost-average-out reconcile --config config.yaml --lookback-days 14
```

Fetches balances, market metadata, open orders, recent orders, and recent fills
from the exchange, then records the reconciliation in SQLite. It is read-only
against the exchange and does not submit, cancel, or modify orders.

Inspect:

- `Balances recorded`,
- `Markets validated`,
- `Open orders`,
- `Recent orders`,
- `Recent fills`,
- `Unresolved app orders`,
- `Unknown Orders`,
- `Execution blocked`.

When `Execution blocked: yes`, do not run live execution until the unresolved
state has been understood.

### `snapshot-balances`

```bash
cost-average-out snapshot-balances --config config.yaml
```

Fetches current exchange balances and stores them as the local
`initial_snapshot`. This is required before relying on
`percentage_basis: initial_snapshot`.

Inspect:

- `Initial balance snapshot recorded`,
- `Markets validated`,
- `Live Trading`,
- `Kill Switch`.

### `plan`

```bash
cost-average-out plan --config config.yaml
cost-average-out plan --config config.yaml --at 2030-01-01T00:00:00Z
```

Builds a read-only sell-plan preview. It validates market metadata, reads
balances and tickers, applies configured sizing and safety limits, and prints
planned, skipped, or blocked items. It does not write cycle rows and does not
submit orders.

Inspect:

- `Execution blocked`,
- `Planned orders`,
- `Skipped/blocked`,
- each symbol's status,
- quantity,
- estimated quote value,
- skip or block reason.

Any unexpected block reason should be understood before live execution.

### `run-once --dry-run`

```bash
cost-average-out run-once --dry-run --config config.yaml
cost-average-out run-once --dry-run --persist-simulation --config config.yaml
```

Evaluates one timer cycle without submitting exchange orders. If no cycle is
due, it exits after printing the schedule decision. If a cycle is due and manual
approval is not required, it builds the same plan preview used by live
execution.

By default, dry-run does not write cycle or planned-order simulation rows.
`--persist-simulation` stores a local simulation in SQLite for inspection.

Inspect:

- `Dry run cycle status`,
- `Scheduled at`,
- `Cycle ID`,
- `Manual approval required`,
- `Execution blocked`,
- `Simulation persisted`,
- `Notification preview`.

### `run-once --live`

```bash
cost-average-out run-once --live --config config.yaml
cost-average-out run-once --live --confirm-first-live-sell --config config.yaml
```

Executes one guarded live cycle. This is the only command that can submit live
exchange orders. It is blocked unless live trading is enabled, the kill switch
is off, the schedule is due, manual approval is not required, there are no
execution-blocking plan reasons, and no app-created exchange orders are
unresolved.

If `require_first_live_sell_confirmation` is true and no completed live cycle
exists yet, the first live run must include `--confirm-first-live-sell`.

Inspect:

- `Live cycle status`,
- `Submitted orders`,
- `Post-submit reconciliation blocked`,
- `Open Orders`,
- `Unknown Orders`,
- `Notification preview`.

If submission times out, the app records
`unknown_requires_reconciliation`. Run `reconcile` and inspect `status` before
retrying.

### `backfill-prices`

```bash
cost-average-out backfill-prices --config config.yaml --days 90
cost-average-out backfill-prices --config config.yaml --days 30 --limit 100
```

Fetches daily OHLCV candles from the exchange and stores them in SQLite. This is
used only by local portfolio history export; it does not affect live sell
execution decisions.

Inspect:

- `Candles fetched`,
- `Candles stored`.

### `portfolio-history`

```bash
cost-average-out portfolio-history --config config.yaml
cost-average-out portfolio-history --config config.yaml --output history.json
```

Exports chart-ready JSON from local ledger and cached candle data. It does not
contact an exchange and does not write SQLite state. If `--output` is omitted,
JSON is written to stdout.

Inspect:

- JSON validity,
- the configured quote currency,
- the exported time series before using it elsewhere.

## Safety Signals

Every operator workflow should make these signals visible before live execution:

- `Live Trading`: must be intentionally enabled for live mode.
- `Kill Switch`: must be off.
- `Exchange`: must match the intended exchange account.
- `Schedule`: must be the intended recurring cadence.
- `Ledger Status`: should be ready.
- `Open Orders`: should be expected.
- `Unknown Orders`: must be zero before a live retry.
- `Last cycle`: should match the operator's understanding of the latest run.
- next or relevant scheduled cycle: should match the intended timer cadence.

When in doubt, run:

```bash
cost-average-out status --config config.yaml
cost-average-out reconcile --config config.yaml
cost-average-out plan --config config.yaml
cost-average-out run-once --dry-run --config config.yaml
```
