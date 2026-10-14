"""Bundle the application sources into a deployable tar.gz (deliverable only).

Included: backend sources, frontend assets, helper scripts, tests, docs and the
offline wheel cache (so the server can install dependencies without PyPI).
Excluded: virtualenvs, caches, databases, logs and temporary files.
"""
from __future__ import annotations

import os
import tarfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREFIX = ""  # members are stored relative to the project root (extract into $APP_DIR)
DIST_DIR = PROJECT_ROOT / "dist"
BUNDLE_NAME = "sleep-schedule.tar.gz"

ROOTS = ["backend", "frontend", "scripts", "tests", "data", "logs", ".wheels", "deploy", "docs"]
FILES = ["README.md", "requirements.txt", "run.py", "start.bat", ".gitignore"]

SKIP_DIRS = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "_tmp",
    ".mypy_cache",
    "node_modules",
}
SKIP_SUFFIXES = (".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm")


def keep(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.name.startswith("_tmp"):
        return False
    if path.suffix in SKIP_SUFFIXES:
        return False
    return True


def main() -> int:
    DIST_DIR.mkdir(exist_ok=True)
    bundle = DIST_DIR / BUNDLE_NAME
    if bundle.exists():
        bundle.unlink()

    added = 0
    with tarfile.open(bundle, "w:gz") as archive:
        for relative in ROOTS:
            root = PROJECT_ROOT / relative
            if not root.exists():
                print(f"  - skip missing root: {relative}")
                continue
            for current_dir, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in filenames:
                    path = Path(current_dir) / name
                    if not keep(path.relative_to(PROJECT_ROOT)):
                        continue
                    archive.add(
                        path,
                        arcname=f"{PREFIX}{path.relative_to(PROJECT_ROOT).as_posix()}"
                        if PREFIX
                        else path.relative_to(PROJECT_ROOT).as_posix(),
                    )
                    added += 1
        for relative in FILES:
            path = PROJECT_ROOT / relative
            if path.exists():
                archive.add(path, arcname=f"{PREFIX}{relative}" if PREFIX else relative)
                added += 1

    size_mb = bundle.stat().st_size / 1024 / 1024
    print(f"bundle: {bundle}")
    print(f"files : {added}")
    print(f"size  : {size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
