# Exchange Adapters

## Scope

The first concrete adapter is Kraken through CCXT. Phase 3 is strictly
read-only and exposes:

- fetch balances,
- fetch market metadata,
- fetch ticker bid/ask quotes,
- fetch open orders,
- fetch recent closed orders and fills,
- normalize exchange order states.

The adapter interface is dependency-injected so unit and integration tests can
use a fake exchange without network access or credentials.

## Credentials

Set credentials outside the YAML config:

```bash
export COST_AVERAGE_OUT_EXCHANGE_API_KEY=...
export COST_AVERAGE_OUT_EXCHANGE_API_SECRET=...
```

The API key requires balance, order, and trade-history read permissions. Do not
grant withdrawal permission. Order-creation permission is not needed for this
read-only reconciliation and planning phases.

## Initial Balance Snapshot

After initializing the ledger and configuring read-only credentials, store the
baseline balance snapshot:

```bash
cost-average-out snapshot-balances --config config.yaml
```

The command validates configured markets first, then records balances with source
`initial_snapshot`. This snapshot can be used by `percentage_basis:
initial_snapshot` planning. It does not submit, cancel, or modify exchange
orders.

## Reconciliation

Initialize the ledger, then reconcile:

```bash
cost-average-out init-ledger --config config.yaml
cost-average-out reconcile --config config.yaml --lookback-days 7
```

Reconciliation validates every allowlisted symbol before private account reads,
stores available and total balances, updates known app-created orders, and
records fills idempotently. It never creates, cancels, or modifies an exchange
order.

An open, partial, unknown, or locally submitting app-created order blocks later
execution. Orders not created by this app are visible but do not trigger this
specific block. An app-created order found remotely without a matching local
record also blocks execution and requires investigation.

## Safety Requirements

- No withdrawal permission is required.
- The adapter must normalize exchange order statuses.
- Timeouts must produce an unknown state that requires reconciliation.
- Real exchange integration tests must be opt-in.
