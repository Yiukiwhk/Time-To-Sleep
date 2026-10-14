#!/usr/bin/env bash
set -e
: "${SUDO_PASS:?请先 export SUDO_PASS=<服务器 sudo 密码>}"
S="$SUDO_PASS"
sudo_run() { echo "$S" | sudo -S -p '' "$@"; }

# Drop leftovers from earlier probing.
sudo_run rm -f /etc/nginx/sites-enabled/zz-probe /etc/nginx/sites-available/zz-probe

SUDO_PASS="$S" bash /tmp/remote_nginx.sh

printf '\n=== gateway behaviour ===\n'
for u in /sleep/ /sleep/admin/ /sleep/api/auth/admin/parts /; do
    printf '%-32s ' "$u"
    curl -s -m 5 -o /tmp/b.out -w '[%{http_code}] %{size_download} %{content_type}\n' "http://127.0.0.1$u" || true
done

printf '\n=== error log (last 8) ===\n'
sudo_run tail -n 8 /var/log/nginx/error.log
