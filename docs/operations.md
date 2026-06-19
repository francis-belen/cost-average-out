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

## Backups

Back up the SQLite database before upgrades and at least daily during live use.
The database is the local audit trail for cycles, orders, fills, balances, and
notifications.

## Recovery

If a run fails after order submission, do not manually delete local state. Run
`reconcile` first so the app can compare local records with exchange orders and
fills.
