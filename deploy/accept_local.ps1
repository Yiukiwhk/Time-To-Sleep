# Local end-to-end acceptance for the advice feature (single command):
#   1) start the backend on a scratch DB in .tmp/accept (write cooldown disabled)
#   2) start the portable nginx (mirrors frontend/, listens on 8082, production vhost)
#   3) static/copy self checks + module graph + smoke (all through nginx)
#   4) headless Edge opens a same-origin probe page: seeds data, logs in, renders the
#      advice page, screenshots it, and reports the rendered DOM back through
#      /healthz?ui=... (lands in the backend access log) for scripted assertions
#   5) restart the backend with the DEFAULT 10s write cooldown on a fresh DB and run
#      tests/e2e_http.py (it asserts that a second write within 10s is rejected)
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

# Phase A runs with the 10s per-user write cooldown disabled so the probe page can seed
# five days of records back to back. The e2e suite asserts that the cooldown DOES reject a
# second write within 10s, so phase B restarts the backend with the default cooldown.
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
            # 用户需求：登录失败要预警"还剩几次机会"；第 5 次失败直接锁定，文案改成锁定倒计时
            if ($json.login_warning) {
                $lw = $json.login_warning
                $hasRemaining = ($null -ne $lw.remaining) -and ([int]$lw.remaining -ge 0) -and ([int]$lw.max -ge 1)
                Check "[$mode] failed login reports remaining attempts" (($lw.status -eq 401) -and $hasRemaining) ("warning=" + ($lw | ConvertTo-Json -Compress))
                Check "[$mode] login warning text shows the remaining tries" ("$($lw.text)" -match "还可尝试|attempt") ("text=" + $lw.text)
                Check "[$mode] lock-out text replaces the countdown" ("$($json.login_locked_text)" -match "锁定|locked") ("text=" + $json.login_locked_text)
                # 用户需求：登录成功后失败计数要清零（连错两次 → 成功登录 → 再错一次应回到满额）
                $lr = $json.login_reset
                Check "[$mode] a successful login clears the failure count" (
                    ($null -ne $lr) -and ([int]$lr.relogin -eq 200) -and
                    ([int]$lr.after -eq ([int]$lr.max - 1)) -and ([int]$lr.after -gt [int]$lr.before)
                ) ("login_reset=" + ($lr | ConvertTo-Json -Compress))
            } else {
                Check "[$mode] probe reported the login warning" $false "login_warning missing from the render summary"
            }
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
            # 现实作息窗口（用户需求）：探针作息（00:30 睡 / 08:30 起，目标 23:00/07:00）全程正常，
            # 所以计划里每一行的午餐都要落在 11:00–15:00、晚餐落在 17:00–22:00。
            # 作息越界的用例不在这里断言（那时算法会回落到相对锚点，出窗才是对的）。
            if (($adviceModes -contains $mode) -and $json.meals_api) {
                $rows = @($json.meals_api)
                $outOfWindow = @()
                $toMinutes = {
                    param($clock)
                    $parts = "$clock".Split(":")
                    if ($parts.Count -lt 2) { return -1 }
                    return ([int]$parts[0] * 60 + [int]$parts[1])
                }
                foreach ($row in $rows) {
                    $lunch = & $toMinutes $row.lunch
                    $dinner = & $toMinutes $row.dinner
                    $wake = & $toMinutes $row.wake
                    $sleep = & $toMinutes $row.sleep
                    $normal = ($wake -le 900) -and (($sleep -le 360) -or ($sleep -ge 1020))
                    if (-not $normal) { continue }
                    if (($lunch -lt 660) -or ($lunch -gt 900) -or ($dinner -lt 1020) -or ($dinner -gt 1320)) {
                        $outOfWindow += ("wake=" + $row.wake + " sleep=" + $row.sleep + " lunch=" + $row.lunch + " dinner=" + $row.dinner)
                    }
                }
                Check "[$mode] lunch snaps into 11:00-15:00 and dinner into 17:00-22:00" ($outOfWindow.Count -eq 0) ("rows=" + $rows.Count + " out=" + ($outOfWindow -join "; "))
                if ($mode -eq "base") {
                    # 今日卡片必须显示接口给的同一天饭点（接口是第一行，卡片就是第一行）
                    Check "today card meal times match the API" (($json.today_meals_dom.lunch -eq $rows[0].lunch) -and ($json.today_meals_dom.dinner -eq $rows[0].dinner)) ("dom=" + $json.today_meals_dom.lunch + "/" + $json.today_meals_dom.dinner + " api=" + $rows[0].lunch + "/" + $rows[0].dinner)
                }
            }
            if ($mode -eq "base") {
                Check "timeline bars drawn" ($json.timeline_bars -ge 4) ("bars=" + $json.timeline_bars)
                # 「现在」标线是有条件的：只有当前钟点落在时间轴窗口内才该画出来。
                # 时间轴可能跨零点（例如 06:04 → 02:04），所以先把"现在"换算到
                # "距轴起点多少分钟"，再看它有没有超出 span——轴外的凌晨时段
                # （02:04–06:04 这种）本来就不该有标线，画了才是 bug。
                $axis = $json.timeline_axis
                $axisStart = -1
                if ($axis -and $axis.start) {
                    $axisParts = "$($axis.start)".Split(":")
                    if ($axisParts.Count -ge 2) { $axisStart = [int]$axisParts[0] * 60 + [int]$axisParts[1] }
                }
                $axisSpan = 0
                if ($axis -and $axis.span) { $axisSpan = [int]$axis.span }
                if ($axisStart -lt 0 -or $axisSpan -le 0) {
                    Check "now marker reported with its axis window" $false "timeline_axis missing from the render summary"
                }
                else {
                    $now = Get-Date
                    $nowMinutes = $now.Hour * 60 + $now.Minute
                    $nowRel = ((($nowMinutes - $axisStart) % 1440) + 1440) % 1440
                    $axisInfo = "now=" + $json.timeline_now + " axis=" + $axis.start + ".." + $axis.end + " span=" + $axisSpan + " clock=" + $now.ToString("HH:mm")
                    if ($nowRel -le $axisSpan) {
                        Check "now marker drawn" ($json.timeline_now -ge 1) $axisInfo
                    }
                    else {
                        # 钟点在轴外：反过来断言标线没有出现（出现就说明画错位置了）
                        Check "now marker hidden outside the axis window" ([int]$json.timeline_now -eq 0) $axisInfo
                    }
                }
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
                    # 三餐时间是 "HH:MM"，必须换算成分钟再比（字符串比较连格式差异都判不出来）。
                    # 午餐可能本来就停在现实窗口下限 11:00，此时"不想吃早餐"再没有可提前的空间，
                    # 算法只保证不把它推后，所以断言写成"不推后 + 有余量时才真的提前"。
                    $toMin = {
                        param($t)
                        if (-not $t) { return -1 }
                        $parts = "$t".Split(":")
                        return ([int]$parts[0]) * 60 + [int]$parts[1]
                    }
                    $bLunch = & $toMin $rf.before.lunch
                    $aLunch = & $toMin $rf.after.lunch
                    $bDinner = & $toMin $rf.before.dinner
                    $aDinner = & $toMin $rf.after.dinner
                    $windowFloor = 11 * 60
                    $shifts = @($rf.checkin_bodies | ForEach-Object { $_.lunch_shift_minutes })
                    $dinnerShifts = @($rf.checkin_bodies | ForEach-Object { $_.dinner_shift_minutes })
                    Check "today dinner moved 60 minutes earlier after refusing" ($bDinner -ge 0 -and $aDinner -eq ($bDinner - 60)) ("before=" + $rf.before.dinner + " after=" + $rf.after.dinner)
                    Check "today lunch did not move later after refusing" ($bLunch -ge 0 -and $aLunch -le $bLunch) ("before=" + $rf.before.lunch + " after=" + $rf.after.lunch)
                    Check "today lunch moved earlier when the window leaves room" ($bLunch -le $windowFloor -or $aLunch -lt $bLunch) ("before=" + $rf.before.lunch + " after=" + $rf.after.lunch)
                    # 午餐的位移是"阶梯 ⊕ 拒绝"的较大值（接口返回的就是最终生效的那个）：
                    # 探针账号本身带着连续跳过早餐的阶梯，所以这里只能断言 ≥60；
                    # 精确等于 60 的是晚餐那条（晚餐没有阶梯，拒绝就是 −60）。
                    Check "refusal reports a lunch shift of at least 60 minutes" (@($shifts | Where-Object { [int]$_ -ge 60 }).Count -ge 1) ("shifts=" + ($shifts -join ","))
                    Check "refusal reports a 60-minute dinner shift" ($dinnerShifts -contains 60) ("shifts=" + ($dinnerShifts -join ","))
                    Check "refusal is recorded for today only" (@($rf.checkin_bodies | Where-Object { $_.refused_breakfast_today -eq $true }).Count -ge 1) ("bodies=" + ($rf.checkin_bodies | ConvertTo-Json -Compress))
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
Write-Host "== phase B: restart backend with the default 10s write cooldown, run e2e =="
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
