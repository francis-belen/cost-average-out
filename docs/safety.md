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
