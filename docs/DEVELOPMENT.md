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

# 2) 算法引擎单元测试（23 项：步长曲线、三餐窗口与现实作息窗口、午餐中点与前移、不想吃补偿、快照/偏离/重算、跨零点时间图、打卡预测与实际一致、入睡日/起床日两个日期与按起床日配对）
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
| `base` | 1440×1240 | 今日卡片、三餐签到按钮 ≥6、时间图条 ≥4、now 标线、侧栏 3 项、页脚参考文献入口、时间图为 flex 布局、折叠表单后方案区占满整行（右侧无空白带）、日卡建议默认折成一行且点开可展开 |
| `refs` | 1440×1240 | 参考文献弹窗打开、≥4 条 DOI 链接 |
| `deviation` | 1440×1240 | 偏离弹窗打开、提供 3 种策略 |
| `records` | 1440×1240 | 真点“新增记录”并提交（断言发出 `POST /api/records` 且 201）、保存后弹窗关闭、默认区间为最近 7 天、列表有行、入睡日期默认昨天、起床日期默认入睡日 +1 且跟着入睡日期走、提交体里带 `wake_date`、表格同时显示两个日期、表单写明三餐属于起床日 |
| `plan` | 1440×1240 | 每日方案行 ≥2、每天迷你时间图条数 ≥3 |
| `refused` | 1440×1240 | 点早餐卡的「跳过」→ 真发 `POST /api/advice/checkin`、今天午餐不推后、晚餐更早、每餐带 `data-meal-shift` 原因、三餐区有统一说明、页面上不再有「不想吃」按钮、点「已吃」还原 |
| `mobile` | 390×900 | 窄屏改用纵向时间图、时间图条 ≥4、无横向溢出 |

每一档都会顺带跑十五条“用户看得到”的回归护栏，任何一条不过整体失败：

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
7. **日卡建议默认折叠**：每张日卡下面原本铺 6 条建议，宽屏下页面被撑得很长、显得空（用户报的
   “空间利用率太低”）。现在默认只留一行按钮（`建议 N 条 · 展开`，点开就地展开、再点收成一行，
   折叠状态只活在 DOM 上，重画即回到默认）。探针把折叠/展开/再折叠的结果与展开前后的卡片高度
   放进 `tips_fold`，各档断言：默认不占位、按钮上带数量、点开可见、再点回折叠、展开时卡片变高。
   只有 1~2 条建议的日卡不显示折叠按钮（`tipsHtml()` 直接铺开）。
8. **记录有两个日期**：一条睡眠记录跨两个日历日，表单里“入睡日期”默认**昨天**（可改）、“起床日期”
   默认**入睡日 + 1**（可改，未手动改过时跟着入睡日期走），三餐勾选属于**起床日**。探针把两个
   输入框的默认值、改入睡日期后起床日期是否跟着变、提交体里有没有 `wake_date`、表格第 3 列是否
   有值、弹窗有没有“起床日”提示放进 `record_form`，`records` 档据此断言 7 条；后端侧的配对
   由 `tests/test_advice_engine.py` 的 5 个新用例守着（默认 +1、可同天、范围与先后校验、
   三餐按起床日配对、打卡回写到起床日那条记录）。
9. **跨零点那晚标成两截并说明相接**：轴窗是“起床前 2 小时 → 入睡后 2 小时”，整夜睡眠因此被切在
   轴的两头（贴入睡那截在轴尾、贴起床那截在轴头），用户会以为“睡了两觉”。前端现在把两截标成
   「睡眠前段 / 睡眠后段」，并在轴上的睡眠徽标里写「跨零点，轴两端相接」。探针按 `data-tl-part`
   与 `data-tl-join`（不依赖中文文案）把两截的标注、各自的钟点区间与轴徽标收进 `tl_today` /
   `tl_mini`，各档断言：两截标注互不相同且非空、两截都带自己的时间区间、轴上有“相接”标记
   （迷你图同样要带）。数据侧的 `segment` / `partial` / `start_time` / `end_time` 由
   `::test_timeline_wraps_around_midnight` 守着。

