#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_PATH="${REPO_ROOT}/dist/HashWatcherGatewayDesktop.app"
RELEASE_DIR="${REPO_ROOT}/release"
DMG_PATH="${RELEASE_DIR}/HashWatcherGatewayDesktop.dmg"
PKG_PATH="${RELEASE_DIR}/HashWatcherGatewayDesktop.pkg"
STAGE_DIR="${RELEASE_DIR}/dmg-staging"
MACOS_APP_SIGN_IDENTITY="${MACOS_APP_SIGN_IDENTITY:-}"
MACOS_INSTALLER_SIGN_IDENTITY="${MACOS_INSTALLER_SIGN_IDENTITY:-}"
APPLE_NOTARY_APPLE_ID="${APPLE_NOTARY_APPLE_ID:-}"
APPLE_NOTARY_TEAM_ID="${APPLE_NOTARY_TEAM_ID:-}"
APPLE_NOTARY_APP_PASSWORD="${APPLE_NOTARY_APP_PASSWORD:-}"

if [[ "${CI:-}" == "true" ]]; then
  if [[ -z "${MACOS_APP_SIGN_IDENTITY}" || -z "${MACOS_INSTALLER_SIGN_IDENTITY}" ]]; then
    echo "CI requires both MACOS_APP_SIGN_IDENTITY and MACOS_INSTALLER_SIGN_IDENTITY." >&2
    exit 1
  fi
  if [[ -z "${APPLE_NOTARY_APPLE_ID}" || -z "${APPLE_NOTARY_TEAM_ID}" || -z "${APPLE_NOTARY_APP_PASSWORD}" ]]; then
    echo "CI requires APPLE_NOTARY_APPLE_ID, APPLE_NOTARY_TEAM_ID, and APPLE_NOTARY_APP_PASSWORD." >&2
    exit 1
  fi
fi

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Missing app bundle: ${APP_PATH}" >&2
  echo "Run ./scripts/build_macos_release.sh first." >&2
  exit 1
fi

if [[ -n "${MACOS_APP_SIGN_IDENTITY}" ]]; then
  echo "Signing app bundle with identity: ${MACOS_APP_SIGN_IDENTITY}"
  /usr/bin/codesign \
    --force \
    --deep \
    --timestamp \
    --options runtime \
    --sign "${MACOS_APP_SIGN_IDENTITY}" \
    "${APP_PATH}"

  /usr/bin/codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
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

pkg_cmd=(
  pkgbuild
  --install-location "/Applications"
  --component "${APP_PATH}"
)
if [[ -n "${MACOS_INSTALLER_SIGN_IDENTITY}" ]]; then
  pkg_cmd+=(--sign "${MACOS_INSTALLER_SIGN_IDENTITY}")
fi
pkg_cmd+=("${PKG_PATH}")
"${pkg_cmd[@]}"

if [[ -n "${APPLE_NOTARY_APPLE_ID}" || -n "${APPLE_NOTARY_TEAM_ID}" || -n "${APPLE_NOTARY_APP_PASSWORD}" ]]; then
  if [[ -z "${APPLE_NOTARY_APPLE_ID}" || -z "${APPLE_NOTARY_TEAM_ID}" || -z "${APPLE_NOTARY_APP_PASSWORD}" ]]; then
    echo "Notarization variables are partially configured. Set all of:" >&2
    echo "  APPLE_NOTARY_APPLE_ID, APPLE_NOTARY_TEAM_ID, APPLE_NOTARY_APP_PASSWORD" >&2
    exit 1
  fi
  echo "Submitting pkg for notarization..."
  xcrun notarytool submit "${PKG_PATH}" \
    --apple-id "${APPLE_NOTARY_APPLE_ID}" \
    --team-id "${APPLE_NOTARY_TEAM_ID}" \
    --password "${APPLE_NOTARY_APP_PASSWORD}" \
    --wait

  echo "Stapling notarization ticket..."
  xcrun stapler staple "${PKG_PATH}"
  xcrun stapler validate "${PKG_PATH}"
fi

echo "Created macOS installers:"
echo "  ${DMG_PATH}"
echo "  ${PKG_PATH}"
