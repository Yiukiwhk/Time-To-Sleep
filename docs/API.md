# API 接口文档

本文是后端 HTTP 接口的完整说明。所有接口挂在 `/api` 前缀下（应用内路由再加各自的
`/auth`、`/records`、`/advice`、`/settings`、`/account`、`/admin` 前缀），请求与响应均为 JSON，
编码 UTF-8。

- 认证方式：**HttpOnly Cookie 会话**（用户端 `ssp_session`，管理后台 `ssp_admin_session`），
  没有 Bearer Token。用 `curl` 调试时需要 `-c cookies.txt -b cookies.txt` 保留 Cookie。
- 开启 `SSP_DEBUG=true` 时，`/api/docs`（Swagger UI）与 `/api/openapi.json` 可用；默认关闭。
- 健康检查 `GET /healthz` 不在 `/api` 下，返回 `{"status":"ok","time":"..."}`，无需认证。

## 通用约定

### 响应与错误码格式

错误响应体统一为：

```json
{
  "detail": "error.write_too_fast",
  "code": "error.write_too_fast",
  "retry_after_seconds": 4,
  "cooldown_seconds": 10
}
```

- `detail` / `code` 是**可直接用于 i18n 的文案键**（前端 `t()` 渲染），不是给人看的中文句子。
- `extra` 字段按需附加（如 429 的 `retry_after_seconds`、401 的 `required_parts` / `remaining_attempts`）。

| 状态码 | 含义 | 典型 `code` |
| --- | --- | --- |
| 400 | 参数语义错误（格式对但取值不合理） | `error.nothing_to_update`、`error.target_requires_both`、`error.wrong_current_password`、`error.password_unchanged`、`error.invalid_date`、`error.date_in_future`、`error.date_too_old`、`error.invalid_time`、`error.nothing_selected`、`error.cannot_disable_self`、`error.cannot_delete_self`、`error.last_admin`、`error.admin_parts_not_allowed`、`error.admin_cannot_deactivate` |
| 401 | 未登录或凭据错误 | `error.invalid_credentials`（附 `remaining_attempts` / `max_attempts`，刚锁定时还有 `retry_after_seconds` / `locked_minutes`）、`error.admin_password_parts`、`error.unauthorized` |
| 403 | 已识别但不允许（账号停用、走错登录口） | `error.account_disabled`、`error.use_admin_login`、`error.forbidden` |
| 404 | 资源不存在**或无权访问** | `error.record_not_found`、`error.user_not_found`、`error.deletion_not_found`、`error.not_found` |
| 409 | 状态冲突（重名、重复提交） | `error.username_taken`、`error.deletion_pending`、`error.deletion_already_handled` |
| 422 | 请求体不符合 schema（缺字段、超长、枚举非法） | `error.invalid_input`，并附 `field` / `message` / `errors[]` |
| 429 | 触发写入冷却 / 账号锁定 | `error.write_too_fast`、`error.account_locked`，响应带 `Retry-After` 头 |
| 500 | 未预期异常 | `error.server` |

两个刻意的设计：

- **越权按 404 处理**：访问别人的记录、别人的用户详情一律返回 404 而不是 403，避免通过状态码
  探测资源是否存在。
- **422 会给出首个出错字段**，便于前端直接把错误提示挂到输入框上：

  ```json
  {
    "detail": "error.invalid_input",
    "code": "error.invalid_input",
    "field": "new_password",
    "message": "String should have at most 72 characters",
    "errors": [{"field": "new_password", "message": "String should have at most 72 characters"}]
  }
  ```

### 分页与写入冷却

- 列表接口统一接受 `page`（默认 1）与 `page_size`（默认 20，最大 100），返回
  `{items, total, page, page_size, pages}`。
- 所有**写操作（POST / PUT / PATCH / DELETE）**受同一用户 10 秒冷却限制
  （`SSP_WRITE_COOLDOWN_SECONDS`），命中返回 429 与 `error.write_too_fast`；剩余秒数也可从
  `GET /api/records/meta` 的 `cooldown_remaining_seconds` 读到。