10. **步长曲线画的是同一份数据**：「调整步长」与「每日方案」之间那张图（横轴天数、纵轴累计移动分钟）
    完全由日卡上的 `step_minutes` 加总而来，所以图上的点与卡片上的数字不会互相矛盾；虚线是“平均速度”
    参考线，曲线在它上方就说明前几天走得比平均快。探针按 `data-curve-card` / `data-curve-point` /
    `data-curve-line` 把点数、首末累计、各天步长之和、是否单调、路径是否为平滑曲线、有没有参考线收进
    `curve_chart`，各档断言：点数 == 日卡数、末点 == 各天步长之和、单调不减、路径是 `M…C…`、参考线存在。
    第二轮又加了四件事，同样只认 `data-*` 钩子：阈值极限线（`data-curve-cap`，标记 `data-curve-cap-mark`）、
    点选气泡（`data-curve-bubble`，点一次开、再点一次关、点页面别处也关）、联动高亮（点第 k 天 →
    `.day-card` 第 k 张加 `is-linked` 并滚进视野）、天数预览（拖 `#days-range` → 卡片带
    `data-curve-preview` 与“预览”角标，点数 = 新天数，松手前不写库）。探针把 `cap_line` / `cap_mark` /
    `cap_expected` / `tap_open` / `tap_len` / `tap_inside` / `tap_closed` / `linked` / `linked_first` /
    `preview_points` / `preview_days` / `preview_badge` / `preview_restored` / `selfcheck` 一并回传，
    各档断言：极限线存在、标记“该有才有”、`selfcheck == ok`（JS 版公式与后端一致）、气泡开合各一次且不出容器、
    联动到第一张日卡、预览点数 == 预览天数且改回后复原。

15. **「目标作息」和「调整步长」宽屏并排**：宽窗口里这两张卡上下堆叠，右半边整条都是空白（用户报的
    “目标作息和调整步长的窗口留空太大”）。现在外面套一层 `.plan-pair`：窄屏是普通块（上下堆叠，
    视觉与改动前一致），`≥1100px` 变成 `2fr : 1fr` 的网格；右栏变窄后数值行加 `white-space: nowrap`
    （否则「60 分钟」会被拆成两行）。探针按 `data-plan-pair` 量两张卡的顶边与宽度，回传 `plan_pair`
    （`viewport_w` / `same_row` / `target_w` / `steps_w` / `ratio`），各档断言：宽屏同一行且宽度比
    接近 2 : 1，窄屏（< 1100）不在同一行。

11. **页面的第一帧就是最终形态**：先按“没有方案”画展开的表单、等数据回来再折叠，用户会看到明显一闪
    （用户报过；也不要拿骨架/“加载中”卡去遮，那只是把闪动换成灰块）。所以建议页是**等待式**加载：
    `renderAdvicePage()` 先 `await Promise.all([options, plan])`，再在同一轮任务里
    `renderShell()` → `bindForm()` → `syncForm()` → `renderPlanView()` 一次画完。
    代价是数据回来之前页面区还是上一页（冷启动时是深色底），线上实测 25–45ms；刷新这一次
    由下面第 12 条的首帧缓存兜住。探针只取两个时间点（`load` 那一刻 + settle 之后），把
    `paint_sample` 的 `panel_first` / `form_first` / `ready_first` / `skeleton_first` /
    `skeleton_later` / `ready_later` 回传，各档断言“页面要么还没画、要么已经画完”
    （`panel_first=1 && form_first=1 && ready_first=0` 才算半成品）。早先用
    requestAnimationFrame 链逐帧采样，会把 headless 的 `--virtual-time-budget` 耗光，
    五档摘要根本回传不了、截图是空白页 —— 别再逐帧采样。

12. **刷新时先把上一屏贴回来**（首帧缓存）：`/api/auth/me` 和页面数据都要时间，这段时间屏幕上不该是黑的
    或者上一页。`frontend/shared/boot-cache.js` 用 `sessionStorage` 存两样东西：上次的登录身份
    （`ssp.boot.user`）和每个路由上次渲染完成的 `#route-view.innerHTML`（`ssp.boot.page.<route>`，
    带 uid 与 12 小时 TTL）。`main.js` 的 `bootstrap()` 一拿到缓存就 `showApp()` +
    `renderRoute({ cached: true })` 把上一屏贴出来，再去后台 `me()` 校验：同一账号就当没事发生
    （不整页重绘，滚动位置不跳），换账号或 401 就 `clearBootCache()` 回登录页。`#route-view` 上的
    `data-painted-from-cache` 是探针钩子（`1` = 现在贴的是缓存，`2` = 已被真实渲染替换）。探针刷新
    一次后回传 `boot_cache`（`keys_before` / `app_at_load` / `content_at_load` / `mark_at_load` /
    `mark_after` / `content_after`），各档断言：缓存写下来了、刷新后外壳立刻在、贴的是缓存、
    随后被真实数据替换、贴出来的内容不为空。

