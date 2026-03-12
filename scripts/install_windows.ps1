param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonExe = "",
    [switch]$SkipService
)

$ErrorActionPreference = "Stop"

function Resolve-SystemPython {
    param([string]$PreferredPath = "")

    if (-not [string]::IsNullOrWhiteSpace($PreferredPath)) {
        if (-not (Test-Path $PreferredPath)) {
            throw "Python executable not found: $PreferredPath"
        }
        return (Resolve-Path $PreferredPath).Path
    }

    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) {
            continue
        }
        $path = $cmd.Source
        if ([string]::IsNullOrWhiteSpace($path)) {
            continue
        }
        if ($path -like "*\Microsoft\WindowsApps\python*.exe") {
            continue
        }
        return $path
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $resolved = (& py -3 -c "import sys; print(sys.executable)").Trim()
            if ($resolved -and (Test-Path $resolved)) {
                return (Resolve-Path $resolved).Path
            }
        } catch {
        }
    }

    throw @"
Python is not available from this shell.

Install Python from https://www.python.org/downloads/windows/
During install, enable: "Add Python to PATH"
Then re-open PowerShell and rerun this command.
"@
}

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Set-Location $RepoRoot

if (-not (Test-Path (Join-Path $RepoRoot "requirements.txt"))) {
    throw "requirements.txt not found. Run this script from the HashWatcherGateway_Desktop repo."
}

$systemPython = Resolve-SystemPython -PreferredPath $PythonExe
$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Write-Host "Using Python:" -ForegroundColor Cyan
Write-Host "  $systemPython"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    & $systemPython -m venv (Join-Path $RepoRoot ".venv")
}

if (-not (Test-Path $venvPython)) {
    throw "Failed to create virtual environment at $venvPython"
}

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $RepoRoot "requirements.txt")

$winswBinary = Join-Path $RepoRoot "install\windows\winsw-x64.exe"
if ($SkipService) {
    Write-Host "Skipping Windows service installation by request (--SkipService)." -ForegroundColor Yellow
} elseif (-not (Test-Path $winswBinary)) {
    Write-Host "WinSW binary not found at install\\windows\\winsw-x64.exe; skipping service install." -ForegroundColor Yellow
    Write-Host "Place WinSW x64 there, then run scripts\\install_windows_service.ps1 as Administrator." -ForegroundColor Yellow
} elseif (-not (Test-IsAdmin)) {
    Write-Host "Not running as Administrator; skipping service install." -ForegroundColor Yellow
    Write-Host "Re-run this script in an elevated PowerShell to auto-install the service." -ForegroundColor Yellow
} else {
    Write-Host "Installing/updating Windows service..." -ForegroundColor Cyan
    & (Join-Path $RepoRoot "scripts\install_windows_service.ps1") -RepoRoot $RepoRoot -PythonExe $venvPython -WinSWBinary $winswBinary
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Start GUI:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\python.exe .\app\gui.py"
Write-Host "API check:" -ForegroundColor Green
Write-Host "  Invoke-RestMethod http://127.0.0.1:8787/api/status"
