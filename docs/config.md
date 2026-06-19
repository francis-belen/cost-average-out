# Config

The app uses a versioned YAML config plus environment variables for secrets.

Start from `config.example.yaml` and keep the real `config.yaml` out of git.

Key sections:

- `exchange`: exchange adapter name.
- `quote_currency`: target cash currency, such as EUR.
- `cost_average_out`: schedule and sell percentage.
- `symbols`: allowlist of pairs the app may sell.
- `safety`: live flag, kill switch, spread, and value limits.
- `notifications`: notification provider settings.

Live orders must remain blocked while `safety.live_trading_enabled` is false or
`safety.kill_switch` is true.
