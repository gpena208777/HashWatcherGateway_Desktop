param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$pythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "Missing $pythonw. Run scripts\install_windows.ps1 first."
}

$guiPath = Join-Path $RepoRoot "app\gui.py"
if (-not (Test-Path $guiPath)) {
    throw "Missing GUI entrypoint: $guiPath"
}

$desktopDir = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopDir "HashWatcher Gateway.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$guiPath`""
$shortcut.WorkingDirectory = $RepoRoot
$shortcut.IconLocation = "$pythonw,0"
$shortcut.Save()

Write-Host "Created desktop shortcut:"
Write-Host "  $shortcutPath"