## 认证 `/api/auth`

### `POST /api/auth/login`

用户端登录。管理员账号不能从用户端口登录（会返回 403 `error.use_admin_login`）。

```json
{
  "username": "demo",
  "password": "demo12345",
  "remember": true
}
```

```json
{
  "id": 2,
  "username": "demo",
  "is_admin": false,
  "is_active": true,
  "language": "zh",
  "theme": "dark",
  "target_sleep_time": "23:00",
  "target_wake_time": "07:00",
  "meal_breakfast": true,
  "meal_lunch": true,
  "meal_dinner": true,
  "created_at": "2026-02-01T08:12:31",
  "last_login_at": "2026-02-20T22:03:10"
}
```

- `remember: true` → 会话 30 天；否则 12 小时（见 `SESSION_TTL_*`）。
- 连续失败 5 次锁定 15 分钟（`SSP_LOGIN_MAX_FAILURES` / `SSP_LOGIN_LOCK_SECONDS`），
  失败的 401 会带**剩余次数预警**，前端显示「用户名或密码错误，还可尝试 N 次」：

  ```json
  {
    "detail": "error.invalid_credentials",
    "code": "error.invalid_credentials",
    "remaining_attempts": 4,
    "max_attempts": 5
  }
  ```

  刚好触发锁定的那次 `remaining_attempts` 为 `0`，并附上解锁倒计时（前端改说「账号已锁定」）；
  锁定期内再登录返回 429 `error.account_locked`（带 `Retry-After` 头）：

  ```json
  {
    "detail": "error.invalid_credentials",
    "code": "error.invalid_credentials",
    "remaining_attempts": 0,
    "max_attempts": 5,
    "retry_after_seconds": 900,
    "locked_minutes": 15
  }
  ```

  用户名不存在时同样记一次失败（防爆破），但**不带** `remaining_attempts`——那里没有账号可锁，
  给倒计时是误导。

  成功登录会把失败计数**清零**：计数只统计「最近一次成功登录之后」的失败（同窗口内），
  所以输错几次再登进来，下次输错又从满额开始提示；窗内的历史失败仍保留在 `login_attempts`
  审计表里，不会被删除。

### `POST /api/auth/admin/login`

管理员**三段密码**登录：`passwords` 数组第 1 个元素是账号密码，其余按顺序对应第 2、3 段。
段数由 `GET /api/auth/admin/parts` 告诉前端；段数不足返回 401 `error.admin_password_parts`
（附 `required_parts`）。

```json
{
  "username": "admin",
  "passwords": ["Admin@12345", "Admin#67890", "Admin$24680"],
  "remember": false
}
```

```json
{
  "parts": 3
}
```

（上面第二段是 `GET /api/auth/admin/parts` 的响应。）

> 这三段默认值是**公开的示例口令**，仅供首次本地启动；上线后请立刻修改（第 1 段可在设置里改，
> 第 2/3 段只能在首次建库前通过 `SSP_BOOTSTRAP_ADMIN_PASSWORDS` 设定）。

