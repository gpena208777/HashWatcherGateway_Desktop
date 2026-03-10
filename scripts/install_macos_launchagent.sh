#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_MAIN="${REPO_ROOT}/app/main.py"
APP_WORKDIR="${REPO_ROOT}/app"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

PLIST_TEMPLATE="${REPO_ROOT}/install/macos/com.hashwatcher.gateway.desktop.plist"
PLIST_DEST="${HOME}/Library/LaunchAgents/com.hashwatcher.gateway.desktop.plist"
LOG_DIR="${HOME}/Library/Logs/hashwatcher-gateway"
STDOUT_LOG="${LOG_DIR}/gateway.log"
STDERR_LOG="${LOG_DIR}/gateway.err.log"

mkdir -p "${HOME}/Library/LaunchAgents" "${LOG_DIR}"

if [[ ! -f "${PLIST_TEMPLATE}" ]]; then
  echo "Missing plist template: ${PLIST_TEMPLATE}" >&2
  exit 1
fi

if [[ ! -f "${APP_MAIN}" ]]; then
  echo "Missing app entrypoint: ${APP_MAIN}" >&2
  exit 1
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 not found. Install Python 3 first." >&2
  exit 1
fi

sed \
  -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
  -e "s|__APP_MAIN__|${APP_MAIN}|g" \
  -e "s|__APP_WORKDIR__|${APP_WORKDIR}|g" \
  -e "s|__STDOUT_LOG__|${STDOUT_LOG}|g" \
  -e "s|__STDERR_LOG__|${STDERR_LOG}|g" \
  "${PLIST_TEMPLATE}" > "${PLIST_DEST}"

launchctl unload "${PLIST_DEST}" >/dev/null 2>&1 || true
launchctl load "${PLIST_DEST}"

echo "Installed and started launch agent:"
echo "  ${PLIST_DEST}"
echo "Check status:"
echo "  launchctl list | grep com.hashwatcher.gateway.desktop"
echo "Logs:"
echo "  tail -f ${STDOUT_LOG}"

