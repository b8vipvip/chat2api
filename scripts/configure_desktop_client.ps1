param(
    [string]$ServerUrl = "https://chat2api.mv3.cn",
    [Parameter(Mandatory = $true)]
    [string]$ApiKey
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
python -m desktop_client configure `
    --server-url $ServerUrl `
    --api-key $ApiKey `
    --extension-dir "$RepoRoot\chrome_extension"
