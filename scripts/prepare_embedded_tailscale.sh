#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENDOR_ROOT="${REPO_ROOT}/app/vendor/tailscale"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m | tr '[:upper:]' '[:lower:]')"
case "${arch}" in
  x86_64|amd64) arch="amd64" ;;
  arm64|aarch64) arch="arm64" ;;
esac
platform_dir="${VENDOR_ROOT}/${os}-${arch}"

mkdir -p "${platform_dir}"

find_mac_bin() {
  local name="$1"
  if command -v "${name}" >/dev/null 2>&1; then
    command -v "${name}"
    return 0
  fi
  if [[ "${os}" == "darwin" ]]; then
    local base="/Applications/Tailscale.app/Contents/MacOS"
    if [[ "${name}" == "tailscale" && -x "${base}/Tailscale" ]]; then
      echo "${base}/Tailscale"
      return 0
    fi
    if [[ "${name}" == "tailscaled" && -x "${base}/tailscaled" ]]; then
      echo "${base}/tailscaled"
      return 0
    fi
  fi
  return 1
}

copy_bin() {
  local src="$1"
  local dst="$2"
  cp "${src}" "${dst}"
  chmod +x "${dst}"
}

if [[ "${os}" == "windows_nt" ]]; then
  echo "Run this script from a POSIX shell environment or copy binaries manually." >&2
  exit 1
fi

cli_src="$(find_mac_bin tailscale || true)"
daemon_src="$(find_mac_bin tailscaled || true)"

if [[ -z "${cli_src}" || -z "${daemon_src}" ]]; then
  echo "Could not locate tailscale and tailscaled binaries." >&2
  echo "Install Tailscale locally first or pass custom paths:" >&2
  echo "  TAILSCALE_BIN=/path/to/tailscale TAILSCALED_BIN=/path/to/tailscaled ${0}" >&2
  exit 1
fi

cli_src="${TAILSCALE_BIN:-${cli_src}}"
daemon_src="${TAILSCALED_BIN:-${daemon_src}}"

copy_bin "${cli_src}" "${platform_dir}/tailscale"
copy_bin "${daemon_src}" "${platform_dir}/tailscaled"

echo "Embedded binaries prepared:"
echo "  ${platform_dir}/tailscale"
echo "  ${platform_dir}/tailscaled"

