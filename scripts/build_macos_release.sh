#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SPEC_PATH="${REPO_ROOT}/packaging/pyinstaller/hashwatcher_gateway.spec"
VENV_PY="${REPO_ROOT}/.venv/bin/python"

cd "${REPO_ROOT}"

if [[ -x "${VENV_PY}" ]]; then
  PYTHON_BIN="${VENV_PY}"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "python3 not found. Install Python 3, then rerun." >&2
  exit 1
fi

"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install -r requirements.txt pyinstaller
"${PYTHON_BIN}" -m PyInstaller --noconfirm --clean "${SPEC_PATH}"

echo "Build complete:"
echo "  ${REPO_ROOT}/dist/HashWatcherGatewayDesktop.app"
echo ""
echo "Next step (recommended): sign and notarize before sharing to users."