### 会话相关

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/auth/me` | 读取当前登录用户（未登录 401） |
| `GET` | `/api/auth/admin/me` | 读取当前管理员（非管理员 401 `error.unauthorized`） |
| `GET` | `/api/auth/admin/parts` | 管理员登录需要几段密码：`{"parts": 3}` |
| `POST` | `/api/auth/logout` | 退出用户端会话，清 Cookie |
| `POST` | `/api/auth/admin/logout` | 退出管理后台会话 |

```json
{
  "ok": true,
  "message": "auth.logged_out",
  "extra": null
}
```

### `POST /api/auth/password`

修改自己的密码；改完**所有会话失效**并清空 Cookie（需要重新登录）。

```json
{
  "current_password": "demo12345",
  "new_password": "demo54321"
}
```

错误：`error.wrong_current_password`（400）、`error.password_unchanged`（400，新旧相同）、
`error.user_not_found`（404，并发删除）。

## 睡眠记录 `/api/records`

字段约定：`sleep_time` / `wake_time` 为 `HH:MM`（24 小时制），`record_date` 为**入睡日历日**、
`wake_date` 为**起床日历日**（都是 `YYYY-MM-DD`，省略则入睡日取今天、起床日按"起床时刻 <= 入睡时刻
→ 次日"推导），三餐布尔字段默认 `true`，`note` 最长 200 字。允许补录最近 **3650 天**、
允许最多 **1 天**未来日期（用于跨零点夜班场景），越界返回 `error.date_too_old` /
`error.date_in_future`；起床日只能是入睡日当天或次日（否则 `error.invalid_date_range`），
起床时刻必须晚于入睡时刻（否则 `error.invalid_time_range`）。

> 一条记录跨两个日历日。它描述的是**起床后那一天**：记录页勾的三餐、计划日卡片、
> 打卡与达标率都以起床日为准（老记录没有 `wake_date` 时按同样的规则推导，行为不变）；
> 入睡日仍是睡眠统计、时间轴与保留策略的标签。

### `POST /api/records`

```json
{
  "sleep_time": "23:40",
  "wake_time": "07:20",
  "record_date": "2026-02-19",
  "wake_date": "2026-02-20",
  "meal_breakfast": true,
  "meal_lunch": true,
  "meal_dinner": false,
  "note": "睡前喝了咖啡"
}
```

```json
{
  "id": 118,
  "user_id": 2,
  "sleep_time": "23:40",
  "wake_time": "07:20",
  "meal_breakfast": true,
  "meal_lunch": true,
  "meal_dinner": false,
  "record_date": "2026-02-19",
  "wake_date": "2026-02-20",
  "note": "睡前喝了咖啡",
  "duration_minutes": 460,
  "created_at": "2026-02-19T23:41:02",
  "updated_at": "2026-02-19T23:41:02",
  "username": null
}
```

`duration_minutes` 由服务端按两个日期与两个时刻计算（`(wake_date - record_date) * 1440 + wake - sleep`），
客户端不用传；不传 `wake_date` 时服务端按跨零点规则推导，与老版本结果一致。

> 记录里**没有**"最晚起床 / 最早入睡"两个字段了：这两个约束已按用户要求删除，
> 请求里带上它们不会报错，但服务端不会保存、也不会再据此限制计划。
成功返回 **201**；10 秒内重复写入返回 429 `error.write_too_fast`。

### 记录查询与维护

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/records` | 自己的记录列表，支持 `start_date`、`end_date`、`page`、`page_size`；按日期倒序 |
| `GET` | `/api/records/meta` | 录入页元信息：`total`、`latest_record_date`、`retention_months`、`retention_cutoff`、`write_cooldown_seconds`、`cooldown_remaining_seconds`、`today`、`max_future_days` |
| `GET` | `/api/records/{record_id}` | 单条记录（非本人返回 404） |
| `PUT` | `/api/records/{record_id}` | 部分更新，只传要改的字段；没有可改字段时 400 `error.nothing_to_update` |
| `DELETE` | `/api/records/{record_id}` | 删除单条 |
| `POST` | `/api/records/batch-delete` | 批量删除：`{"ids": [1, 2, 3]}`，最多 500 个 id |

```json
{
  "items": [
    {
      "id": 118,
      "user_id": 2,
      "sleep_time": "23:40",
      "wake_time": "07:20",
      "meal_breakfast": true,
      "meal_lunch": true,
      "meal_dinner": false,
      "record_date": "2026-02-19",
      "wake_date": "2026-02-20",
      "note": null,
      "duration_minutes": 460,
      "created_at": "2026-02-19T23:41:02",
      "updated_at": "2026-02-19T23:41:02"
    }
  ],
  "total": 37,
  "page": 1,
  "page_size": 20,
  "pages": 2,
  "stats": null
}
```

```json
{
  "ok": true,
  "message": "records.deleted",
  "extra": {"deleted": 3}
}
```

