#!/usr/bin/env bash
# Round 5: MIME matrix, UA matrix, host matrix, TLS check. No parentheses anywhere.
# DEPLOY_HOST (server address) must be exported; run_remote_check.ps1 does that.
DEPLOY_HOST="${DEPLOY_HOST:?请先 export DEPLOY_HOST=<服务器地址>}"
echo "=== A. exact file types via http://127.0.0.1 ==="
for p in /sleep/ /sleep /sleep/user/ /sleep/user/index.html /sleep/user/css/app.css /sleep/admin/ /sleep/admin/index.html /sleep/admin/css/admin.css /sleep/shared/base.css /sleep/shared/i18n.js /sleep/shared/ui.js /sleep/shared/api.js /sleep/shared/timepicker.js /sleep/user/js/main.js /sleep/user/favicon.svg ; do
    printf '  %-42s %s\n' "$p" "$(curl -s -m 8 -o /dev/null -w '%{http_code} %{content_type} %{size_download}' http://127.0.0.1$p)"
done

echo
echo "=== B. Firefox user agent ==="
for p in /sleep/user/css/app.css /sleep/shared/base.css /sleep/user/js/main.js ; do
    printf '  %-42s %s\n' "$p" "$(curl -s -m 8 -o /dev/null -w '%{http_code} %{content_type}' -A 'Mozilla/5.0 Windows NT 10.0 rv:128.0 Firefox/128.0' http://127.0.0.1$p)"
done

echo
echo "=== C. css/js requests recorded by nginx, last 15 ==="
sudo -n grep -E '\.css|\.js' /var/log/nginx/access.log 2>/dev/null | tail -15

echo
echo "=== D. Host header variations for base.css ==="
for h in "$DEPLOY_HOST" sleep _ ; do
    printf '  Host=%-18s %s\n' "$h" "$(curl -s -m 8 -o /dev/null -w '%{http_code} %{content_type}' -H "Host: $h" http://127.0.0.1/sleep/shared/base.css)"
done

echo
echo "=== E. TLS config presence and https behaviour ==="
grep -rn 'listen.*443' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head -5 || echo "no TLS listener configured"
curl -s -m 6 -o /dev/null -w 'https status=%{http_code}\n' "https://$DEPLOY_HOST/sleep/" 2>&1 || echo "https connection failed, as expected with no TLS listener"

echo
echo "=== F. does /sleep/something-not-a-file fall back to the SPA? ==="
for p in /sleep/records /sleep/undefined.css ; do
    printf '  %-26s %s\n' "$p" "$(curl -s -m 8 -o /dev/null -w '%{http_code} %{content_type}' http://127.0.0.1$p)"
done

