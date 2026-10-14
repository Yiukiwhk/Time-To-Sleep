#!/usr/bin/env python3
"""检查后端会发出的 i18n 代码与前端词条是否一一对应（中英都要有）。

用法：
    python scripts/check_advice_i18n.py            # 只报告问题，有问题时退出码 1
    python scripts/check_advice_i18n.py --verbose  # 额外打印统计与多余词条

做三件事：
1. 从 backend/app 里抽出所有形如 "advice.xxx" 的字符串字面量（后端可能返回给前端的代码）；
2. 从 frontend/shared/i18n.js 里分别解析 zh / en 两个词条表（不执行 JS，纯文本解析）；
3. 报告：后端代码在 zh / en 里缺失的、两个语言表不对称的、前端静态 t("...") 词条缺失的。

注意：前端有些键是拼出来的（例如 t("advice.tip." + tip)），脚本无法静态解析，
这类键由后端代码的抽取结果兜底覆盖。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP = ROOT / "backend" / "app"
I18N_JS = ROOT / "frontend" / "shared" / "i18n.js"
FRONTEND_JS = ROOT / "frontend"

CODE_RE = re.compile(r'"(advice\.[A-Za-z0-9_.]+)"')
KEY_RE = re.compile(r'^\s{4}"([^"]+)":', re.MULTILINE)
CALL_RE = re.compile(r't\(\s*"((?:advice|ref|error|common|settings)\.[A-Za-z0-9_.]+)"')


def backend_codes() -> set[str]:
    codes: set[str] = set()
    for path in sorted(BACKEND_APP.rglob("*.py")):
        text = path.read_bytes().decode("utf-8")
        # 以 "." 结尾的是前缀（例如 startswith("advice.warn.")），不是具体代码
        codes.update(code for code in CODE_RE.findall(text) if not code.endswith("."))
    return codes


def dict_regions(text: str) -> dict[str, set[str]]:
    """按 `  zh: {` / `  en: {` 切出两个词条表区域并抽出键名。"""
    marks = []
    for match in re.finditer(r"^\s{2}(zh|en):\s*\{", text, re.MULTILINE):
        marks.append((match.group(1), match.end()))
    regions: dict[str, set[str]] = {}
    for index, (name, start) in enumerate(marks):
        end = marks[index + 1][1] if index + 1 < len(marks) else len(text)
        regions[name] = set(KEY_RE.findall(text[start:end]))
    return regions


def frontend_literals() -> set[str]:
    keys: set[str] = set()
    for path in sorted(FRONTEND_JS.rglob("*.js")):
        if path.name == "i18n.js":
            continue
        text = path.read_bytes().decode("utf-8")
        keys.update(CALL_RE.findall(text))
    return keys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not I18N_JS.exists():
        print(f"找不到词条文件：{I18N_JS}")
        return 2

    text = I18N_JS.read_bytes().decode("utf-8")
    regions = dict_regions(text)
    if set(regions) != {"zh", "en"}:
        print(f"词条表解析异常，只找到：{sorted(regions)}")
        return 2

    zh, en = regions["zh"], regions["en"]
    codes = backend_codes()
    literals = frontend_literals()

    problems: list[str] = []

    missing_zh = sorted(code for code in codes if code not in zh)
    missing_en = sorted(code for code in codes if code not in en)
    if missing_zh:
        problems.append("后端代码缺中文词条：" + ", ".join(missing_zh))
    if missing_en:
        problems.append("后端代码缺英文词条：" + ", ".join(missing_en))

    only_zh = sorted(zh - en)
    only_en = sorted(en - zh)
    if only_zh:
        problems.append("只有中文没有英文：" + ", ".join(only_zh))
    if only_en:
        problems.append("只有英文没有中文：" + ", ".join(only_en))

    missing_literal_zh = sorted(key for key in literals if key not in zh)
    missing_literal_en = sorted(key for key in literals if key not in en)
    if missing_literal_zh:
        problems.append("前端静态调用缺中文词条：" + ", ".join(missing_literal_zh))
    if missing_literal_en:
        problems.append("前端静态调用缺英文词条：" + ", ".join(missing_literal_en))

    if args.verbose:
        print(f"后端 advice.* 代码 {len(codes)} 个")
        print(f"前端静态 t() 词条 {len(literals)} 个")
        print(f"i18n.js：zh {len(zh)} 条 / en {len(en)} 条")
        unused = sorted((zh & en) - codes - literals)
        if unused:
            print(f"未被静态引用（可能是拼接键或历史遗留）：{len(unused)} 条")

    if problems:
        print("i18n 检查未通过：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(f"i18n 检查通过：后端 {len(codes)} 个代码、前端 {len(literals)} 个静态词条，中英均齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
