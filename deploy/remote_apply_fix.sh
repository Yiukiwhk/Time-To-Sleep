#!/usr/bin/env bash
# ============================================================================
#  Sleep Schedule - republish the frontend + install the root-based vhost.
#
#  Root cause of the blank UI: the pages used to reference "../shared/..." which
#  the browser normalises to "/shared/..." (dropping any path prefix), so the
#  ES modules 404ed and the whole module graph failed.  Pages now use site
#  absolute paths (/shared/..., /user/css/..., /admin/js/...) and the site is
#  served from the domain root, matching the backend's own mount points.
#
#  Expects the uploaded bundle at $STAGE (see deploy/apply_fix.ps1).
# ============================================================================
set -euo pipefail

: "${SUDO_PASS:?请先 export SUDO_PASS=<服务器 sudo 密码>}"
PASS="$SUDO_PASS"
sudo_run() { echo "$PASS" | sudo -S -p '' "$@"; }

STAGE="${STAGE:-/tmp/sleep-fix}"
WEBROOT=/var/www/sleep-schedule
APP_DIR=/opt/sleep-schedule
VHOST=/etc/nginx/sites-available/sleep-schedule
BACKUP="$VHOST.dsh-backup"

# Parse the whole file before touching anything: a syntax error half way through
# a release leaves the site in a mixed state (frontend copied, backend not).  The
# local development machine has no bash, so this is the first place it can run.
bash -n "$0" || { echo "!! syntax error in $0"; exit 1; }

echo "== 1. static files =="
test -d "$STAGE" || { echo "missing stage dir $STAGE"; exit 1; }
sudo_run mkdir -p "$WEBROOT"
for sub in user admin shared; do
    if [ -d "$STAGE/frontend/$sub" ]; then
        # --delete keeps the published tree identical to the source tree
        sudo_run rsync -a --delete "$STAGE/frontend/$sub/" "$WEBROOT/$sub/"
        printf '   %-7s %s files\n' "$sub" "$(find "$WEBROOT/$sub" -type f | wc -l)"
    fi
done
sudo_run chown -R www-data:www-data "$WEBROOT"
sudo_run find "$WEBROOT" -type d -exec chmod 755 {} +
sudo_run find "$WEBROOT" -type f -exec chmod 644 {} +
# probe pages used while diagnosing the blank UI
sudo_run rm -f "$WEBROOT/zz-probe.html" "$WEBROOT/bootcheck.html" "$WEBROOT/sleep-schedule/zz-probe.html"

echo "== 2. application tree (frontend) =="
# NOTE: never use plain `rsync -a` here.  The stage directory is uploaded with
# `scp -r`, which leaves 0700 admin-owned directories; `-a` would copy those modes
# onto $APP_DIR/frontend and the sleepapp service user could no longer traverse it
# ("PermissionError: [Errno 13] Permission denied: .../frontend/shared" on boot).
if [ -d "$STAGE/frontend" ]; then
    sudo_run rsync -rlt --delete --no-owner --no-group --chmod=D755,F644 "$STAGE/frontend/" "$APP_DIR/frontend/"
    sudo_run chown -R sleepapp:sleepapp "$APP_DIR/frontend"
    sudo_run chmod -R a+rX "$APP_DIR/frontend"
fi

# Python modules shipped by a release: the adaptive-v2 advice engine, the auth
# guard (login-failure warning), the records cooldown and the config defaults.
# Must stay in sync with $backendFiles in deploy/apply_fix.ps1.
BACKEND_FILES="app/api/admin.py app/api/advice.py app/api/auth.py app/api/records.py app/config.py app/core/timing.py app/db.py app/models.py app/schemas.py app/services/advice.py app/services/advice_plan.py app/services/auth_service.py"

