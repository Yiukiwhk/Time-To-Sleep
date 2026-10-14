"""Show exactly how a local file differs from its deployed counterpart.

Usage: python deploy/diff_deployed.py <remote_copy> <local_file> [--max 6]
"""
from __future__ import annotations

import difflib
import pathlib
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
limit = 6
if "--max" in sys.argv:
    limit = int(sys.argv[sys.argv.index("--max") + 1])

remote_path, local_path = args[0], args[1]
remote_raw = pathlib.Path(remote_path).read_bytes()
local_raw = pathlib.Path(local_path).read_bytes()
print(f"remote bytes={len(remote_raw)} crlf={remote_raw.count(bytes([13, 10]))} bom={remote_raw[:3] == bytes([239, 187, 191])}")
print(f"local  bytes={len(local_raw)} crlf={local_raw.count(bytes([13, 10]))} bom={local_raw[:3] == bytes([239, 187, 191])}")
r = remote_raw.decode("utf-8").splitlines()
l = local_raw.decode("utf-8").splitlines()
diff = [d for d in difflib.unified_diff(r, l, "deployed", "local", lineterm="", n=0)]
print(f"diff hunks: {len([d for d in diff if d.startswith('@@')])}")
for line in diff[: limit + 4]:
    print("  " + line[:180])
