#!/usr/bin/env bash
# Server-side installer for the sleep-schedule web app.
# Run as the unprivileged deploy user; privileged steps go through sudo.
set -euo pipefail

: "${SUDO_PASS:?请先 export SUDO_PASS=<服务器 sudo 密码>}"
PASS="$SUDO_PASS"
APP_DIR=/opt/sleep-schedule
RUN_USER=sleepapp
SERVICE=sleep-schedule
DATA_DIR=/var/lib/sleep-schedule
LOG_DIR=/var/log/sleep-schedule
BUNDLE=/tmp/sleep-schedule-bundle.tar.gz
PORT=8011

log() { printf '\n=== %s ===\n' "$*"; }
sudo_run() { echo "$PASS" | sudo -S -p '' "$@"; }

log "0. stop old service (if any)"
sudo_run systemctl stop "$SERVICE" 2>/dev/null || true

log "1. users and directories"
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  sudo_run useradd --system --create-home --home-dir /home/$RUN_USER --shell /usr/sbin/nologin "$RUN_USER"
fi
sudo_run mkdir -p "$APP_DIR" "$DATA_DIR" "$LOG_DIR"
sudo_run chown -R "$RUN_USER:$RUN_USER" "$APP_DIR" "$DATA_DIR" "$LOG_DIR"

log "2. unpack application bundle"
sudo_run rm -rf "$APP_DIR/sleep-schedule"   # stale dir from an earlier bundle layout
sudo_run tar -xzf "$BUNDLE" -C "$APP_DIR"
sudo_run chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"
sudo_run ls -la "$APP_DIR"

log "3. install python dependencies into the app virtualenv"
# --without-pip: this host has no python3-venv/ensurepip, and dependencies are
# installed straight from the bundled wheel cache instead of PyPI.
sudo_run rm -rf "$APP_DIR/.venv"
sudo_run python3 -m venv --without-pip "$APP_DIR/.venv"
sudo_run "$APP_DIR/.venv/bin/python" -V
sudo_run "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/bootstrap_deps.py" \
  --target "$APP_DIR/.venv/lib/python3.12/site-packages" \
  --wheels "$APP_DIR/.wheels"
sudo_run chown -R "$RUN_USER:$RUN_USER" "$APP_DIR/.venv"

log "3b. verify the installed runtime"
if sudo_run -u "$RUN_USER" -H "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/check_env.py"; then
  echo "dependency check: OK"
else
  echo "dependency check FAILED - see the output above"
  exit 1
fi

log "4. create the database schema and the built-in administrator"
sudo_run -u "$RUN_USER" -H env \
  SSP_DATA_DIR="$DATA_DIR" \
  SSP_DB_PATH="$DATA_DIR/app.db" \
  SSP_LOG_DIR="$LOG_DIR" \
  SSP_LOG_FILE="$LOG_DIR/app.log" \
  "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/seed_admin.py" --bootstrap-admin
sudo_run -u "$RUN_USER" -H env \
  SSP_DATA_DIR="$DATA_DIR" \
  SSP_DB_PATH="$DATA_DIR/app.db" \
  SSP_LOG_DIR="$LOG_DIR" \
  SSP_LOG_FILE="$LOG_DIR/app.log" \
  "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/seed_admin.py" --check

log "5. systemd service"
# The unit is written to a temp file first: `sudo -S` consumes stdin for the
# password, so piping a heredoc through sudo would swallow the unit body.
unit_tmp=$(mktemp)
cat >"$unit_tmp" <<UNIT
[Unit]
Description=Sleep Schedule Advisor API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR/backend
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=SSP_HOST=127.0.0.1
Environment=SSP_PORT=$PORT
Environment=SSP_DATA_DIR=$DATA_DIR
Environment=SSP_DB_PATH=$DATA_DIR/app.db
Environment=SSP_LOG_DIR=$LOG_DIR
Environment=SSP_LOG_FILE=$LOG_DIR/app.log
Environment=SSP_LOG_LEVEL=INFO
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1 --no-server-header
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
sudo_run install -m 0644 -o root -g root "$unit_tmp" /etc/systemd/system/$SERVICE.service
rm -f "$unit_tmp"
sudo_run systemd-analyze verify /etc/systemd/system/$SERVICE.service
sudo_run systemctl daemon-reload
sudo_run systemctl enable "$SERVICE" >/dev/null
sudo_run systemctl restart "$SERVICE"
sleep 3
sudo_run systemctl is-active "$SERVICE"
if ! sudo_run systemctl is-active --quiet "$SERVICE"; then
  echo "service failed to start; last journal lines:"
  sudo_run journalctl -u "$SERVICE" -n 40 --no-pager
  exit 1
fi

log "5b. smoke-check the API on the local port"
printf 'healthz    : '; curl -s -m 10 -w ' [%{http_code}]\n' "http://127.0.0.1:$PORT/healthz"
printf 'admin parts: '; curl -s -m 10 -w ' [%{http_code}]\n' "http://127.0.0.1:$PORT/api/auth/admin/parts"
printf 'anonymous  : '; curl -s -m 10 -o /dev/null -w '[%{http_code}]\n' "http://127.0.0.1:$PORT/api/auth/me"

log "6. static files for nginx"
sudo_run mkdir -p /var/www/sleep-schedule
sudo_run cp -r "$APP_DIR/frontend/." /var/www/sleep-schedule/
sudo_run chown -R www-data:www-data /var/www/sleep-schedule

log "7. nginx virtual host on port 80 (deploy/remote_nginx.sh)"
if [ -f "$APP_DIR/deploy/remote_nginx.sh" ]; then
  bash "$APP_DIR/deploy/remote_nginx.sh"
  if [ -e /etc/nginx/sites-enabled/default ]; then
    echo "(disabling the stock nginx default site)"
    sudo_run rm -f /etc/nginx/sites-enabled/default
    sudo_run nginx -t && sudo_run systemctl reload nginx
  fi
else
  echo "!! $APP_DIR/deploy/remote_nginx.sh not found - nginx not configured"
fi

log "8. local verification"
printf 'healthz    : '; curl -s -m 10 -o /dev/null -w '[%{http_code}]\n' "http://127.0.0.1:$PORT/healthz"
printf 'api me     : '; curl -s -m 10 -o /dev/null -w '[%{http_code}]\n' "http://127.0.0.1:$PORT/api/auth/me"
printf 'via 80     : '; curl -s -m 10 -o /dev/null -w '[%{http_code}]\n' "http://127.0.0.1/sleep/"
printf 'asset      : '; curl -s -m 10 -o /dev/null -w '[%{http_code}]\n' "http://127.0.0.1/sleep/shared/base.css"
printf 'api via 80 : '; curl -s -m 10 -w ' [%{http_code}]\n' "http://127.0.0.1/sleep/api/auth/admin/parts"
log "done"
