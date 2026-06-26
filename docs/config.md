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

## Schedule Semantics

Supported intervals are `daily`, `weekly`, and `monthly`. The `start_date` is
the recurrence anchor in the configured IANA timezone. A cycle is scheduled at
local midnight and remains `due` for that local calendar date. After that date,
it is `missed` until the next scheduled cycle.

Monthly schedules retain the start day where possible and use the final day of
shorter months. For example, a schedule starting January 31 runs on February 28
in a non-leap year and returns to March 31.

The only supported missed-cycle policy is `require_manual_approval`. The app
must not automatically catch up a missed sell.

Inspect the schedule without contacting an exchange:

```bash
.venv/bin/cost-average-out schedule-status --config config.yaml
```

For deterministic inspection, provide an offset-aware ISO-8601 instant:

```bash
.venv/bin/cost-average-out schedule-status --config config.yaml \
  --at 2030-01-01T12:00:00+01:00
```

## Notifications

Supported providers are:

- `none`: do not send notifications.
- `webhook`: POST JSON notifications to the URL in
  `COST_AVERAGE_OUT_NOTIFICATION_WEBHOOK_URL`.

Webhook payloads include `kind`, `title`, and `body`. Kinds distinguish
`success`, `skip`, `block`, and `failure`.

Live orders must remain blocked while `safety.live_trading_enabled` is false or
`safety.kill_switch` is true.
