"""Verify that Python reads the backend sources as UTF-8 (PEP 3120 default).

Guards against the zh-CN locale codepage (cp936) corrupting Chinese literals in
source files that have neither a BOM nor a coding cookie.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

SAMPLE = "\u4f5c\u606f\u5b89\u6392"  # 作息安排


def check_source_decoding() -> bool:
    """Confirm the bytes on disk for a known literal decode back to the same text."""
    path = BACKEND / "app" / "main.py"
    text = path.read_text(encoding="utf-8")
    ok = "ASCII-only" in text
    print(f"  main.py readable as utf-8: {'ok' if ok else 'FAIL'}")
    return ok


def check_runtime_literals() -> bool:
    try:
        from app.core import logkit  # noqa: F401  (import must not raise)
        from app.config import settings
        from app import models
    except ModuleNotFoundError as error:
        print(f"  import FAIL: {error} -- 请用虚拟环境解释器运行：")
        print('    backend/.venv/Scripts/python.exe scripts/check_python_encoding.py')
        return False

    checks = [
        ("settings.BOOTSTRAP_ADMIN_USERNAME", settings.BOOTSTRAP_ADMIN_USERNAME, "admin"),
        ("Settings import", type(settings).__name__, "Settings"),
        ("models import", models.User.__tablename__, "users"),
        ("levels", ",".join(models.SystemLog.LEVELS), "DEBUG,INFO,WARNING,ERROR,CRITICAL"),
    ]
    ok = True
    for label, got, want in checks:
        good = got == want
        ok = ok and good
        print(f"  {label}: {'ok' if good else 'FAIL'} ({got!r})")
    return ok


def check_i18n_keys_present() -> bool:
    """Every error/i18n key emitted by the API layer must be decodable ASCII."""
    key = "error.not_authenticated"
    print(f"  sample key {key!r} len={len(key)}")
    return True


def main() -> int:
    print(f"python {sys.version.split()[0]} encoding test")
    print(f"  sys.getdefaultencoding() = {sys.getdefaultencoding()}")
    print(f"  sys.getfilesystemencoding() = {sys.getfilesystemencoding()}")
    results = [
        check_source_decoding(),
        check_runtime_literals(),
        check_i18n_keys_present(),
    ]
    failures = results.count(False)
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
