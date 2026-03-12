param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonExe = "",
    [string]$WinSWBinary = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

function Resolve-PythonExe {
    param([string]$PreferredPath = "")

    if (-not [string]::IsNullOrWhiteSpace($PreferredPath)) {
        if (Test-Path $PreferredPath) {
            return (Resolve-Path $PreferredPath).Path
        }
        throw "Python executable not found: $PreferredPath"
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

    throw "Python not found in PATH (or only Microsoft Store alias found). Install Python from python.org and retry."
}

$installDir = Join-Path $RepoRoot "install\windows"
$templatePath = Join-Path $installDir "hashwatcher-gateway.template.xml"
$appMain = Join-Path $RepoRoot "app\main.py"
$appWorkDir = Join-Path $RepoRoot "app"
if (-not (Test-Path $appWorkDir)) {
    throw "Missing app directory: $appWorkDir"
}

if (-not (Test-Path $appMain)) {
    throw "Missing app entrypoint: $appMain"
}

if ([string]::IsNullOrWhiteSpace($WinSWBinary)) {
    $WinSWBinary = Join-Path $installDir "winsw-x64.exe"
}
if (-not (Test-Path $WinSWBinary)) {
    throw "Missing WinSW binary at '$WinSWBinary'. Download WinSW x64 and place it there."
}

$serviceExe = Join-Path $installDir "HashWatcherGatewayDesktop.exe"
$serviceXml = Join-Path $installDir "HashWatcherGatewayDesktop.xml"

if (Test-Path $serviceExe) {
    try { & $serviceExe stop | Out-Null } catch {}
    try { & $serviceExe uninstall | Out-Null } catch {}
}

if ($Uninstall) {
    Write-Host "Uninstalled Windows service wrapper (if present):"
    Write-Host "  HashWatcherGatewayDesktop"
    exit 0
}

$resolvedPython = Resolve-PythonExe -PreferredPath $PythonExe
$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not [string]::IsNullOrWhiteSpace($PythonExe) -and $PythonExe -like "*\python.exe") {
    $resolvedPython = (Resolve-Path $PythonExe).Path
} elseif (Test-Path $venvPython) {
    $resolvedPython = $venvPython
}

Copy-Item $WinSWBinary $serviceExe -Force

$appMainArg = "`"$appMain`""
$xml = Get-Content $templatePath -Raw
$xml = $xml.Replace("__PYTHON_EXE__", $resolvedPython)
$xml = $xml.Replace("__APP_MAIN__", $appMainArg)
$xml = $xml.Replace("__APP_WORKDIR__", $appWorkDir)
Set-Content -Path $serviceXml -Value $xml -Encoding UTF8

& $serviceExe install
& $serviceExe start

Write-Host "Installed and started Windows service wrapper:"
Write-Host "  $serviceExe"
Write-Host "Service name: HashWatcherGatewayDesktop"
Write-Host "Python executable:"
Write-Host "  $resolvedPython"
