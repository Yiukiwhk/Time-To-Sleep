# Run deploy/remote_check.sh on the deployment host over ssh.
#
# Connection settings come from deploy/local.settings.ps1, or from the
# SSP_DEPLOY_* environment variables when that file does not exist.  Copy
# deploy/local.settings.example.ps1 to deploy/local.settings.ps1 and fill it in.
$ErrorActionPreference = "Stop"

# ------------------------------------------------------------- settings -----
# Chinese error text is stored as base64 and decoded at runtime: this file must
# stay pure ASCII, because Windows PowerShell 5.1 decodes a BOM-less .ps1 as ANSI.
function Get-SspMessage([string]$Utf8Base64) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Utf8Base64))
}
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
if (-not $DeployPort) { $DeployPort = 22 }
$target = "$DeployUser@$DeployHost"
$sudoB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($DeployPassword))

Set-Content -LiteralPath (Join-Path $env:TEMP "dsh_askpass.bat") -Value "@echo off`r`necho $DeployPassword" -Encoding ASCII
$env:SSH_ASKPASS = Join-Path $env:TEMP "dsh_askpass.bat"
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "localhost:0"

$common = @(
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "UserKnownHostsFile=$env:USERPROFILE\.ssh\dsh_known_hosts",
  "-o", "PubkeyAuthentication=no",
  "-o", "NumberOfPasswordPrompts=1",
  "-o", "ConnectTimeout=25",
  "-o", "LogLevel=ERROR",
  "-p", "$DeployPort"
)

$remote = Get-Content -LiteralPath (Join-Path $PSScriptRoot "remote_check.sh") -Raw
# DEPLOY_HOST feeds the host matrix inside remote_check.sh; SUDO_PASS feeds sudo.
& ssh @common $target "export DEPLOY_HOST=$DeployHost SUDO_PASS=`"`$(echo `"$sudoB64`" | base64 -d)`"`n$remote" 2>&1