echo "== 2b. backend code =="
BACKEND_BACKUP=/tmp/sleep-backend-backup
if [ -d "$STAGE/backend" ]; then
    sudo_run rm -rf "$BACKEND_BACKUP"
    for rel in $BACKEND_FILES; do
        if [ -f "$APP_DIR/backend/$rel" ]; then
            sudo_run mkdir -p "$BACKEND_BACKUP/$(dirname "$rel")"
            sudo_run cp -a "$APP_DIR/backend/$rel" "$BACKEND_BACKUP/$rel"
        fi
    done
    sudo_run rsync -rlt --no-owner --no-group --chmod=D755,F644 "$STAGE/backend/" "$APP_DIR/backend/"
    sudo_run chown -R sleepapp:sleepapp "$APP_DIR/backend"
    sudo_run chmod -R a+rX "$APP_DIR/backend"
    printf '   synced %s module(s); rollback copy in %s\n' "$(printf '%s\n' $BACKEND_FILES | wc -l)" "$BACKEND_BACKUP"
else
    echo "   no backend payload in stage (frontend-only release)"
fi

echo "== 2c. restart service and wait for health =="
sudo_run systemctl restart sleep-schedule
healthy=0
for _ in $(seq 1 25); do
    if curl -fsS -m 3 http://127.0.0.1:8011/healthz >/dev/null 2>&1; then healthy=1; break; fi
    sleep 1
done
if [ "$healthy" != "1" ]; then
    echo "!! service did not become healthy; rolling back backend modules"
    if [ -d "$BACKEND_BACKUP" ]; then
        sudo_run rsync -rlt --no-owner --no-group --chmod=D755,F644 "$BACKEND_BACKUP/" "$APP_DIR/backend/"
    fi
    # the failure may have been caused by directory modes rather than by the code
    sudo_run chown -R sleepapp:sleepapp "$APP_DIR/backend" "$APP_DIR/frontend"
    sudo_run chmod -R a+rX "$APP_DIR/backend" "$APP_DIR/frontend"
    sudo_run systemctl restart sleep-schedule
    sleep 4
    if curl -fsS -m 3 http://127.0.0.1:8011/healthz >/dev/null 2>&1; then
        echo "   rollback ok: service healthy again on the previous build"
    else
        echo "   !! rollback did not restore health - check journalctl -u sleep-schedule"
    fi
    sudo_run journalctl -u sleep-schedule -n 40 --no-pager || true
    exit 1
fi
echo "   sleep-schedule: healthy after restart"

echo "== 3. vhost =="
test -f "$STAGE/nginx-sleep-schedule.conf" || { echo "missing vhost in stage"; exit 1; }
if [ -f "$VHOST" ]; then sudo_run cp -n "$VHOST" "$BACKUP" || true; fi
sudo_run install -m 0644 -o root -g root "$STAGE/nginx-sleep-schedule.conf" "$VHOST"
sudo_run ln -sf "$VHOST" /etc/nginx/sites-enabled/sleep-schedule
sudo_run rm -f /etc/nginx/sites-enabled/default
if ! sudo_run nginx -t; then
    echo "!! nginx -t failed, restoring backup"
    if [ -f "$BACKUP" ]; then
        sudo_run cp "$BACKUP" "$VHOST"
        sudo_run nginx -t
    fi
    exit 1
fi
sudo_run systemctl reload nginx
sleep 1
sudo_run systemctl is-active --quiet sleep-schedule && echo "   sleep-schedule service: active" || echo "   !! sleep-schedule service not active"

echo "== 4. verification (http://127.0.0.1) =="
fail=0
check() {  # check <label> <url> <want-status> [needle]
    local label="$1" url="$2" want="$3" needle="${4:-}"
    local body status hits
    body=$(curl -s -m 10 "$url" || true)
    status=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$url" || true)
    if [ "$status" != "$want" ]; then
        printf '  FAIL %-30s %s (want %s)\n' "$label" "$status" "$want"; fail=$((fail + 1)); return
    fi
    # NOTE: use `grep -c`, not `grep -q`: with `set -o pipefail` the early exit of
    # `grep -q` makes the feeding printf die of SIGPIPE, so the pipeline reports
    # failure even though the needle was found (this produced a bogus FAIL for a
    # 38 KB module whose match sits in the first line).
    if [ -n "$needle" ]; then
        hits=$(printf '%s' "$body" | grep -c -- "$needle" || true)
        if [ "${hits:-0}" -lt 1 ]; then
            printf '  FAIL %-30s body missing %s\n' "$label" "$needle"; fail=$((fail + 1)); return
        fi
    fi
    printf '  ok   %-30s %s\n' "$label" "$status"
}

