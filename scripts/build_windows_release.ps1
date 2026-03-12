param(
    [string]$PythonExe = "py",
    [string]$PythonVersionArg = "-3"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$specPath = Join-Path $repoRoot "packaging\pyinstaller\hashwatcher_gateway.spec"

Set-Location $repoRoot

& $PythonExe $PythonVersionArg -m pip install --upgrade pip
& $PythonExe $PythonVersionArg -m pip install -r requirements.txt pyinstaller
& $PythonExe $PythonVersionArg -m PyInstaller --noconfirm --clean $specPath

Write-Host "Build complete:"
Write-Host "  $repoRoot\dist\HashWatcherGatewayDesktop\HashWatcherGatewayDesktop.exe"
Write-Host ""
Write-Host "Next step (recommended): code-sign the executable before sharing."