批量删除时 `ids` 为空返回 400 `error.nothing_selected`；其中不属于当前用户的 id 会被忽略
（按不存在处理，不会泄露是否存在）。

## 建议 `/api/advice`

算法规则与常量含义见 [ALGORITHM.md](ALGORITHM.md)。

### `POST /api/advice`

生成未来若干天的作息调整建议。

```json
{
  "mode": "fast",
  "days": 7,
  "use_target": true,
  "meal_breakfast": true,
  "meal_lunch": true,
  "meal_dinner": true,
  "save": true
}
```

- `mode`：`fast`（尽快调整，参考 1 天）或 `slow`（温和调整，默认 7 天）；只影响步子分配。
- `days`：1–30；省略则按需推算。三个 `meal_*` 省略时沿用用户设置。
- `use_target`：`true` 时优先使用自定义目标作息，否则用系统推荐目标（响应 `source` 会说明用了哪个）。
- `save`：默认 `true`，把这次结果落库为“当前计划”快照，保证跨设备看到同一份计划。

```json
{
  "mode": "fast",
  "days": 7,
  "source": "custom_target",
  "basis": {"sample_days": 14, "avg_sleep_time": "00:20", "avg_wake_time": "08:10"},
  "target": {"sleep_time": "23:00", "wake_time": "07:00"},
  "steps": {"cap_minutes": 60, "hard_cap_minutes": 120, "shape": {"alpha": 0.6, "power": 1.0},
            "first_step_minutes": 53, "max_step_minutes": 53, "last_step_minutes": 33,
            "feasible": true, "suggested_days": 7},
  "meal_plan": {"breakfast_skipped_streak": 0, "lunch_shift_minutes": 0, "refused_breakfast_today": false, "dinner_shift_minutes": 0,
                "history": {"breakfast": {"eaten": 5, "skipped": 0, "refused": 0, "unknown": 2}, "lunch": {"eaten": 5, "skipped": 0, "refused": 0, "unknown": 2}, "dinner": {"eaten": 5, "skipped": 0, "refused": 0, "unknown": 2}},
                "recent": [{"date": "2026-02-20", "meals": {"breakfast": {"state": "eaten", "source": "record"}, "lunch": {"state": "eaten", "source": "record"}, "dinner": {"state": "unknown", "source": null}}]},
  "warnings": [],
  "notices": [{"code": "advice.notice.short_plan", "days": 5}],
  "notes": [],
  "plan": [
    {
      "index": 1,
      "date": "2026-02-20",
      "sleep_time": "23:45",
      "wake_time": "07:05",
      "duration_minutes": 440,
      "shift_sleep_minutes": -35,
      "shift_wake_minutes": -35,
      "step_minutes": 35,
      "meals": {
        "breakfast": {"enabled": true, "best_time": "07:20", "time": "07:20", "window_start": "07:20", "window_end": "07:35", "bar_minutes": 30, "anchor": "wake", "shifted_minutes": 0, "note": null},
        "lunch": {"enabled": true, "best_time": "12:18", "time": "12:18", "window_start": "12:18", "window_end": "14:18", "bar_minutes": 60, "anchor": "both", "shifted_minutes": 0, "note": null},
        "dinner": {"enabled": true, "best_time": "19:15", "time": "19:15", "window_start": "19:15", "window_end": "20:45", "bar_minutes": 60, "anchor": "sleep", "shifted_minutes": 0, "note": null}
      },
      "timeline": {"axis_start": "05:05", "axis_end": "01:45", "span_minutes": 1240, "sleep_label": "23:45–07:05", "sleep_minutes": 1000, "items": [{"kind": "sleep", "segment": "morning", "start": 0, "end": 120, "start_time": "05:05", "end_time": "07:05", "partial": true}, {"kind": "meal", "meal": "breakfast", "start": 135, "end": 165, "start_time": "07:20", "end_time": "07:35", "partial": false, "shifted_minutes": 0}, {"kind": "sleep", "segment": "night", "start": 1120, "end": 1240, "start_time": "23:45", "end_time": "01:45", "partial": true}]},
      "tips": ["advice.tip.keep_regular"]
    }
  ],
  "today": {
    "index": 0, "date": "2026-02-20", "sleep_time": "23:45", "wake_time": "07:05",
    "duration_minutes": 440, "shift_sleep_minutes": -35, "shift_wake_minutes": -35,
    "step_minutes": 35, "meals": {}, "timeline": {}, "tips": [],
    "refusal_preview": {
      "date": "2026-02-20",
      "lunch": {"best_time": "11:00", "window_start": "11:00", "window_end": "13:00"},
      "dinner": {"best_time": "18:15", "window_start": "18:15", "window_end": "19:45"},
      "lunch_shift_minutes": 60,
      "dinner_shift_minutes": 60
    }
  },
  "timeline": {"axis_start": "05:05", "axis_end": "01:45", "span_minutes": 1240, "sleep_label": "23:45–07:05", "sleep_minutes": 1000, "items": [{"kind": "sleep", "segment": "morning", "start": 0, "end": 120, "start_time": "05:05", "end_time": "07:05", "partial": true}, {"kind": "sleep", "segment": "night", "start": 1120, "end": 1240, "start_time": "23:45", "end_time": "01:45", "partial": true}]},
  "plan_meta": {"start_date": "2026-02-20", "target_date": "2026-02-26", "postponed_days": 0, "replans": 0, "saved": true, "plan_id": 41},
  "generated_at": "2026-02-20T07:30:00",
  "engine": "adaptive-v2"
}
```

