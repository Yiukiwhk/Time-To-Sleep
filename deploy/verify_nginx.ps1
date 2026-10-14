# Assert that deploy/nginx-sleep-schedule.conf routes everything the frontends
# need.  Uses deploy/serve_local.ps1 to run a portable nginx on 127.0.0.1:8082.
#
#   .\deploy\verify_nginx.ps1                 # routing only (API expects 502)
#   .\deploy\verify_nginx.ps1 -WithBackend    # API checks expect real JSON
param(
    # Defaults to this checkout (deploy/ lives in the repo root), so a fresh clone runs as-is.
    [string]$Root,
    [int]$Port = 8082,
    [string]$Upstream = "127.0.0.1:18011",
    [switch]$WithBackend,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"

if (-not $Root) {
    $derived = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $derived "run.py")) { $Root = $derived }
    elseif (Test-Path -LiteralPath (Join-Path (Get-Location).Path "run.py")) { $Root = (Get-Location).Path }
    else { throw "Cannot locate the project root (no run.py found). Pass -Root <path-to-checkout>." }
}

& (Join-Path $Root "deploy\serve_local.ps1") -Root $Root -Port $Port -Upstream $Upstream -Start | Out-Host

try {
    # /sleep/api/ is longer than /sleep/, so the old-prefix API stays proxied
    # instead of being 302'd to the new prefix.
    if ($WithBackend) {
        $apiCases = @(
            @("/api/auth/admin/parts", "200", "parts"),
            @("/api/auth/me", "401", "error.not_authenticated"),
            @("/sleep/api/auth/me", "401", "error.not_authenticated"),
            @("/healthz", "200", "ok"), @("/sleep/healthz", "200", "ok")
        )
    }
    else {
        $apiCases = @(
            @("/api/auth/me", "502", ""), @("/healthz", "502", ""),
            @("/sleep/api/auth/me", "502", ""), @("/sleep/healthz", "502", "")
        )
    }

    $cases = @(
        # 站点入口
        @("/", "302", ""), @("/nope", "200", "login-form"),
        # 用户端
        @("/user/", "200", "login-form"), @("/user", "301", ""),
        @("/user/index.html", "200", "login-form"), @("/user/nope", "200", "login-form"),
        @("/user/css/app.css", "200", "app-shell"),
        @("/user/js/main.js", "200", "/shared/api.js"),
        @("/user/js/pages/records.js", "200", "/shared/api.js"),
        @("/user/js/pages/advice.js", "200", "/shared/i18n.js"),
        @("/user/js/pages/settings.js", "200", "/shared/ui.js"),
        @("/user/favicon.svg", "200", "<svg"),
        @("/favicon.ico", "200", "<svg"),
        # 真正缺失的静态资源必须 404：绝不能以 200 + text/html 落回 SPA 壳，
        # 否则浏览器只会报模块 MIME 类型错误，排查成本极高。
        @("/user/nothing.css", "404", ""),
        @("/user/nothing.js", "404", ""),
        @("/shared/nothing.js", "404", ""),
        # 旧版模块相对自身解析出来的地址（/user/shared/... 与 /admin/shared/...）
        # 由 ^~ alias 兼容，保证缓存里的旧模块仍能加载。
        @("/user/shared/api.js", "200", "API_PREFIX"),
        @("/user/shared/i18n.js", "200", "normalizeLang"),
        @("/admin/shared/api.js", "200", "API_PREFIX"),
        @("/admin/shared/ui.js", "200", "openModal"),
        # 共用模块
        @("/shared/base.css", "200", "--bg"),
        @("/shared/api.js", "200", "API_PREFIX"),
        @("/shared/i18n.js", "200", "normalizeLang"),
        @("/shared/ui.js", "200", "openModal"),
        @("/shared/timepicker.js", "200", "createTimePicker"),
        # 管理端
        @("/admin/", "200", "admin-shell"), @("/admin", "301", ""),
        @("/admin/index.html", "200", "admin-shell"), @("/admin/nope", "200", "admin-shell"),
        @("/admin/css/admin.css", "200", "admin-shell"),
        @("/admin/js/app.js", "200", "/shared/api.js"),
        @("/admin/js/state.js", "200", "/shared/i18n.js"),
        @("/admin/js/pages/users.js", "200", "/shared/i18n.js"),
        @("/admin/js/pages/userDetail.js", "200", "/shared/i18n.js"),
        @("/admin/js/pages/requests.js", "200", "/shared/ui.js"),
        @("/admin/js/pages/logs.js", "200", "/shared/i18n.js"),
        @("/admin/js/pages/dashboard.js", "200", "/shared/ui.js"),
        @("/admin/nothing.css", "404", ""),
        @("/admin/nothing.js", "404", ""),
        # 旧地址兼容
        @("/sleep", "302", ""), @("/sleep/", "302", ""), @("/sleep/admin", "302", ""),
        @("/sleep/admin/", "302", ""), @("/sleep/user/", "302", ""), @("/sleep/shared/base.css", "302", "")
    )

    $bad = 0
    foreach ($c in ($cases + $apiCases)) {
        $code = & curl.exe -s -m 8 -o NUL -w "%{http_code}" "http://127.0.0.1:$Port$($c[0])"
        $has = $true
        if ($c[2] -ne "" -and $code -eq "200") {
            $body = & curl.exe -s -m 8 "http://127.0.0.1:$Port$($c[0])"
            $has = ($body -join "`n") -match [regex]::Escape($c[2])
        }
        $ok = ($code -eq $c[1]) -and $has
        if (-not $ok) { $bad++ }
        Write-Host ("  {0} {1,-32} {2} (want {3}){4}" -f $(if ($ok) { "ok  " } else { "FAIL" }), $c[0], $code, $c[1], $(if ($has) { "" } else { "  missing '$($c[2])'" }))
    }
    Write-Host ""
    if ($bad -eq 0) { Write-Host "nginx routing: OK" } else { Write-Host "nginx routing: $bad check(s) failed" }
}
finally {
    if (-not $KeepRunning) { & (Join-Path $Root "deploy\serve_local.ps1") -Root $Root -Stop | Out-Null }
}
exit $bad