check "root -> /user/"              "http://127.0.0.1/"                          302 ''
check "user index"                  "http://127.0.0.1/user/"                     200 'data-theme'
check "user index: /shared/base.css" "http://127.0.0.1/user/"                    200 '/shared/base.css'
check "user index: no ../shared"    "http://127.0.0.1/user/"                     200 'login-form'
check "user app.css"                "http://127.0.0.1/user/css/app.css"          200 '--accent'
check "user main.js"                "http://127.0.0.1/user/js/main.js"           200 '/shared/api.js'
check "user main.js is absolute"    "http://127.0.0.1/user/js/main.js"           200 'from "/shared/api.js"'
check "user favicon"                "http://127.0.0.1/user/favicon.svg"          200 '<svg'
check "admin index"                 "http://127.0.0.1/admin/"                    200 'admin-shell'
check "admin index: /shared/base"   "http://127.0.0.1/admin/"                    200 '/shared/base.css'
check "admin admin.css"             "http://127.0.0.1/admin/css/admin.css"       200 'admin-shell'
check "admin app.js"                "http://127.0.0.1/admin/js/app.js"           200 '/shared/api.js'
check "admin app.js is absolute"    "http://127.0.0.1/admin/js/app.js"           200 'from "/shared/api.js"'
check "admin users.js"              "http://127.0.0.1/admin/js/pages/users.js"   200 '/shared/i18n.js'
check "shared base.css"             "http://127.0.0.1/shared/base.css"           200 '--bg'
check "shared api.js"               "http://127.0.0.1/shared/api.js"             200 'API_PREFIX'
check "shared i18n.js"              "http://127.0.0.1/shared/i18n.js"            200 'normalizeLang'
check "shared ui.js"                "http://127.0.0.1/shared/ui.js"              200 'openModal'
# adaptive-v2 advice page: new modules + new API surface
check "shared timeline.js"          "http://127.0.0.1/shared/timeline.js"        200 'renderTimeline'
check "shared references.js"        "http://127.0.0.1/shared/references.js"      200 'CITATION_NOTE'
check "user references-modal.js"    "http://127.0.0.1/user/js/references-modal.js" 200 'references'
check "user advice page module"     "http://127.0.0.1/user/js/pages/advice.js"   200 'timeline'
check "advice plan needs login"     "http://127.0.0.1/api/advice/plan"           401 'error.not_authenticated'
check "advice options needs login"  "http://127.0.0.1/api/advice/options"        401 'error.not_authenticated'
# a missing module must 404 instead of coming back as the SPA shell with 200
check "missing user asset 404"      "http://127.0.0.1/user/nope.js"              404 ''
check "missing admin asset 404"     "http://127.0.0.1/admin/nope.css"            404 ''
check "missing shared asset 404"    "http://127.0.0.1/shared/nope.js"            404 ''
# paths an old cached module still asks for (module-relative ../shared/...)
check "legacy module path /user"    "http://127.0.0.1/user/shared/api.js"        200 'API_PREFIX'
check "legacy module path /admin"   "http://127.0.0.1/admin/shared/ui.js"        200 'openModal'
check "api parts"                   "http://127.0.0.1/api/auth/admin/parts"      200 '"parts"'
check "api me 401"                  "http://127.0.0.1/api/auth/me"               401 'error.not_authenticated'
check "healthz"                     "http://127.0.0.1/healthz"                   200 'ok'
# legacy /sleep/... addresses stay reachable (302) or proxied
check "/sleep -> 302"               "http://127.0.0.1/sleep"                     302 ''
check "/sleep/ -> 302"              "http://127.0.0.1/sleep/"                    302 ''
check "legacy /sleep/admin -> 302"   "http://127.0.0.1/sleep/admin"               302 ''
check "/sleep/shared/x -> 302"      "http://127.0.0.1/sleep/shared/base.css"     302 ''
check "/sleep/api compat"           "http://127.0.0.1/sleep/api/auth/me"         401 'error.not_authenticated'
check "old entry gone"              "http://127.0.0.1/sleep/user/index.html"     302 ''
# the probe page is deleted; an unknown path falls back to the user shell (200)
check "probe page removed"          "http://127.0.0.1/zz-probe.html"             200 'login-form'

