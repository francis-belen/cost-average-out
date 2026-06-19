# Exchange Adapters

## Scope

Start with one exchange adapter and keep the interface small:

- fetch balances,
- fetch market metadata,
- fetch open orders,
- fetch recent closed orders and fills,
- create spot market or limit orders,
- cancel orders if the execution policy requires it.

## Safety Requirements

- No withdrawal permission is required.
- The adapter must normalize exchange order statuses.
- Timeouts must produce an unknown state that requires reconciliation.
- Real exchange integration tests must be opt-in.

## Suggested Interface

```text
fetch_balances()
fetch_markets(symbols)
fetch_open_orders(symbols)
fetch_recent_orders(symbols, since)
create_order(symbol, side, amount, order_type, price=None)
fetch_order(order_id, symbol)
```
