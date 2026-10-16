# 本地开发指南

本文面向在本机搭建、调试、自检这个项目的开发者。运行期配置项的含义见
[CONFIGURATION.md](CONFIGURATION.md)，算法规则的来龙去脉见 [ALGORITHM.md](ALGORITHM.md)，
线上发版流程见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 环境准备

- Python 3.12+（CI 与开发机都在 3.12 上验证）。
- 无需 Node.js / 打包器：前端是原生 ES Module + 原生 CSS，改完刷新即可，没有构建步骤。
- 可选工具：`node`（只用于 `node --check` 语法检查）、本机 Edge（只用于无头截图验收）、
  便携 Nginx（`deploy/serve_local.ps1` 会自动下载到 `.tmp/nginx-test/`）。

```powershell
# 1) 建虚拟环境（Windows 示例；Linux/macOS 用 python3 -m venv backend/.venv）
python -m venv backend\.venv

# 2) 安装依赖
backend\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 受限/离线环境（pip 不可用、无法 chmod 或写临时目录）时改用项目内安装器：
backend\.venv\Scripts\python.exe scripts\bootstrap_deps.py

# 3) 环境自检：期望最后一行输出 failures: 0
backend\.venv\Scripts\python.exe scripts\check_env.py

# 4) 启动开发服务（--reload 开启热重载，且不会自动开浏览器）
backend\.venv\Scripts\python.exe run.py --reload
```

启动后：用户端 <http://127.0.0.1:8000/user/>、管理后台 <http://127.0.0.1:8000/admin/>、
健康检查 <http://127.0.0.1:8000/healthz>。开启 `SSP_DEBUG=true` 时还会暴露
<http://127.0.0.1:8000/api/docs>（Swagger）与 `/api/openapi.json`。

## 目录结构详解

### 后端 `backend/app/`（分层：入口 → API → service → core/db）

| 路径 | 职责 |
| --- | --- |
| `backend/app/main.py` | 应用工厂、中间件、静态托管与 SPA 兜底、启动引导、定时清理任务 |
| `backend/app/config.py` | 全部环境变量与默认值（`SSP_` 前缀），唯一配置入口 |
| `backend/app/db.py` | 引擎 / 会话 / `init_db()`；SQLite WAL + `busy_timeout` + 写入重试 |
| `backend/app/models.py` | ORM 模型：用户、会话、登录记录、睡眠记录、建议计划与快照、注销申请、系统日志 |
| `backend/app/schemas.py` | Pydantic 请求/响应模型（API 契约的唯一来源） |
| `backend/app/errors.py` | 统一错误处理与错误码（`error.*`） |
| `backend/app/deps.py` | 依赖注入：数据库会话、当前用户、当前管理员、分页、客户端信息 |
| `backend/app/api/` | 路由层：`auth.py`、`records.py`、`advice.py`、`settings_api.py`、`account.py`、`admin.py`、`common.py` |
| `backend/app/services/` | 业务层：`advice.py`（纯函数算法）、`advice_plan.py`（快照/偏离/达标率/重算）、`auth_service.py`、`sessions.py`、`records.py`、`deletion.py`、`retention.py` |
| `backend/app/core/` | 通用件：`security.py`（bcrypt 与签名）、`logkit.py`（日志与库日志）、`timing.py` |

### 前端 `frontend/`（无构建，两个独立 SPA + 一份共享层）

| 路径 | 职责 |
| --- | --- |
| `frontend/user/` | 用户端 SPA：`index.html`、`js/main.js`、`js/pages/advice.js`、`js/pages/records.js`、`js/pages/settings.js`、`js/references-modal.js`、`css/app.css` |
| `frontend/admin/` | 管理后台 SPA：`index.html`、`js/app.js`、`js/state.js`、`js/pages/{dashboard,users,userDetail,requests,logs}.js`、`css/admin.css` |
| `frontend/shared/` | 共享层：`api.js`（fetch 封装与错误码）、`i18n.js`（`t()` / `tp()`）、`ui.js`、`timeline.js`（24 小时时间图）、`timepicker.js`、`references.js`、`base.css` |

前端用 hash 路由（`/user/#/advice`、`/admin/#/logs`）。后端把 `frontend/shared`、`frontend/user`、
`frontend/admin` 分别挂到 `/shared`、`/user`、`/admin`，与 Nginx 的路径布局完全一致，所以
同一份代码用 `python run.py` 或 Nginx 托管时地址不变。

### 其它目录