# No module may import a sibling directory relatively: the browser would resolve
# /user/js/main.js + ../shared/api.js to /user/shared/api.js and the page renders
# blank while every request still reports 200.
echo "== 4b. no relative shared imports in the published tree =="
leftovers=$(sudo_run grep -rIl -- '\.\./shared/' "$WEBROOT" || true)
if [ -n "$leftovers" ]; then
    echo "  FAIL relative shared imports still deployed:"; printf '    %s\n' $leftovers
    fail=$((fail + 1))
else
    echo "  ok   no ../shared/ anywhere under $WEBROOT"
fi
printf '  ..   %-30s %s\n' "static file count" "$(find "$WEBROOT" -type f | wc -l)"
printf '  ..   %-30s %s\n' "webroot top level" "$(ls -1 "$WEBROOT" | tr '\n' ' ')"

# The three new POST routes must exist: without a session they answer 401 (or 422
# when the auth dependency runs after body validation) - a 404/405 would mean the
# deployed backend is still the old build.
echo "== 4c. adaptive-v2 POST routes exist =="
for route in checkin notice-ack replan; do
    code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
        -d '{}' "http://127.0.0.1/api/advice/$route" || true)
    case "$code" in
        401 | 422) printf '  ok   %-30s %s\n' "POST /api/advice/$route" "$code" ;;
        *) printf '  FAIL %-30s %s (want 401/422)\n' "POST /api/advice/$route" "$code"; fail=$((fail + 1)) ;;
    esac
done

echo "== 4d. database migration =="
schema=$(sudo_run python3 "$STAGE/schema_check.py" /var/lib/sleep-schedule/app.db 2>/dev/null || true)
printf '  ..   %-30s %s\n' "schema" "${schema:-<unavailable>}"
case "$schema" in
    *'"users_has_notice_ack": true'*) echo "  ok   users.advice_notice_ack present" ;;
    *) echo "  FAIL users.advice_notice_ack missing"; fail=$((fail + 1)) ;;
esac
case "$schema" in
    *advice_plans*) echo "  ok   advice_plans table present" ;;
    *) echo "  FAIL advice_plans table missing"; fail=$((fail + 1)) ;;
esac
case "$schema" in
    *meal_checks*) echo "  ok   meal_checks table present" ;;
    *) echo "  FAIL meal_checks table missing"; fail=$((fail + 1)) ;;
esac
case "$schema" in
    *'"meal_checks_has_refused": true'*) echo "  ok   meal_checks.refused present" ;;
    *) echo "  FAIL meal_checks.refused missing"; fail=$((fail + 1)) ;;
esac
# "two dates": sleep_records gains an explicit wake date, and the migration back-fills
# old rows so nothing is left without one.
case "$schema" in
    *'"sleep_records_has_wake_date": true'*) echo "  ok   sleep_records.wake_date present" ;;
    *) echo "  FAIL sleep_records.wake_date missing"; fail=$((fail + 1)) ;;
esac
case "$schema" in
    *'"sleep_records_without_wake_date": 0'*) echo "  ok   every record has a wake date" ;;
    *) echo "  FAIL records without a wake date"; fail=$((fail + 1)) ;;
esac

