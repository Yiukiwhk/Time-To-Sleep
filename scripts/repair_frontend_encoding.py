"""Repair the frontend files whose text was round-tripped through cp936 by PowerShell.

The damage pattern: a UTF-8 file was read with the GBK code page and written back
as UTF-8, so every Chinese character turned into two/three "GBK-latin" characters
(e.g. 管理 -> \u7ba1\u7406 was mis-decoded, producing U+7EE0..U+951B style glyphs)
and a UTF-8 BOM was prepended.

Repair = strip the BOM, then encode each affected run back through cp936 to
recover the original UTF-8 bytes.  ASCII-only runs are left untouched.
"""
from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1] / "frontend"
FILES = [
    "admin/js/app.js",
    "admin/js/pages/dashboard.js",
    "admin/js/pages/logs.js",
    "admin/js/pages/requests.js",
    "admin/js/pages/userDetail.js",
    "admin/js/pages/users.js",
    "admin/js/state.js",
    "user/js/main.js",
    "user/js/pages/advice.js",
    "user/js/pages/records.js",
    "user/js/pages/settings.js",
]


def repair(text: str) -> tuple[str, int]:
    """Return (fixed_text, number_of_runs_repaired)."""
    out: list[str] = []
    run: list[str] = []
    fixed_runs = 0

    def flush() -> None:
        nonlocal run, fixed_runs
        if not run:
            return
        chunk = "".join(run)
        run = []
        try:
            recovered = chunk.encode("cp936").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            out.append(chunk)
            return
        fixed_runs += 1
        out.append(recovered)

    for ch in text:
        # Only the mis-decoded CJK ranges and the replacement char take part in a run.
        if 0x2E80 <= ord(ch) <= 0x9FFF or ch == "\ufffd":
            run.append(ch)
        else:
            flush()
            out.append(ch)
    flush()
    return "".join(out), fixed_runs


def main() -> int:
    apply = "--apply" in sys.argv
    total = 0
    for rel in FILES:
        path = ROOT / rel
        raw = path.read_bytes()
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8")
        if had_bom:
            text = text.lstrip("\ufeff")
        fixed, runs = repair(text)
        total += runs
        flag = "BOM" if had_bom else "   "
        print(f"{flag} runs={runs:3}  {rel}")
        if apply:
            path.write_bytes(fixed.encode("utf-8"))
    print(f"total runs repaired: {total}")
    if not apply:
        print("(dry run - pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
