#!/usr/bin/env bash
set -euo pipefail

APP_USER="costavgout"
APP_GROUP="costavgout"
APP_DIR="/opt/cost-average-out"
CONFIG_DIR="/etc/cost-average-out"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
ENV_FILE="${CONFIG_DIR}/cost-average-out.env"
DATA_DIR="/var/lib/cost-average-out"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

if ! getent group "${APP_GROUP}" >/dev/null; then
  groupadd --system "${APP_GROUP}"
fi

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${APP_GROUP}" --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0755 "${APP_DIR}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${CONFIG_DIR}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0700 "${DATA_DIR}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  install -o "${APP_USER}" -g "${APP_GROUP}" -m 0640 "${REPO_DIR}/config.example.yaml" "${CONFIG_FILE}"
  sed -i 's#^database_path: .*#database_path: /var/lib/cost-average-out/cost_average_out.sqlite3#' "${CONFIG_FILE}"
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  install -o "${APP_USER}" -g "${APP_GROUP}" -m 0400 /dev/null "${ENV_FILE}"
fi

chown "${APP_USER}:${APP_GROUP}" "${CONFIG_DIR}" "${CONFIG_FILE}" "${ENV_FILE}" "${DATA_DIR}"
chmod 0750 "${CONFIG_DIR}"
chmod 0640 "${CONFIG_FILE}"
chmod 0400 "${ENV_FILE}"
chmod 0700 "${DATA_DIR}"

install -o root -g root -m 0644 "${SCRIPT_DIR}/systemd/cost-average-out.service" "${SYSTEMD_DIR}/cost-average-out.service"
install -o root -g root -m 0644 "${SCRIPT_DIR}/systemd/cost-average-out.timer" "${SYSTEMD_DIR}/cost-average-out.timer"

systemctl daemon-reload

echo "Production deployment files installed in dry-run mode."
echo "Runtime config: ${CONFIG_FILE}"
echo "Secrets file: ${ENV_FILE}"
echo "Data directory: ${DATA_DIR}"
echo "Do not switch the service to --live until validation and manual approval are complete."
