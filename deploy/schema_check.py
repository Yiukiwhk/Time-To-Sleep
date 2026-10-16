"""Verify the deployed SQLite schema after a release (run on the server).

    sudo python3 deploy/schema_check.py /var/lib/sleep-schedule/app.db

Prints one ASCII-only JSON line so the remote shell script can grep it.
"""

from __future__ import annotations

import json
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/sleep-schedule/app.db"

connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
try:
    user_columns = [row[1] for row in connection.execute("pragma table_info(users)")]
    tables = sorted(row[0] for row in connection.execute("select name from sqlite_master where type='table'"))
    # meal_checks.refused arrives with the "rather not eat breakfast" feature; without the
    # ALTER TABLE migration the column is missing and every check-in would 500.
    meal_columns = [row[1] for row in connection.execute("pragma table_info(meal_checks)")] if "meal_checks" in tables else []
    # sleep_records.wake_date arrives with the "two dates" feature (bedtime date + wake date);
    # the migration also back-fills old rows, so remaining empty rows should be zero.
    record_columns = [row[1] for row in connection.execute("pragma table_info(sleep_records)")] if "sleep_records" in tables else []
    empty_wake_dates = 0
    if "wake_date" in record_columns:
        empty_wake_dates = int(
            connection.execute(
                "select count(*) from sleep_records where wake_date is null or wake_date = ''"
            ).fetchone()[0]
        )
finally:
    connection.close()

print(json.dumps({
    "users_has_notice_ack": "advice_notice_ack" in user_columns,
    "meal_checks_has_refused": "refused" in meal_columns,
    "sleep_records_has_wake_date": "wake_date" in record_columns,
    "sleep_records_without_wake_date": empty_wake_dates,
    "tables": tables,
}, ensure_ascii=False))
