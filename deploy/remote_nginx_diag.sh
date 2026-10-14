set -u
: "${SUDO_PASS:?请先 export SUDO_PASS=<服务器 sudo 密码>}"
echo "=== /var/www tree ==="
ls -la /var/www/
ls -la /var/www/sleep-schedule/ 2>&1 | head
echo "=== nginx user ==="
grep -E "^user" /etc/nginx/nginx.conf
echo "=== nginx error log tail ==="
echo "$SUDO_PASS" | sudo -S -p '' tail -20 /var/log/nginx/error.log
echo "=== direct curl with verbose ==="
curl -s -m 10 -o /dev/null -w '%{http_code}\n' "http://127.0.0.1/sleep/index.html"
curl -s -m 10 -D - -o /dev/null "http://127.0.0.1/sleep/" | head -8
