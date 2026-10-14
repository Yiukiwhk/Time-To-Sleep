# Upload the corrected frontend + backend code + vhost and run
# deploy/remote_apply_fix.sh (which restarts the systemd service and verifies).
#
#   .\deploy\apply_fix.ps1
#
# Connection settings come from deploy/local.settings.ps1, or from the
# SSP_DEPLOY_* environment variables when that file does not exist.  Copy
# deploy/local.settings.example.ps1 to deploy/local.settings.ps1 and fill it in.
# An explicit -Server / -User / -Password / -Port argument still wins.
param(
    [string]$Server,
    [string]$User,
    [string]$Password,
    [int]$Port,
    # Project root.  Empty by default: it is derived below from the script
    # location (deploy/ sits in the repo root), with a fallback to the current
    # directory because this script is sometimes executed from a temporary copy
    # (some machines block running unsigned .ps1 straight from the checkout).
    # Pass -Root explicitly to override.
    [string]$Root
)

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
if (-not $Server) { $Server = $DeployHost }
if (-not $User) { $User = $DeployUser }
if (-not $Password) { $Password = $DeployPassword }
if (-not $Port) { $Port = $DeployPort }
if (-not $Port) { $Port = 22 }
# sudo on the server reads this environment variable; base64 keeps the value
# intact regardless of the characters the password contains.
$sudoB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Password))

# Resolve the project root when the caller did not pass one.
if (-not $Root) {
    $derived = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $derived "run.py")) { $Root = $derived }
    elseif (Test-Path -LiteralPath (Join-Path (Get-Location).Path "run.py")) { $Root = (Get-Location).Path }
    else { throw "project root not found (no run.py next to deploy\). Pass -Root <path-to-checkout>." }
}

$root = $Root.TrimEnd("\")
if (-not (Test-Path -LiteralPath (Join-Path $root "frontend\user\index.html"))) {
    throw "project root not found (expected frontend\user\index.html under $root)"
}

# Python modules shipped by a release: the adaptive-v2 advice engine, the auth
# guard (login-failure warning), the records cooldown and the config defaults.
# Keep in sync with the rollback list in deploy/remote_apply_fix.sh.
$backendFiles = @(
    "app\api\advice.py",
    "app\api\auth.py",
    "app\api\records.py",
    "app\config.py",
    "app\db.py",
    "app\models.py",
    "app\schemas.py",
    "app\services\advice.py",
    "app\services\advice_plan.py",
    "app\services\auth_service.py"
)

$askpass = Join-Path $env:TEMP "dsh_askpass.bat"
Set-Content -LiteralPath $askpass -Value "@echo off`r`necho $Password" -Encoding ASCII
$env:SSH_ASKPASS = $askpass
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "localhost:0"

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
$common = $commonOptions + @("-p", "$Port")
$scpCommon = $commonOptions + @("-P", "$Port")
$target = "$User@$Server"

# ---------------------------------------------------------------- stage ------
$stage = Join-Path $env:TEMP "sleep-fix-stage"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "frontend") | Out-Null
Copy-Item -LiteralPath (Join-Path $root "frontend\user")   -Destination (Join-Path $stage "frontend\user")   -Recurse
Copy-Item -LiteralPath (Join-Path $root "frontend\admin")  -Destination (Join-Path $stage "frontend\admin")  -Recurse
Copy-Item -LiteralPath (Join-Path $root "frontend\shared") -Destination (Join-Path $stage "frontend\shared") -Recurse
foreach ($rel in $backendFiles) {
    $src = Join-Path $root "backend\$rel"
    if (-not (Test-Path -LiteralPath $src)) { throw "missing backend file $rel" }
    $dst = Join-Path $stage "backend\$rel"
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst
}
Copy-Item -LiteralPath (Join-Path $root "deploy\nginx-sleep-schedule.conf") -Destination $stage
Copy-Item -LiteralPath (Join-Path $root "deploy\remote_apply_fix.sh") -Destination $stage
Copy-Item -LiteralPath (Join-Path $root "deploy\schema_check.py") -Destination $stage
Get-ChildItem -Path $stage -Recurse -Include "__pycache__" -Directory | Remove-Item -Recurse -Force
Write-Host ("staged: " + (Get-ChildItem -Path $stage -Recurse -File).Count + " files")

# ---------------------------------------------------------------- upload -----
& ssh @common $target "rm -rf /tmp/sleep-fix && mkdir -p /tmp/sleep-fix"
if ($LASTEXITCODE -ne 0) { throw "ssh pre-clean failed" }
& scp @scpCommon -r (Join-Path $stage "frontend") "${target}:/tmp/sleep-fix/"
if ($LASTEXITCODE -ne 0) { throw "scp frontend failed" }
& scp @scpCommon -r (Join-Path $stage "backend") "${target}:/tmp/sleep-fix/"
if ($LASTEXITCODE -ne 0) { throw "scp backend failed" }
foreach ($f in @("nginx-sleep-schedule.conf", "remote_apply_fix.sh", "schema_check.py")) {
    & scp @scpCommon (Join-Path $stage $f) "${target}:/tmp/sleep-fix/"
    if ($LASTEXITCODE -ne 0) { throw "scp $f failed" }
}
Write-Host "uploaded to /tmp/sleep-fix"

# ------------------------------------------------------------------ run ------
$remote = "chmod +x /tmp/sleep-fix/remote_apply_fix.sh && export SUDO_PASS=`"`$(echo `"$sudoB64`" | base64 -d)`" && STAGE=/tmp/sleep-fix bash /tmp/sleep-fix/remote_apply_fix.sh"
& ssh @common $target $remote
exit $LASTEXITCODE