13. **静态资源要压缩 + 预加载**：首屏 12 个 ES 模块（`main.js` + 7 个一层依赖 + 4 个二层）
    未压缩共 214.6KB，RTT≈40ms 的链路上光下载就要 200ms+，比接口那 40ms 更容易被肉眼看见。
    两处配置：nginx vhost 里 `gzip on`（`gzip_types` 必须同时写 `text/javascript` 与
    `application/javascript`）；`frontend/user/index.html` 里对全部模块写 `rel="modulepreload"`
    （同源不要加 `crossorigin`，凭据模式不一致会下载两份）。上线自证 4s 会真量
    `size_download`（gzip vs identity）与 `content-encoding: gzip`。
14. **“重新生成方案”要显眼，也要在手边**：它原先只是目标作息卡头部的一个 `btn-ghost`
    （透明底、13px 灰字），比旁边“复制方案”还弱，而且卡片在“今天”下面、1440×900 上要下滚才见。
    现在头部那颗改成 `btn-primary`，另外在今天卡底部加一条 `data-regenerate` 的次要入口；
    两颗按钮共用同一段事件委托（挂在 `#advice-result` 上，`root.dataset.regenBound` 防重复绑定），
    因为局部重绘（`renderTodayOnly`）会换掉按钮节点。探针回传 `regenerate_primary` /
    `regenerate_bottom`，并**点下面那颗**验证“展开表单 + 滚到表单”这条路真的通。

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

15. **三餐只看昨天与当天**：记录页的「三餐记录」带一个独立的三餐日期（默认入睡日，凌晨那一觉默认
    前移一天），算法只按“昨天晚餐 / 今天早餐 / 今天午餐 / 今天晚餐”四条规则移动今天与明天的时间，
    不再累计连续天数。探针把点「跳过」前后午/晚餐的时间、`data-meal-shift` 的原因码、统一说明文案、
    打卡响应里的 `meal_plan.today` 放进 `refused`，`refused` 档据此断言（午餐不推后、晚餐更早、
    至少两条原因、说明非空、打卡记为 `skipped`、点「已吃」还原）。后端侧由
    `::test_today_breakfast_skip_shifts_lunch_and_dinner` 等 6 个用例守着。

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
- **弹窗里自定义按钮要 `close(值)`，不要 `host.remove()`**：`shared/ui.js` 的 `openModal()` 只通过
  它自己的 `close()` resolve Promise；把弹窗节点直接删掉，`await openModal(...)` 永远不返回，
  后面的逻辑（例如偏离弹窗的 `applyStrategy()`）等于没写，而且防重入标志会卡住。
  偏离弹窗就是这么坏的：点「重新计算每日步长」毫无反应，弹窗还再也不会出现。
  另外底部主按钮可以传 `okText: null` 来隐藏——两个都写「关闭」会渲染出一模一样的两个按钮。

- **新增一个页面区块，就给它一条断言**：记录页的「记录概览」是纯前端算的（不新增接口），
  验收靠探针回传的 `records_overview`（`present`/`tiles`/`rows`/`bands`/`bars`/`ref`/`pace_line`/
  `ticks`/`labels_ok`/`linked`）加两条 `Check`（概览存在、横轴刻度是钟点）。只画不量的 UI 会在
  重构里悄悄消失。
- **表单默认值只用内存里已有的数据，开弹窗别发请求**：三餐默认值是从列表里那条记录的
  `meal_*` 推的（`findMealRow()`，同一天有多条取 `updated_at` 最新的），因为
  `recordApi` 里根本没有 `mealState` 方法——上一轮只加了后端 `GET /api/records/meal-state`，
  客户端方法没加，结果每次打开弹窗都抛 `TypeError`，被 async 函数的 try/catch 之外变成
  unhandled rejection，验收探针因此卡死在那儿、永远不回报。教训是：**新增客户端调用时先确认
  方法真的存在**（`grep` 一下 `api.js`），否则它只会在某个异步分支里静默地炸。

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

