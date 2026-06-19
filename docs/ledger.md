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
- `price_candles`
- `notifications`

Important constraints:

- `cycles.cycle_id` is unique.
- Planned orders are unique per cycle and symbol.
- Exchange order IDs are unique when known.
- Candle rows are unique per exchange, symbol, timeframe, and timestamp.

Never delete ledger rows to recover from an unknown order state. Reconcile with
the exchange first.