# The deployed code itself must carry this release: a 10 second write cooldown and
# the "N attempts left" warning on a failed login.  Checking the shipped files (not
# just the HTTP surface) catches a half-applied release where the service still
# answers from bytecode of the previous build.
echo "== 4e. deployed code carries the 10s cooldown, the login warning and the failure-count reset =="
grep_file() {  # grep_file <label> <file> <needle>
    local label="$1" file="$2" needle="$3" hits
    hits=$(grep -c -- "$needle" "$file" 2>/dev/null || true)
    if [ "${hits:-0}" -ge 1 ]; then
        printf '  ok   %-30s %s\n' "$label" "$needle"
    else
        printf '  FAIL %-30s %s missing in %s\n' "$label" "$needle" "$file"
        fail=$((fail + 1))
    fi
}
grep_file "config: 10s cooldown"    "$APP_DIR/backend/app/config.py"                 'WRITE_COOLDOWN_SECONDS", 10'
grep_file "records: 10s docstring"  "$APP_DIR/backend/app/api/records.py"            '10 秒防重复提交'
grep_file "auth service: warning"   "$APP_DIR/backend/app/services/auth_service.py"  'def failure_warning'
grep_file "api auth: warning used"  "$APP_DIR/backend/app/api/auth.py"               'failure_warning('
grep_file "i18n: warning key"       "$WEBROOT/shared/i18n.js"                        'error.invalid_credentials_attempts'
grep_file "i18n: warning branch"    "$WEBROOT/shared/i18n.js"                        'remaining_attempts'
# 注意 needle 里不要出现 [ ] ( ) 这类字符：grep 默认是 BRE，会把 [:32] 当成
# 字符类，导致「文件里明明有这行」也报 missing（这一条踩过坑）
grep_file "auth service: reset"     "$APP_DIR/backend/app/services/auth_service.py"  'def count_failures'
grep_file "api admin: unlock reset" "$APP_DIR/backend/app/api/admin.py"              'LoginAttempt.username == target.username'

# This release removes the "latest wake / earliest sleep" constraints (the old DB
# columns stay behind as dead columns) and ships the refusal preview, so the check
# is both "the new code is here" and "the removed feature is really gone".
grep_absent() {  # grep_absent <label> <file> <needle>
    local label="$1" file="$2" needle="$3" hits
    hits=$(grep -c -- "$needle" "$file" 2>/dev/null || true)
    if [ "${hits:-0}" -eq 0 ]; then
        printf '  ok   %-30s %s absent\n' "$label" "$needle"
    else
        printf '  FAIL %-30s %s still present in %s\n' "$label" "$needle" "$file"
        fail=$((fail + 1))
    fi
}
echo "== 4f. constraints removed; the refusal preview is deployed =="
grep_file "advice service: preview"    "$APP_DIR/backend/app/services/advice.py"  'def refusal_preview'
grep_file "advice service: state fn"   "$APP_DIR/backend/app/services/advice.py"  'def breakfast_state_today'
grep_file "schemas: preview field"     "$APP_DIR/backend/app/schemas.py"          'refusal_preview: RefusalPreview'
grep_file "api advice: preview wiring" "$APP_DIR/backend/app/api/advice.py"       'breakfast_missed_today'
grep_file "frontend: preview hint"     "$WEBROOT/user/js/pages/advice.js"         'data-refusal-preview'
grep_file "frontend: today redraw"     "$WEBROOT/user/js/pages/advice.js"         'renderTodayOnly'
grep_file "frontend: moved label"      "$WEBROOT/user/js/pages/advice.js"         'meal-moved'
grep_file "i18n: preview key"          "$WEBROOT/shared/i18n.js"                  'advice.meal.refusal_preview'
grep_file "i18n: moved key"            "$WEBROOT/shared/i18n.js"                  'advice.meal.moved'
grep_absent "records: old field gone"  "$WEBROOT/user/js/pages/records.js"        'latest_wake_time'
grep_absent "timepicker: old picker"   "$WEBROOT/shared/timepicker.js"            'createOptionalTimePicker'
grep_absent "i18n: old picker keys"    "$WEBROOT/shared/i18n.js"                  'pickerLatestWake'
grep_absent "schemas: old field gone"  "$APP_DIR/backend/app/schemas.py"          'latest_wake_time'

# This release also deletes the citation-correction record (docs/REFERENCES.md) and the
# sentence that recited it from the two shipped scripts. docs/ is not part of the webroot,
# so the check covers what users actually receive: references.js and i18n.js.
echo "== 4g. the citation-correction record is gone from the shipped scripts =="
grep_absent "references.js: no record" "$WEBROOT/shared/references.js"             '原始需求'
grep_absent "i18n.js: no record"       "$WEBROOT/shared/i18n.js"                   '原始需求'
grep_absent "references.js: no 更正"   "$WEBROOT/shared/references.js"             '更正'
grep_file   "references.js: note kept" "$WEBROOT/shared/references.js"             'Crossref / PubMed'

