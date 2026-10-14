"""Walk the browser's module graph and prove every file it needs is really there.

The blank-page bug that took a long time to find was an import graph that 404'd:
`/user/js/main.js` asked for `../shared/api.js`, the browser resolved that against the
*module's own URL* to `/user/shared/api.js`, the file did not exist, and an SPA
fallback answered with the HTML shell and HTTP 200 - so nothing in the Network panel
looked like a 404.  This script reproduces the browser's resolution exactly and fails
loudly on any missing or mis-typed file.

Usage:
    python tests/check_module_graph.py                            # http://127.0.0.1:8000
    python tests/check_module_graph.py http://127.0.0.1:8082
    python tests/check_module_graph.py http://127.0.0.1:8000 --page /admin/

Exit code 0 when every discovered URL resolves to a file of the expected type.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PAGES = ("/user/", "/admin/")

SCRIPT_SRC = re.compile(r"""<script[^>]*\ssrc=["']([^"']+)["']""", re.I)
LINK_HREF = re.compile(r"""<link[^>]*\shref=["']([^"']+)["']""", re.I)
JS_SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)["']([^"']+)["']""", re.I
)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.I)

WANTED_TYPE = {
    ".js": ("javascript", "module"),
    ".mjs": ("javascript",),
    ".css": ("text/css",),
    ".svg": ("image/svg+xml",),
    ".json": ("application/json",),
}


def fetch(url: str, timeout: float = 20.0):
    request = urllib.request.Request(url, headers={"User-Agent": "module-graph-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, response.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()
    except Exception as error:  # noqa: BLE001 - report, never crash the run
        return 0, f"<{type(error).__name__}: {error}>", b""


def resolve(base: str, specifier: str) -> str | None:
    if specifier.startswith(("data:", "blob:", "http://", "https://", "//")):
        return None
    if specifier.startswith("#") or not specifier:
        return None
    return urllib.parse.urljoin(base, specifier)


def expected_type(url: str) -> tuple[str, ...] | None:
    path = urllib.parse.urlparse(url).path.lower()
    for suffix, wanted in WANTED_TYPE.items():
        if path.endswith(suffix):
            return wanted
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "base",
        nargs="?",
        default="http://127.0.0.1:8000",
        help="site root (default: http://127.0.0.1:8000)",
    )
    parser.add_argument("--page", action="append", default=None, help="entry page path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    pages = args.page or list(PAGES)

    seen: set[str] = set()
    problems: list[str] = []
    checked = 0

    queue: list[tuple[str, str]] = []  # (url, referrer)
    for page in pages:
        queue.append((base + page, "<entry>"))

    while queue:
        url, referrer = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)

        status, content_type, body = fetch(url)
        checked += 1
        kind = content_type.split(";")[0].strip()
        wanted = expected_type(url)
        note = ""

        if status != 200:
            note = "MISSING"
        elif wanted and not any(w in kind for w in wanted):
            note = f"WRONG TYPE (wanted {'/'.join(wanted)})"
        if "html" in kind and (url.endswith(".js") or url.endswith(".css")):
            note = "SPA FALLBACK SERVED HTML FOR AN ASSET"

        if note:
            problems.append(f"{url} -> {status} {kind or '?'} [{note}] (needed by {referrer})")

        if args.verbose or note:
            print(f"  {status} {kind or '-':<24} {url}{'  <-- ' + note if note else ''}")

        if status != 200:
            continue

        text = body.decode("utf-8", "replace")
        found: list[str] = []
        if kind.startswith(("text/html", "application/xhtml")):
            found += SCRIPT_SRC.findall(text) + LINK_HREF.findall(text)
        if "javascript" in kind or url.endswith((".js", ".mjs")):
            found += JS_SPECIFIER.findall(text)
        if "css" in kind:
            found += CSS_URL.findall(text)

        for specifier in found:
            target = resolve(url, specifier)
            if target:
                queue.append((target, url))

    print(f"checked {checked} url(s) from {len(pages)} entry page(s)")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print("  FAIL " + problem)
        return 1
    print("module graph: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
