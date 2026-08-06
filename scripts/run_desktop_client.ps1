param(
    [switch]$LaunchNow
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
if ($LaunchNow) {
    python -m desktop_client run --launch-now
} else {
    python -m desktop_client run
}
