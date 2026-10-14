"""检查前端 JS/HTML 里用到的 class 是否都在 CSS 中有定义（粗粒度，只报缺失名）。

用法：
    backend/.venv/Scripts/python.exe scripts/check_css_classes.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# 这些是 JS 动态拼接或纯语义标记，不需要样式
IGNORE = {"hidden", "active", "show", "open", "on", "off", "checked", "selected", "wide", "danger"}


def main() -> int:
    css = "\n".join(p.read_text(encoding="utf-8") for p in FRONTEND.rglob("*.css"))
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))

    used: dict[str, set[str]] = {}
    for path in list(FRONTEND.rglob("*.js")) + list(FRONTEND.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(FRONTEND))
        for match in re.finditer(r'class="([^"$]*)"', text):
            for name in match.group(1).split():
                used.setdefault(name, set()).add(rel)
        for match in re.finditer(r'classList\.(?:add|toggle|remove)\("([\w-]+)"', text):
            used.setdefault(match.group(1), set()).add(rel)
        for match in re.finditer(r'className\s*=\s*"([^"$]*)"', text):
            for name in match.group(1).split():
                used.setdefault(name, set()).add(rel)

    missing = {
        name: where
        for name, where in used.items()
        if name not in defined and name not in IGNORE and not name.startswith("data-")
    }
    print(f"classes used: {len(used)}  defined in css: {len(defined)}  missing: {len(missing)}")
    for name, where in sorted(missing.items()):
        print(f"  - {name}  ({', '.join(sorted(where))})")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
