# 配置项

所有可调参数集中在 `backend/app/config.py` 的 `Settings` 类里，运行时通过**环境变量**
覆盖。变量名统一加 `SSP_` 前缀（例如类里的 `PORT` 对应环境变量 `SSP_PORT`）。

## 读取规则

- 取值优先级：**命令行参数 > 环境变量 > 代码内默认值**。`run.py` 的 `--host/--port` 会先写入
  `SSP_HOST` / `SSP_PORT` 再交给服务。
- 空字符串等同于“未设置”，会回落到默认值。
- 布尔值接受 `1 / true / yes / on`（不分大小写）为真，其余为假。
- 列表型变量用英文逗号分隔（如 `SSP_CORS_ORIGINS`）。
- 配置在**进程启动时读取一次**，改完必须重启服务（systemd：`systemctl daemon-reload &&
  systemctl restart sleep-schedule`）。

> **关于 `.env`**：当前实现**不会自动读取 `.env` 文件** —— `config.py` 只从进程环境变量取值
> （`os.environ.get("SSP_...")`），仓库里也没有引入 `python-dotenv`。因此请按下面三种方式之一
> 注入变量，不要直接往项目根目录丢 `.env` 并期望生效：
>
> - **Windows 本地开发**（当前会话有效，推荐）：
>
>   ```powershell
>   $env:SSP_PORT = "8011"
>   $env:SSP_DEBUG = "true"
>   backend\.venv\Scripts\python.exe run.py
>   ```
>
>   （一次性设置：`setx SSP_DEBUG true`，需要重开终端生效。）
> - **Linux 前台临时运行**：
>
>   ```bash
>   SSP_HOST=127.0.0.1 SSP_PORT=8011 python run.py --no-browser
>   ```
> - **systemd 服务**：写进单元的 `[Service]` 段（`deploy/remote_install.sh` 生成的就是这种形式），
>   改完 `systemctl daemon-reload && systemctl restart sleep-schedule`：
>
>   ```ini
>   [Service]
>   WorkingDirectory=/opt/sleep-schedule/backend
>   Environment=SSP_HOST=127.0.0.1
>   Environment=SSP_PORT=8011
>   Environment=SSP_DATA_DIR=/var/lib/sleep-schedule
>   Environment=SSP_DB_PATH=/var/lib/sleep-schedule/app.db
>   Environment=SSP_LOG_DIR=/var/log/sleep-schedule
>   Environment=SSP_LOG_FILE=/var/log/sleep-schedule/app.log
>   Environment=SSP_LOG_LEVEL=INFO
>   EnvironmentFile=-/etc/sleep-schedule.env   # 可选：把敏感变量单独放这个文件（0600 权限）
>   ```
>
>   若确实想要 `.env` 语义，systemd 侧用 `EnvironmentFile=`（见上一行）；本地开发用上面的
>   PowerShell/systemd 方式，或自己包一层读取 `.env` 的启动脚本。

