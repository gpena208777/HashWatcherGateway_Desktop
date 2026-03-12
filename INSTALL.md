# Install HashWatcher Gateway Desktop

Start here if you are an end user.

## Fastest Path (No Terminal)

1. Open GitHub Releases for this repo.
2. Download your platform installer:
   - Windows: `HashWatcherGatewayDesktop-Setup.exe`
   - macOS: `HashWatcherGatewayDesktop.pkg` or `HashWatcherGatewayDesktop.dmg`
3. Install and open the app.

Release note: these installers are auto-built and attached when maintainers push a `v*` tag.

## Command-Line Path (From Git)

### Windows (PowerShell)

Run in the repo root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\install_windows.ps1
.\scripts\create_windows_shortcut.ps1
```

### macOS (Terminal)

Run in the repo root:

```bash
./scripts/install_macos.sh
```

This now builds `HashWatcherGatewayDesktop.app` and installs it into `/Applications` (or `~/Applications` if system Applications is not writable).

## Quick Verify

### Windows

```powershell
Get-Service HashWatcherGatewayDesktop
Invoke-RestMethod http://127.0.0.1:8787/api/status
```

### macOS

```bash
launchctl list | grep com.hashwatcher.gateway.desktop
curl -fsS http://127.0.0.1:8787/api/status
```
