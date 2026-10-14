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
finally:
    connection.close()

print(json.dumps({
    "users_has_notice_ack": "advice_notice_ack" in user_columns,
    "meal_checks_has_refused": "refused" in meal_columns,
    "tables": tables,
}, ensure_ascii=False))