`timeline.items` 的元素是 `{"kind": "sleep"|"meal", ...}`：睡眠条目带 `segment`（`morning` =
轴头那截、`night` = 轴尾那截）与 `partial: true`，三餐条目带 `meal` 与 `shifted_minutes`。
`start` / `end` 是**相对 `axis_start` 的分钟偏移**（不是钟点字符串），`start_time` / `end_time`
是给人看的钟点。轴窗是「起床前 2 小时 → 入睡后 2 小时」，所以整夜睡眠被切在轴的两头：
前端把两截标成「睡眠前段 / 睡眠后段」，并在轴上的睡眠徽标里写「跨零点，轴两端相接」
（数据结构与跨零点处理见 [ALGORITHM.md](ALGORITHM.md) 第 9 节）。

今日卡片上另有一个 **`today.refusal_preview`**（只有今天有）：它预先算好“现在点『不想吃早餐』会把
午餐 / 晚餐挪到几点”，字段与 `meals.*` 同构（`best_time` / `window_start` / `window_end`），外加
`lunch_shift_minutes` / `dinner_shift_minutes`。前端据此在**点击瞬间**就改掉卡片上的数字（实测
25ms，而这次打卡请求往返约 1.5s），不用等响应、也不用刷新页面；预测值与打卡后真正落库的时间
一致，由 `tests/test_advice_engine.py::test_refusal_preview_matches_the_real_checkin` 守着。
当天已经是“不想吃”状态时前端不再显示这句提示（再点一次没有意义）。

`steps` 描述的是**曲线本身**，而不是某张日卡：

- `cap_minutes`：每日建议步长（现在前移/后移都是 **60**）。它只是提示阈值，**不会截断计划**；
- `hard_cap_minutes`：单日输入上限（120），`fast` 档的可行判定与输入校验用它；
- `shape`：形状函数的参数 `{alpha, power}`（当前 `0.6 / 1.0`）。曲线由“总偏移 + 天数”现场生成，
  不再返回固定分段表；
- `first_step_minutes` / `max_step_minutes` / `last_step_minutes`：首日、最大一步、末日各要移动多少分钟
  （都与对应日卡的 `step_minutes`、卡片右上角“今天移动 N 分钟”同源）。形状函数下首日就是最大一步，
  末日仍保留首日约 62% 的速度；
