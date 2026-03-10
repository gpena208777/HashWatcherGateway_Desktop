# HashWatcher Gateway Desktop

Standalone desktop gateway for macOS and Windows that mirrors the Umbrel HashWatcher Gateway behavior:

- Miner polling and normalization
- Subnet miner discovery
- Local dashboard and JSON API on port `8787`
- Tailscale setup/status controls
- Miner request proxy endpoint

This repo is intentionally separate from the Umbrel/Pi codebase.

## Project Layout

```
app/
  main.py
  gateway/
    hub_agent.py
    tailscale_setup.py
    network_utils.py
    assets/
      icon.png
      step4a.png
      step4b.png
      step4c.png
install/
  macos/
  windows/
scripts/
requirements.txt
```

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app/main.py
```

Open:

- Dashboard: `http://localhost:8787`
- Status API: `http://localhost:8787/api/status`

## Runtime Environment Variables

- `PI_HOSTNAME` default: local machine hostname
- `AGENT_ID` default: `hashwatcher-gateway`
- `STATUS_HTTP_BIND` default: `0.0.0.0`
- `STATUS_HTTP_PORT` default: `8787`
- `RUNTIME_CONFIG_PATH` default: `~/.hashwatcher-gateway/runtime_config.json`
- `BITAXE_HOST` default: empty
- `BITAXE_SCHEME` default: `http`
- `BITAXE_ENDPOINTS` default: `/system/info,/api/system/info`
- `POLL_SECONDS` default: `10`
- `HTTP_TIMEOUT_SECONDS` default: `5`
- `TAILSCALE_BIN` optional absolute path to `tailscale` binary
- `HOST_IP` optional manual LAN IP override for subnet detection
- `TS_ACCEPT_ROUTES` default: `false`
- `DEFAULT_LAN_PREFIX` default: `24`

## Tailscale Notes

- The desktop gateway uses the local `tailscale` CLI.
- Route advertisement can require elevated permissions.
- In Tailscale admin, subnet routes still need approval for full remote access.

## Service Installers

- macOS launch agent: `scripts/install_macos_launchagent.sh`
- Windows service (WinSW wrapper): `scripts/install_windows_service.ps1`

