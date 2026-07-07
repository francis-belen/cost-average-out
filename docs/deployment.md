# Deployment

Cost Average Out supports three operating contexts:

- Development runs from a working checkout and may use `.env`, `config.yaml`,
  and `./data/` inside the project directory.
- Staging uses production-like paths and permissions, but stays in dry-run mode
  while config, credentials, schedule, logs, and ledger behavior are validated.
- Production uses the same hardened layout as staging. The default systemd
  service remains dry-run until switching to live mode is an intentional manual
  deployment step.

The deployment layout is:

```text
/opt/cost-average-out/                       application checkout
/etc/cost-average-out/config.yaml            runtime config
/etc/cost-average-out/cost-average-out.env   secrets
/var/lib/cost-average-out/                   SQLite data
```

## Local `.env`

`.env` in the project directory is only for local development. Staging and
production secrets must live outside the checkout in
`/etc/cost-average-out/cost-average-out.env`.

Keeping secrets out of `/opt/cost-average-out` prevents accidental commits,
keeps deployments repeatable, and lets systemd read secrets through
`EnvironmentFile=` without mixing runtime credentials into source files.

Never commit real API keys, webhook URLs, or copied production config files.

## Install Staging

Clone or update the application checkout at `/opt/cost-average-out`, create the
virtual environment, and install the package there. Then run:

```bash
cd /opt/cost-average-out
sudo deploy/install-staging.sh
```

The installer creates the `costavgout` Linux user, creates required directories,
installs systemd units, and preserves existing runtime config and secrets. If
`/etc/cost-average-out/config.yaml` does not exist, it copies
`config.example.yaml` and sets `database_path` to
`/var/lib/cost-average-out/cost_average_out.sqlite3`. If the secrets file does
not exist, it creates an empty one.

## Install Production

Production uses the same filesystem layout and service template. The installed
service intentionally remains in dry-run mode.

```bash
cd /opt/cost-average-out
sudo deploy/install-production.sh
```

Review `/etc/cost-average-out/config.yaml`, enter secrets, run the validation
commands, inspect logs, and test the timer before considering live mode.

## Edit Secrets Safely

Edit secrets as root without printing them to the terminal:

```bash
sudoedit /etc/cost-average-out/cost-average-out.env
```

Expected variables include:

```bash
COST_AVERAGE_OUT_EXCHANGE_API_KEY=
COST_AVERAGE_OUT_EXCHANGE_API_SECRET=
COST_AVERAGE_OUT_NOTIFICATION_WEBHOOK_URL=
```

Do not use `cat`, shell history, command-line arguments, or chat logs for real
secret values. After editing, restore strict permissions:

```bash
sudo chown costavgout:costavgout /etc/cost-average-out/cost-average-out.env
sudo chmod 400 /etc/cost-average-out/cost-average-out.env
```

## Verify Permissions

Check ownership and modes:

```bash
sudo stat -c '%U:%G %a %n' \
  /etc/cost-average-out \
  /etc/cost-average-out/config.yaml \
  /etc/cost-average-out/cost-average-out.env \
  /var/lib/cost-average-out
```

Expected output:

```text
costavgout:costavgout 750 /etc/cost-average-out
costavgout:costavgout 640 /etc/cost-average-out/config.yaml
costavgout:costavgout 400 /etc/cost-average-out/cost-average-out.env
costavgout:costavgout 700 /var/lib/cost-average-out
```

## Validate Deployment

Run validation as the deployment user from the application checkout:

```bash
cd /opt/cost-average-out
sudo -u costavgout bash -lc 'set -a; . /etc/cost-average-out/cost-average-out.env; set +a; .venv/bin/cost-average-out validate-config --config /etc/cost-average-out/config.yaml'
sudo -u costavgout bash -lc 'set -a; . /etc/cost-average-out/cost-average-out.env; set +a; .venv/bin/cost-average-out init-ledger --config /etc/cost-average-out/config.yaml'
sudo -u costavgout bash -lc 'set -a; . /etc/cost-average-out/cost-average-out.env; set +a; .venv/bin/cost-average-out status --config /etc/cost-average-out/config.yaml'
sudo -u costavgout bash -lc 'set -a; . /etc/cost-average-out/cost-average-out.env; set +a; .venv/bin/cost-average-out reconcile --config /etc/cost-average-out/config.yaml'
sudo -u costavgout bash -lc 'set -a; . /etc/cost-average-out/cost-average-out.env; set +a; .venv/bin/cost-average-out snapshot-balances --config /etc/cost-average-out/config.yaml'
sudo -u costavgout bash -lc 'set -a; . /etc/cost-average-out/cost-average-out.env; set +a; .venv/bin/cost-average-out plan --config /etc/cost-average-out/config.yaml'
sudo -u costavgout bash -lc 'set -a; . /etc/cost-average-out/cost-average-out.env; set +a; .venv/bin/cost-average-out run-once --dry-run --config /etc/cost-average-out/config.yaml'
```

These commands load the protected env file without printing values. On a real
host, systemd will load the same file through `EnvironmentFile=`.

The core commands are:

```bash
cost-average-out validate-config --config /etc/cost-average-out/config.yaml
cost-average-out init-ledger --config /etc/cost-average-out/config.yaml
cost-average-out status --config /etc/cost-average-out/config.yaml
cost-average-out reconcile --config /etc/cost-average-out/config.yaml
cost-average-out snapshot-balances --config /etc/cost-average-out/config.yaml
cost-average-out plan --config /etc/cost-average-out/config.yaml
cost-average-out run-once --dry-run --config /etc/cost-average-out/config.yaml
```

## Enable And Inspect Timer

Installers copy the unit files and run `systemctl daemon-reload`. Enable the
timer only after config and secrets are ready:

```bash
sudo systemctl enable --now cost-average-out.timer
systemctl list-timers cost-average-out.timer
systemctl status cost-average-out.timer
```

Inspect service logs:

```bash
journalctl -u cost-average-out.service -n 100 --no-pager
journalctl -u cost-average-out.service -f
```

Trigger one dry-run service execution manually:

```bash
sudo systemctl start cost-average-out.service
systemctl status cost-average-out.service
```

## Switch To Live Mode

The checked-in service file uses:

```text
ExecStart=/opt/cost-average-out/.venv/bin/cost-average-out run-once --dry-run --config /etc/cost-average-out/config.yaml
```

For production, keep this default until dry-run output, ledger state, exchange
state, timer behavior, and logs have all been reviewed.

To switch intentionally:

```bash
sudo systemctl edit --full cost-average-out.service
```

Replace `--dry-run` with `--live` in `ExecStart=`, then reload and run one
manual service execution while watching logs:

```bash
sudo systemctl daemon-reload
sudo systemctl start cost-average-out.service
journalctl -u cost-average-out.service -n 100 --no-pager
```

Leave `safety.live_trading_enabled: false` in the config until the live switch is
intentional. The application safety controls and the systemd command must both
allow live trading before orders can be submitted.
