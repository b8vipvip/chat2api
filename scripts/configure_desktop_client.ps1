param(
    [string]$ServerUrl = "https://chat2api.mv3.cn",
    [Parameter(Mandatory = $true)]
    [string]$ApiKey,
    [string]$ChromePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Arguments = @(
    "-m", "desktop_client", "configure",
    "--server-url", $ServerUrl,
    "--api-key", $ApiKey
)
if ($ChromePath) {
    $Arguments += @("--chrome-path", $ChromePath)
}
python @Arguments
