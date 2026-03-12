# HashWatcher Gateway Desktop

<p align="center">
  <img src="app/gateway/assets/imagelogo copy.png" alt="HashWatcher Logo" width="152" />
</p>

Standalone desktop gateway for macOS and Windows that mirrors Umbrel gateway behavior:

- Miner polling and normalization
- Subnet miner discovery
- Local dashboard + JSON API on port `8787`
- Tailscale setup/status controls
- Miner HTTP proxy endpoint

This repo is separate from Umbrel/Pi code.

## Start Here

Use [INSTALL.md](INSTALL.md) for end-user setup only (no developer noise).

## End-User Install (Recommended)

For a seamless setup, users should install from GitHub Releases:

- Windows: `HashWatcherGatewayDesktop-Setup.exe` (installer)
- macOS: `HashWatcherGatewayDesktop.pkg` or `HashWatcherGatewayDesktop.dmg`

Easy GitHub step for non-technical users: click **Code** -> **Download ZIP**, extract it, then follow [INSTALL.md](INSTALL.md).
Release installers (`.dmg`, `.pkg`, `.exe`) are automatically attached to GitHub Releases on each `v*` tag.

## Command-Line Install From Git (Source Users)

Use these exact commands from the repo root.

### Windows (PowerShell, one command)

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\install_windows.ps1
```

Optional desktop shortcut:

```powershell
.\scripts\create_windows_shortcut.ps1
```

### macOS (Terminal, one command)

```bash
./scripts/install_macos.sh
```

`install_macos.sh` now builds and installs `HashWatcherGatewayDesktop.app` into `/Applications` (or `~/Applications` when needed).
It also generates a macOS `.icns` from `app/gateway/assets/icon.png` so the installed app icon matches your project icon.

Do not run macOS commands on Windows (`launchctl`, `.sh`, `source`), and do not run PowerShell commands on macOS.

## Required Gateway Name

This desktop gateway is configured to use:

`HashWatcherGatewayDesktop`

as the machine name (`PI_HOSTNAME`) by default in app code and service templates.

## Prerequisites (Developers)

1. Install the HashWatcher mobile/desktop app from [www.HashWatcher.app](https://www.HashWatcher.app).
2. Install Tailscale on your phone and sign in with the same user you will use for this gateway.
3. Follow updates on X: [@HashWatcher](https://x.com/HashWatcher).
4. Install Python 3.10+.
5. Download this repo (clone in terminal or download ZIP from GitHub and extract).

Tailscale requirement (simple):

- If you run this repo on Apple Silicon Mac, it is already included (`app/vendor/tailscale/darwin-arm64`). Nothing else to do.
- If you run this repo on 64-bit Windows (amd64), it is also included (`app/vendor/tailscale/windows-amd64`). Nothing else to do.
- If you run on a platform not bundled here (for example Windows ARM64), install Tailscale on that machine first so `tailscale`/`tailscaled` are available.
- If you are using a packaged desktop release build, this should already be bundled.

Important: keep the gateway app running continuously so polling, route status, and remote access stay active.

## Project Layout

```text
app/
  main.py
  gui.py
  gateway/
    hub_agent.py
    tailscale_setup.py
    network_utils.py
    assets/
install/
  macos/
  windows/
scripts/
requirements.txt
```

## Embedded Tailscale Runtime

For platforms not already bundled in this repo, you can prepare embedded binaries with:

```bash
./scripts/prepare_embedded_tailscale.sh
```

This copies `tailscale` and `tailscaled` into:

`app/vendor/tailscale/<platform>/`

Then launch the desktop app normally and it will auto-start embedded `tailscaled`.

Note: for end-user packaged app builds, include these binaries in the app bundle so users do not need standalone Tailscale install.

## Run Desktop App (Developer Mode)

### Desktop App

The desktop app includes a guided wizard with iPhone-style next steps:

- Start/stop gateway process
- Step-by-step `Back` / `Next` flow
- Auth key paste flow (same as Umbrel/Pi)
- Subnet entry
- Connect / turn on / turn off / disconnect actions
- Live route-approval status checks
- Direct links to Tailscale Keys and Machines pages
- Embedded `tailscaled` auto-start support when bundled
- Optional advanced Tailscale API key generation utility

### First Time Setup (Clone + Open Folder)

#### macOS/Linux (clone to Desktop)

```bash
cd ~/Desktop && git clone https://github.com/gpena208777/HashWatcherGateway_Desktop.git && cd HashWatcherGateway_Desktop
```

#### Windows (PowerShell, clone to Desktop)

```powershell
cd $HOME\Desktop; git clone https://github.com/gpena208777/HashWatcherGateway_Desktop.git; cd .\HashWatcherGateway_Desktop
```

If you downloaded a ZIP instead of cloning, extract it and run `cd` into that extracted `HashWatcherGateway_Desktop` folder first.

### Run Command (macOS/Linux)

```bash
python3 -m venv .venv && source .venv/bin/activate && python -m pip install -r requirements.txt && python app/gui.py
```

### Run Command (Windows PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; python -m pip install -r requirements.txt; python .\app\gui.py
```

