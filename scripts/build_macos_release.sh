#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SPEC_PATH="${REPO_ROOT}/packaging/pyinstaller/hashwatcher_gateway.spec"

cd "${REPO_ROOT}"

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --clean "${SPEC_PATH}"

echo "Build complete:"
echo "  ${REPO_ROOT}/dist/HashWatcherGatewayDesktop.app"
echo ""
echo "Next step (recommended): sign and notarize before sharing to users."
