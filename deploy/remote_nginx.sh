set -euo pipefail
# ============================================================================
#  Sleep Schedule - nginx vhost installation (sub-path aware)
#
#  Layout served:  http://<server-ip>/sleep/        user app
#                  http://<server-ip>/sleep/admin/  admin console
#                  http://<server-ip>/sleep/api/    FastAPI on 127.0.0.1:8011
#
#  Static files are copied to /var/www/sleep-schedule/ and requested by the
#  browser through relative URLs, so no response rewriting is needed.
#  The site prefix defaults to /sleep/schedule but is detected automatically
#  from the existing vhost when one is already present.
# ============================================================================

: "${SUDO_PASS:?请先 export SUDO_PASS=<服务器 sudo 密码>}"
PASS="$SUDO_PASS"
sudo_run() { echo "$PASS" | sudo -S -p '' "$@"; }

WEBROOT=/var/www
APP_DIR="$WEBROOT/sleep-schedule"
VHOST=/etc/nginx/sites-available/sleep-schedule
BACKUP="$VHOST.dsh-backup"

# ---------------------------------------------------------------- prefix ----
PREFIX=""
if [ -f "$VHOST" ]; then
    for candidate in $(grep -vE '^\s*#' "$VHOST" | grep -oE '/[A-Za-z0-9_.-]+/api/' | sed 's#/api/$##' | sort -u); do
        if curl -s -m 5 -o /dev/null "http://127.0.0.1${candidate}/api/auth/admin/parts" \
           && curl -s -m 5 "http://127.0.0.1${candidate}/api/auth/admin/parts" | grep -q '"parts"'; then
            PREFIX="$candidate"
            break
        fi
        # a wrong prefix lands on the SPA catch-all, which answers with HTML
        if curl -s -m 5 "http://127.0.0.1${candidate}/api/auth/admin/parts" | grep -qi '<!DOCTYPE html'; then
            PREFIX="$candidate"
            break
        fi
    done
fi
PREFIX="${PREFIX:-/sleep}"

echo "site prefix        : $PREFIX"
echo "static directory   : $APP_DIR"

# ------------------------------------------------------------------ files ----
printf 'static files       : '
find "$APP_DIR" -type f | wc -l

# ---------------------------------------------------------------- vhost ------
TMP_CFG=$(mktemp)
cat >"$TMP_CFG" <<NGINX
# Sleep Schedule - user app and administrator console.
#
#   ${PREFIX}/        -> ${APP_DIR}/user/
#   ${PREFIX}/admin/  -> ${APP_DIR}/admin/
#   ${PREFIX}/shared/ -> ${APP_DIR}/shared/
#   ${PREFIX}/api/    -> FastAPI application on 127.0.0.1:8011 (prefix stripped)
#
# Frontend assets reference each other through relative URLs, so nginx only has
# to map locations onto the static directory; nothing is rewritten in transit.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root ${WEBROOT};

    # Redirect the bare prefix to its directory form.
    location = ${PREFIX} {
        return 301 ${PREFIX}/;
    }

    # API: the trailing slash on proxy_pass makes nginx replace the matched
    # location prefix with "/api/", so the app receives /api/auth/... rather
    # than ${PREFIX}/api/auth/...
    location ${PREFIX}/api/ {
        proxy_pass http://127.0.0.1:8011/api/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        client_max_body_size 1m;
        add_header X-Served-By sleep-schedule always;
    }

    # User app entry point.  An internal rewrite is used instead of alias/index
    # because nginx resolves alias + index inside an exact-match location
    # against the server root, producing a spurious 404.
    location = ${PREFIX}/ {
        rewrite ^${PREFIX}/\$ ${PREFIX}/user/index.html last;
    }

    # Admin console entry point (PC only).
    location = ${PREFIX}/admin/ {
        rewrite ^${PREFIX}/admin/\$ ${PREFIX}/admin/index.html last;
    }

    # Static assets: ${PREFIX}/<anything> -> ${APP_DIR}/<anything>
    location ${PREFIX}/ {
        rewrite ^${PREFIX}/(.*)\$ /sleep-schedule/\$1 break;
    }

    # The bare host name greets visitors with the user app.
    location = / {
        return 302 ${PREFIX}/;
    }
}
NGINX

if [ -f "$VHOST" ]; then
    sudo_run cp -n "$VHOST" "$BACKUP" || true
fi

echo "$PASS" | sudo -S -p '' install -m 0644 -o root -g root "$TMP_CFG" "$VHOST"
rm -f "$TMP_CFG"

if ! sudo_run nginx -t; then
    echo "!! nginx config test failed - restoring the previous vhost"
    if [ -f "$BACKUP" ]; then
        echo "$PASS" | sudo -S -p '' cp "$BACKUP" "$VHOST"
        sudo_run nginx -t
    fi
    exit 1
fi
sudo_run systemctl reload nginx
sleep 1

# ------------------------------------------------------------- verification --
fail=0
check() {  # check <label> <url> <expected-status> [required-body-substring]
    local label="$1" url="$2" want="$3" needle="${4:-}"
    local body status
    body=$(curl -s -m 10 "$url" || true)
    status=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$url" || true)
    if [ "$status" != "$want" ]; then
        printf '  FAIL %-26s %s (want %s)\n' "$label" "$status" "$want"
        fail=$((fail + 1))
        return
    fi
    if [ -n "$needle" ] && ! printf '%s' "$body" | grep -q -- "$needle"; then
        printf '  FAIL %-26s body missing %s\n' "$label" "$needle"
        fail=$((fail + 1))
        return
    fi
    printf '  ok   %-26s %s\n' "$label" "$status"
}

echo
echo "verification (http://127.0.0.1):"
check "${PREFIX}/"                 "http://127.0.0.1${PREFIX}/"                 200 'data-theme'
check "${PREFIX}/admin/"           "http://127.0.0.1${PREFIX}/admin/"           200 'admin-shell'
check "user css"                   "http://127.0.0.1${PREFIX}/user/css/app.css" 200 '--accent'
check "admin css"                  "http://127.0.0.1${PREFIX}/admin/css/admin.css" 200 'admin-shell'
check "shared base.css"            "http://127.0.0.1${PREFIX}/shared/base.css"  200 '--bg'
check "user main.js"               "http://127.0.0.1${PREFIX}/user/js/main.js"  200 'shared/api.js'
check "admin app.js"               "http://127.0.0.1${PREFIX}/admin/js/app.js"  200 'shared/api.js'
check "shared i18n.js"             "http://127.0.0.1${PREFIX}/shared/i18n.js"   200 'normalizeLang'
check "favicon"                    "http://127.0.0.1${PREFIX}/user/favicon.svg" 200 '<svg'
check "api parts (json)"           "http://127.0.0.1${PREFIX}/api/auth/admin/parts" 200 '"parts"'
check "api me (401 json)"          "http://127.0.0.1${PREFIX}/api/auth/me"      401 'error.not_authenticated'
check "healthz"                    "http://127.0.0.1${PREFIX}/api/../healthz"   404 ''
printf '  ..   %-26s %s\n' "root redirect" "$(curl -s -m 10 -o /dev/null -w '%{http_code} -> %{redirect_url}' http://127.0.0.1/ || true)"

echo
if [ "$fail" -eq 0 ]; then
    echo "nginx installation: OK"
else
    echo "nginx installation: $fail check(s) failed"
    exit 1
fi
