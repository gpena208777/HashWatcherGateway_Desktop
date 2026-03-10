param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$PythonExe = "",
    [string]$WinSWBinary = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        throw "Python not found in PATH. Install Python 3 and retry."
    }
    $PythonExe = $pythonCmd.Source
}

$installDir = Join-Path $RepoRoot "install\windows"
$templatePath = Join-Path $installDir "hashwatcher-gateway.template.xml"
$appMain = Join-Path $RepoRoot "app\main.py"
$appWorkDir = Join-Path $RepoRoot "app"

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

Copy-Item $WinSWBinary $serviceExe -Force

$xml = Get-Content $templatePath -Raw
$xml = $xml.Replace("__PYTHON_EXE__", $PythonExe)
$xml = $xml.Replace("__APP_MAIN__", $appMain)
$xml = $xml.Replace("__APP_WORKDIR__", $appWorkDir)
Set-Content -Path $serviceXml -Value $xml -Encoding UTF8

try { & $serviceExe stop | Out-Null } catch {}
try { & $serviceExe uninstall | Out-Null } catch {}

& $serviceExe install
& $serviceExe start

Write-Host "Installed and started Windows service wrapper:"
Write-Host "  $serviceExe"
Write-Host "Service name: HashWatcherGatewayDesktop"

