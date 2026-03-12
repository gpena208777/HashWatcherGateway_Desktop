#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_PATH="${REPO_ROOT}/dist/HashWatcherGatewayDesktop.app"
RELEASE_DIR="${REPO_ROOT}/release"
DMG_PATH="${RELEASE_DIR}/HashWatcherGatewayDesktop.dmg"
PKG_PATH="${RELEASE_DIR}/HashWatcherGatewayDesktop.pkg"
STAGE_DIR="${RELEASE_DIR}/dmg-staging"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Missing app bundle: ${APP_PATH}" >&2
  echo "Run ./scripts/build_macos_release.sh first." >&2
  exit 1
fi

rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}" "${RELEASE_DIR}"
cp -R "${APP_PATH}" "${STAGE_DIR}/HashWatcherGatewayDesktop.app"

hdiutil create \
  -volname "HashWatcherGatewayDesktop" \
  -srcfolder "${STAGE_DIR}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}"

pkgbuild \
  --install-location "/Applications" \
  --component "${APP_PATH}" \
  "${PKG_PATH}"

echo "Created macOS installers:"
echo "  ${DMG_PATH}"
echo "  ${PKG_PATH}"