- `feasible` / `suggested_days`：`feasible = max_step_minutes <= cap_minutes`，不满足时计划照旧排满
  所选天数（末日仍落在目标上），只是最大一步超过 60 分钟 —— `advice.steps.infeasible` /
  `advice.notice.infeasible` 用 `{step}`（= `max_step_minutes`）与 `{cap}`（= `cap_minutes`）把代价写清楚。

加 `shape` / `first_step_minutes` 之前存下的旧快照没有这些字段，前端会退回从日卡的 `step_minutes`
反推首 / 最大 / 收尾三个数，所以老计划的卡片不会出现空值或花括号。

### `GET /api/advice/plan`

读取**当前计划快照**：今日目标、偏离程度、达标率、连续达标天数、未确认的通知等。返回体与
`POST /api/advice` 同构，另带 `deviation`、`adherence`、`auto_recomputed`、`acked_notices`、
`engine` 等状态字段。没有快照时 `plan_meta.saved` 为 `false`。

### `POST /api/advice/checkin`

三餐卡片的“已吃 / 跳过 / 不想吃”一键确认，会写回当天记录（因此受 10 秒写入冷却限制）。

```json
{
  "meal": "breakfast",
  "eaten": false,
  "refused": false,
  "date": "2026-02-20"
}
```

- `refused`：只有早餐用得上。`true` 表示“不想吃”，与“跳过”一样计入连续未吃早餐，
  **并把当天的午餐与晚餐一起提前 60 分钟**（晚餐 −60，午餐按“取中点”跟着提前，正常作息下
  不会早于现实窗口起点 11:00，所以实测浮动在 60–90 分钟之间）；
  给非早餐的餐次传 `true` 会被服务端忽略。点“已吃”即可撤销。
- `date` 省略表示今天。
- `meal_plan.breakfast_missed_today`：今天的早餐是不是“跳过 / 不想吃”（`refused_breakfast_today`
  只看“不想吃”）。它是 `today.refusal_preview` 里判断“再点一次”该怎么算阶梯的依据。

```json
{
  "ok": true,
  "date": "2026-02-20",
  "meal": "breakfast",
  "eaten": false,
  "refused": true,
  "meal_plan": {
    "breakfast_skipped_streak": 1,
    "lunch_shift_minutes": 60,
    "breakfast_missed_today": true,
    "refused_breakfast_today": true,
    "dinner_shift_minutes": 60,
    "history": {"breakfast": {"eaten": 4, "skipped": 1, "refused": 1, "unknown": 2}, "lunch": {"eaten": 5, "skipped": 0, "refused": 0, "unknown": 2}, "dinner": {"eaten": 5, "skipped": 0, "refused": 0, "unknown": 2}},
    "recent": [{"date": "2026-02-20", "meals": {"breakfast": {"state": "refused", "source": "checkin"}, "lunch": {"state": "unknown", "source": null}, "dinner": {"state": "unknown", "source": null}}}]
  },
  "today": {"date": "2026-02-20", "meals": {"lunch": {"best_time": "11:00", "window_end": "13:00"}, "dinner": {"best_time": "18:15", "window_end": "19:45"}}},
  "timeline": {"axis_start": "05:05", "axis_end": "01:45", "span_minutes": 1240, "sleep_label": "23:45–07:05", "sleep_minutes": 1000, "items": [{"kind": "sleep", "segment": "morning", "start": 0, "end": 120, "start_time": "05:05", "end_time": "07:05", "partial": true}, {"kind": "sleep", "segment": "night", "start": 1120, "end": 1240, "start_time": "23:45", "end_time": "01:45", "partial": true}]}
}
```

响应除 `meal_plan` 外还带 `today`（当天卡片，含三餐与时间图，以及重算后的 `refusal_preview`）与
`timeline`，前端据此刻度重画，不必再拉一次整页。界面上的即时反馈分两步：点击时先用**点击前**
那份 `today.refusal_preview` 直接改数字并标出“旧 → 新”，响应回来后再用这里的 `today` 校正
（两者一致，见上文 `test_refusal_preview_matches_the_real_checkin`）；失败则回滚到点击前的快照。

