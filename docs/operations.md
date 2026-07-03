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
.venv/bin/cost-average-out validate-config --config config.yaml
.venv/bin/cost-average-out status --config config.yaml
.venv/bin/cost-average-out reconcile --config config.yaml
.venv/bin/cost-average-out snapshot-balances --config config.yaml
.venv/bin/cost-average-out plan --config config.yaml
.venv/bin/cost-average-out run-once --dry-run --config config.yaml
.venv/bin/cost-average-out backfill-prices --config config.yaml --days 7
.venv/bin/cost-average-out portfolio-history --config config.yaml --output history.json
```

Check that:

- live trading is disabled unless a live test is intentional,
- unresolved exchange orders are zero,
- `plan` has no unexpected `blocked` reasons,
- `run-once --dry-run` submits no live orders,
- `portfolio-history` writes valid JSON,
- any generated temporary export file is removed or stored intentionally.

This checklist is read-only against the exchange except for local SQLite writes.

## Production Readiness Status

The application has passed its first controlled Kraken live sell test. On
2026-06-25, a due cycle submitted three real market sell orders, recorded three
fills, reconciled with zero unresolved app-created orders, and was stored locally
as a completed cycle.

That means the software is production-capable for personal self-hosted use. A
specific installation is production only after it is deployed on the intended
host with the intended config, schedule, credentials, timer, and backup routine.

## Production Readiness Plan

For a new host, changed config, changed exchange key, or upgraded version, use
this safe progression:

1. Run the MVP validation checklist above with live trading disabled.
2. Create a separate exchange API key for live testing. It may have balance,
   order, trade-history, and spot order-creation permissions. It must not have
   withdrawal permission.
3. Configure exactly one test symbol and a sell percentage that produces an order
   above the exchange minimum. Keep `max_sell_value_per_cycle` low, such as
   EUR 5-10, for the first live test.
4. Keep `require_first_live_sell_confirmation: true`.
5. Run pre-live checks:

   ```bash
   .venv/bin/cost-average-out validate-config --config config.yaml
   .venv/bin/cost-average-out reconcile --config config.yaml
   .venv/bin/cost-average-out plan --config config.yaml
   .venv/bin/cost-average-out run-once --dry-run --config config.yaml
   ```

6. Execute the first live sell only when the plan is intentional:

   ```bash
   .venv/bin/cost-average-out run-once --live --confirm-first-live-sell --config config.yaml
   ```

7. Reconcile immediately:

   ```bash
   .venv/bin/cost-average-out reconcile --config config.yaml
   .venv/bin/cost-average-out status --config config.yaml
   ```

Production-ready criteria for personal use:

- one live order was submitted intentionally,
- the exchange order ID was persisted locally,
- fills and balances reconciled successfully,
- rerunning the same cycle did not duplicate the sell,
- `status` shows zero unresolved exchange orders,
- backup and restore procedure has been tested,
- `systemd` dry-run timer has been tested,
- optional webhook notifications have been tested if enabled.

## Recommended Production Host

For production use, run Cost Average Out on a dedicated VM or similarly isolated
host. Recommended baseline:

- dedicated Linux VM, not a shared workstation,
- dedicated Unix user such as `cost-average-out`,
- project-owned Python virtual environment,
- `.env`, `config.yaml`, and SQLite database readable only by that user,
- exchange API key scoped only to the required permissions and never withdrawal,
- `systemd timer` running `run-once`, initially with `--dry-run`,
- daily SQLite backups copied off-host,
- logs monitored through `journalctl`,
- live mode enabled only after repeated dry-run validation,
- the temporary live-test schedule replaced with the intended production
  schedule before unattended operation.

This keeps credentials, local ledger state, scheduling, and backups isolated from
other projects and reduces the blast radius of a host compromise.

## Deploy To A New VM

Use this procedure for a fresh Linux VM. Keep the first deployment in dry-run
mode until the timer, logs, credentials, schedule, and backups are verified.

### 1. Prepare the host

Create a dedicated Unix user and install the minimum runtime tools:

```bash
sudo adduser --system --group --home /opt/cost-average-out cost-average-out
sudo apt update
sudo apt install -y git python3 python3-venv sqlite3
```

`sqlite3` is not required by the app itself, but it is useful for local backup
and restore checks.

### 2. Install the app

Clone the repository, create the virtual environment, and install the package:

```bash
sudo -u cost-average-out git clone <YOUR_REPO_URL> /opt/cost-average-out
cd /opt/cost-average-out

