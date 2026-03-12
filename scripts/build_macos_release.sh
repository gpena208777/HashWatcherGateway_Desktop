#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SPEC_PATH="${REPO_ROOT}/packaging/pyinstaller/hashwatcher_gateway.spec"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
PNG_ICON="${REPO_ROOT}/app/gateway/assets/icon.png"
ICONSET_DIR="${REPO_ROOT}/packaging/macos/HashWatcherGatewayDesktop.iconset"
ICNS_ICON="${REPO_ROOT}/packaging/macos/HashWatcherGatewayDesktop.icns"

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

if command -v iconutil >/dev/null 2>&1 && command -v sips >/dev/null 2>&1 && [[ -f "${PNG_ICON}" ]]; then
  mkdir -p "${REPO_ROOT}/packaging/macos"
  rm -rf "${ICONSET_DIR}"
  mkdir -p "${ICONSET_DIR}"

  sips -z 16 16 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_16x16.png" >/dev/null
  sips -z 32 32 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_32x32.png" >/dev/null
  sips -z 64 64 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_128x128.png" >/dev/null
  sips -z 256 256 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_256x256.png" >/dev/null
  sips -z 512 512 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "${PNG_ICON}" --out "${ICONSET_DIR}/icon_512x512.png" >/dev/null
  cp "${PNG_ICON}" "${ICONSET_DIR}/icon_512x512@2x.png"

  iconutil -c icns "${ICONSET_DIR}" -o "${ICNS_ICON}"
fi

"${PYTHON_BIN}" -m PyInstaller --noconfirm --clean "${SPEC_PATH}"

echo "Build complete:"
echo "  ${REPO_ROOT}/dist/HashWatcherGatewayDesktop.app"
echo ""
echo "Next step (recommended): sign and notarize before sharing to users."