## 记录页的四条护栏（第十六条）

- **保存失败不关窗**：表单提交走弹窗内的 `submit()`，成功才 `close(true)`；失败只写 `[data-form-error]`，输入一律保留。探针守的断言是「submitted + modal closed + wake date posted」三条同时成立。
- **重叠预检两层**：后端 `_check_no_overlap()` 抛 400 `error.overlapping_record`（背靠背合法、完全重复必拒、编辑排除自己），前端提交前用本地列表再算一遍。**种测试数据时小心**：表单默认 `23:30–07:30` 与种子记录同夜就可能被判重叠，`records` 档故意把种子挪到 `20:00–22:00`。
- **按夜晚分组**：时间带一行 = 入睡日 18:00 → 次日 18:00，同夜多条共用一行、跨 18:00 切开；漂移每夜一个点。`nightsOf()` 是唯一入口，旧的 `seriesOf()` 已删除（4z 用 `grep_absent` 守着）。
- **短睡不算夜**：`NAP_MAX_MINUTES = 120`，`is_nap()` 为真的记录不进平均入睡/起床/时长，也不参与偏离与达标配对；概览显示「已排除 N 段小睡」。

## 日期控件：自绘日历与两个查询上限

- 原生 `<input type="date">` 的日历层是浏览器 chrome，颜色改不了，所以
  `frontend/user/js/date-picker.js` 把它藏起来（`value` 仍是唯一真值，表单收集和验收探针
  都照旧读它），旁边放一个按钮打开自绘网格。入口只有两个：`enhanceDateInputs(root)`
  （幂等，重复渲染后重复调用即可）和 `clearDateMarkCache()`；`records.js` 在 `load()`
  拉完列表后调后者，增删改之后绿点才会重算。
- 绿点 = 那天有睡眠记录（按 `record_date`）：本月 `is-marked`（实心 `--ok`），相邻月
  `is-marked-soft`（22% 透明），选中 `is-selected`（主题蓝描边），今天 `is-today`
  （内描边），超出输入框 `min` / `max` 的 `is-disabled`。弹层挂在 `document.body`
  （`position: fixed`、`z-index: 99999`、宽 268），点外面 / Esc / 选中一天都会关；
  **不要加 resize 关闭**，headless 和窗口抖动会把它误关。
- **`page_size` 上限 100**：`GET /api/records` 超过就 422 `error.invalid_input`，日历按
  `page_size=100` 查。
- **日期过滤最多宽限 1 天**（`MAX_FUTURE_DAYS = 1`）：拿“本月 1 号 → 本月最后一天”去查当月，
  月底还没到时后端直接 400 `error.date_in_future`；`markedOf()` 以前把这个异常吞成空集合，
  于是**当月一个绿点都不显示**（曾经被误判成截图时机问题）。现在 `end_date` 夹到今天，
  整月都在未来的月份连请求都不发。
- 自检：`.tmp/accept/zz-picker-check.html`（`?hold=1` 停在日历打开的状态，方便截图；
  不带参数时还会点一天，验证输入框回写和起床日联动）要从 nginx docroot 打开，用探针建的
  `shot-records` / `User@12345` 登录，结果通过 `/healthz?ui=picker:...` 回传；`4aa` 用
  `grep_file` / `grep_absent` 守着这些约定。
## 图标：内联 SVG 与 data-icon

- 界面里的图标不再用 emoji。emoji 的颜色写死在字形里（深色主题改不动）、不同系统字形不同、
  行高会被撑开，也没法统一线宽。现在全部集中在 `frontend/shared/icons.js`：`ICONS` 里 24 个
  24×24 的线性路径，`iconSvg(name, size)` 输出 `<svg class="ic" … stroke="currentColor">`，
  颜色跟随文字，所以主题切换不用改图标。
- 两种用法：写 HTML 时留 `<span class="nav-icon" data-icon="records"></span>`（`hydrateIcons()`
  在启动时填充，幂等，可重复调）；在 JS 模板里直接拼 `${iconSvg("moon")}`。
  主题按钮切换用 `setIcon(el, name)`，只是换 `innerHTML`。
