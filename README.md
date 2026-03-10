# HashWatcher Gateway Desktop

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

1. Install Python 3.10+ (with Tkinter support).
2. Install Tailscale from [tailscale.com/download](https://tailscale.com/download).
3. Sign in to Tailscale at least once on the machine.
4. Clone this repo.

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

## Local Run (Any Platform)

### GUI App (Recommended)

The GUI starts/stops the gateway process, shows live logs, and opens the dashboard.

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app/gui.py
```

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python .\app\gui.py
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
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
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
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
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

## Tailscale Setup Flow

1. Open `http://localhost:8787`.
2. Enter Tailscale auth key.
3. Connect Tailscale.
4. In Tailscale admin Machines page, approve advertised subnet routes.
5. Confirm `/api/tailscale/status` reports:
   - `authenticated: true`
   - `routesApproved: true`

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
- `HOST_IP` optional manual LAN IP override for subnet detection
- `TS_ACCEPT_ROUTES` default: `false`
- `DEFAULT_LAN_PREFIX` default: `24`