sudo -u cost-average-out python3 -m venv .venv
sudo -u cost-average-out .venv/bin/python -m pip install --upgrade pip
sudo -u cost-average-out .venv/bin/python -m pip install -e .
```

### 3. Configure secrets and runtime files

Create `/opt/cost-average-out/config.yaml` from the known-good local config.
Create `/opt/cost-average-out/.env` from `.env.example`, then fill in exchange
credentials:

```bash
sudo -u cost-average-out cp .env.example .env
sudo -u cost-average-out nano .env
```

Required values:

```bash
COST_AVERAGE_OUT_EXCHANGE_API_KEY=...
COST_AVERAGE_OUT_EXCHANGE_API_SECRET=...
COST_AVERAGE_OUT_CONFIG=/opt/cost-average-out/config.yaml
```

Lock down ownership and permissions:

```bash
sudo chown -R cost-average-out:cost-average-out /opt/cost-average-out
sudo chmod 600 /opt/cost-average-out/.env /opt/cost-average-out/config.yaml
sudo mkdir -p /opt/cost-average-out/data
sudo chown cost-average-out:cost-average-out /opt/cost-average-out/data
```

The exchange API key should have only the permissions needed by the intended
mode. It must not have withdrawal permission.

### 4. Validate manually

Run the safe checks as the deployment user:

```bash
cd /opt/cost-average-out
sudo -u cost-average-out bash -lc 'set -a; source .env; set +a; .venv/bin/cost-average-out validate-config --config config.yaml'
sudo -u cost-average-out bash -lc 'set -a; source .env; set +a; .venv/bin/cost-average-out init-ledger --config config.yaml'
sudo -u cost-average-out bash -lc 'set -a; source .env; set +a; .venv/bin/cost-average-out status --config config.yaml'
sudo -u cost-average-out bash -lc 'set -a; source .env; set +a; .venv/bin/cost-average-out reconcile --config config.yaml'
sudo -u cost-average-out bash -lc 'set -a; source .env; set +a; .venv/bin/cost-average-out plan --config config.yaml'
sudo -u cost-average-out bash -lc 'set -a; source .env; set +a; .venv/bin/cost-average-out run-once --dry-run --config config.yaml'
```

Do not continue if `status` reports unresolved exchange or app orders, if
`reconcile` reports `Execution blocked: yes`, or if the plan is not intentional.

### 5. Install the systemd dry-run timer

Install the example units and edit paths or schedule as needed:

```bash
sudo cp /opt/cost-average-out/docs/cost-average-out.service.example /etc/systemd/system/cost-average-out.service
sudo cp /opt/cost-average-out/docs/cost-average-out.timer.example /etc/systemd/system/cost-average-out.timer
sudo nano /etc/systemd/system/cost-average-out.service
sudo nano /etc/systemd/system/cost-average-out.timer
```

Keep the service in dry-run mode first:

```ini
ExecStart=/opt/cost-average-out/.venv/bin/cost-average-out run-once --dry-run --config /opt/cost-average-out/config.yaml
```

Enable and inspect the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cost-average-out.timer
systemctl list-timers cost-average-out.timer
journalctl -u cost-average-out.service -n 100 --no-pager
```

### 6. Test backups

Create a backup directory and run one manual backup:

```bash
sudo mkdir -p /opt/cost-average-out/backups
sudo chown cost-average-out:cost-average-out /opt/cost-average-out/backups
sudo -u cost-average-out sqlite3 /opt/cost-average-out/data/cost_average_out.sqlite3 ".backup '/opt/cost-average-out/backups/cost_average_out-test.sqlite3'"
```

Verify that the backup file exists and can be copied off-host. Do not enable
unattended live execution before backup and restore have been tested.