| 路径 | 职责 |
| --- | --- |
| `scripts/` | 自检与工具脚本：`check_env.py`、`bootstrap_deps.py`、`smoke_http.py`、`absolute_shared_imports.py`、`check_i18n_keys.py`、`check_advice_i18n.py`、`check_css_classes.py`、`check_frontend_imports.py`、`check_python_encoding.py`、`scan_frontend_encoding.py`、`repair_frontend_encoding.py`、`seed_admin.py` |
| `tests/` | `smoke_test.py`（后端冒烟）、`test_advice_engine.py`（pytest 单元测试）、`e2e_http.py`、`check_module_graph.py`（模块图校验） |
| `deploy/` | Nginx vhost、本地验收与发版脚本（见 [DEPLOYMENT.md](DEPLOYMENT.md)） |
| `data/`、`logs/`、`.tmp/`、`dist/`、`.wheels/` | 运行期产物，均被 `.gitignore` 忽略 |

## 配置项速查

配置全部走**进程环境变量**（`SSP_` 前缀），默认值可直接用于本地开发；当前实现**不会自动读取
项目根目录的 `.env`**（`config.py` 只做 `os.environ.get("SSP_...")`）。完整表格（变量 / 默认值 /
说明）、注入变量的几种方式与常见组合见 [CONFIGURATION.md](CONFIGURATION.md)。

## 测试与自检命令

> 测试依赖不在 `requirements.txt` 里（那是最小运行环境），需要额外装一次：
> `python -m pip install pytest httpx==0.28.1`。
> 其中 `httpx` 是 FastAPI/Starlette `TestClient` 的依赖，`starlette` 已在 `requirements.txt`
> 钉在 `1.7.0`——`starlette 1.8+` 要求换成 `httpx2`，不钉版本时新环境会装到 1.8 导致测试无法收集。

```bash
# 1) 后端接口冒烟：进程内 TestClient，使用独立临时库，不碰 data/app.db
backend/.venv/Scripts/python.exe tests/smoke_test.py

# 2) 算法引擎单元测试（16 项：步长曲线、三餐窗口与现实作息窗口、午餐中点与前移、不想吃补偿、快照/偏离/重算、跨零点时间图、打卡预测与实际一致）
backend/.venv/Scripts/python.exe -m pytest tests/test_advice_engine.py -q

# 3) 真实服务端到端（先启动服务，再另开终端执行；约 74 项断言）
backend/.venv/Scripts/python.exe tests/e2e_http.py http://127.0.0.1:8000
#    线上 / 加固过的环境用环境变量换管理员凭据（真实口令不进仓库，默认值仍指向内置 bootstrap 账号）：
#    SSP_E2E_ADMIN_USERNAME=tyq-admin SSP_E2E_ADMIN_PARTS=口令1,口令2,口令3 \
#        backend/.venv/Scripts/python.exe tests/e2e_http.py http://你的服务器地址

# 4) 静态资源与路由冒烟
backend/.venv/Scripts/python.exe scripts/smoke_http.py http://127.0.0.1:8000

# 5) 模块图校验：像浏览器一样递归拉取 import 图，校验状态码与 MIME
backend/.venv/Scripts/python.exe tests/check_module_graph.py http://127.0.0.1:8000
```

```bash
# 6) 前端/后端静态一致性自检
python scripts/check_i18n_keys.py         # i18n key 覆盖 + tp() 占位符参数
python scripts/check_css_classes.py       # CSS 类名覆盖（新增类必须能在样式表里找到）
python scripts/check_frontend_imports.py  # 调用了 /shared 导出却没 import（点击时才炸 ReferenceError）
python scripts/check_advice_i18n.py       # 后端会发回的 advice.* 代码在中英词条表里是否齐全
python scripts/absolute_shared_imports.py --check   # 不允许 ../shared 之类的相对跨目录 import
backend/.venv/Scripts/python.exe scripts/check_python_encoding.py   # 源码编码（需要 venv 解释器）
python scripts/scan_frontend_encoding.py  # 前端文件编码扫描
```

```bash
# 7) 语法检查（CI 同款）
find frontend -name '*.js' -print0 | xargs -0 -n1 node --check
find deploy -name '*.sh' -print0 | xargs -0 -n1 bash -n
```

CI（`.github/workflows/ci.yml`，ubuntu-latest + Python 3.12）会把上面的静态自检、前端语法检查、
部署脚本语法检查、pytest、冒烟测试全部跑一遍，然后真的启动服务跑 `e2e_http.py`、
`smoke_http.py`、`check_module_graph.py`。推送前本地跑一遍能省一次往返。

## 一键本地验收 `deploy/accept_local.ps1`

