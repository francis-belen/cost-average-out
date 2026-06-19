# Security Policy

This project controls exchange API access and can submit real orders when live
trading is enabled. Treat configuration, logs, and runtime data as sensitive.

## Supported Versions

Security review applies to the latest version on the default branch only until a
formal release policy exists.

## Reporting a Vulnerability

This repository is maintained as a personal open-source project. If GitHub
private vulnerability reporting is enabled, use that channel. Otherwise, contact
the maintainer through the contact method listed in the public profile or README.

Do not open public issues that include API keys, account identifiers, balances,
order IDs, logs with secrets, or private operational details.

## API Key Guidance

Use the minimum exchange API permissions required. Do not enable withdrawal
permissions for this app. Store API keys outside git, preferably in environment
variables, an ignored local env file, or a host secret manager.
