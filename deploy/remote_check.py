"""Ad-hoc remote inspection over paramiko.

Usage:
    python remote_check.py [remote-script-file]

Reads the script from a file (default: deploy/remote_check.sh next to this
file) and runs it on the server with bash -s, streaming both stdout and
stderr back.  Kept separate from the deployment scripts so that diagnostics
never need shell quoting through PowerShell.

Connection settings come from the environment:
    SSP_DEPLOY_HOST      server address (required)
    SSP_DEPLOY_USER      ssh user (default: root)
    SSP_DEPLOY_PASSWORD  ssh password (default: empty, e.g. when using keys)
    SSP_DEPLOY_PORT      ssh port (default: 22)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("SSP_DEPLOY_HOST", "")
if not HOST:
    print("缺少服务器地址：请先设置环境变量 SSP_DEPLOY_HOST=<服务器地址>（可选 SSP_DEPLOY_USER / SSP_DEPLOY_PASSWORD / SSP_DEPLOY_PORT）")
    sys.exit(2)
USER = os.environ.get("SSP_DEPLOY_USER", "root")
PASSWORD = os.environ.get("SSP_DEPLOY_PASSWORD", "")
PORT = int(os.environ.get("SSP_DEPLOY_PORT", "22"))


def main() -> int:
    here = Path(__file__).resolve().parent
    script_path = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "remote_check.sh"
    script = script_path.read_text(encoding="utf-8")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=HOST,
        port=PORT,
        username=USER,
        password=PASSWORD,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        stdin, stdout, stderr = client.exec_command("bash -s", timeout=180)
        stdin.write(script)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
    finally:
        client.close()

    sys.stdout.write(out)
    if err.strip():
        sys.stdout.write("\n--- stderr ---\n" + err)
    print(f"\n[remote exit code: {code}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