改动前端或算法之后推荐跑一次这条命令；它会自己起服务、起站点、开无头浏览器、跑断言、收尾。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\accept_local.ps1
# 常用开关
#   -SkipBrowser                      跳过无头 Edge 渲染与截图
#   -BrowserOnly                      只跑浏览器那一段（服务需已在跑）
#   -Modes mobile -BrowserOnly        只跑某一档，快速迭代
#   -Modes records,plan               逗号分隔多档
#   -KeepRunning                      跑完保留两个服务
```

执行顺序：以 `.tmp\accept\app.db` 为临时库启动后端（**关闭 10 秒写入冷却**，便于连续铺数据）
→ 用 `deploy/serve_local.ps1` 起便携 Nginx（8082，加载生产 vhost）→ 跑编码/导入/i18n/CSS 自检 +
模块图 + 冒烟（全部经 Nginx）→ 无头 Edge 打开同源探针页 `zz-smoke-advice.html`，登录并渲染，
截图并把 DOM 统计写回 `/healthz?ui=...` 供脚本断言 → **清库、以默认 10 秒冷却重启后端跑
`tests/e2e_http.py`** → 关服务。截图落在 `.tmp\accept\advice-*.png`。

七档浏览器场景：

| 档位 | 视口 | 断言要点 |
| --- | --- | --- |
| `base` | 1440×1240 | 今日卡片、三餐签到按钮 ≥6、时间图条 ≥4、now 标线、侧栏 3 项、页脚参考文献入口、时间图为 flex 布局、折叠表单后方案区占满整行（右侧无空白带） |
| `refs` | 1440×1240 | 参考文献弹窗打开、≥4 条 DOI 链接 |
| `deviation` | 1440×1240 | 偏离弹窗打开、提供 3 种策略 |
| `records` | 1440×1240 | 真点“新增记录”并提交（断言发出 `POST /api/records` 且 201）、保存后弹窗关闭、默认区间为最近 7 天、列表有行 |
| `plan` | 1440×1240 | 每日方案行 ≥2、每天迷你时间图条数 ≥3 |
| `refused` | 1440×1240 | 早餐卡片提供「不想吃」按钮 → 真点提交打卡（`POST /api/advice/checkin`）、晚餐精确提前 60 分钟、午餐不推后（有空间时提前）、按钮进入激活态、点「吃了」撤销后恢复、快速模式说明固定单日并隐藏天数滑块 |
| `mobile` | 390×900 | 窄屏改用纵向时间图、时间图条 ≥4、无横向溢出 |

每一档都会顺带跑六条“用户看得到”的回归护栏，任何一条不过整体失败：

1. **没有漏翻的 i18n key**：正文里不允许出现 `advice.* / records.* / common.*` 这类原始 key。
2. **没有未填充的占位符**：正文里不允许出现 `{streak}` 这种花括号参数（`tp()` 漏传参数的典型症状）。
3. **时间条不压字**：时间图的每个条都不能与时间刻度或“睡眠/早餐/午餐/晚餐”图例的矩形相交
   （用 `getBoundingClientRect` 判定，正是用户报过的“示例 UI 和时间图交叉”）。
4. **登录失败带剩余次数预警**：探针故意用错密码登一次，断言 401 响应里有 `remaining_attempts`
   且渲染出的提示含有剩余次数；随后用正确密码再登一次把失败计数清零（否则连跑几次会把账号锁掉），
   锁定时改成锁定倒计时文案（`error.account_locked`）同样是断言项。
5. **成功登录清零失败计数**：连错两次 → 成功登录 → 再错一次，`remaining_attempts` 必须回到
   `max_attempts - 1`（没有清零时会继续往下掉）。探针把这三步的结果放进 `login_reset`，
   验收脚本对每个档位断言一遍；`tests/e2e_http.py` 与 `tests/smoke_test.py` 也有同一条用例
   （管理员解锁同样清掉窗口内的失败记录，所以解锁后不会立刻又被算成"还剩 0 次"）。
6. **方案区占满整行**：生成表单折叠后 `display:none`，它就不再是 `.advice-layout` 的网格项，
   方案区会被自动补进左边那条 320px 的窄列（用户报过的“PC 页面挤在一边、时间图被压缩在一排、
   空间利用率太低”）。探针把 `.advice-layout` 与 `#advice-result` 的 `getBoundingClientRect`
   放进 `advice_layout`，建议页各档据此断言：右边缘不留空白带（`result_right_gap ≤ 4`），
   折叠态还要求方案区宽度≈布局宽度。

## 前端开发硬约束

1. **模块说明符必须写站点绝对路径**：`/shared/api.js`、`/user/js/main.js`、`/admin/js/app.js`，
   不要写 `../shared/...`。ES Module 的 import 说明符是相对**模块自身 URL**解析的，
   `frontend/user/js/main.js` 里的 `../shared/api.js` 会被解析成 `/user/shared/api.js` ——
   这个路径在后端与 Nginx 两种布局下都不存在，模块图整体失败后页面就是“只剩背景”的空白界面
   （2026-10 线上故障的根因）。批量修正与校验：`python scripts/absolute_shared_imports.py [--check]`。