- 尺寸由容器决定：`.ic` 默认 `1em`（`base.css`），导航 `.nav-icon .ic` 17px，
  时间滚轮 `.time-spin .ic` 12px，时间带图例 `.tl-icon .ic` 12px。
- 后台左侧导航原先没有任何样式（灰底原生按钮、图标紧贴文字），这次在 `admin.css` 里补了
  `.admin-shell .nav` / `.nav-item` / `.nav-icon` 三组规则；后台页面不加载 `user/css/app.css`，
  它的样式别往那边写。
- 加图标只改 `icons.js`：在 `ICONS` 里加一条路径，然后在 HTML/模板里写 `data-icon` 或
  `iconSvg(...)`。想先看效果就打开 `.tmp/accept/icon-sheet.html`（要放进 nginx docroot），
  它把 24 个图标按 17px / 24px、深色 / 浅色各排一遍；`advice` 原来是罗盘，17px 下指针会糊成
  一团，所以换成了灯泡。
- 想确认线上真的换了：`4ab` 用 `grep_file` 守着 `icons.js` / `data-icon` / `.admin-shell .nav-item`，
  再用 `grep_absent` 确认两个入口的 emoji logo 和旧导航 emoji 已经不在。
## 域名与 HTTPS：yiuki.me（2026-10 起）

- 域名在阿里云，解析 `@` 和 `www` 都指到 39.106.196.92（NS 是 dns7/dns8.hichina.com）。
  **备案（ICP）没通过前，阿里云会按 Host 在边缘直接返回 `403 Non-compliance ICP Filing`**，
  请求根本到不了 nginx；这个拦截不分地域（境外节点测过也是 403），所以 Let's Encrypt 的
  HTTP-01 验证也会被拦，证书只能等备案通过后再签。本机自测要用
  `curl -H 'Host: yiuki.me' http://127.0.0.1/` 才绕过拦截。
- `deploy/nginx-sleep-schedule.conf` 里 443/证书/跳转的行都带 `#TLS#` 前缀且是注释状态：
  证书文件不存在时 `nginx -t` 会直接失败，配置不能提前带 443 上线。
- 证书就位后跑一次 `sudo bash enable-tls.sh`：删掉行首的 `#TLS# `、`nginx -t`、reload，
  最后用本机 curl 自检。脚本先备份再改，`nginx -t` 失败会自动回滚；重复执行只做校验和 reload。
- 签发命令（备案通过后，服务器上）：

  ```
  sudo certbot certonly --webroot -w /var/www/certbot -d yiuki.me -d www.yiuki.me \
       --deploy-hook "systemctl reload nginx"
  ```

  `location ^~ /.well-known/acme-challenge/` 必须常驻（根目录 `/var/www/certbot`），
  自动续期（certbot.timer，每天两次）也走这个路径。
- 跳转策略：域名一律 `301 https://yiuki.me`（www 也跳到裸域）。80 上的域名由文件末尾
  单独那个 `#TLS# server` 块跳转（它在 80 上按 `server_name` 命中，443 上根本不会进），
  主块里只剩一条针对 `www` 的 443 跳转，所以不会出现"https 跳 https"的死循环；
  `$host` 是 IP 的直连入口不跳，留作备用。想临时回退就把 443 那几行重新注释掉并 reload，
  IP 入口始终可用。
- Cookie：`backend/app/services/sessions.py` 按 `X-Forwarded-Proto` 决定是否加 `Secure`
  —— HTTPS 加、HTTP 不加，否则 IP 直连会登录不上。nginx 的 `/api/` 已经带
  `proxy_set_header X-Forwarded-Proto $scheme;`。
- 验证时注意：systemctl reload nginx 是优雅重启，旧 worker 会短暂继续用旧配置，
  刚 reload 完立刻 curl 可能还是旧行为（本地演练里就出现过一次 302）。enable-tls.sh
  和演练脚本都先 sleep 2 再自检。
- 备案下来后补两件事：页脚加备案号并链到 `https://beian.miit.gov.cn/`（阿里云会抽查），
  以及确认 80 端口只剩 301（`curl -I -H 'Host: yiuki.me' http://yiuki.me/`）。