## Install As Background Service

### macOS (launch agent)

1. Create and populate virtualenv:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```
2. Install launch agent (uses `PI_HOSTNAME=HashWatcherGatewayDesktop`):
   ```bash
   PYTHON_BIN="$(pwd)/.venv/bin/python" ./scripts/install_macos_launchagent.sh
   ```
3. Verify:
   ```bash
   launchctl list | grep com.hashwatcher.gateway.desktop
   curl -fsS http://127.0.0.1:8787/api/status | jq '.hostname'
   ```

Logs:

- `~/Library/Logs/hashwatcher-gateway/gateway.log`
- `~/Library/Logs/hashwatcher-gateway/gateway.err.log`

### Windows (WinSW service)

1. Create and populate virtualenv:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```
2. Download WinSW x64 and place it at:
   - `install\windows\winsw-x64.exe`
3. Open PowerShell as Administrator and install service:
   ```powershell
   .\scripts\install_windows_service.ps1 -PythonExe "$PWD\.venv\Scripts\python.exe"
   ```
4. Verify:
   ```powershell
   Get-Service HashWatcherGatewayDesktop
   (Invoke-RestMethod http://127.0.0.1:8787/api/status).hostname
   ```

The service template sets `PI_HOSTNAME=HashWatcherGatewayDesktop`.

Common Windows pitfalls from support logs:

- `py` not recognized: use `python` instead.
- `source .venv/bin/activate` on Windows is wrong; use `.\.venv\Scripts\Activate.ps1`.
- `requirements.txt` not found: you are in the wrong folder. `cd` into the repo first.
- `launchctl`/`jq` errors on Windows: those are macOS/Linux commands.
- `C:\Users\...\AppData\Local\Microsoft\WindowsApps\python.exe`: this is a Store alias, not a real Python runtime.

## Build Release App (Maintainers)

Build native app bundles with PyInstaller:

### macOS

```bash
./scripts/build_macos_release.sh
```

Output:

- `dist/HashWatcherGatewayDesktop.app`

### Windows (PowerShell)

```powershell
.\scripts\build_windows_release.ps1
```

Output:

- `dist\HashWatcherGatewayDesktop\HashWatcherGatewayDesktop.exe`

Note: sign and notarize release artifacts before distributing to users.

## Automated Release Assets (Maintainers)

On push of a tag like `v1.0.2`, GitHub Actions now builds and publishes:

- `HashWatcherGatewayDesktop.dmg`
- `HashWatcherGatewayDesktop.pkg`
- `HashWatcherGatewayDesktop-Setup.exe`

macOS release signing/notarization requires these GitHub repository secrets:

- `APPLE_CODESIGN_P12_BASE64` (base64 of `.p12` containing Developer ID Application + Developer ID Installer certs)
- `APPLE_CODESIGN_P12_PASSWORD`
- `MACOS_APP_SIGN_IDENTITY` (example: `Developer ID Application: Your Name (TEAMID)`)
- `MACOS_INSTALLER_SIGN_IDENTITY` (example: `Developer ID Installer: Your Name (TEAMID)`)
- `APPLE_NOTARY_APPLE_ID`
- `APPLE_NOTARY_TEAM_ID`
- `APPLE_NOTARY_APP_PASSWORD` (app-specific password for notarization)

Tag/push example:

```bash
git tag v1.0.2
git push origin v1.0.2
```

## Tailscale Setup Flow (In-App Wizard, Umbrel/Pi style)

1. Open the desktop app (`python app/gui.py`) and click **Start Gateway**.
2. Open **Guided Onboarding** tab.
3. Use the wizard action button and `Next` through:
   - Start Gateway
   - Open Keys Page and paste auth key
   - Connect with auth key
   - Open Machines page for route approval
   - Refresh and verify completion
4. Completion criteria in status panel:
   - Tailscale is online/authenticated
   - Route approval is approved

## Runtime Environment Variables

- `PI_HOSTNAME` default: `HashWatcherGatewayDesktop`
- `AGENT_ID` default: `hashwatcher-gateway-desktop`
- `STATUS_HTTP_BIND` default: `0.0.0.0`
- `STATUS_HTTP_PORT` default: `8787`
- `RUNTIME_CONFIG_PATH` default: `~/.hashwatcher-gateway/runtime_config.json`
- `POLL_SECONDS` default: `10`
- `HTTP_TIMEOUT_SECONDS` default: `5`
- `TAILSCALE_BIN` optional absolute path to `tailscale`
- `TAILSCALED_BIN` optional absolute path to `tailscaled`
- `TS_EMBEDDED` default: `1` (auto-start embedded daemon when available)
- `HOST_IP` optional manual LAN IP override for subnet detection
- `TS_ACCEPT_ROUTES` default: `false`
- `DEFAULT_LAN_PREFIX` default: `24`
