# Run one shell command on the deployment host over ssh (password via askpass).
#
#   .\deploy\ssh_run.ps1 -Command "systemctl is-active nginx"
#
# Connection settings come from deploy/local.settings.ps1, or from the
# SSP_DEPLOY_* environment variables when that file does not exist.  Copy
# deploy/local.settings.example.ps1 to deploy/local.settings.ps1 and fill it in.
# Keeps the ssh options identical to apply_fix.ps1 so the known_hosts file and
# password handling stay consistent.
param(
    [Parameter(Mandatory = $true)][string]$Command,
    [string]$Server,
    [string]$User,
    [string]$Password,
    [int]$Port
)

# ------------------------------------------------------------- settings -----
# Chinese error text is stored as base64 and decoded at runtime: this file must
# stay pure ASCII, because Windows PowerShell 5.1 decodes a BOM-less .ps1 as ANSI.
function Get-SspMessage([string]$Utf8Base64) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Utf8Base64))
}

# Real values live in the git-ignored local.settings.ps1; environment variables
# are the fallback for CI or a machine without that file.  An explicit -Server /
# -User / -Password / -Port argument still wins.
$localSettings = Join-Path $PSScriptRoot 'local.settings.ps1'
if (Test-Path -LiteralPath $localSettings) {
    . $localSettings
}
else {
    if ($env:SSP_DEPLOY_HOST) { $DeployHost = $env:SSP_DEPLOY_HOST }
    if ($env:SSP_DEPLOY_USER) { $DeployUser = $env:SSP_DEPLOY_USER }
    if ($env:SSP_DEPLOY_PASSWORD) { $DeployPassword = $env:SSP_DEPLOY_PASSWORD }
    if ($env:SSP_SUDO_PASS) { $SudoPass = $env:SSP_SUDO_PASS }
    if ($env:SSP_DEPLOY_PORT) { $DeployPort = [int]$env:SSP_DEPLOY_PORT }
}
if (-not $DeployHost) { throw (Get-SspMessage -Utf8Base64 "57y65bCR5pyN5Yqh5Zmo5Zyw5Z2A77ya6K+35YWI6K6+572u546v5aKD5Y+Y6YePIFNTUF9ERVBMT1lfSE9TVD085pyN5Yqh5Zmo5Zyw5Z2APu+8jOaIluWkjeWItiBkZXBsb3lcbG9jYWwuc2V0dGluZ3MuZXhhbXBsZS5wczEg5Li6IGRlcGxveVxsb2NhbC5zZXR0aW5ncy5wczEg5bm25aGr5YaZ44CC") }
if (-not $DeployUser -or -not $DeployPassword) { throw (Get-SspMessage -Utf8Base64 "6K+35aSN5Yi2IGRlcGxveVxsb2NhbC5zZXR0aW5ncy5leGFtcGxlLnBzMSDkuLogZGVwbG95XGxvY2FsLnNldHRpbmdzLnBzMSDlubbloavlhpnvvIzmiJborr7nva7njq/looPlj5jph48gU1NQX0RFUExPWV8q44CC") }
if (-not $Server) { $Server = $DeployHost }
if (-not $User) { $User = $DeployUser }
if (-not $Password) { $Password = $DeployPassword }
if (-not $Port) { $Port = $DeployPort }
if (-not $Port) { $Port = 22 }

$askpass = Join-Path $env:TEMP "dsh_askpass.bat"
Set-Content -LiteralPath $askpass -Value "@echo off`r`necho $Password" -Encoding ASCII
$env:SSH_ASKPASS = $askpass
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "localhost:0"

$common = @(
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=$env:USERPROFILE\.ssh\dsh_known_hosts",
    "-o", "PubkeyAuthentication=no",
    "-o", "NumberOfPasswordPrompts=1",
    "-o", "ConnectTimeout=25",
    "-o", "LogLevel=ERROR",
    "-p", "$Port"
)

& ssh @common "$User@$Server" $Command
exit $LASTEXITCODE
