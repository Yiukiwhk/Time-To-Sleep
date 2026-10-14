# Local end-to-end acceptance for the advice feature (single command):
#   1) start the backend on a scratch DB in .tmp/accept (write cooldown disabled)
#   2) start the portable nginx (mirrors frontend/, listens on 8082, production vhost)
#   3) static/copy self checks + module graph + smoke (all through nginx)
#   4) headless Edge opens a same-origin probe page: seeds data, logs in, renders the
#      advice page, screenshots it, and reports the rendered DOM back through
#      /healthz?ui=... (lands in the backend access log) for scripted assertions
#   5) restart the backend with the DEFAULT 30s write cooldown on a fresh DB and run
#      tests/e2e_http.py (it asserts that a second write within 30s is rejected)
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\accept_local.ps1
#   ... -KeepRunning      keep both servers up after the checks
#   ... -SkipBrowser      skip the Edge render + screenshot step
#
# NOTE: this file carries non-ASCII (Chinese) comments, so it MUST stay UTF-8 **with BOM**:
# Windows PowerShell 5.1 decodes a BOM-less .ps1 as ANSI (GBK here), which mangles the text
# and can break the parser (a mojibake string ate its closing quote once already).
# After editing, re-check with the real parser:
#   powershell.exe -File .\deploy\parse51.ps1 -Dir .\deploy   (see .tmp\study-verify\parse51.ps1)
param(
    # Both default to this checkout, derived from the script location, so a fresh
    # clone (or the annotated study copy) runs without editing paths.
    [string]$Root,
    [string]$Python,
    [int]$BackendPort = 18011,
    [int]$NginxPort = 8082,
    [string[]]$Modes = @("base", "refs", "deviation", "records", "plan", "refused", "mobile"),
    [switch]$BrowserOnly,
    [switch]$SkipBrowser,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# Resolve the project root: prefer the parent of this script (deploy/ lives in the
# repo root), fall back to the current directory, and fail loudly when neither
# looks like this project (recognised by run.py next to the deploy folder).
if (-not $Root) {
    $derived = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $derived "run.py")) { $Root = $derived }
    elseif (Test-Path -LiteralPath (Join-Path (Get-Location).Path "run.py")) { $Root = (Get-Location).Path }
    else { throw "Cannot locate the project root (no run.py found). Pass -Root <path-to-checkout>." }
}
if (-not $Python) { $Python = Join-Path $Root "backend\.venv\Scripts\python.exe" }

# powershell.exe -File passes "-Modes records,plan" as ONE string, so split it here
$Modes = @($Modes | ForEach-Object { $_ -split "," } | Where-Object { $_ })

$accept = Join-Path $Root ".tmp\accept"
$probe = Join-Path $accept "zz-smoke-advice.html"
$base = "http://127.0.0.1:$NginxPort"
New-Item -ItemType Directory -Path $accept -Force | Out-Null

$script:failed = 0
function Check([string]$Name, [bool]$Ok, [string]$Detail = "") {
    if ($Ok) { Write-Host ("  [PASS] " + $Name) }
    else { Write-Host ("  [FAIL] " + $Name + "  " + $Detail); $script:failed++ }
}

function Stop-Listener([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

function Wait-Http([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest -UseBasicParsing $Url -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep -Milliseconds 400 }
    }
    return $false
}

# Phase A runs with the 30s per-user write cooldown disabled so the probe page can seed
# five days of records back to back. The e2e suite asserts that the cooldown DOES reject a
# second write within 30s, so phase B restarts the backend with the default cooldown.
function Start-Backend([switch]$CooldownOff, [string]$Tag) {
    if ($CooldownOff) { $env:SSP_WRITE_COOLDOWN_SECONDS = "0" }
    else { Remove-Item Env:\SSP_WRITE_COOLDOWN_SECONDS -ErrorAction SilentlyContinue }
    $env:SSP_DB_PATH = Join-Path $accept "app.db"
    $env:SSP_DATA_DIR = $accept
    $env:SSP_LOG_DIR = $accept
    Start-Process -FilePath $Python `
        -ArgumentList @("run.py", "--port", "$BackendPort", "--host", "127.0.0.1", "--no-browser") `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $accept "srv$Tag.out") -RedirectStandardError (Join-Path $accept "srv$Tag.err")
}

