#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
DIST_APP="${REPO_ROOT}/dist/HashWatcherGatewayDesktop.app"
SYSTEM_APPS_DIR="/Applications"
USER_APPS_DIR="${HOME}/Applications"
APP_NAME="HashWatcherGatewayDesktop.app"

cd "${REPO_ROOT}"

if [[ ! -f "${REPO_ROOT}/requirements.txt" ]]; then
  echo "requirements.txt not found. Run this script from the HashWatcherGateway_Desktop repo." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3, then rerun this script." >&2
  exit 1
fi

if [[ ! -x "${VENV_PY}" ]]; then
  echo "Creating virtual environment..."
  python3 -m venv "${REPO_ROOT}/.venv"
fi

echo "Installing dependencies..."
"${VENV_PY}" -m pip install --upgrade pip
"${VENV_PY}" -m pip install -r "${REPO_ROOT}/requirements.txt"

echo "Installing launch agent..."
PYTHON_BIN="${VENV_PY}" "${REPO_ROOT}/scripts/install_macos_launchagent.sh"

echo "Building macOS app bundle..."
"${REPO_ROOT}/scripts/build_macos_release.sh"

if [[ ! -d "${DIST_APP}" ]]; then
  echo "Expected app bundle not found: ${DIST_APP}" >&2
  exit 1
fi

install_dir="${SYSTEM_APPS_DIR}"
if [[ ! -w "${SYSTEM_APPS_DIR}" ]]; then
  install_dir="${USER_APPS_DIR}"
fi

mkdir -p "${install_dir}"
target_app="${install_dir}/${APP_NAME}"
rm -rf "${target_app}"
cp -R "${DIST_APP}" "${target_app}"

echo ""
echo "Install complete."
echo "App installed to:"
echo "  ${target_app}"
echo "Open app:"
echo "  open \"${target_app}\""
echo "API check:"
echo "  curl -fsS http://127.0.0.1:8787/api/status"
