#!/usr/bin/env python
"""Create the database schema (and optionally the built-in administrator).

Usage (from the project root, inside the virtualenv):

    python scripts/seed_admin.py --check        # schema + admin status only
    python scripts/seed_admin.py --bootstrap-admin
    python scripts/seed_admin.py --create-user alice --password 'Alice@12345'

The same code path is executed by `backend/app/main.py` on every start, so
running this script is only required to prepare a brand new database file
before the service starts, or to seed an extra account from the shell.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select  # noqa: E402

from app import models  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import init_db, write_session  # noqa: E402


def _status() -> tuple[int, int]:
    with write_session() as session:
        users = session.execute(select(func.count()).select_from(models.User)).scalar_one()
        admins = session.execute(
            select(func.count()).select_from(models.User).where(models.User.is_admin.is_(True))
        ).scalar_one()
    return int(users), int(admins)


def _create_user(username: str, password: str, *, is_admin: bool, language: str, theme: str) -> None:
    with write_session() as session:
        exists = session.execute(
            select(models.User).where(models.User.username == username)
        ).scalar_one_or_none()
        if exists is not None:
            print(f"user {username!r} already exists, nothing to do")
            return
        user = models.User(
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
            is_active=True,
            language=language,
            theme=theme,
        )
        session.add(user)
    print(f"created {'admin' if is_admin else 'user'} {username!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the SQLite database")
    parser.add_argument("--check", action="store_true", help="only report status")
    parser.add_argument("--bootstrap-admin", action="store_true", help="create the built-in admin")
    parser.add_argument("--create-user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--admin", action="store_true", help="create --create-user as administrator")
    parser.add_argument("--language", default="zh", choices=["zh", "en"])
    parser.add_argument("--theme", default="dark", choices=["dark", "light"])
    args = parser.parse_args()

    print(f"database : {settings.DB_PATH}")
    print(f"data dir : {settings.DATA_DIR}")
    print(f"log dir  : {settings.LOG_DIR}")

    if args.check:
        if not Path(settings.DB_PATH).exists():
            print("status   : database file missing (run without --check to create it)")
            return 1
        users, admins = _status()
        print(f"status   : ok, users={users}, admins={admins}")
        return 0

    init_db()
    print("schema   : ready")

    if args.bootstrap_admin:
        # Importing app.main pulls in the API layer, which opens the rotating
        # log file at import time - make sure its directory exists first.
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        from app.main import bootstrap_admin

        bootstrap_admin()
        print(f"admin    : {settings.BOOTSTRAP_ADMIN_USERNAME!r} ensured "
              f"({settings.ADMIN_PASSWORD_PARTS} password parts)")

    if args.create_user:
        if not args.password:
            parser.error("--create-user requires --password")
        _create_user(
            args.create_user,
            args.password,
            is_admin=args.admin,
            language=args.language,
            theme=args.theme,
        )

    users, admins = _status()
    print(f"status   : ok, users={users}, admins={admins}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
