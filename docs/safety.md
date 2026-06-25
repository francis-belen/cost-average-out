# Safety

Cost Average Out is conservative by default because automated selling is hard to
reverse.

Required controls:

- dry-run first,
- explicit `live_trading_enabled`,
- kill switch,
- first-live-sell confirmation,
- symbol allowlist,
- available-balance-only sizing,
- max sell value per cycle,
- minimum remaining value per symbol,
- min order notional checks,
- spread/liquidity guard,
- unknown order state blocks execution.

The app must reconcile before live orders. A timeout or crash after order
submission must produce an unknown state that blocks retries until exchange state
is checked.


## Sell Planning

Planning computes sells only for configured allowlist symbols. Sizing is based on
available balance, not locked or total balance. For each symbol, the planner
validates exchange minimum amount/notional, maximum sell value per cycle, minimum
remaining value, and bid/ask spread before an item can be marked `planned`.
Unsafe items are marked `skipped` or `blocked` with an explicit reason.


## Dry Run

Dry-run execution never submits exchange orders. It only fetches read-only
exchange state and, when explicitly requested with `--persist-simulation`, writes
a local simulation cycle and planned orders to SQLite.


## Live Execution

Live execution requires `run-once --live` plus `live_trading_enabled: true`. The
kill switch blocks all live orders regardless of CLI flags. If first-live-sell
confirmation is enabled, the first live run must include
`--confirm-first-live-sell`.

The app records exchange order IDs immediately after submission. If submission
times out, it records `unknown_requires_reconciliation` and does not retry the
order automatically. Existing partial, open, submitting, or unknown app-created
orders block further live execution until reconciliation resolves them.
