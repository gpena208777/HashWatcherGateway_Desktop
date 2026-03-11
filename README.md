# HashWatcher Gateway Desktop

<p align="center">
  <img src="app/gateway/assets/icon.png" alt="HashWatcher Logo" width="152" />
</p>

Standalone desktop gateway for macOS and Windows that mirrors Umbrel gateway behavior:

- Miner polling and normalization
- Subnet miner discovery
- Local dashboard + JSON API on port `8787`
- Tailscale setup/status controls
- Miner HTTP proxy endpoint

This repo is separate from Umbrel/Pi code.

## Required Gateway Name

This desktop gateway is configured to use:

`HashWatcherGatewayDesktop`

as the machine name (`PI_HOSTNAME`) by default in app code and service templates.

## Prerequisites

1. Install the HashWatcher mobile/desktop app from [www.HashWatcher.app](https://www.HashWatcher.app).
2. Follow updates on X: [@HashWatcher](https://x.com/HashWatcher).
3. Install Python 3.10+ (with `pip` + `venv` support).
4. Clone this repo.

For source/developer runs, you need Tailscale binaries available either:

- system-installed (`tailscale` + `tailscaled`), or
- vendored into `app/vendor/tailscale/<platform>/` (embedded runtime mode).

Important: keep the gateway app running continuously so polling, route status, and remote access stay active.

### Dependency Fail-Safes

If your machine is missing Python or `pip`, use these checks first.

#### macOS

```bash
# Check Python
python3 --version

# If python3 is missing, install it (Homebrew example)
brew install python@3.11

# Ensure pip exists
python3 -m ensurepip --upgrade
python3 -m pip install --upgrade pip
```

#### Windows (PowerShell)

```powershell
# Check Python launcher
py -3 --version

# If missing, install Python with winget
winget install -e --id Python.Python.3.11

# Ensure pip exists
py -3 -m ensurepip --upgrade
py -3 -m pip install --upgrade pip
```

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

To run like Umbrel/Pi (self-managed local daemon), prepare embedded binaries:

```bash
./scripts/prepare_embedded_tailscale.sh
```

This copies `tailscale` and `tailscaled` into:

`app/vendor/tailscale/<platform>/`

Then launch GUI normally and it will auto-start embedded `tailscaled`.

Note: for end-user packaged app builds, include these binaries in the app bundle so users do not need standalone Tailscale install.

## Local Run (Any Platform)

### GUI App (Recommended)

The GUI now includes a guided wizard with iPhone-style next steps:

- Start/stop gateway process
- Step-by-step `Back` / `Next` flow
- Auth key paste flow (same as Umbrel/Pi)
- Subnet entry
- Connect / turn on / turn off / disconnect actions
- Live route-approval status checks
- Direct links to Tailscale Keys and Machines pages
- Embedded `tailscaled` auto-start support when bundled
- Optional advanced Tailscale API key generation utility

### macOS/Linux (with fail-safes)

```bash
python3 --version
python3 -m ensurepip --upgrade || true
python3 -m pip install --upgrade pip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app/gui.py
```

### Windows (PowerShell, with fail-safes)

```powershell
py -3 --version
py -3 -m ensurepip --upgrade
py -3 -m pip install --upgrade pip
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\app\gui.py
```

If `Activate.ps1` is blocked, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Open:

- Dashboard: `http://localhost:8787`
- API status: `http://localhost:8787/api/status`

### Headless Run (Optional)

If you want to run without the GUI:

```bash
export PI_HOSTNAME=HashWatcherGatewayDesktop
python app/main.py
```

## Install As Background Service

### macOS (launch agent)

1. Create and populate virtualenv:
   ```bash
   python3 -m ensurepip --upgrade || true
   python3 -m pip install --upgrade pip
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
   py -3 -m ensurepip --upgrade
   py -3 -m pip install --upgrade pip
   py -3 -m venv .venv
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
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

## Tailscale Setup Flow (In-App Wizard, Umbrel/Pi style)

1. Open the GUI (`python app/gui.py`) and click **Start Gateway**.
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
- `BITAXE_HOST` default: empty
- `BITAXE_SCHEME` default: `http`
- `BITAXE_ENDPOINTS` default: `/system/info,/api/system/info`
- `POLL_SECONDS` default: `10`
- `HTTP_TIMEOUT_SECONDS` default: `5`
- `TAILSCALE_BIN` optional absolute path to `tailscale`
- `TAILSCALED_BIN` optional absolute path to `tailscaled`
- `TS_EMBEDDED` default: `1` (auto-start embedded daemon when available)
- `HOST_IP` optional manual LAN IP override for subnet detection
- `TS_ACCEPT_ROUTES` default: `false`
- `DEFAULT_LAN_PREFIX` default: `24`
