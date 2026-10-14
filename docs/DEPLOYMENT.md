# 部署与运维

本文描述单机部署（Nginx + systemd + SQLite）的拓扑、Nginx vhost 要点、发版脚本用法、
发版检查清单与回滚策略，以及运维要点和已知限制。开发环境搭建见
[DEVELOPMENT.md](DEVELOPMENT.md)，配置项含义见 [CONFIGURATION.md](CONFIGURATION.md)。

> 下文所有主机名、地址、账号都写成占位符：站点地址用 `http://<your-server>`，SSH 主机用
> `<your-server>`，登录用户用 `<deploy-user>`，口令一律通过环境变量或本地未入库的设置文件提供
> （见 `deploy/local.settings.example.ps1`）。请勿把真实凭据写进仓库。

## 服务器拓扑

```text
浏览器
  │  http://<your-server>/
  ▼
Nginx (监听 80)
  ├── /user/、/admin/、/shared/  → 静态文件，root /var/www/sleep-schedule
  ├── /api/                      → 反向代理 http://127.0.0.1:8011/api/
  └── /healthz                   → 反向代理 http://127.0.0.1:8011/healthz
                                        │
                                        ▼
                        systemd 服务 sleep-schedule
                        （uvicorn app.main:app，单进程单 worker，
                          工作目录 /opt/sleep-schedule/backend，
                          服务用户 sleepapp，监听 127.0.0.1:8011）
                                        │
                                        ▼
                        SQLite  /var/lib/sleep-schedule/app.db（WAL）
```

| 项目 | 取值 |
| --- | --- |
| 应用目录 | `/opt/sleep-schedule` |
| 静态站点根 | `/var/www/sleep-schedule`（由部署脚本从应用目录的 `frontend/` 发布） |
| 数据目录 | `/var/lib/sleep-schedule`，数据库 `/var/lib/sleep-schedule/app.db` |
| 日志目录 | `/var/log/sleep-schedule`（`app.log` 轮转） |
| 服务用户 | `sleepapp`（静态文件属主 `www-data`） |
| 后端监听 | `127.0.0.1:8011`（仅本机，不对外暴露） |
| 进程模型 | 单机、单进程、单 worker（SQLite 单写者，不要开多 worker） |
| TLS | 未启用（见“已知限制”） |

## Nginx vhost 关键点

配置模板是 `deploy/nginx-sleep-schedule.conf`，安装方式：