Write-Host "== stop stale listeners =="
Stop-Listener $BackendPort
Stop-Listener $NginxPort
Remove-Item (Join-Path $accept "app.db*") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $accept "srv.err"), (Join-Path $accept "srv.out"), (Join-Path $accept "srv2.err"), (Join-Path $accept "srv2.out") -Force -ErrorAction SilentlyContinue

Write-Host "== start backend (port $BackendPort, write cooldown disabled for seeding) =="
$backend = Start-Backend -CooldownOff -Tag ""
Check "backend /healthz ready" (Wait-Http "http://127.0.0.1:$BackendPort/healthz" 40) "see .tmp\accept\srv.err"

Write-Host "== start portable nginx (port $NginxPort -> 127.0.0.1:$BackendPort) =="
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "deploy\serve_local.ps1") `
    -Port $NginxPort -Upstream "127.0.0.1:$BackendPort" -Start | Write-Host
Check "nginx ready at $base/user/" (Wait-Http "$base/user/" 30) "nginx did not come up"

$www = Join-Path $Root ".tmp\nginx-test\nginx-1.26.2\www\sleep-schedule"
if (Test-Path -LiteralPath $probe) { Copy-Item -LiteralPath $probe -Destination (Join-Path $www "zz-smoke-advice.html") -Force }

if (-not $BrowserOnly) {
Write-Host "== static + copy self checks =="
& $Python (Join-Path $Root "scripts\absolute_shared_imports.py") --check | Out-Host
Check "no relative shared imports" ($LASTEXITCODE -eq 0)
& $Python (Join-Path $Root "scripts\check_i18n_keys.py") | Out-Host
Check "i18n keys covered" ($LASTEXITCODE -eq 0)
& $Python (Join-Path $Root "scripts\check_css_classes.py") | Out-Host
Check "css classes covered" ($LASTEXITCODE -eq 0)
& $Python (Join-Path $Root "scripts\check_advice_i18n.py") | Out-Host
Check "advice i18n codes present" ($LASTEXITCODE -eq 0)

Write-Host "== routing / module graph / smoke (through nginx) =="
& $Python (Join-Path $Root "tests\check_module_graph.py") $base | Out-Host
Check "module graph" ($LASTEXITCODE -eq 0)
& $Python (Join-Path $Root "scripts\smoke_http.py") $base | Out-Host
Check "static + route smoke" ($LASTEXITCODE -eq 0)
}

if (-not $SkipBrowser) {
    Write-Host "== headless Edge renders the advice page (base / references / deviation) =="
    $edge = (Get-Command msedge.exe -ErrorAction SilentlyContinue).Source
    if (-not $edge) {
        foreach ($candidate in @("$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe")) {
            if (Test-Path -LiteralPath $candidate) { $edge = $candidate; break }
        }
    }
    if (-not $edge) { Check "edge binary found" $false "msedge.exe not found" }
    else {
        foreach ($mode in $Modes) {
            $png = Join-Path $accept ("advice-" + $mode + ".png")
            Remove-Item -LiteralPath $png -Force -ErrorAction SilentlyContinue
            $before = @(Select-String -LiteralPath (Join-Path $accept "srv.out") -Pattern 'ui=' -Encoding UTF8).Count
            $profile = Join-Path $accept ("edge-profile-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
            $route = if ($mode -eq "records") { "/user/%23/records" } else { "/user/%23/advice" }
            $url = "$base/zz-smoke-advice.html?to=$route&mode=$mode"
            $size = if ($mode -eq "mobile") { "390,900" } else { "1440,1240" }
            if ($mode -eq "mobile") { $url = "$url&w=390" } # headless windows have a minimum width; pin the iframe instead
            Start-Process -FilePath $edge -Wait -NoNewWindow -ArgumentList @(
                "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--user-data-dir=$profile", "--window-size=$size",
                "--virtual-time-budget=45000", "--screenshot=$png", $url
            )
            $bytes = if (Test-Path -LiteralPath $png) { (Get-Item -LiteralPath $png).Length } else { 0 }
            Check "[$mode] screenshot ($bytes bytes)" ($bytes -gt 12000) "too small, page probably did not render: $png"

            $lines = @(Select-String -LiteralPath (Join-Path $accept "srv.out") -Pattern 'ui=' -Encoding UTF8)
            $line = if ($lines.Count -gt $before) { $lines[$lines.Count - 1] } else { $null }
            if (-not $line) { Check "[$mode] probe wrote render summary" $false "no new ui= line in .tmp\accept\srv.out"; continue }
            $raw = [regex]::Match($line.Line, 'ui=([^ ]+)').Groups[1].Value
            $json = [System.Uri]::UnescapeDataString($raw) | ConvertFrom-Json
            Write-Host ("  [" + $mode + "] render summary: " + ($json | ConvertTo-Json -Compress))
            Check "[$mode] SPA rendered the app view" ($json.view -eq "app") ("view=" + $json.view)
            Check "[$mode] no page JS errors" (@($json.errors).Count -eq 0) ("errors=" + ($json.errors -join "; "))
            Check "[$mode] body has text" ($json.body_len -gt 200) ("len=" + $json.body_len)
            # 回归护栏：i18n 漏翻会原样显示 key（records.meal.undefined 就是这么被用户看到的）
            Check "[$mode] no leaked i18n keys" (@($json.leaked_keys).Count -eq 0) ("leaked=" + (@($json.leaked_keys) -join ", "))
            Check "[$mode] no unfilled template placeholders" (@($json.leaked_placeholders).Count -eq 0) ("placeholders=" + (@($json.leaked_placeholders) -join ", "))
            # 回归护栏：时间条不能压到时间刻度或"睡眠/早餐/午餐/晚餐"示例上
            Check "[$mode] timeline bars clear of axis/legend" (@($json.overlaps).Count -eq 0) ("overlaps=" + (@($json.overlaps) -join ", "))
            # 回归护栏：只数 DOM 节点会漏掉"条被压成细线、文字被裁光"，这里量几何
            # 记录页与后台不渲染建议页的时间图，只有这些档位要求几何摘要存在
            $adviceModes = @("base", "refs", "deviation", "plan", "refused", "mobile")
            if ($json.tl_today) {
                Check "[$mode] today timeline items are geometrically visible" ((@($json.tl_today.invisible)[0] -eq 0) -and ($json.tl_today.items -ge 4)) ("variant=" + $json.tl_today.variant + " items=" + $json.tl_today.items + " invisible=" + $json.tl_today.invisible + " min_h=" + $json.tl_today.min_item_h)
            } elseif ($adviceModes -contains $mode) {
                Check "[$mode] today timeline probe returned geometry" $false "tl_today missing from the render summary"
            }
            if ($json.tl_mini) {
                Check "[$mode] mini timeline items are geometrically visible" (@($json.tl_mini.invisible)[0] -eq 0) ("variant=" + $json.tl_mini.variant + " items=" + $json.tl_mini.items + " invisible=" + $json.tl_mini.invisible)
            } elseif ($adviceModes -contains $mode) {
                Check "[$mode] mini timeline probe returned geometry" $false "tl_mini missing from the render summary"
            }
            if ($mode -eq "base") {
                Check "timeline bars drawn" ($json.timeline_bars -ge 4) ("bars=" + $json.timeline_bars)
                Check "now marker drawn" ($json.timeline_now -ge 1) ("now=" + $json.timeline_now)
                Check "today card present" ($json.today_cards -ge 1) ("cards=" + $json.today_cards)
                Check "meal check-in buttons (>=7: eaten/skipped/refused + lunch + dinner)" ($json.checkin_buttons -ge 7) ("buttons=" + $json.checkin_buttons)
                Check "sidebar nav items (3)" ($json.nav_items -eq 3) ("nav=" + $json.nav_items)
                Check "footer references entry" ($json.footer_refs -ge 1) ("refs=" + $json.footer_refs)
                Check "timeline uses flex layout" ($json.style_ok -eq "flex") ("display=" + $json.style_ok)
            }
            if ($mode -eq "records") {
                # 用户报的"新增记录没反应"：必须真的发出 POST /api/records 并拿到 201
                Check "record form submitted (POST /api/records 201)" ($json.record_form.submit_ok -eq $true) ("calls=" + (@($json.record_form.submit_calls) | ConvertTo-Json -Compress))
                Check "record modal closed after save" ($json.record_form.modal_still_open -eq 0) "modal still open"
                $wantStart = (Get-Date).AddDays(-6).ToString("yyyy-MM-dd")
                $wantEnd = (Get-Date).ToString("yyyy-MM-dd")
                Check "records default range is the last 7 days" (($json.record_form.filters.start -eq $wantStart) -and ($json.record_form.filters.end -eq $wantEnd)) ("got " + $json.record_form.filters.start + " .. " + $json.record_form.filters.end + " want " + $wantStart + " .. " + $wantEnd)
                Check "record list has rows" ([int]$json.record_form.rows -ge 1) ("rows=" + $json.record_form.rows)
            }
            if ($mode -eq "plan") {
                Check "daily plan rows rendered" ([int]$json.plan_rows -ge 2) ("rows=" + $json.plan_rows)
                Check "mini timelines drawn (>= 3 per day)" ([int]$json.timeline_bars -ge 6) ("bars=" + $json.timeline_bars)
            }
            if ($mode -eq "refs") {
                Check "references modal opened" ($json.modal_open -eq 1) "no .modal-host in the frame"
                Check "four DOI links rendered" ($json.modal_dois -ge 4) ("dois=" + $json.modal_dois)
            }
            if ($mode -eq "refused") {
                # 早餐"不想吃"：必须真的发出 checkin 请求、今天的三餐被提前、方案列表第一行跟着变、
                # 点"已吃"能撤销；顺带确认"快速调整"模式下天数区域给的是只读说明而不是空白
                $rf = $json.refused
                if (-not $rf) { Check "[$mode] refusal probe returned a summary" $false "refused missing from the render summary" }
                else {
                    Check "breakfast card offers the refused button" ([int]$rf.has_button -eq 1) "no [data-refused] button"
                    Check "refusing breakfast posted a check-in" (@($rf.checkin_calls | Where-Object { $_.method -eq "POST" -and $_.status -eq 200 }).Count -ge 1) ("calls=" + (@($rf.checkin_calls) | ConvertTo-Json -Compress))
                    Check "today dinner moved earlier after refusing" ($rf.after.dinner -ne $rf.before.dinner -and $rf.after.dinner -ne "") ("before=" + $rf.before.dinner + " after=" + $rf.after.dinner)
                    Check "today lunch moved earlier after refusing" ($rf.after.lunch -ne $rf.before.lunch -and $rf.after.lunch -ne "") ("before=" + $rf.before.lunch + " after=" + $rf.after.lunch)
                    Check "daily plan first row follows the today card" ($rf.after.row -match [regex]::Escape($rf.after.dinner)) ("row=" + $rf.after.row + " dinner=" + $rf.after.dinner)
                    Check "dinner shift hint shown" ($rf.dinner_hint -ne "") ("hint=" + $rf.dinner_hint)
                    Check "refused button shows the active state" ([int]$rf.active_refused -ge 1) ("active_refused=" + $rf.active_refused)
                    Check "refused active state clears after eating" ([int]$rf.active_refused_after_undo -eq 0) ("active_refused_after_undo=" + $rf.active_refused_after_undo)
                    Check "clicking ate-it undoes the shift" (($rf.undone.dinner -eq $rf.before.dinner) -and ($rf.undone.lunch -eq $rf.before.lunch)) ("undone=" + $rf.undone.lunch + "/" + $rf.undone.dinner + " before=" + $rf.before.lunch + "/" + $rf.before.dinner)
                    Check "fast mode explains the fixed single-day plan" ($rf.days_locked -ne "") ("days_locked='" + $rf.days_locked + "'")
                    Check "fast mode hides the day slider" ([int]$rf.days_range_hidden -eq 1) ("range_hidden=" + $rf.days_range_hidden)
                }
            }
            if ($mode -eq "deviation") {
                Check "deviation modal opened" ($json.modal_open -eq 1) "no .modal-host in the frame"
                Check "deviation offers 3 strategies" ($json.modal_strategies -ge 3) ("strategies=" + $json.modal_strategies)
            }
            if ($mode -eq "mobile") {
                Check "narrow viewport uses the vertical timeline" ($json.tl_v -eq "flex" -and $json.tl_h -eq "none") ("tl_h=" + $json.tl_h + " tl_v=" + $json.tl_v)
                Check "timeline bars drawn (narrow)" ($json.timeline_bars -ge 4) ("bars=" + $json.timeline_bars)
                # 用户报的"手机版时间图很多不显示"：窄屏必须一行一条，行有高度、名称不被裁、小条有最小可见长度
                Check "narrow timeline renders one row per item" ($json.tl_today.variant -eq "v" -and [double]$json.tl_today.min_item_h -ge 14) ("variant=" + $json.tl_today.variant + " min_h=" + $json.tl_today.min_item_h)
                Check "narrow timeline labels are readable (not clipped)" ($json.tl_today.labels_clipped -eq 0) ("clipped=" + $json.tl_today.labels_clipped)
                Check "narrow meal bars keep a visible minimum length" ([double]$json.tl_today.min_bar_w -ge 3) ("min_bar_w=" + $json.tl_today.min_bar_w)
                Check "narrow mini timelines readable (labels + bars)" ($json.tl_mini.variant -eq "v" -and $json.tl_mini.labels_clipped -eq 0 -and [double]$json.tl_mini.min_bar_w -ge 3) ("variant=" + $json.tl_mini.variant + " clipped=" + $json.tl_mini.labels_clipped + " min_bar_w=" + $json.tl_mini.min_bar_w)
                $vw = [int]($json.viewport -split "x")[0]
                Check "no horizontal overflow at $vw" ([int]$json.doc_w -le $vw + 2) ("doc_w=" + $json.doc_w + " offenders=" + (@($json.overflow) -join ", "))
            }
        }
    }
}

if (-not $BrowserOnly) {
Write-Host "== phase B: restart backend with the default 30s write cooldown, run e2e =="
Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
Stop-Listener $BackendPort
Remove-Item (Join-Path $accept "app.db*") -Force -ErrorAction SilentlyContinue
$backend = Start-Backend -Tag "2"
Check "backend /healthz ready (default cooldown)" (Wait-Http "http://127.0.0.1:$BackendPort/healthz" 40) "see .tmp\accept\srv2.err"
& $Python (Join-Path $Root "tests\e2e_http.py") $base | Out-Host
Check "e2e suite (through nginx, default cooldown)" ($LASTEXITCODE -eq 0)
}

if (-not $KeepRunning) {
    Write-Host "== stop servers =="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "deploy\serve_local.ps1") -Stop | Write-Host
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    Stop-Listener $BackendPort
    Stop-Listener $NginxPort
}

Write-Host ""
if ($script:failed -eq 0) { Write-Host "local acceptance: ALL PASS"; exit 0 }
Write-Host ("local acceptance: " + $script:failed + " check(s) failed")
exit 1
