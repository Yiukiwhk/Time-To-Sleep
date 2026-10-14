# Serve frontend + deploy/nginx-sleep-schedule.conf from a portable nginx instance,
# so the exact production vhost can be exercised locally before shipping it.
#
#   .\deploy\serve_local.ps1 -Upstream 127.0.0.1:18011 -Start
#   .\deploy\serve_local.ps1 -Stop
#
# Mirrors frontend/ into <root>/.tmp/nginx-test/nginx-1.26.2/www/sleep-schedule,
# rewrites the vhost's /var/www root and listen port, and (with -Start) launches
# nginx detached.  Prints the local base URL on success.
param(
    # Both default to this checkout (deploy/ lives in the repo root), so a fresh clone runs as-is.
    [string]$Root,
    [string]$Python,
    [int]$Port = 8082,
    [string]$Upstream = "127.0.0.1:18011",
    [switch]$Start,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

if (-not $Root) {
    $derived = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $derived "run.py")) { $Root = $derived }
    elseif (Test-Path -LiteralPath (Join-Path (Get-Location).Path "run.py")) { $Root = (Get-Location).Path }
    else { throw "Cannot locate the project root (no run.py found). Pass -Root <path-to-checkout>." }
}
if (-not $Python) { $Python = Join-Path $Root "backend\.venv\Scripts\python.exe" }

$tmp = Join-Path $Root ".tmp"
$base = Join-Path $tmp "nginx-test\nginx-1.26.2"
$nginx = Join-Path $base "nginx.exe"
$conf = Join-Path $base "conf\prod.conf"

function Stop-Nginx {
    # -p . resolves relative to the current directory, so nginx must be started
    # from its own prefix or it cannot even open logs/error.log.
    if (Test-Path -LiteralPath $nginx) {
        Push-Location $base
        try {
            $ErrorActionPreference = "Continue"
            & $nginx -s stop -c conf/prod.conf -p . 2>$null | Out-Null
        }
        catch { }
        finally { Pop-Location }
        Start-Sleep -Milliseconds 600
    }
    Get-Process nginx -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $base "logs\prod.pid") -Force -ErrorAction SilentlyContinue
}

if ($Stop) { Stop-Nginx; Write-Host "nginx stopped"; exit 0 }

# ------------------------------------------------------------ nginx binary ----
if (-not (Test-Path -LiteralPath $nginx)) {
    Write-Host "downloading nginx for Windows ..."
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $dl = Join-Path $tmp "dl_nginx.py"
    @'
import io, sys, os, urllib.request, zipfile
dst = sys.argv[1]
data = urllib.request.urlopen("https://nginx.org/download/nginx-1.26.2.zip", timeout=180).read()
os.makedirs(dst, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(data)) as zf:
    zf.extractall(dst)
print("nginx extracted:", len(data), "bytes")
'@ | Set-Content -LiteralPath $dl -Encoding UTF8
    & $Python $dl (Join-Path $tmp "nginx-test")
    if (-not (Test-Path -LiteralPath $nginx)) { throw "nginx.exe not available" }
}

# ------------------------------------------------------------ mirror root -----
$www = Join-Path $base "www\sleep-schedule"
if (Test-Path -LiteralPath $www) { Remove-Item -LiteralPath $www -Recurse -Force }
New-Item -ItemType Directory -Path $www -Force | Out-Null
foreach ($d in @("user", "admin", "shared")) {
    Copy-Item -LiteralPath (Join-Path $Root "frontend\$d") -Destination (Join-Path $www $d) -Recurse
}
Write-Host ("mirrored static tree: " + (Get-ChildItem -Path $www -Recurse -File).Count + " files")

# --------------------------------------------------------------- config -------
$wwwN = (Join-Path $base "www") -replace '\\', '/'
$cfg = [System.IO.File]::ReadAllText((Join-Path $Root "deploy\nginx-sleep-schedule.conf"), [System.Text.Encoding]::UTF8)
$cfg = $cfg.Replace('/var/www', $wwwN)
$cfg = $cfg.Replace('listen 80 default_server;', "listen $Port;")
$cfg = $cfg.Replace('listen [::]:80 default_server;', '')
$cfg = $cfg.Replace('127.0.0.1:8011', $Upstream)
$head = "worker_processes 1;`nerror_log logs/prod-error.log warn;`npid logs/prod.pid;`nevents { worker_connections 128; }`nhttp {`n    include mime.types;`n    default_type application/octet-stream;`n    access_log logs/prod-access.log;`n"
[System.IO.File]::WriteAllText($conf, $head + $cfg + "`n}`n", (New-Object System.Text.UTF8Encoding($false)))

Stop-Nginx
Push-Location $base
try {
    # nginx reports to stderr, which PowerShell turns into a NativeCommandError.
    $ErrorActionPreference = "Continue"
    & $nginx -t -c conf/prod.conf -p . 2>&1 | ForEach-Object { Write-Host "  $_" }
    $testCode = $LASTEXITCODE
    if ($testCode -ne 0) { throw "nginx -t failed (exit $testCode)" }
    if (-not $Start) { Write-Host "config written to $conf"; exit 0 }

    Start-Process -FilePath $nginx -ArgumentList '-c', 'conf/prod.conf', '-p', '.' -WorkingDirectory $base -WindowStyle Hidden
    $ready = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 400
        $probe = & curl.exe -s -m 3 -o NUL -w "%{http_code}" "http://127.0.0.1:$Port/" 2>$null
        if ($probe -match '^[1-5][0-9][0-9]$') { $ready = $true; break }
    }
    if (-not $ready) { throw "nginx did not start listening on $Port" }
    Write-Host "nginx serving http://127.0.0.1:$Port/  (upstream $Upstream)"
}
finally { Pop-Location }
