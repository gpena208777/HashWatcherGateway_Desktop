param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$InnoSetupCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$distDir = Join-Path $RepoRoot "dist"
$issPath = Join-Path $RepoRoot "packaging\windows\HashWatcherGatewayDesktop.iss"
$releaseDir = Join-Path $RepoRoot "release"

if (-not (Test-Path $distDir)) {
    throw "Missing dist directory: $distDir. Run scripts\build_windows_release.ps1 first."
}

$exeCandidates = Get-ChildItem -Path $distDir -Filter "HashWatcherGatewayDesktop.exe" -File -Recurse -ErrorAction SilentlyContinue
if (-not $exeCandidates -or $exeCandidates.Count -eq 0) {
    throw "Missing built app executable under '$distDir'. Run scripts\build_windows_release.ps1 first."
}
$entryExe = ($exeCandidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$sourceDir = Split-Path -Parent $entryExe

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

Write-Host "Using built executable:"
Write-Host "  $entryExe"
Write-Host "Created Windows installer:"
Write-Host "  $installerPath"
