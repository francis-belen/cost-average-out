# Ledger

The SQLite ledger is the local source of record for app decisions. The exchange
remains the source of truth for real orders, fills, fees, and balances.

Initial tables:

- `app_metadata`
- `cycles`
- `planned_orders`
- `exchange_orders`
- `fills`
- `ledger_events`
- `balances`

Important constraints:

- `cycles.cycle_id` is unique.
- Planned orders are unique per cycle and symbol.
- Exchange order IDs are unique when known.

Never delete ledger rows to recover from an unknown order state. Reconcile with
the exchange first.

## Initialization and Status

Create or upgrade the configured database with:

```bash
.venv/bin/cost-average-out init-ledger --config config.yaml
```

Migrations are versioned in `schema_migrations` and are safe to run repeatedly.
The command creates parent directories as needed.

Inspect the ledger without modifying it:

```bash
.venv/bin/cost-average-out status --config config.yaml
```

Status reports cycle counts, the most recent cycle, planned-order count, and
unresolved exchange-order count. It fails explicitly if the ledger has not been
initialized.

## Transaction Boundaries

Cycle creation and planned-order insertion use one SQLite transaction. If any
planned order violates a constraint, including duplicate symbols in one cycle,
the cycle and all associated rows are rolled back.

Cycle state changes append an audit event in the same transaction. Supported
cycle states include `skipped`, `blocked`, `partial`, `completed`, `failed`, and
`unknown_requires_reconciliation`.