# This release also fixes the folded plan form: it used to blank out every child of the
# card and leave an empty shell behind (the "generate new plan" button it expected was
# never rendered). Folding now hides the whole card, so the shipped CSS must say
# display:none and the dead toggle must be gone from the shipped script.
echo "== 4h. the folded form hides the whole card =="
grep_absent "advice.js: dead toggle gone" "$WEBROOT/user/js/pages/advice.js"        'advice-form-toggle'
grep_file   "advice.js: single form shape" "$WEBROOT/user/js/pages/advice.js"       'function formHtml()'
grep_file   "app.css: folded card hidden"  "$WEBROOT/user/css/app.css"              'form-collapsed { display: none'
grep_absent "app.css: no empty shell"      "$WEBROOT/user/css/app.css"              'form-collapsed { gap: 0'

# 4i. Desktop layout: a folded form must not squeeze the plan into the 320px column.
# Hiding the form makes it stop being a grid item, so the plan fell into the narrow
# first track and the page hugged the left edge. The fix is the .form-folded class on
# the layout plus a centred page, so all three pieces must be in the shipped files.
echo "== 4i. the folded layout uses the full row on a desktop =="
grep_file   "app.css: folded layout is one column" "$WEBROOT/user/css/app.css"      'form-folded'
grep_file   "advice.js: layout follows the fold"   "$WEBROOT/user/js/pages/advice.js" 'form-folded'
grep_file   "app.css: page centred on wide screens" "$WEBROOT/user/css/app.css"     'margin-inline: auto'
grep_file   "app.css: two day columns on wide screens" "$WEBROOT/user/css/app.css"  'min-width: 1200px'

# The six tips under each day used to be a full list, which is what made the desktop
# page feel empty. They are folded behind a one-line button now, and the copy that
# promised the remaining days "can wait" is gone, so check both the new pieces and
# the absence of the old wording.
echo "== 4j. the day tips fold away and name the last step =="
grep_file   "advice.js: tips fold markup"  "$WEBROOT/user/js/pages/advice.js"          'tips-fold'
grep_file   "advice.js: tips fold wiring"  "$WEBROOT/user/js/pages/advice.js"          'data-tips-toggle'
grep_file   "app.css: folded tips style"   "$WEBROOT/user/css/app.css"                 '.tips-toggle'
grep_file   "i18n.js: fold labels"         "$WEBROOT/shared/i18n.js"                  'advice.tipsShow'
grep_file   "advice.py: last step minutes" "$APP_DIR/backend/app/services/advice.py"  'last_step_minutes'
grep_absent "i18n.js: vague infeasible copy gone" "$WEBROOT/shared/i18n.js"           '剩下的再慢慢来'

# 4k. "two dates": a record carries a bedtime date and a wake date; meals, check-ins and
# the adherence stats all pair on the wake day, while the bedtime day still labels sleep
# statistics.  grep_file proves the new code shipped; grep_absent proves the single-date
# form is gone from the published records page.
echo "== 4k. a record carries a bedtime date and a wake date =="
grep_file "db: wake date migration"        "$APP_DIR/backend/app/db.py"                   'wake_date'
grep_file "timing: explicit span"          "$APP_DIR/backend/app/core/timing.py"          'def span_minutes'
grep_file "records api: wake date check"   "$APP_DIR/backend/app/api/records.py"          '_check_wake_date'
grep_file "advice: wake day pairing"       "$APP_DIR/backend/app/services/advice.py"      'def wake_day_of'
grep_file "advice_plan: wake day checkin"  "$APP_DIR/backend/app/services/advice_plan.py" 'wake_day_of'
grep_file "records.js: wake date field"    "$WEBROOT/user/js/pages/records.js"            'f-wake-date'
grep_file "i18n.js: wake date label"       "$WEBROOT/shared/i18n.js"                      'records.wakeDate'
grep_file "i18n.js: wake date errors"      "$WEBROOT/shared/i18n.js"                      'error.invalid_date_range'
grep_absent "records.js: single date form gone" "$WEBROOT/user/js/pages/records.js"       'records.date'

