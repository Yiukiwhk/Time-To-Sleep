"""环境自检：确认运行期依赖可导入、版本可用。"""
import sys

MODULES = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "pydantic",
    "pydantic_settings",
    "bcrypt",
    "httpx",
    "pytest",
    "starlette",
    "email_validator",
    "anyio",
]


def main() -> int:
    print("interpreter:", sys.version)
    failures = []
    for name in MODULES:
        try:
            module = __import__(name)
            version = getattr(module, "__version__", "?")
            print(f"  OK   {name:18} {version}")
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"  FAIL {name:18} {type(exc).__name__}: {exc}")

    # 关键 ABI 检查：pydantic_core 的 C 扩展必须真的能加载
    try:
        from pydantic_core import ArgsKwargs  # noqa: F401

        print("  OK   pydantic_core.ArgsKwargs")
    except Exception as exc:  # noqa: BLE001
        failures.append("pydantic_core")
        print(f"  FAIL pydantic_core.ArgsKwargs {type(exc).__name__}: {exc}")

    try:
        import bcrypt

        bcrypt.hashpw(b"probe", bcrypt.gensalt(rounds=4))
        print("  OK   bcrypt hash/verify")
    except Exception as exc:  # noqa: BLE001
        failures.append("bcrypt")
        print(f"  FAIL bcrypt roundtrip {type(exc).__name__}: {exc}")

    print("\nfailures:", len(failures), failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
