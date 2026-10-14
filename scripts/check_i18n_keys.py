"""檢查前端用到的 i18n key 是否都存在于 frontend/shared/i18n.js 的两个字典中。

用法（在 sleep-schedule 目录下）：
    backend/.venv/Scripts/python.exe scripts/check_i18n_keys.py

输出缺失的 key 及其出现位置；有缺失时退出码为 1。
仅做静态文本扫描，允许 data-i18n / t("...") / tp("...", { ... }) 三种写法。

同时校验**带参数的调用**：tp("key", { a, b }) 里字典值用到的每个 {name} 占位符
都必须在该对象字面量里出现（历史上 advice.planMeta.targetDate 因字典缺词条、
advice.meal.lunch_advanced 因漏传 {streak} 而在页面上漏出键名/占位符）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "frontend" / "shared" / "i18n.js"

KEY_RE = re.compile(r'"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)"\s*:')
# t("key" / tp("key" / data-i18n="key"（含 -placeholder/-title）
USE_RE = re.compile(r"""(?:\b(?:t|tp)\(|data-i18n(?:-placeholder|-title)?=["'])\s*["'`]([A-Za-z][A-Za-z0-9_.]*)["'`]""")
# tp("key", { a: x, b: y }) —— 只处理第二个参数是内联对象字面量的调用
PARAM_RE = re.compile(
    r"""\btp\(\s*["'`]([A-Za-z][A-Za-z0-9_.]*)["'`]\s*,\s*\{([^{}]*)\}""",
    re.S,
)
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
DYNAMIC_RE = re.compile(r"records\.meal\.|advice\.tip\.")


def parse_dicts() -> tuple[dict[str, str], dict[str, str]]:
    """返回 (zh, en) 两个字典的 key->原文，用于检查两种语言是否配对。"""
    text = I18N.read_text(encoding="utf-8")
    start = text.index("{")
    end = text.rindex("}")
    body = text[start + 1 : end]
    # 顶层形如  zh: { ... },  en: { ... }
    parts = re.split(r"\n  (zh|en)\s*:\s*\{", body)
    # parts = ['', 'zh', '...dict body...', 'en', '...dict body...']
    dicts: dict[str, dict[str, str]] = {}
    for index in range(1, len(parts) - 1, 2):
        name = parts[index]
        chunk = parts[index + 1]
        chunk = chunk[: chunk.rindex("}")] if "}" in chunk else chunk
        found: dict[str, str] = {}
        for line in chunk.splitlines():
            match = re.match(r'\s*"([^"]+)"\s*:\s*"(.*)",?\s*$', line)
            if match:
                found[match.group(1)] = match.group(2)
        dicts[name] = found
    return dicts.get("zh", {}), dicts.get("en", {})


def load_keys() -> tuple[set[str], dict[str, int]]:
    text = I18N.read_text(encoding="utf-8")
    # 两个字典：按 "}," 分隔的顶层对象较难解析，这里直接收集所有 key
    keys: dict[str, int] = {}
    for match in KEY_RE.finditer(text):
        keys[match.group(1)] = keys.get(match.group(1), 0) + 1
    return set(keys), keys


def scan_uses() -> tuple[dict[str, set[str]], list[tuple[str, str, list[str], str]]]:
    """返回 (key -> 出现的文件集合, 参数问题列表)。"""
    uses: dict[str, set[str]] = {}
    param_problems: list[tuple[str, str, list[str], str]] = []
    targets = list((ROOT / "frontend").rglob("*.js")) + list((ROOT / "frontend").rglob("*.html"))
    for path in targets:
        if path.name == "i18n.js":
            continue
        rel = str(path.relative_to(ROOT))
        text = path.read_text(encoding="utf-8")
        for match in USE_RE.finditer(text):
            key = match.group(1)
            if not key or key.endswith("."):
                continue
            uses.setdefault(key, set()).add(rel)
        for match in PARAM_RE.finditer(text):
            key, body = match.group(1), match.group(2)
            if "..." in body:
                continue  # 展开语法：无法静态判定，跳过
            provided: set[str] = set()
            for token in body.split(","):
                found = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", token)
                if found:
                    provided.add(found.group(1))  # 同时覆盖 { streak } 与 { shift: x }
            param_problems.append((key, rel, sorted(provided), body.strip()))
    return uses, param_problems


def main() -> int:
    known, _counts = load_keys()
    uses, param_problems = scan_uses()
    zh, en = parse_dicts()
    missing = []
    for key, where in sorted(uses.items()):
        if key in known:
            continue
        # 模板拼接的 key（t(`records.meal.${x}`)）不会命中 USE_RE，这里只报直接写死的
        missing.append((key, sorted(where)))

    # tp(...) 内联对象字面量：字典里的每个 {name} 都必须在调用点出现
    param_errors: list[str] = []
    for key, rel, provided, body in param_problems:
        if key not in known:
            continue  # 缺键的情况已由 missing 报告
        needed: set[str] = set()
        for text in (zh.get(key, ""), en.get(key, "")):
            needed |= set(PLACEHOLDER_RE.findall(text))
        gap = sorted(needed - set(provided))
        if gap:
            param_errors.append(f"  - {key} 缺少参数 {gap}  ({rel})  传入: {{{body}}}")

    # 中英文字典 key 数量应当一致
    text = I18N.read_text(encoding="utf-8")
    dict_count = len(re.findall(r"^  \},?$", text, re.M))
    print(f"i18n keys found: {len(known)} (dict blocks: {dict_count})")
    print(f"parsed zh keys: {len(zh)}  en keys: {len(en)}")
    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    if only_zh:
        print(f"only in zh: {len(only_zh)} -> {only_zh[:12]}")
    if only_en:
        print(f"only in en: {len(only_en)} -> {only_en[:12]}")

    problems = 0
    if only_zh or only_en:
        problems += 1
    print(f"keys used in frontend: {len(uses)}")
    if missing:
        print(f"missing keys: {len(missing)}")
        for key, where in missing:
            print(f"  - {key}  ({', '.join(where)})")
        problems += 1
    else:
        print("missing keys: 0")
    print(f"tp() calls with inline params checked: {len(param_problems)}")
    if param_errors:
        print(f"placeholder mismatches: {len(param_errors)}")
        for line in param_errors:
            print(line)
        problems += 1
    else:
        print("placeholder mismatches: 0")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
