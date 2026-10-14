# Upload a single file to the server and (optionally) publish it into the web root.
#
#   .\deploy\push_file.ps1 -Local <path> -Remote <path-on-server> [-WebRoot]
#
# Connection settings come from deploy/local.settings.ps1, or from the
# SSP_DEPLOY_* environment variables when that file does not exist.  Copy
# deploy/local.settings.example.ps1 to deploy/local.settings.ps1 and fill it in.
# Uses the same password-based SSH transport as the rest of the deploy folder so
# that no extra tooling has to be installed on the Windows side.
param(
    [Parameter(Mandatory = $true)][string]$Local,
    [Parameter(Mandatory = $true)][string]$Remote,
    [switch]$WebRoot
)

$ErrorActionPreference = "Continue"

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
# The sudo password travels base64 encoded, so quoting can never break the
# remote command no matter which characters the password contains.
$sudoB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($DeployPassword))

$env:SSH_ASKPASS = Join-Path $env:TEMP "dsh_askpass.bat"
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "localhost:0"
Set-Content -LiteralPath $env:SSH_ASKPASS -Value "@echo off`r`necho $DeployPassword" -Encoding ASCII

# Shared connection options.  The port flag differs per tool: ssh takes -p <port>,
# scp takes -P <port>.  Handing "-p 22" to scp makes it treat "22" as a source file
# ("stat local \"22\": No such file or directory"), so the two are built separately.
$commonOptions = @(
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=$env:USERPROFILE\.ssh\dsh_known_hosts",
    "-o", "PubkeyAuthentication=no",
    "-o", "NumberOfPasswordPrompts=1",
    "-o", "ConnectTimeout=25",
    "-o", "LogLevel=ERROR"
)
$common = $commonOptions + @("-p", "$DeployPort")
$scpCommon = $commonOptions + @("-P", "$DeployPort")

if (-not (Test-Path -LiteralPath $Local)) {
    Write-Error "local file not found: $Local"
    exit 2
}

& scp @scpCommon $Local "${target}:$Remote" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "scp failed for $Local"
    exit 1
}
Write-Host "uploaded $Local -> $Remote"

if ($WebRoot) {
    $name = Split-Path -Leaf $Remote
    $webroot = "/var/www/sleep-schedule/$name"
    # Wrapped in single quotes so the double quotes reach the remote bash intact.
    $remoteCmd = "'echo ""$sudoB64"" | base64 -d | sudo -S cp ""$Remote"" ""$webroot"" && " +
                 "echo ""$sudoB64"" | base64 -d | sudo -S chown www-data:www-data ""$webroot"" && " +
                 "echo ""published://sleep/$name""'"
    & ssh @common $target $remoteCmd 2>&1 | Select-Object -Last 1
}