### 7. First live run

Before live mode, rerun the pre-live checks. Confirm the schedule, symbols,
sell percentage, and estimated values are intentional, and confirm there are no
unresolved exchange or app orders.

Set `safety.live_trading_enabled: true` only when ready. The first live run
should be manual:

```bash
sudo -u cost-average-out bash -lc 'set -a; source /opt/cost-average-out/.env; set +a; /opt/cost-average-out/.venv/bin/cost-average-out run-once --live --confirm-first-live-sell --config /opt/cost-average-out/config.yaml'
```

Immediately reconcile and inspect status:

```bash
sudo -u cost-average-out bash -lc 'set -a; source /opt/cost-average-out/.env; set +a; /opt/cost-average-out/.venv/bin/cost-average-out reconcile --config /opt/cost-average-out/config.yaml'
sudo -u cost-average-out bash -lc 'set -a; source /opt/cost-average-out/.env; set +a; /opt/cost-average-out/.venv/bin/cost-average-out status --config /opt/cost-average-out/config.yaml'
```

Only after the first live run reconciles cleanly should the systemd service be
changed from `--dry-run` to `--live`.

## CLI Output

Cost Average Out is not a continuously running dashboard. Each command wakes up,
performs one bounded task, prints a snapshot, records local state when required,
and exits. Operator-facing commands highlight safety state such as live trading,
kill switch, exchange, schedule, ledger status, open orders, unresolved app orders, last
run, and next or relevant run where that information is available.

For commands that support output formats, `--output rich` is the default
human-readable mode. In an interactive terminal, Rich uses restrained tables and
panels for alignment. When stdout is redirected, captured by CI, or written to
systemd journals, Rich automatically falls back to plain text with stable labels
such as `Execution blocked`, `Unresolved app orders`, and `Notification preview`.

Use `--output json` on supported operator commands when automating with scripts,
CI, or AI agents. JSON output is deterministic, includes a top-level
`schema_version`, contains no Rich formatting, and omits secrets. See
[CLI Reference](./cli.md) for every command and its side effects.

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

- Timeout: run `reconcile`, inspect `status`, and retry only after unresolved app orders
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
.venv/bin/cost-average-out snapshot-balances --config config.yaml
```

The command is read-only against the exchange and writes only to the local SQLite
ledger.


## Dry Run

Preview sell sizing and safety decisions without creating exchange orders:

```bash
.venv/bin/cost-average-out plan --config config.yaml
.venv/bin/cost-average-out run-once --dry-run --config config.yaml
```

By default, dry runs do not write cycle/order simulation rows. To store a local
simulation in the SQLite ledger, add `--persist-simulation`:

```bash
.venv/bin/cost-average-out run-once --dry-run --persist-simulation --config config.yaml
```

The dry-run output includes planned quantities, estimated quote values,
skip/block reasons, and a notification preview.


## Live Run

Live execution requires both config opt-in and an explicit CLI flag:

```bash
.venv/bin/cost-average-out run-once --live --config config.yaml
```

If `require_first_live_sell_confirmation` is true and no completed live cycle
exists yet, add the explicit confirmation flag for the first live sell:

```bash
.venv/bin/cost-average-out run-once --live --confirm-first-live-sell --config config.yaml
```

Live execution is blocked when `live_trading_enabled` is false, `kill_switch` is
true, a missed cycle requires manual approval, the plan has block reasons, or any
app-created exchange order is unresolved. Submission timeouts are persisted as
`unknown_requires_reconciliation`; reconcile before retrying. Non-timeout
exchange rejections fail the local cycle without recording an exchange order.


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
.venv/bin/cost-average-out init-ledger --config config.yaml
.venv/bin/cost-average-out backfill-prices --config config.yaml --days 90
.venv/bin/cost-average-out portfolio-history --config config.yaml --output history.json
```

`portfolio-history` exports chart-ready JSON with `schema_version` and a `rows`
array containing per-asset cached close prices, quantities, values, total quote
value, missing price symbols, and a valuation status. Missing candles are
reported as `partial`; they are not silently ignored.
