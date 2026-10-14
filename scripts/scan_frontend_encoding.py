"""扫描前端文本文件是否有编码损伤（BOM、非 UTF-8、GBK 双重编码乱码）。

用法：python scripts/scan_frontend_encoding.py
退出码：0 = 干净，1 = 有可疑文件（CI 直接用它当门禁）。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "frontend"
# 只收录“正常中文里几乎不会出现”的乱码残留字符（GBK/UTF-8 双重编码的典型产物），
# 以及解码失败的替换字符 U+FFFD。
# 注意：不要把「的」「劳」这类常用汉字放进来——它们在任何中文文件里都会命中，
# 会让本检查永久失败（历史 bug：曾把「的」当作乱码标记，导致 15 个文件误报）。
MOJIBAKE_MARKERS = (
    "\u9225",  # 鈥
    "\u951b",  # 锛
    "\u7ee0",  # 绠
    "\u93b4",  # 鎴
    "\u935c",  # 鍜
    "\u9427",  # 鐧
    "\ufffd",  # 替换字符：解码失败的直接证据
)
SUFFIXES = ("*.js", "*.css", "*.html", "*.svg", "*.json")


def main() -> int:
    suspects = 0
    for pattern in SUFFIXES:
        for path in sorted(ROOT.rglob(pattern)):
            raw = path.read_bytes()
            bom = raw.startswith(b"\xef\xbb\xbf")
            try:
                text = raw.decode("utf-8")
                enc = "utf-8"
            except UnicodeDecodeError:
                text = raw.decode("utf-8", "replace")
                enc = "NOT-UTF8"
            moji = sum(text.count(ch) for ch in MOJIBAKE_MARKERS)
            if bom or enc != "utf-8" or moji:
                suspects += 1
                label = "BOM" if bom else "   "
                rel = path.relative_to(ROOT.parent)
                print(f"{label} {enc:8} mojibake={moji:4}  {rel}")
    print(f"suspect files: {suspects}")
    return 1 if suspects else 0


if __name__ == "__main__":
    sys.exit(main())
