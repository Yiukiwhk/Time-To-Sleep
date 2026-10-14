"""Rewrite cross-directory shared-module imports to site-absolute paths.

Why: a browser resolves an ES module specifier against the *module's own URL*, so
`../shared/api.js` inside /user/js/main.js means /user/shared/api.js - a path that
exists in neither the nginx layout nor the FastAPI mounts.  The import 404s, the
whole module graph fails, and the SPA renders as an empty page (only the body
background shows).  Site-absolute `/shared/...` resolves identically no matter
which page loaded the module.

Usage:
    python scripts/absolute_shared_imports.py [--check]

--check only reports what would change (exit code 1 when something would).

Safety: this script reads and writes strictly as UTF-8 (the frontend is UTF-8
without BOM).  Never rewrite these files with Windows PowerShell 5.1 cmdlets such
as Get-Content/Set-Content without -Encoding UTF8: PowerShell 5.1 decodes UTF-8 as
the ANSI codepage and silently turns every Chinese comment into mojibake.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

PATTERNS = (
    ('from "../shared/', 'from "/shared/'),
    ('from "../../shared/', 'from "/shared/'),
    ('import("../shared/', 'import("/shared/'),
    ('import("../../shared/', 'import("/shared/'),
)


def mojibake_lines(text: str) -> list[int]:
    """Lines holding the classic UTF-8-read-as-GBK artifacts (鈥 锛 銆 ...)."""
    markers = ("锛", "鈥", "銆", "鈭", "鎴", "鍜", "鐨", "鏄", "涓€", "璺緞")
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if any(marker in line for marker in markers)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report only, do not write")
    args = parser.parse_args()

    changed_files = 0
    remaining: list[str] = []
    suspects: list[str] = []

    for path in sorted(FRONTEND.rglob("*.js")):
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        new = text
        for old, replacement in PATTERNS:
            new = new.replace(old, replacement)

        bad = mojibake_lines(new)
        if bad:
            suspects.append(f"{path.relative_to(ROOT)} lines {bad[:5]}")

        if new != text:
            changed_files += 1
            print(f"rewrite {path.relative_to(ROOT)}: {len(raw)} -> {len(new.encode('utf-8'))} bytes")
            if not args.check:
                # bytes in, bytes out: the CRLF line endings of the checkout survive untouched.
                path.write_bytes(new.encode("utf-8"))

        if "../shared/" in new:
            remaining.append(str(path.relative_to(ROOT)))

    total = len(list(FRONTEND.rglob("*.js")))
    print(f"scanned {total} js files, {changed_files} changed")
    if remaining:
        print("STILL RELATIVE: " + ", ".join(remaining))
    if suspects:
        print("MOJIBAKE SUSPECTS: " + "; ".join(suspects))
    if args.check and changed_files:
        return 1
    return 1 if (remaining or suspects) else 0


if __name__ == "__main__":
    raise SystemExit(main())
