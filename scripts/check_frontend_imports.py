"""Catch "used but never imported" helpers in the frontend.

The blank-page incident and the broken record form both came from a module that
referenced a shared helper it never imported (the browser then throws a
ReferenceError at click time and the UI silently does nothing).  A bundler would
have caught this; a plain ES-module setup needs a lint-ish check, so this script
compares the exported names of ``frontend/shared/*.js`` against every frontend
module's import list.

    python scripts/check_frontend_imports.py

Exits 1 when a module uses a shared export without importing it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

EXPORT_RE = re.compile(r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M)
IMPORT_RE = re.compile(r"^import\s+(?P<clause>[^;]+?)\s+from\s+[\"'](?P<from>[^\"']+)[\"']\s*;", re.M)
BRACES_RE = re.compile(r"\{(?P<names>[^}]*)\}")


def shared_exports() -> dict[str, Path]:
    exports: dict[str, Path] = {}
    for path in sorted((FRONTEND / "shared").glob("*.js")):
        for name in EXPORT_RE.findall(path.read_text(encoding="utf-8")):
            exports.setdefault(name, path)
    return exports


def imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for match in IMPORT_RE.finditer(source):
        clause = match.group("clause")
        braces = BRACES_RE.search(clause)
        if braces:
            for raw in braces.group("names").split(","):
                item = raw.strip()
                if not item:
                    continue
                names.add(item.split(" as ")[-1].strip())
        else:
            names.add(clause.strip().split(" as ")[-1].strip())
    return names


def strip_imports(source: str) -> str:
    """Blank out import statements and string/comment bodies so hits are real uses."""
    without_imports = IMPORT_RE.sub("", source)
    without_imports = re.sub(r"/\*.*?\*/", "", without_imports, flags=re.S)
    without_imports = re.sub(r"//[^\n]*", "", without_imports)
    without_imports = re.sub(r"`(?:\\.|[^`\\])*`", "``", without_imports, flags=re.S)
    without_imports = re.sub(r"'(?:\\.|[^'\\])*'", "''", without_imports)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', without_imports)


def main() -> int:
    exports = shared_exports()
    # 导出名 -> 定义它的文件（同一个文件里用自己的导出是合法的，不算问题）
    owners: dict[str, Path] = {}
    for path in sorted((FRONTEND / "shared").glob("*.js")):
        for name in EXPORT_RE.findall(path.read_text(encoding="utf-8")):
            owners.setdefault(name, path)

    problems: list[str] = []
    checked = 0
    for path in sorted(FRONTEND.rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        body = strip_imports(source)
        imported = imported_names(source)
        checked += 1
        for name, origin in exports.items():
            if name in imported or owners.get(name) == path:
                continue
            if re.search(rf"(?<![\w$.]){re.escape(name)}\s*\(", body):
                problems.append(
                    f"{path.relative_to(ROOT).as_posix()}: uses {name}() (exported by "
                    f"{origin.relative_to(ROOT).as_posix()}) but never imports it"
                )

    print(f"frontend modules checked: {checked}")
    print(f"shared exports known: {len(exports)}")
    if problems:
        print(f"problems: {len(problems)}")
        for item in problems:
            print(f"  {item}")
        return 1
    print("problems: 0")
    print("frontend imports: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