```bash
sudo cp deploy/nginx-sleep-schedule.conf /etc/nginx/sites-available/sleep-schedule
sudo ln -sf /etc/nginx/sites-available/sleep-schedule /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

- **路径与后端挂载点一致**：后端自己也是把 `frontend/shared`、`frontend/user`、`frontend/admin`
  挂到 `/shared`、`/user`、`/admin`，所以同一份前端代码用 `python run.py` 直接跑和用 Nginx 跑，
  地址完全相同。
- **页面里的资源引用一律写站点绝对路径**（`/shared/...`、`/user/css/...`）。不要用 `../` 跨目录：
  浏览器会先把 `../` 规范化掉，`/user/js/pages/x.js` 里的 `../../shared/y.js` 会变成 `/shared/y.js`
  而丢掉 `/user` 前缀；更常见的是 ES 模块按**模块自身 URL**解析，`/user/js/main.js` 里的
  `../shared/api.js` 变成 `/user/shared/api.js`（后端与 Nginx 两种布局下都不存在），整站模块图失败、
  界面空白。前端所有跨目录 import 已改成 `/shared/...`，由 `scripts/absolute_shared_imports.py` 维护。
- **资源 location 必须 `try_files $uri =404`**：静态资源缺失时要 404，绝不能落到 SPA 兜底，
  否则缺失的 `.js` 会以 `200 + text/html` 返回，浏览器只报 MIME 类型错误，极难定位。
- **只用 `root`，不要用 `alias` 配 `try_files`**：`alias` 不会重映射 `$uri`，实测
  （nginx 1.24 / 1.26）会造成目录 404 或静态请求落到 SPA 兜底。
- **`^~ /user/shared/`（与 `/admin/shared/`）别名回真正的 `/shared/`**：旧版本模块用的是
  相对自身的 `../shared/...`，浏览器会解析成 `/user/shared/...`；这条别名让还缓存着旧模块的
  浏览器也能正常加载。必须用 `^~`，否则会被静态资源正则 location 抢走。
- **旧 `/sleep/...` 地址全部 302 到新地址**（`= /sleep`、`/sleep/user/`、`/sleep/admin/`、
  `/sleep/shared/`、`/sleep/` 等），避免已发出去的书签失效；`/sleep/api/` 继续反代到后端。
- `/api/` 反代带 `X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto` 与 `Cache-Control: no-store`，
  `client_max_body_size 1m`，`proxy_read_timeout 60s`。
- 发版前本地验证：`deploy/verify_nginx.ps1` 会用便携 Nginx 起同一份 vhost 并跑 40+ 条路由断言，
  加 `-WithBackend` 时连真接口（API 断言期望真实 JSON，否则 API 请求 502 也算通过）。

## 安装与发版脚本

| 脚本 | 作用 |
| --- | --- |
| `deploy/package.py` | 把 `backend/ frontend/ scripts/ tests/ deploy/ docs/` 与 `README.md`、`requirements.txt`、`run.py`、`start.bat`、`.gitignore` 打成 `dist/sleep-schedule.tar.gz`（跳过 `__pycache__`、`.pyc/.log/.db`、虚拟环境等）。 |
| `deploy/remote_install.sh` | 服务器侧安装：在 `/opt/sleep-schedule` 建 venv（离线环境从随包 `.wheels/` 装依赖）、写 systemd 单元、发布静态文件到 `/var/www/sleep-schedule`、启动并自检。以普通部署用户运行，特权步骤走 `sudo`。 |
| `deploy/apply_fix.ps1` | Windows 侧发版入口：暂存改动 → `scp` 上传 → 调 `remote_apply_fix.sh`，全程使用 askpass 口令。 |
| `deploy/remote_apply_fix.sh` | 远端执行：`rsync` 同步到 `/opt/sleep-schedule`、发布静态文件、装 vhost（`nginx -t` 失败自动回滚）、`systemctl reload nginx` + 重启服务，然后跑约 33 条断言验收入口、模块图、旧地址与路由，并提供回滚清单。 |
| `deploy/push_file.ps1` | 单文件上传；`-WebRoot` 时同时发布到 `/var/www/sleep-schedule`。 |
| `deploy/ssh_run.ps1` | 在服务器上执行任意命令（口令经 askpass），例如只读诊断。 |
| `deploy/verify_nginx.ps1` | 本地用便携 Nginx 验证 vhost 路由；`-WithBackend` 连真接口。 |
| `deploy/serve_local.ps1` | 起/停本地便携 Nginx（默认 8082，加载生产 vhost），供本地验收与调试。 |
| `deploy/accept_local.ps1` | 一键本地验收（六档浏览器场景 + e2e），见 [DEVELOPMENT.md](DEVELOPMENT.md)。 |
| `deploy/remote_diag.sh`、`remote_nginx.sh`、`remote_nginx_diag.sh`、`remote_check*.sh`、`diff_deployed.py`、`schema_check.py`、`compare_encoding.py` | 只读诊断与核对工具（服务状态、vhost、部署文件与本地是否一致、数据库 schema、线上与本地编码差异）。 |

连接参数不写在脚本里：复制 `deploy/local.settings.example.ps1` 为 `deploy/local.settings.ps1`
（该文件被 git 忽略）并填自己的主机/用户/口令，或用 `SSP_DEPLOY_*` 环境变量提供。

```powershell
# 单文件上传示例
.\deploy\push_file.ps1 -Local .\frontend\user\index.html -Remote /tmp/index.html -WebRoot

# 远端只读诊断示例
.\deploy\ssh_run.ps1 -Command "systemctl status sleep-schedule --no-pager"
```

## 发版检查清单

1. **本地验收**：`deploy/accept_local.ps1` 全绿（含 `e2e_http.py`），改动前端时不要加 `-SkipBrowser`。
2. **暂存**：`deploy/apply_fix.ps1` 按“本次只改这几个文件”的清单暂存，确认没把 `.tmp/`、`data/`、
   `logs/`、真实凭据带进包里。
3. **上传**：`scp` 到服务器临时目录（脚本自动完成）。
4. **同步**：远端 `rsync -rlt --no-owner --no-group --chmod=D755,F644` 到 `/opt/sleep-schedule`，
   随后补 `chown sleepapp:sleepapp` 与 `chmod -R a+rX`（**不要用 `rsync -a`**，原因见踩坑记录）；
   静态文件发布到 `/var/www/sleep-schedule` 并归 `www-data`。
5. **重启**：`systemctl restart sleep-schedule`，需要时 `systemctl reload nginx`。
6. **健康检查**：`curl -fsS http://127.0.0.1:8011/healthz` 与 `http://<your-server>/healthz` 均返回
   `{"status":"ok","time":"..."}`。
7. **线上端到端**：`python tests/e2e_http.py http://<your-server>`、
   `python tests/check_module_graph.py http://<your-server>`、`python scripts/smoke_http.py http://<your-server>`。
