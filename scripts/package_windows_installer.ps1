param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$InnoSetupCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$sourceDir = Join-Path $RepoRoot "dist\HashWatcherGatewayDesktop"
$entryExe = Join-Path $sourceDir "HashWatcherGatewayDesktop.exe"
$issPath = Join-Path $RepoRoot "packaging\windows\HashWatcherGatewayDesktop.iss"
$releaseDir = Join-Path $RepoRoot "release"

if (-not (Test-Path $entryExe)) {
    throw "Missing built app executable: $entryExe. Run scripts\build_windows_release.ps1 first."
}

if (-not (Test-Path $issPath)) {
    throw "Missing Inno Setup script: $issPath"
}

if (-not (Test-Path $InnoSetupCompiler)) {
    throw "Inno Setup compiler not found: $InnoSetupCompiler"
}

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = $env:GITHUB_REF_NAME
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = "1.0.0"
}
$Version = $Version.Trim()
if ($Version.StartsWith("v")) {
    $Version = $Version.Substring(1)
}

& $InnoSetupCompiler "/DSourceDir=$sourceDir" "/DOutDir=$releaseDir" "/DMyAppVersion=$Version" $issPath

$installerPath = Join-Path $releaseDir "HashWatcherGatewayDesktop-Setup.exe"
if (-not (Test-Path $installerPath)) {
    throw "Installer build did not produce expected output: $installerPath"
}

Write-Host "Created Windows installer:"
Write-Host "  $installerPath"
