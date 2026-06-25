# Operations

## Runtime Model

Run the app as a timer-driven command, not as a permanently running trading bot.
A typical host setup is:

- Linux VM.
- `systemd timer` or cron.
- SQLite database stored under `data/`.
- Daily database backup.
- Exchange API keys provided through environment variables.

## Example Timer Flow

```text
run-once
  load config
  reconcile exchange state
  check due or missed cycle
  plan orders
  submit orders only if safe and live-enabled
  reconcile fills
  notify
  exit
```

## MVP Validation Checklist

Run this sequence after setup, upgrades, or before a release tag:

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

Check that:

- live trading is disabled unless a live test is intentional,
- unresolved exchange orders are zero,
- `plan` has no unexpected `blocked` reasons,
- `run-once --dry-run` submits no live orders,
- `portfolio-history` writes valid JSON,
- any generated temporary export file is removed or stored intentionally.

This checklist is read-only against the exchange except for local SQLite writes.

## Backups

Back up the SQLite database before upgrades and at least daily during live use.
The database is the local audit trail for cycles, orders, fills, balances, and
notifications. Use SQLite's online backup command while the app is idle:

```bash
sqlite3 data/cost_average_out.sqlite3 ".backup 'backup/cost_average_out-$(date -u +%Y%m%dT%H%M%SZ).sqlite3'"
```

## Recovery

If a run fails after order submission, do not manually delete local state. Run
`reconcile` first so the app can compare local records with exchange orders and
fills.

- Timeout: run `reconcile`, inspect `status`, and retry only after unknown orders
  are resolved.
- Partial fill: run `reconcile`; the app blocks further live execution while the
  app-created order remains open or partial.
- VM restart: run `status` first, then `reconcile`; do not rerun live execution
  until unresolved warnings are gone.
- Bad config: fix config, run `validate-config`, then use `plan` before any live
  run.


## Initial Snapshot

Take an initial snapshot after the ledger is initialized and before relying on
`percentage_basis: initial_snapshot`:

```bash
cost-average-out snapshot-balances --config config.yaml
```

The command is read-only against the exchange and writes only to the local SQLite
ledger.


## Dry Run

Preview sell sizing and safety decisions without creating exchange orders:

```bash
cost-average-out plan --config config.yaml
cost-average-out run-once --dry-run --config config.yaml
```

By default, dry runs do not write cycle/order simulation rows. To store a local
simulation in the SQLite ledger, add `--persist-simulation`:

```bash
cost-average-out run-once --dry-run --persist-simulation --config config.yaml
```

The dry-run output includes planned quantities, estimated quote values,
skip/block reasons, and a notification preview.


## Live Run

Live execution requires both config opt-in and an explicit CLI flag:

```bash
cost-average-out run-once --live --config config.yaml
```

If `require_first_live_sell_confirmation` is true and no completed live cycle
exists yet, add the explicit confirmation flag for the first live sell:

```bash
cost-average-out run-once --live --confirm-first-live-sell --config config.yaml
```

Live execution is blocked when `live_trading_enabled` is false, `kill_switch` is
true, a missed cycle requires manual approval, the plan has block reasons, or any
app-created exchange order is unresolved. Submission timeouts are persisted as
`unknown_requires_reconciliation`; reconcile before retrying.


## Notifications

Use `notifications.provider: none` for local testing. For webhook notifications,
set:

```yaml
notifications:
  provider: webhook
```

and provide the URL through the environment:

```bash
export COST_AVERAGE_OUT_NOTIFICATION_WEBHOOK_URL=https://example.invalid/webhook
```

Notification kinds distinguish success, skip, block, and failure states.


## systemd

Example units are provided in:

- `docs/cost-average-out.service.example`
- `docs/cost-average-out.timer.example`

Install them under `/etc/systemd/system/`, update paths and user/group, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cost-average-out.timer
systemctl list-timers cost-average-out.timer
```

Start with `--dry-run` in the service file. Switch to `--live` only after read-only
and dry-run execution have been validated.


## Price Cache and Portfolio History

Backfill daily candles after reconciliation/snapshot data exists:

```bash
cost-average-out init-ledger --config config.yaml
cost-average-out backfill-prices --config config.yaml --days 90
cost-average-out portfolio-history --config config.yaml --output history.json
```

`portfolio-history` exports chart-ready JSON rows with per-asset cached close
prices, quantities, values, total quote value, missing price symbols, and a
valuation status. Missing candles are reported as `partial`; they are not silently
ignored.
