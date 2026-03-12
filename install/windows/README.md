# Windows Install (Command-Line)

From an elevated PowerShell window in the repo root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\install_windows.ps1
```

This script:

1. Finds a real Python install (ignores Microsoft Store alias stubs).
2. Creates `.venv`.
3. Installs dependencies from `requirements.txt`.
4. Installs and starts `HashWatcherGatewayDesktop` service (when running as Administrator and `winsw-x64.exe` exists).

## Required WinSW Binary

Place WinSW x64 at:

`install\windows\winsw-x64.exe`

If missing, dependency install still succeeds, but service installation is skipped.

## Service Management

Install/update service:

```powershell
.\scripts\install_windows_service.ps1
```

Uninstall service:

```powershell
.\scripts\install_windows_service.ps1 -Uninstall
```

Check service:

```powershell
Get-Service HashWatcherGatewayDesktop
Invoke-RestMethod http://127.0.0.1:8787/api/status
```

## GUI Shortcut

Create desktop shortcut after install:

```powershell
.\scripts\create_windows_shortcut.ps1
```