## 环境变量一览

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SSP_HOST` | `0.0.0.0` | 监听地址。对外暴露时建议改 `127.0.0.1` 交由 Nginx 反代（`run.py --host` 可覆盖）。 |
| `SSP_PORT` | `8000` | 监听端口（线上示例用 `8011`，`run.py --port` 可覆盖）。 |
| `SSP_DATA_DIR` | `<项目根>/data` | 数据目录，启动时自动创建。 |
| `SSP_LOG_DIR` | `<项目根>/logs` | 日志目录，启动时自动创建。 |
| `SSP_FRONTEND_DIR` | `<项目根>/frontend` | 静态前端目录；指向不存在的位置时会关闭静态托管（测试用它隔离）。 |
| `SSP_DB_PATH` | `<项目根>/data/app.db` | SQLite 文件路径（WAL 模式，同目录会出现 `-wal` / `-shm`）。 |
| `SSP_LOG_FILE` | `<项目根>/logs/app.log` | 轮转日志文件路径。 |
| `SSP_DB_BUSY_TIMEOUT_MS` | `8000` | SQLite 锁等待超时（毫秒）。 |
| `SSP_DB_WRITE_RETRIES` | `6` | 写入遇到 `database is locked` 的最大重试次数。 |
| `SSP_DB_RETRY_BASE_DELAY` | `0.08` | 写入重试的初始退避秒数（指数增长）。 |
| `SSP_DB_RETRY_MAX_DELAY` | `1.0` | 写入重试的单次最大退避秒数。 |
| `SSP_SESSION_COOKIE_NAME` | `ssp_session` | 用户端会话 Cookie 名（HttpOnly）。 |
| `SSP_ADMIN_COOKIE_NAME` | `ssp_admin_session` | 管理后台会话 Cookie 名（与用户端隔离）。 |
| `SSP_SESSION_TTL_SECONDS` | `2592000`（30 天） | 勾选“记住登录”时的会话有效期。 |
| `SSP_SESSION_TTL_SHORT_SECONDS` | `43200`（12 小时） | 未勾选“记住登录”时的会话有效期。 |
| `SSP_LOGIN_MAX_FAILURES` | `5` | 连续登录失败达到该次数即锁定账号。 |
| `SSP_LOGIN_LOCK_SECONDS` | `900`（15 分钟） | 锁定持续时间。 |
| `SSP_LOGIN_FAILURE_WINDOW_SECONDS` | `900`（15 分钟） | 失败计数窗口，窗口外的失败不计入。 |
| `SSP_WRITE_COOLDOWN_SECONDS` | `30` | 同一用户两次写操作的冷却秒数，命中返回 429 `error.write_too_fast`；铺数据脚本可临时设为 `0`。 |
| `SSP_RETENTION_MONTHS` | `6` | 睡眠记录保留月数，超期由清理任务删除。 |
| `SSP_CLEANUP_INTERVAL_SECONDS` | `21600`（6 小时） | 清理任务运行间隔（最小 60 秒）。 |
| `SSP_DEFAULT_PAGE_SIZE` | `20` | 分页默认每页条数。 |
| `SSP_MAX_PAGE_SIZE` | `100` | 分页每页最大条数（超出会被夹到该值）。 |
| `SSP_MAX_USERNAME_LEN` | `32` | 用户名长度上限。 |
| `SSP_MIN_USERNAME_LEN` | `3` | 用户名长度下限。 |
| `SSP_MIN_PASSWORD_LEN` | `8` | 密码长度下限。 |
| `SSP_MAX_PASSWORD_LEN` | `72` | 密码长度上限（bcrypt 上限，超出直接 422）。 |
| `SSP_MAX_NOTE_LEN` | `200` | 睡眠记录备注长度上限。 |
| `SSP_ADMIN_PASSWORD_PARTS` | `3` | 管理员多段密码段数（登录时需依次提供全部段）。 |
| `SSP_CORS_ORIGINS` | `http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173` | 允许跨域访问 API 的来源白名单（逗号分隔）。 |
| `SSP_CORS_ORIGIN_REGEX` | 本机 + 内网网段的正则 | 允许的来源正则（默认覆盖 `localhost`、`127.0.0.1`、`10.x`、`192.168.x`、`172.16–31.x`）。 |
| `SSP_BOOTSTRAP_ADMIN_USERNAME` | `admin` | 首次启动时创建（或提升）的管理员用户名。 |
| `SSP_BOOTSTRAP_ADMIN_PASSWORDS` | `Admin@12345,Admin#67890,Admin$24680` | 内置管理员的三段密码（逗号分隔）。**第 1 段是账号密码，第 2/3 段存哈希后登录时必须一并提供。** |
| `SSP_LOG_LEVEL` | `INFO` | 日志级别（`DEBUG` / `INFO` / `WARNING` / `ERROR`）。 |
| `SSP_LOG_RETENTION_ROWS` | `20000` | `system_logs` 表保留的最大行数，超出由清理任务裁剪。 |
| `SSP_DEBUG` | `false` | 调试模式：为 `true` 时暴露 `/api/docs`（Swagger）与 `/api/openapi.json`，并放宽部分校验。 |

## 常见组合

### 本地开发

```powershell
$env:SSP_DEBUG = "true"          # 打开 /api/docs
$env:SSP_PORT = "8011"           # 避开 8000 上跑着的其它服务
$env:SSP_LOG_LEVEL = "DEBUG"
backend\.venv\Scripts\python.exe run.py --reload
```

### 生产（单机 Nginx + systemd）

关键差异只有四点：**只监听回环、明确的绝对数据/日志路径、关闭 DEBUG、收紧 CORS**。

```ini
Environment=SSP_HOST=127.0.0.1
Environment=SSP_PORT=8011
Environment=SSP_DATA_DIR=/var/lib/sleep-schedule
Environment=SSP_DB_PATH=/var/lib/sleep-schedule/app.db
Environment=SSP_LOG_DIR=/var/log/sleep-schedule
Environment=SSP_LOG_FILE=/var/log/sleep-schedule/app.log
Environment=SSP_LOG_LEVEL=INFO
Environment=SSP_DEBUG=false
Environment=SSP_CORS_ORIGINS=https://<your-domain>
Environment=SSP_BOOTSTRAP_ADMIN_PASSWORDS=<第一次启动前改成三段强口令>
```

上线前请务必改掉内置管理员的三段默认密码：默认值是公开的，等于没有防护。第 1 段可在登录后
从个人设置里修改，第 2/3 段只能在**首次建库前**通过 `SSP_BOOTSTRAP_ADMIN_PASSWORDS` 设定。

### 测试与 CI

`tests/smoke_test.py`、`tests/test_advice_engine.py`、`.github/workflows/ci.yml` 都会把
`SSP_DATA_DIR` / `SSP_LOG_DIR` / `SSP_DB_PATH` / `SSP_LOG_FILE` 指到临时目录，并把
`SSP_WRITE_COOLDOWN_SECONDS` 设为 `0`（避免连续写入被 429 挡住），因此测试不会碰你的
`data/app.db`。要复现“连续写入被拒”的行为时，把冷却设回默认值：

```powershell
$env:SSP_WRITE_COOLDOWN_SECONDS = "0"   # 本地铺数据
Remove-Item Env:\SSP_WRITE_COOLDOWN_SECONDS   # 恢复默认 30 秒
```

## 相关文档

- 部署拓扑、systemd 单元与发版流程：[DEPLOYMENT.md](DEPLOYMENT.md)
- 开发环境与自检命令：[DEVELOPMENT.md](DEVELOPMENT.md)
- 接口与错误码：[API.md](API.md)