2. **文案一律走 `t()` / `tp()`**，不要在 JS 或 HTML 里写死中文/英文；后端返回的 `note` 之类字段
   只能放**不需要参数**的文案键，带参数的句子由前端用数字字段拼（后端塞 `{streak}` 会原样显示）。
   改完跑 `python scripts/check_i18n_keys.py` 与 `python scripts/check_advice_i18n.py`。
3. **新增 CSS 类要过 `scripts/check_css_classes.py`**：页面里用到但样式表里不存在的类名会直接失败。
4. **改了前端之后跑 `python scripts/check_frontend_imports.py`**：少写一个 import 不会在加载时报错，
   只会在用户点击时抛 `ReferenceError`（曾经“新增记录点了没反应”就是这个原因），验收里
   `-Modes records` 那档会真点一次表单兜底。
5. **保持 UTF-8 无 BOM + CRLF**（与仓库其它文件一致），中文注释不要用 ANSI/GBK 写回。

## Windows 侧踩坑

- **前端少写一个 import 不会报错，只会在用户点击时炸**：`records.js` 里 `createTimePicker`
  忘了 import，`onMount` 抛 `ReferenceError`，于是“新增记录”点了完全没反应（页面上既没有请求、
  也没有提示）。改动前端后跑 `python scripts/check_frontend_imports.py`，并在验收里**真点一次表单**
  （`-Modes records` 那档会断言真的发出了 `POST /api/records` 且返回 201）。
- **后端返回的 note / 代码字段只能放“不需要参数”的文案键**：`advice.meal.lunch_advanced` 带
  `{streak}/{shift}`，后端直接塞进 `meals.lunch.note` 就会在页面上显示成 `{streak}` 原文。
  带参数的句子一律由 `meal_plan` 里的数字字段在前端用 `tp()` 拼；验收里那条
  “no unfilled template placeholders”就是拦这个的。
- 本机默认 shell 是 Windows PowerShell 5.1：`Get-Content` 默认按 ANSI/GBK 解码，改前端文件必须
  用 `[System.IO.File]::ReadAllText/WriteAllText` 指定 UTF-8（本仓库前端为 UTF-8 无 BOM、CRLF），
  否则中文注释会变成乱码；批量改文本优先写成 Python 脚本。
- `.ps1` 被执行策略拦截：统一用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <脚本>`。
- `curl.exe` 接收含双引号的 JSON 会被 PowerShell 拆坏（报 `JSON decode error`）：把 JSON 写进文件
  再用 `--data-binary "@文件"`。
- headless Edge 必须 `Start-Process -FilePath msedge.exe -ArgumentList <参数> -Wait`，直接
  `& msedge.exe ...` 不产出截图；`--dump-dom` 在该版本返回空。
- 含括号的远程命令直接塞进 `ssh` 会触发 bash 语法错误，且脚本必须存为 LF（CRLF 会报
  `$'\r': command not found`）。
- 发布脚本里**不要用 `rsync -a`** 把暂存目录同步进 `/opt/sleep-schedule`：暂存目录是 `scp -r`
  上传的，目录权限是 `0700 admin:admin`，`-a` 会把这种权限带到应用目录，`sleepapp` 用户
  立刻起不来（`PermissionError: [Errno 13] Permission denied: .../frontend/shared`）。
  用 `rsync -rlt --no-owner --no-group --chmod=D755,F644` 再补一次 `chown sleepapp:sleepapp`
  与 `chmod -R a+rX`。
- 远程断言里**不要用 `grep -q`**：`set -o pipefail` 下它命中后立刻退出，喂数据的 `printf`
  会被 SIGPIPE 杀掉，于是整条管道判为失败，出现“内容明明有却报 body missing”的假失败。
  改用 `hits=$(printf '%s' "$body" | grep -c -- "$needle" || true)` 再比较 `hits -ge 1`。
- PowerShell 5.1 里 `*>` 重定向写出来的是 UTF-16 文件，Python 侧要按 `utf-16` 解码
  （或改用 `Set-Content -Encoding UTF8`）。

## 提交信息与分支风格

- 分支：`main` 保持可发布；功能用 `feat/<简短主题>`，修复用 `fix/<简短主题>`，文档用 `docs/<主题>`。
- 提交信息用祈使句，一行标题 + 可选正文，标题里带上范围，例如：

  ```text
  fix(records): 修正新增记录弹窗未关闭的问题
  docs(readme): 重排快速开始与 API 概览
  ```

- 一次提交只做一件事；改前端/算法的那次提交请附上跑过的自检命令与结果（尤其是
  `deploy/accept_local.ps1` 的通过情况）。
- 推送前按“测试与自检命令”一节至少跑一遍冒烟 + pytest + 静态自检。