# 4l. the night that crosses midnight is drawn as two labelled halves (the bedtime-side
# piece at the tail of the axis, the wake-side piece at its head) and the sleep badge on
# the axis says the two ends are one night.  The hooks are data-tl-part / data-tl-join so
# the acceptance script can assert on them without matching Chinese copy.
echo "== 4l. the night is labelled as two halves that join =="
grep_file "timeline.js: halves are tagged"  "$WEBROOT/shared/timeline.js" 'data-tl-part'
grep_file "timeline.js: halves join note"   "$WEBROOT/shared/timeline.js" 'data-tl-join'
grep_file "timeline.js: half labels"        "$WEBROOT/shared/timeline.js" 'advice.timeline.sleepFirst'
grep_file "i18n.js: first half label"       "$WEBROOT/shared/i18n.js"     'advice.timeline.sleepFirst'
grep_file "i18n.js: second half label"      "$WEBROOT/shared/i18n.js"     'advice.timeline.sleepLast'
grep_file "i18n.js: join note"              "$WEBROOT/shared/i18n.js"     'advice.timeline.sleepSplit'

# 4m. The step curve is no longer a fixed pacing table (60/45/30/15) that ran out
# halfway: it is generated from the total offset and the chosen number of days by a
# shape function, so the last day still moves. The card reports the daily hint plus
# the first / biggest / last day; the old table must be gone from the shipped files.
echo "== 4m. the step curve is generated from the offset and the days =="
grep_file   "advice.js: step card hook"        "$WEBROOT/user/js/pages/advice.js"        'data-steps-card'
grep_file   "advice.js: first/max/last shown"  "$WEBROOT/user/js/pages/advice.js"        'data-steps-shape'
grep_absent "advice.js: fixed table gone"      "$WEBROOT/user/js/pages/advice.js"        'curve_minutes'
grep_file   "i18n.js: shape copy"              "$WEBROOT/shared/i18n.js"                 'advice.steps.shape'
grep_file   "advice.py: shape parameters"      "$APP_DIR/backend/app/services/advice.py" 'STEP_SHAPE_ALPHA'
grep_absent "advice.py: fixed curve gone"      "$APP_DIR/backend/app/services/advice.py" 'STEP_CURVE'

# 4n. The cumulative step curve (days on x, minutes moved so far on y) is drawn from the
# same day steps the cards show, with an even-pace reference line.  Pure frontend: the
# hooks are data-curve-card / data-curve-line so acceptance can measure it.
echo "== 4n. the cumulative step curve is deployed =="
grep_file "advice.js: curve card hook"   "$WEBROOT/user/js/pages/advice.js" 'data-curve-card'
grep_file "advice.js: smooth path"       "$WEBROOT/user/js/pages/advice.js" 'smoothPath'
grep_file "app.css: curve styles"        "$WEBROOT/user/css/app.css"        '.curve-svg'
grep_file "i18n.js: curve title"         "$WEBROOT/shared/i18n.js"          'advice.curve.title'

# 4o. The curve now also draws the daily cap slope, shows a tap bubble, jumps to the matching
# day card and previews a different number of days without a request.  Hooks stay data-*.
echo "== 4o. the curve can be read day by day and previewed =="
grep_file "advice.js: cap slope hook"    "$WEBROOT/user/js/pages/advice.js" 'data-curve-cap'
grep_file "advice.js: tap bubble hook"   "$WEBROOT/user/js/pages/advice.js" 'data-curve-bubble'
grep_file "advice.js: shared shape math" "$WEBROOT/user/js/pages/advice.js" 'shapeSteps'
grep_file "advice.js: days preview"      "$WEBROOT/user/js/pages/advice.js" 'scheduleCurvePreview'
grep_file "app.css: cap line style"      "$WEBROOT/user/css/app.css"        '.curve-cap'
grep_file "app.css: bubble style"        "$WEBROOT/user/css/app.css"        '.curve-bubble'
grep_file "i18n.js: cap copy"            "$WEBROOT/shared/i18n.js"          'advice.curve.capLine'
grep_file "i18n.js: preview copy"        "$WEBROOT/shared/i18n.js"          'advice.curve.captionPreview'

