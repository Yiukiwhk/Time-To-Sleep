"""Compare a deployed JS file with its local counterpart (encoding + import shape).

Usage:  python deploy/compare_encoding.py <remote_file> <local_file> [<remote_file> <local_file> ...]
Prints an ASCII-only JSON report so console codepages cannot distort the result.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

pairs = sys.argv[1:]
report = {}
for remote_path, local_path in zip(pairs[0::2], pairs[1::2]):
    remote = pathlib.Path(remote_path).read_bytes()
    local = pathlib.Path(local_path).read_bytes()
    rt = remote.decode("utf-8", errors="replace")
    lt = local.decode("utf-8", errors="replace")
    report[local_path] = {
        "remote_bytes": len(remote),
        "local_bytes": len(local),
        "remote_cjk": len(re.findall(r"[\u4e00-\u9fff]", rt)),
        "local_cjk": len(re.findall(r"[\u4e00-\u9fff]", lt)),
        "remote_replacement_chars": rt.count("\ufffd"),
        "local_replacement_chars": lt.count("\ufffd"),
        "remote_relative_shared": "../shared/" in rt,
        "local_absolute_shared": 'from "/shared/' in lt,
        "remote_sample_cjk": re.findall(r"[\u4e00-\u9fff]{3,}", rt)[:3],
        "local_sample_cjk": re.findall(r"[\u4e00-\u9fff]{3,}", lt)[:3],
        "identical_except_imports": rt.replace('from "../shared/', 'from "/shared/').replace(
            'from "../../shared/', 'from "/shared/'
        )
        == lt,
    }
print(json.dumps(report, ensure_ascii=True, indent=1))
