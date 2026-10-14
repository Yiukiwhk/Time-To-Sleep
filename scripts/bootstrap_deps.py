#!/usr/bin/env python
"""离线/受限环境依赖安装器（不依赖 pip 的临时目录与 chmod）。

背景：本机沙箱禁止 chmod 与写入沙箱外临时目录，pip 无法正常工作。
本脚本改为：
  1. 直接访问 PyPI 下载 .whl 到项目内 .wheels/ 缓存；
  2. 用 zipfile 直接解包到 backend/.venv/Lib/site-packages；
  3. 自己解析 METADATA 里的 Requires-Dist 做依赖闭包。

安全阀（重要）：
  * 只安装 ALLOWED 里列出的发行版，其它依赖一律跳过并打印；
  * 依赖里带 extras 的条目（如 pkg[test]）直接跳过，避免把依赖方的
    测试/文档依赖整棵树拉进来（曾因此装进 784 个发行版、2.7GB）；
  * marker 无法判定时默认「不需要」，不是「需要」。

用法：
    python scripts/bootstrap_deps.py [--target DIR] [--clean] [--skip-deps] [packages...]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WHEEL_CACHE = PROJECT_ROOT / ".wheels"
VENV_ROOT = PROJECT_ROOT / "backend" / ".venv"
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
USER_AGENT = "ssp-bootstrap/2.0 (python {})".format(sys.version.split()[0])
MAX_DISTS = 60


def _venv_python(venv_root: Path) -> Path:
    """venv 的解释器路径（Windows 用 Scripts/python.exe，Linux 用 bin/python）。"""
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def default_site_packages(venv_root: Path) -> Path:
    """venv 的 site-packages 路径（Windows 与 POSIX 布局不同）。"""
    if os.name == "nt":
        return venv_root / "Lib" / "site-packages"
    version = "{}.{}".format(*sys.version_info[:2])
    return venv_root / "lib" / f"python{version}" / "site-packages"


def reexec_in_target_interpreter() -> None:
    """若解释器与目标 venv 不一致，用 venv 的解释器重新执行自己。

    关键：wheel 兼容性判定依赖 sys.version_info / sys.maxsize，
    必须在「目标解释器」里跑，否则会挑到 ABI 不匹配的 wheel
    （例如给 3.12 venv 装 cp310 的 pydantic_core）。
    """
    target_python = _venv_python(VENV_ROOT)
    if not target_python.exists():
        return
    if Path(sys.executable).resolve() == target_python.resolve():
        return
    if os.environ.get("SSP_BOOTSTRAP_REEXEC") == "1":
        return
    env = dict(os.environ, SSP_BOOTSTRAP_REEXEC="1")
    argv = [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]]
    print(f"[bootstrap] re-exec with target interpreter: {target_python}")
    sys.stdout.flush()
    completed = subprocess.run(argv, env=env)
    raise SystemExit(completed.returncode)

# 只允许这些发行版落地（顶层需求 + 真实运行时依赖）
ALLOWED = {
    "annotated-doc",
    "annotated-types",
    "anyio",
    "bcrypt",
    "certifi",
    "click",
    "colorama",
    "dnspython",
    "email-validator",
    "fastapi",
    "greenlet",
    "h11",
    "httpcore",
    "httpx",
    "idna",
    "iniconfig",
    "packaging",
    "pluggy",
    "pydantic",
    "pydantic-core",
    "pydantic-settings",
    "pygments",
    "pytest",
    "python-dotenv",
    "sniffio",
    "sqlalchemy",
    "starlette",
    "typing-extensions",
    "typing-inspection",
    "uvicorn",
}
# 归一化名（下划线/点 视作 连字符）
ALLOWED_NORM = {n.replace("_", "-").replace(".", "-") for n in ALLOWED}

DEFAULT_PACKAGES = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sqlalchemy>=2.0.30",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "email-validator>=2.1",
    "bcrypt>=4.1",
    "httpx>=0.27",
    "pytest>=8.0",
]

# 额外约束（在 DEFAULT_PACKAGES 约束之外追加），
# 用于避开拖入大树的版本：
#   fastapi>=0.141 起把 opentelemetry-api 变成硬依赖（遥测），无必要，锁在 0.140.x；
#   httpx 1.x 目前只有预发布版本。
EXTRA_CONSTRAINTS = {
    "fastapi": "<0.141",
    "httpx": ">=0.27,<1",
}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


# --------------------------------------------------------------------------- #
# 平台标签
# --------------------------------------------------------------------------- #
def _cp_version(tag: str) -> tuple[int, int] | None:
    """从 cp312 / cp312t 之类的标签里取出 (3, 12)。"""
    if not tag.startswith("cp"):
        return None
    digits = re.sub(r"\D", "", tag)
    if len(digits) < 2:
        return None
    major = int(digits[0])
    minor = int(digits[1:]) if len(digits) > 1 else 0
    return major, minor


# 目标平台覆盖（None = 当前系统）；`--platform linux-x86_64` 可在 Windows 上
# 预取 Linux 轮子，用于离线部署包。
_PLATFORM_OVERRIDE: str | None = None


def platform_tags() -> tuple[str, ...]:
    """当前平台可接受的 wheel 平台标签（精确匹配用）。"""
    if _PLATFORM_OVERRIDE:
        # 允许 `linux-x86_64` 这样的简写：展开成完整的 manylinux 标签集合。
        if _PLATFORM_OVERRIDE.startswith("linux-"):
            arch = _PLATFORM_OVERRIDE.split("-", 1)[1]
            tags = [f"manylinux_2_{minor}_{arch}" for minor in range(40, 16, -1)]
            tags += [
                f"manylinux2014_{arch}",
                f"manylinux2010_{arch}",
                f"manylinux1_{arch}",
                f"linux_{arch}",
            ]
            return tuple(tags)
        return (_PLATFORM_OVERRIDE,)
    if os.name == "nt":
        return ("win_amd64",) if sys.maxsize > 2 ** 32 else ("win32",)
    machine = (os.uname().machine if hasattr(os, "uname") else "") or "x86_64"
    arch = {"aarch64": "aarch64", "arm64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        machine, machine
    )
    tags = [f"manylinux_2_{minor}_{arch}" for minor in range(40, 16, -1)]
    tags += [f"manylinux2014_{arch}", f"manylinux2010_{arch}", f"manylinux1_{arch}", f"linux_{arch}"]
    return tuple(tags)


def is_compatible(filename: str) -> bool:
    """判断 wheel 文件名是否与当前解释器 + 当前平台匹配。

    关键点（曾因此装错 wheel）：
      * cpXY 是「精确」版本标签：cp310 的 wheel 在 3.12 上不可用，
        除非 ABI 标签是 abi3（稳定 ABI，可跨更高版本使用）；
      * py3 / py3X 是纯 Python 标签，py3 通吃，pyXY 要求 >= 自身版本；
      * 平台标签必须属于当前系统（win32 wheel 在 64 位解释器上会 DLL 加载失败）。
    """
    if not filename.endswith(".whl"):
        return False
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return False
    py_tags = parts[-3].split(".")
    abi_tags = parts[-2].split(".")
    plat_tags = parts[-1].split(".")

    major, minor = sys.version_info[:2]
    has_abi3 = any(tag == "abi3" for tag in abi_tags)
    has_none_abi = any(tag == "none" for tag in abi_tags)

    py_ok = False
    for tag in py_tags:
        if tag == "py3":
            py_ok = True
        elif re.fullmatch(r"py\d", tag):
            py_ok = int(tag[2:]) == major
        elif re.fullmatch(r"py\d{2}", tag):
            py_ok = (int(tag[2]), int(tag[3])) <= (major, minor)
        else:
            parsed = _cp_version(tag)
            if parsed is None:
                continue
            if parsed == (major, minor):
                py_ok = True
            elif has_abi3 and parsed <= (major, minor):
                py_ok = True
    if not py_ok:
        return False

    # ABI 标签：none（纯 Python）/ abi3（稳定 ABI）/ 与 py 标签同版本
    abi_ok = has_none_abi or has_abi3
    if not abi_ok:
        for tag in abi_tags:
            parsed = _cp_version(tag)
            if parsed == (major, minor):
                abi_ok = True
    if not abi_ok:
        return False

    # abi3 wheel 走稳定 ABI，平台标签只是一个任意值（bcrypt / pydantic_core
    # 的 abi3 轮子就带着 win_amd64 标签发布），因此不做平台校验。
    if has_abi3:
        return True

    accepted = set(platform_tags())
    for tag in plat_tags:
        if tag == "any":
            return True
        # manylinux 的 platform tag 里用 '.' 分隔多个别名
        # （例如 manylinux_2_17_x86_64.manylinux2014_x86_64），逐个比对。
        if any(part in accepted for part in tag.split(".")):
            return True
    return False


# --------------------------------------------------------------------------- #
# 版本与依赖解析
# --------------------------------------------------------------------------- #
def parse_version(text: str) -> tuple:
    parts = re.findall(r"\d+", text)
    return tuple(int(p) for p in parts[:4]) if parts else (0,)


def version_key(text: str) -> tuple:
    version = parse_version(text)
    return version + (0,) * (4 - len(version))


def fetch_json(name: str) -> dict:
    url = PYPI_JSON.format(name=name)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(.*)$")

_MARKER_ENV = {
    "os_name": "nt" if os.name == "nt" else "posix",
    "sys_platform": "win32" if os.name == "nt" else "linux",
    "platform_system": "Windows" if os.name == "nt" else "Linux",
    "platform_machine": "AMD64" if os.name == "nt" else (os.uname().machine if hasattr(os, "uname") else "x86_64"),
    "platform_release": "",
    "platform_version": "",
    "implementation_name": "cpython",
    "implementation_version": "{}.{}.{}".format(*sys.version_info[:3]),
    "python_version": "{}.{}".format(*sys.version_info[:2]),
    "python_full_version": "{}.{}.{}".format(*sys.version_info[:3]),
    "extra": "",  # 我们从不安装 extras
}


def _marker_applies(marker: str) -> bool:
    """极简 marker 求值：只处理常见的平台/版本条件。

    无法判定时返回 False（保守：不装）。extras 相关 marker 一律 False。
    """
    marker = marker.strip()
    if not marker:
        return True
    if "extra" in marker:
        return False
    expr = marker
    for key in sorted(_MARKER_ENV, key=len, reverse=True):
        expr = re.sub(rf"\b{key}\b", repr(_MARKER_ENV[key]), expr)
    # 剩余的未知标识符（未替换到的名字）→ 判定失败
    leftovers = re.findall(r"\b(?!and\b|or\b|not\b|in\b)[A-Za-z_][A-Za-z_0-9]*\b", expr)
    if leftovers:
        return False
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - 仅常量表达式
    except Exception:  # noqa: BLE001
        return False


def parse_requirement(spec: str) -> tuple[str, str, str] | None:
    """把 "pkg[extra]>=1.0,<2 ; marker" 解析成 (name, extras, constraints)。

    带 extras 的依赖直接跳过：我们从不安装 extras。
    """
    if ";" in spec:
        spec, marker = spec.split(";", 1)
        if not _marker_applies(marker):
            return None
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return None
    match = _SPEC_RE.match(spec)
    if not match:
        return None
    name, constraints = match.group(1), match.group(2).strip()
    extras = ""
    extra_match = re.search(r"\[([^\]]*)\]", spec)
    if extra_match:
        extras = extra_match.group(1).strip()
    if extras:
        return None
    return name, extras, constraints


def constraints_satisfied(version: str, constraints: str) -> bool:
    if not constraints:
        return True
    current = version_key(version)
    for clause in constraints.split(","):
        clause = clause.strip()
        if not clause or clause.startswith(";"):
            continue
        match = re.match(r"^(===|==|>=|<=|!=|~=|>|<)\s*(.+)$", clause)
        if not match:
            continue
        operator, raw = match.group(1), match.group(2).strip().rstrip(".*")
        if operator == "~=":
            base = version_key(raw)
            if not (current >= base and current[: len(base) - 1] == base[: len(base) - 1]):
                return False
            continue
        target = version_key(raw)
        if operator in ("==", "===") and current != target:
            return False
        if operator == "!=" and current == target:
            return False
        if operator == ">=" and current < target:
            return False
        if operator == "<=" and current > target:
            return False
        if operator == ">" and current <= target:
            return False
        if operator == "<" and current >= target:
            return False
    return True


def select_wheel(package_json: dict, constraints: str, allow_prerelease: bool = False) -> tuple[str, str, str]:
    """在候选版本中挑一个满足约束、且有可用 wheel 的稳定版本。"""
    releases = package_json.get("releases", {})
    candidates = sorted(releases.keys(), key=version_key, reverse=True)

    def usable(version: str) -> bool:
        if not constraints_satisfied(version, constraints):
            return False
        if allow_prerelease:
            return True
        # 排除 yanked 与预发布版本（默认只装稳定版）
        files = releases.get(version) or []
        if files and all(f.get("yanked") for f in files):
            return False
        return not any(mark in version for mark in ("a", "b", "rc", "dev", ".post"))

    for version in [v for v in candidates if usable(v)] or (candidates if allow_prerelease else []):
        files = releases.get(version) or []
        wheels = [f for f in files if f.get("filename", "").endswith(".whl")]
        compatible = [f for f in wheels if is_compatible(f["filename"])]
        if not compatible:
            continue
        # 优先纯 Python wheel，其次 abi3，最后 cp 专用
        compatible.sort(key=lambda f: (0 if f["filename"].endswith("none-any.whl") else 1, len(f["filename"])))
        chosen = compatible[0]
        return version, chosen["url"], chosen["filename"]
    raise RuntimeError(f"no compatible wheel found for {package_json['info']['name']} {constraints!r}")


def download(url: str, destination: Path) -> Path:
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    destination.write_bytes(data)
    return destination


def read_metadata(wheel_path: Path) -> tuple[str, str, list[str]]:
    """返回 (name, version, requires_dist)。"""
    with zipfile.ZipFile(wheel_path) as archive:
        meta_name = next(
            (n for n in archive.namelist() if n.endswith(".dist-info/METADATA")), None
        )
        if not meta_name:
            return wheel_path.stem, "0", []
        raw = archive.read(meta_name).decode("utf-8", "replace")
    name = wheel_path.stem
    version = "0"
    requires: list[str] = []
    for line in raw.splitlines():
        if line.startswith("Name: "):
            name = line[6:].strip()
        elif line.startswith("Version: "):
            version = line[9:].strip()
        elif line.startswith("Requires-Dist: "):
            requires.append(line[14:].strip())
    return name, version, requires


def extract(wheel_path: Path, target: Path) -> int:
    """解包 wheel（跳过脚本目录）；返回写出的文件数。"""
    count = 0
    with zipfile.ZipFile(wheel_path) as archive:
        for member in archive.infolist():
            name = member.filename
            if name.endswith("/"):
                continue
            if ".data/" in name:
                # wheel 的 data 目录：<pkg>-<ver>.data/{scripts|data|headers|purelib|platlib}/...
                _, _, rest = name.partition(".data/")
                category, _, relative = rest.partition("/")
                if category == "scripts" or not relative:
                    continue
                destination = target / relative
            else:
                destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source:
                destination.write_bytes(source.read())
            count += 1
    return count


def _wheel_parts(filename: str) -> list[str] | None:
    """把 wheel 文件名拆成 {name}-{version}(-{build})?-{python}-{abi}-{platform}。

    bcrypt 之类发行版只带 4 段（没有 build tag），因此所有取值都从末尾数。
    """
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return parts


def _dist_info(parts: list[str]) -> tuple[str, str, str, str, str]:
    """(name, version, python tag, abi tag, platform tag)。"""
    return parts[0], parts[1], parts[-3], parts[-2], parts[-1]


def wheel_tag_strings(wheel_path: Path) -> list[str]:
    """读 wheel 内的 *.dist-info/WHEEL 里的 Tag: 行（可能有多条）。"""
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            wheel_meta = next((n for n in archive.namelist() if n.endswith(".dist-info/WHEEL")), None)
            if wheel_meta is None:
                return []
            raw = archive.read(wheel_meta).decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError):
        return []
    return [line[4:].strip() for line in raw.splitlines() if line.startswith("Tag:")]


def wheel_matches_platform(wheel_path: Path) -> bool:
    """判断本地 wheel 是否真的适配「目标平台」。

    纯 Python 轮子（abi 标签为 none）天然跨平台，直接放行；
    带扩展模块的轮子才需要看 WHEEL 元数据里的 Tag: 行——因为文件名里的
    平台标签只按构建机写（bcrypt 的 abi3 轮子带 win_amd64 标签，Linux 也能用）。
    """
    parts = _wheel_parts(wheel_path.name)
    if parts is None:
        return False
    if parts[-2] == "none":
        return True

    accepted = set(platform_tags())
    tags = wheel_tag_strings(wheel_path)
    for tag in tags:
        pieces = tag.split("-")
        if len(pieces) != 3:
            continue
        _, abi, platform_tag = pieces
        if any(part in accepted for part in platform_tag.split(".")):
            return True
        if abi == "abi3" and platform_tag == "any":
            return True

    has_abi3 = "abi3" in parts[-2]
    for tag in parts[-1].split("."):
        if tag == "any" and has_abi3:
            return True
        if tag in accepted:
            return True
    return False


def build_wheel_index(directory: Path) -> dict[str, dict[str, Path]]:
    """扫描本地 wheel 仓库：{归一化名: {版本: wheelfile}}（只保留平台兼容的）。"""
    index: dict[str, dict[str, Path]] = {}
    if not directory.is_dir():
        return index
    for wheel in sorted(directory.glob("*.whl")):
        if not is_compatible(wheel.name):
            continue
        parts = _wheel_parts(wheel.name)
        if parts is None:
            continue
        if not wheel_matches_platform(wheel):
            continue
        name, version = norm(parts[0]), parts[1]
        index.setdefault(name, {})[version] = wheel
    return index


def select_local_wheel(
    index: dict[str, dict[str, Path]], key: str, constraints: str
) -> tuple[str, Path] | None:
    """从本地仓库挑一个满足约束的最高版本。"""
    versions = index.get(key)
    if not versions:
        return None
    for version in sorted(versions.keys(), key=version_key, reverse=True):
        if constraints_satisfied(version, constraints):
            return version, versions[version]
    return None


def main() -> int:
    reexec_in_target_interpreter()

    parser = argparse.ArgumentParser(description="Install wheels without pip temp dirs")
    parser.add_argument("packages", nargs="*", default=None)
    parser.add_argument("--target", default=None, help="site-packages 目录（默认当前 venv）")
    parser.add_argument("--wheels", default=None, help="本地 wheel 仓库目录（离线优先）")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--allow-any", action="store_true", help="禁用 ALLOWED 白名单（危险）")
    parser.add_argument(
        "--platform",
        default=None,
        help="目标平台标签覆盖，例如 linux-x86_64（默认当前系统）",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="只把解析到的 wheel 下载进 .wheels 缓存，不安装",
    )
    args = parser.parse_args()

    global _PLATFORM_OVERRIDE
    _PLATFORM_OVERRIDE = args.platform

    target = Path(args.target) if args.target else default_site_packages(VENV_ROOT)
    if args.clean and target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    WHEEL_CACHE.mkdir(parents=True, exist_ok=True)
    local_index = build_wheel_index(Path(args.wheels)) if args.wheels else {}
    if local_index:
        print(f"[bootstrap] local wheelhouse: {args.wheels} ({len(local_index)} distributions)")

    requested = args.packages or DEFAULT_PACKAGES
    queue: list[tuple[str, str]] = []          # (name, constraints)
    for spec in requested:
        parsed = parse_requirement(spec)
        if parsed:
            queue.append((parsed[0], parsed[2]))

    installed: list[tuple[str, str]] = []
    skipped: list[str] = []
    package_json_cache: dict[str, dict] = {}
    resolved: dict[str, str] = {}              # 归一化名 -> 版本
    seen: set[str] = set()

    while queue:
        if len(installed) >= MAX_DISTS:
            print(f"  ! 已安装 {MAX_DISTS} 个发行版，停止（安全阀）")
            break
        name, constraints = queue.pop(0)
        key = norm(name)
        if key in seen:
            continue
        if not args.allow_any and key not in ALLOWED_NORM:
            skipped.append(key)
            continue
        extra = EXTRA_CONSTRAINTS.get(key)
        if extra:
            constraints = f"{constraints},{extra}" if constraints else extra
        wheel_path: Path | None = None
        version = ""
        meta_name = name
        meta_version = "0"
        requires: list[str] = []

        if local_index:
            picked = select_local_wheel(local_index, key, constraints)
            if picked is not None:
                version, wheel_path = picked
                meta_name, meta_version, requires = read_metadata(wheel_path)
                if norm(meta_name) != key:
                    wheel_path = None  # 文件名与 METADATA 不一致，退回 PyPI
        if wheel_path is None:
            if key in package_json_cache:
                package_json = package_json_cache[key]
            else:
                try:
                    package_json = fetch_json(name)
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                    print(f"  ! skip {name}: {exc}")
                    continue
                package_json_cache[key] = package_json
            try:
                version, url, filename = select_wheel(package_json, constraints)
            except RuntimeError as exc:
                # 该版本轮子不可用（例如只有源码包）时不要中断整条解析链
                print(f"  ! {exc}")
                continue
            wheel_path = download(url, WHEEL_CACHE / filename)
            meta_name, meta_version, requires = read_metadata(wheel_path)

        if key in resolved:
            # 已装过这个发行版：只在版本不一致时告警，避免重复解包
            if parse_version(resolved[key]) != parse_version(version):
                print(f"  ~ {name}: 已装 {resolved[key]}，忽略本次约束 {version}")
            continue
        seen.add(key)
        if args.download_only:
            count = 0
        else:
            count = extract(wheel_path, target)
        resolved[key] = meta_version
        installed.append((meta_name, meta_version))
        print(f"  + {meta_name} {meta_version}  ({count} files)")

        if not args.skip_deps:
            for requirement in requires:
                parsed = parse_requirement(requirement)
                if parsed:
                    queue.append((parsed[0], parsed[2]))

    print(f"\ninstalled {len(installed)} distributions into {target}")
    for name, version in sorted(installed):
        print(f"    {name}=={version}")
    if skipped:
        print("\nskipped (not in ALLOWED):")
        for name in sorted(set(skipped)):
            print(f"    {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