连续多天未吃早餐会让午餐建议逐步前移（`shifted_minutes` 递增，每次 60 / 90 / 120 分钟，
下限是起床后 2 小时与现实窗口起点 11:00 中更晚的那个），规则见 [ALGORITHM.md](ALGORITHM.md)。

### `POST /api/advice/notice-ack`

“我已知晓，不再提示”，账号级记忆、跨设备生效。

```json
{
  "notice": "advice.notice.short_plan"
}
```

```json
{
  "ok": true,
  "acked_notices": ["advice.notice.short_plan"]
}
```

### `POST /api/advice/replan`

偏离过大（> 90 分钟）时由用户选择处理策略。

```json
{
  "strategy": "postpone"
}
```

- `postpone`：整体顺延目标日期，保持每天步长不变。
- `recompute`：保持目标日期，重新分配更小的步长。
- `keep`：继续原计划（不写新快照）。

响应是刷新后的计划状态（与 `GET /api/advice/plan` 同构），额外带 `strategy` 与 `saved` 字段。

### `GET /api/advice/options`

前端渲染选项用的元信息，不需要先生成完整建议。

```json
{
  "modes": [
    {"value": "fast", "label": "advice.mode.fast", "days": 1},
    {"value": "slow", "label": "advice.mode.slow", "days": 7}
  ],
  "default_days": 7,
  "max_days": 30,
  "has_custom_target": true,
  "target_sleep_time": "23:00",
  "target_wake_time": "07:00",
  "meal_flags": {"breakfast": true, "lunch": true, "dinner": true},
  "steps": {
    "cap_minutes": 60,
    "hard_cap_minutes": 120,
    "shape": {"alpha": 0.6, "power": 1.0},
    "short_plan_daily_threshold": 60
  }
}
```

## 设置与账号 `/api/settings`、`/api/account`

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/settings` | — | 读取当前账号配置（同 `UserPublic`） |
| `PUT` | `/api/settings` | `{"language":"en","theme":"light","meal_dinner":false}` | 部分更新语言 / 主题 / 三餐开关；无字段时 400 |
| `PUT` | `/api/settings/target` | `{"target_sleep_time":"23:00","target_wake_time":"07:00"}` | 设置自定义目标作息 |
| `GET` | `/api/account/deletion` | — | 查询当前注销申请状态 |
| `POST` | `/api/account/deletion` | `{"reason":"不再使用"}` | 提交注销申请（管理员在后台审批） |
| `DELETE` | `/api/account/deletion` | — | 撤销自己的注销申请 |

```json
{
  "language": "zh",
  "theme": "dark",
  "meal_breakfast": true,
  "meal_lunch": true,
  "meal_dinner": false,
  "target_sleep_time": "23:00",
  "target_wake_time": "07:00"
}
```

`PUT /api/settings/target` 的 `target_sleep_time` / `target_wake_time` **必须同时给出或同时为
`null`**（`null` 表示清空、回到系统推荐），只给一个返回 400 `error.target_requires_both`。

```json
{
  "id": 7,
  "user_id": 2,
  "username": "demo",
  "status": "pending",
  "reason": "不再使用",
  "requested_at": "2026-02-20T09:00:00",
  "handled_at": null,
  "handled_by": null,
  "handled_by_username": null,
  "handle_note": null
}
```

重复提交返回 409 `error.deletion_pending`；管理员账号不能申请注销（400
`error.admin_cannot_deactivate`）。

## 管理后台 `/api/admin`

**所有接口都要求管理员会话**（`current_admin`），并写审计日志。管理员对用户睡眠记录
**只读 + 可删除**（不能修改），符合需求约束。

### 用户管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/users` | 用户列表，支持 `q`（用户名模糊）、`is_admin`、`is_active`、分页；每项带 `record_count`、`locked`、`last_login_at`、`last_login_ip` |
| `GET` | `/api/admin/users/{user_id}` | 单个用户详情（不存在 404 `error.user_not_found`） |
| `POST` | `/api/admin/users` | 新建用户；`is_admin: true` 时必须提供 `admin_passwords`（第 2/3 段） |
| `PATCH` | `/api/admin/users/{user_id}` | 启停 / 改语言主题；停用自己 400 `error.cannot_disable_self`，停用最后一个管理员 400 `error.last_admin` |
| `POST` | `/api/admin/users/{user_id}/password` | 重置某用户密码（`{"new_password":"..."}`） |
| `POST` | `/api/admin/users/{user_id}/unlock` | 解除登录锁定，并清掉该用户名在失败窗口内的记录（解锁后重新从满额开始提示剩余次数） |
| `DELETE` | `/api/admin/users/{user_id}` | 删除用户；删自己 400 `error.cannot_delete_self`；删最后一个管理员 400 `error.last_admin` |