8. **截图留档**：无头 Edge 跑同源探针页，把用户端/建议页/管理后台截图存到 `docs/screenshots/`，
   文件名沿用现有命名（`advice-*.png`、`live-*.png`）。

> 线上保留了 30 秒写入冷却，探针页连续铺多天记录时只有第 1 条落库（页面日志会显示
> `429 (冷却中)`），所以线上截图里的“样本/连续未吃早餐”比本地稀疏；完整行为
> （午餐前移、提示横幅、>90 分偏离弹窗）由本地验收（冷却关闭）覆盖。

无头验证“登录之后”的界面时注意：本机 Edge 的 CDP `Page` 域会崩溃（`Target crashed`），
改用“同源小页面 `fetch` 真登录（会话是 httponly cookie）→ `location.replace()` 跳转 → 截图”
的办法，截完记得删掉那个临时页。

## 回滚策略

- **代码回滚**：`deploy/remote_apply_fix.sh` 记录本次改动的文件清单（与 `apply_fix.ps1` 的
  `$backendFiles` 保持同步），出问题按清单从 `$STAGE` 或上一版包里把文件还原后重启服务。
- **配置回滚**：安装 vhost 前先备份现有站点配置；脚本在 `nginx -t` 失败时会自动还原旧配置并退出，
  不会把站点留在坏配置上。
- **数据回滚**：SQLite 文件级备份（见下节）。发布前先备份一次 `app.db`，需要时停服替换。
- 任何回滚后都重跑第 6、7 步（健康检查 + 线上端到端）确认站点真的恢复。

## 运维要点

- **备份**：直接复制 `data/app.db`（线上为 `/var/lib/sleep-schedule/app.db`）。库是 WAL 模式，
  建议先停服或用 SQLite 在线备份命令（`sqlite3 app.db ".backup backup.db"`），不要只拷主文件
  而漏掉 `-wal` / `-shm`。
- **日志**：同时写入 `logs/app.log`（轮转）与 `system_logs` 表，后台「日志」页可按级别/事件/关键字
  检索。排查接口问题优先看 systemd 日志与 `app.log`。
- **数据保留**：定时任务每 6 小时清理一次 —— 删除超过 6 个月的睡眠记录、过期会话、30 天前的
  登录记录，并把 `system_logs` 裁剪到最近 20000 行；后台「概览」也可手动触发清理。
- **并发写入**：SQLite 开启 WAL + `busy_timeout`，写入遇到 `database is locked` 会按指数退避重试
  （默认最多 6 次，0.08s → 1.0s）。仍建议保持单进程单 worker。
- **反代 / 跨域**：默认只允许本机与内网网段来源；若前端独立部署，把域名加入 `SSP_CORS_ORIGINS`。
  取客户端 IP 时会读取 `X-Forwarded-For` / `X-Real-IP`。
- **权限**：应用目录 `/opt/sleep-schedule` 必须让 `sleepapp` 可遍历（`chmod -R a+rX`），
  否则启动即报 `PermissionError: [Errno 13] Permission denied: .../frontend/shared`。
- **改配置**：改 systemd 单元的 `Environment=`（或 `EnvironmentFile=` 指向的文件）后
  `systemctl daemon-reload && systemctl restart sleep-schedule`；应用**不会自动读取**项目根目录的
  `.env`，变量注入方式与清单见 [CONFIGURATION.md](CONFIGURATION.md)。

## 已知限制

- **单机单进程 SQLite**：不支持多实例/多 worker 水平扩展；写入串行化，高并发写入不是目标场景。
- **未启用 HTTPS**：按需求保持明文 HTTP。若部署到公网，建议在前面加一层做 TLS 终止的反向代理，
  并把站点域名加入 `SSP_CORS_ORIGINS`。
- **写入冷却 30 秒**：每个用户两次写入之间默认有 30 秒冷却（`SSP_WRITE_COOLDOWN_SECONDS`），
  命中时返回 429 与 `error.write_too_fast`。这是刻意保留的防刷与防误操作限制，脚本铺数据时
  需要临时设为 0。
- 管理员密码第 2/3 段只能在首次建库时通过环境变量设置，后台只能改第 1 段。
- 管理员不能修改用户睡眠记录（只读 + 可删除）。
- 无用户端自助注册、无数据导出、无图表统计（按需求不做）。
- 建议引擎为 `adaptive-v2`，其中相位移动速率与“三餐只做节律锚点”两条有文献支撑，其余常量
  属工程默认值，详见 [ALGORITHM.md](ALGORITHM.md) 与 [REFERENCES.md](REFERENCES.md)。