# 4p. The "your nights are off the plan" dialog used to remove itself from the DOM, so its
# promise never resolved: choosing "recompute" silently did nothing and the dialog could never
# appear again.  Now the footer has exactly one button and each strategy resolves the dialog.
echo "== 4p. the deviation dialog resolves its choice =="
grep_file   "advice.js: single close button"  "$WEBROOT/user/js/pages/advice.js" 'okText: null'
grep_file   "advice.js: strategy resolves"    "$WEBROOT/user/js/pages/advice.js" 'close(btn.dataset.strategy)'
grep_file   "ui.js: optional primary button"  "$WEBROOT/shared/ui.js"            'okText === null'
grep_file   "i18n.js: strategy copy"          "$WEBROOT/shared/i18n.js"          'advice.deviation.postpone'
grep_absent "advice.js: old silent path gone" "$WEBROOT/user/js/pages/advice.js" 'chosen = btn.dataset.strategy'
grep_absent "i18n.js: dead dismiss label"     "$WEBROOT/shared/i18n.js"          'advice.deviation.dismiss'

echo
echo "== 4q. the advice page waits for the data before it paints =="
grep_file "advice.js: both requests first" "$WEBROOT/user/js/pages/advice.js" 'await Promise.all'
grep_absent "advice.js: no skeleton" "$WEBROOT/user/js/pages/advice.js" 'data-advice-skeleton'
grep_absent "advice.js: no plan memory" "$WEBROOT/user/js/pages/advice.js" 'ssp.advice.hasPlan'
grep_absent "app.css: no skeleton styles" "$WEBROOT/user/css/app.css" '.skeleton-row'
grep_absent "advice.js: old loader card gone" "$WEBROOT/user/js/pages/advice.js" 'function renderStatus'

echo "== 4r. a refresh paints the last screen before the data =="
grep_file "boot cache: module shipped" "$WEBROOT/shared/boot-cache.js" 'export function clearBootCache'
grep_file "main.js: restore identity" "$WEBROOT/user/js/main.js" 'readBootUser()'
grep_file "main.js: restore snapshot" "$WEBROOT/user/js/main.js" 'readPage(state.user.id, state.route)'
grep_file "main.js: remember snapshot" "$WEBROOT/user/js/main.js" 'writePage(state.user.id, state.route, view.innerHTML)'
grep_file "main.js: cleared on 401" "$WEBROOT/user/js/main.js" 'clearBootCache();'

echo "== 4s. static assets are compressed and the module graph is preloaded =="
grep_file "index.html: modulepreload" "$WEBROOT/user/index.html" 'rel="modulepreload"'
grep_file "vhost: gzip on" "$VHOST" 'gzip on;'
gz=$(curl -s -m 10 -H 'Accept-Encoding: gzip' -o /dev/null -w '%{size_download}' "http://127.0.0.1/user/js/pages/advice.js" || true)
raw=$(curl -s -m 10 -H 'Accept-Encoding: identity' -o /dev/null -w '%{size_download}' "http://127.0.0.1/user/js/pages/advice.js" || true)
if [ -n "$gz" ] && [ -n "$raw" ] && [ "$gz" -lt "$raw" ]; then
    printf '  ok   %-30s %s < %s bytes\n' "gzip shrinks advice.js" "$gz" "$raw"
else
    printf '  FAIL %-30s gz=%s raw=%s\n' "gzip shrinks advice.js" "$gz" "$raw"; fail=$((fail + 1))
fi
hdr=$(curl -sI -m 10 -H 'Accept-Encoding: gzip' "http://127.0.0.1/user/js/pages/advice.js" | tr 'A-Z' 'a-z' | grep -c 'content-encoding: gzip' || true)
if [ "${hdr:-0}" -ge 1 ]; then
    printf '  ok   %-30s %s\n' "advice.js answers gzip" "content-encoding: gzip"
else
    printf '  FAIL %-30s no content-encoding: gzip\n' "advice.js answers gzip"; fail=$((fail + 1))
fi

if [ "$fail" -eq 0 ]; then
    echo "fix applied: OK"
else
    echo "fix applied: $fail check(s) failed"
    exit 1
fi