```json
{
  "username": "demo2",
  "password": "demo23456",
  "is_admin": false,
  "language": "zh",
  "theme": "dark",
  "admin_passwords": []
}
```

```json
{
  "items": [
    {
      "id": 2,
      "username": "demo",
      "is_admin": false,
      "is_active": true,
      "language": "zh",
      "theme": "dark",
      "target_sleep_time": "23:00",
      "target_wake_time": "07:00",
      "failed_login_count": 0,
      "locked_until": null,
      "locked": false,
      "created_at": "2026-02-01T08:12:31",
      "last_login_at": "2026-02-20T22:03:10",
      "last_login_ip": "203.0.113.7",
      "record_count": 37
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

### 用户记录（只读 + 删除）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/users/{user_id}/records` | 只读列表，支持 `start_date`、`end_date`、分页 |
| `DELETE` | `/api/admin/users/{user_id}/records/{record_id}` | 删除单条（不存在 404 `error.record_not_found`） |
| `POST` | `/api/admin/users/{user_id}/records/batch-delete` | `{"ids":[1,2,3]}`，空列表 400 `error.nothing_selected` |

### 注销申请

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/deletion-requests` | 支持 `status`（`pending` / `approved` / `rejected`）、`username`、分页 |
| `POST` | `/api/admin/deletion-requests/{request_id}` | 审批：`{"action":"approve","note":"已确认"}`；已处理过 409 `error.deletion_already_handled`；不能批准自己的申请 400 `error.cannot_delete_self` |

```json
{
  "action": "approve",
  "note": "用户已确认，批准注销"
}
```

```json
{
  "ok": true,
  "message": "admin.deletion_approved",
  "extra": {"request_id": 7}
}
```

### 日志、统计与维护

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/admin/logs` | 系统日志检索：`level`、`event`、`q`（消息模糊）、`user_id`、分页；返回带 `level_counts` |
| `GET` | `/api/admin/stats` | 概览统计：用户数、记录数、今日活跃、待处理注销申请、日志量等 |
| `POST` | `/api/admin/cleanup` | 手动触发一次清理（过期会话、超期记录、日志裁剪） |

```json
{
  "items": [
    {
      "id": 9182,
      "level": "INFO",
      "event": "login_success",
      "message": "user demo logged in",
      "user_id": 2,
      "username": "demo",
      "ip": "203.0.113.7",
      "path": "/api/auth/login",
      "created_at": "2026-02-20T22:03:10"
    }
  ],
  "total": 9182,
  "page": 1,
  "page_size": 20,
  "pages": 460,
  "level_counts": {"INFO": 8800, "WARNING": 350, "ERROR": 32}
}
```

```json
{
  "ok": true,
  "message": "admin.cleanup_done",
  "extra": {"sleep_records": 12, "sessions": 3, "system_logs": 140, "login_attempts": 8}
}
```

## 相关文档

- 算法规则、常量与文献依据：[ALGORITHM.md](ALGORITHM.md)、[REFERENCES.md](REFERENCES.md)
- 配置项与默认值：[CONFIGURATION.md](CONFIGURATION.md)
- 本地起服务与自检：[DEVELOPMENT.md](DEVELOPMENT.md)
