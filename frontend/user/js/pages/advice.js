/* ==========================================================================
   作息建议页（adaptive-v2）
   - 挂载时先读 GET /api/advice/plan：有快照就展示"当前计划"，没有才显示生成表单
   - 今日卡片：入睡/起床目标、当天步长、三餐最佳时间与"已吃 / 跳过"一键确认
   - 时间图见 /shared/timeline.js：顶部大图 + 每日行小图
   - 偏离三级处理：none 不提示 / auto 一行静默说明 / ask 弹窗选策略
   - 所有后端字符串都是 i18n key，统一走 t(key, params)
   ========================================================================== */

import { adviceApi, ApiError } from "/shared/api.js";
import { t } from "/shared/i18n.js";
import { renderTimeline } from "/shared/timeline.js";
import {
  escapeHtml,
  fmtDateTime,
  fmtDuration,
  openModal,
  qs,
  toastErr,
  toastOk,
} from "/shared/ui.js";

const MEALS = ["breakfast", "lunch", "dinner"];
const MEAL_ICONS = { breakfast: "🥣", lunch: "🍱", dinner: "🍲" };

const view = {
  options: null,
  state: null, // 当前计划状态（plan / today / timeline / deviation / adherence / meal_plan / ...）
  form: {
    mode: "fast",
    days: 7,
    use_target: true,
    meal_breakfast: true,
    meal_lunch: true,
    meal_dinner: true,
  },
  pending: false,
  navigate: null,
  formOpen: false,
  deviating: false,
  langBound: false,
};

/* ------------------------------- 小工具 ------------------------------- */

// t() 不会替换缺失的参数（原样留 "{x}"），所以先兜一层默认值
const tp = (key, params) => t(key, params || {});

/** 分钟数 -> 本地化时长：不足 1 小时用"n 分钟"，其余用 h/m */
function fmtMinutes(minutes) {
  const value = Math.round(Number(minutes) || 0);
  return value >= 60 ? fmtDuration(value) : `${value} ${t("advice.minutes")}`;
}

/** 当天调整幅度：0 显示"无需调整"，否则是 "−30 分钟" / "+30 min" 这样的短标签 */
function shiftText(minutes) {
  const value = Math.round(Number(minutes) || 0);
  if (value === 0) return t("advice.shiftNone");
  const sign = value > 0 ? "+" : "−";
  return `${sign}${Math.abs(value)} ${t("advice.minutes")}`;
}

const enabledMeals = (meals) => MEALS.filter((key) => meals?.[key]?.enabled);

function mealIcon(key) {
  return MEAL_ICONS[key] || "🍽";
}

const mealName = (key) => t(`records.meal.${key}`);

function mealStateOf(mealPlan, key) {
  const today = view.state?.today?.date || view.state?.plan?.today?.date;
  const row = (mealPlan?.recent || []).find((item) => item.date === today);
  return row?.meals?.[key]?.state || "unknown";
}

/* ------------------------------ 提示条 ------------------------------ */

const NOTICE_CLASS = { info: "notice-info", warn: "notice-warn", danger: "notice-danger" };

function noticesHtml(data = {}) {
  const notices = data.notices || [];
  const shown = new Set(notices.map((item) => item.code));
  // warnings 只保留后端历史上单独返回的 advice.warn.*，且不与 notices 重复渲染
  const legacy = (data.warnings || []).filter((code) => !shown.has(code));

  const noticeNodes = notices
    .map((notice) => {
      if (notice.acked) return "";
      const params = notice.params || {};
      const ack =
        notice.ackable && !notice.acked
          ? `<button class="btn btn-sm" data-ack="${escapeHtml(notice.code)}">${escapeHtml(
              t("advice.notice.ack")
            )}</button>`
          : "";
      return `<div class="notice ${NOTICE_CLASS[notice.severity] || "notice-info"}">
        <div class="notice-body"><p>${escapeHtml(tp(notice.code, params))}</p>${ack}</div>
      </div>`;
    })
    .filter(Boolean);

  const warnNodes = legacy.map(
    (code) => `<div class="notice notice-warn"><div class="notice-body"><p>${escapeHtml(t(code))}</p></div></div>`
  );

  return noticeNodes.concat(warnNodes).join("");
}

/** 偏离：auto 只给一行安静说明；ask 交给弹窗；none 什么都不显示 */
function deviationLineHtml(data = {}) {
  if (data.deviation?.level !== "auto" && !data.auto_recomputed) return "";
  return `<div class="deviation-line">🔄 ${escapeHtml(t("advice.deviation.auto"))}</div>`;
}

/* ------------------------------ 今日卡片 ------------------------------ */

function mealHintHtml(mealPlan) {
  const streak = Number(mealPlan?.breakfast_skipped_streak) || 0;
  const shift = Number(mealPlan?.lunch_shift_minutes) || 0;
  if (!streak || !shift) return "";
  return `<p class="tiny muted">${escapeHtml(
    tp("advice.meal.lunch_advanced", { streak, shift })
  )}</p>`;
}

function todayMealHtml(key, meal, mealPlan, canCheckin, preview) {
  const state = mealStateOf(mealPlan, key);
  const best = meal.best_time || meal.time || "--:--";
  const window = `${meal.window_start || "--:--"}–${meal.window_end || "--:--"}`;
  const note = meal.note ? `<p class="tiny muted">${escapeHtml(t(meal.note))}</p>` : "";
  // 晚餐被"不想吃早餐"带着提前时，给一句说明：否则用户只看到时间变了却不知道为什么
  const dinnerShift = Number(mealPlan?.dinner_shift_minutes) || 0;
  const dinnerHint =
    key === "dinner" && dinnerShift > 0
      ? `<p class="tiny muted">${escapeHtml(tp("advice.meal.dinner_advanced", { shift: dinnerShift }))}</p>`
      : "";
  // "不想吃"只在早餐出现：它和"跳过"一样算没吃，但额外把今天的午餐与晚餐一起提前
  const refusedBtn =
    key === "breakfast"
      ? `<button class="btn btn-sm checkin-btn${state === "refused" ? " active-refused" : ""}" data-meal="${key}" data-eaten="0" data-refused="1">${escapeHtml(
          t("advice.meal.checkin.refused")
        )}</button>`
      : "";
  // 点之前就告诉用户"点了午餐晚餐会变成几点"：值来自后端算好的预测，和点下去的结果同源
  const previewLine =
    key === "breakfast" && state !== "refused" && preview?.lunch?.best_time
      ? `<p class="tiny meal-preview" data-refusal-preview>${escapeHtml(
          tp("advice.meal.refusal_preview", {
            lunch: preview.lunch.best_time,
            dinner: preview.dinner?.best_time || "--:--",
          })
        )}</p>`
      : "";
  const checkin = canCheckin
    ? `<div class="checkin-row">
        <button class="btn btn-sm checkin-btn${state === "eaten" ? " active-ok" : ""}" data-meal="${key}" data-eaten="1">${escapeHtml(
          t("advice.meal.checkin.eaten")
        )}</button>
        <button class="btn btn-sm checkin-btn${state === "skipped" ? " active-skip" : ""}" data-meal="${key}" data-eaten="0">${escapeHtml(
          t("advice.meal.checkin.skipped")
        )}</button>
        ${refusedBtn}
        ${
          state === "unknown"
            ? `<span class="tiny muted">${escapeHtml(t("advice.meal.unknown"))}</span>`
            : `<span class="tiny muted">${escapeHtml(t("advice.meal.checkin.saved"))}</span>`
        }
      </div>`
    : "";

  return `<div class="today-meal" data-meal="${key}">
    <div class="tm-head">
      <span class="m-icon">${mealIcon(key)}</span>
      <span class="m-name">${escapeHtml(mealName(key))}</span>
    </div>
    <div class="tm-best">
      <strong class="mono" data-best>${escapeHtml(best)}</strong>
      <span class="tm-best-label">${escapeHtml(t("advice.meal.best"))}</span>
    </div>
    <div class="tm-window">
      <span>${escapeHtml(t("advice.meal.window"))}</span>
      <strong class="mono" data-window>${escapeHtml(window)}</strong>
    </div>
    ${note}
    ${dinnerHint}
    ${checkin}
    ${previewLine}
  </div>`;
}

function todayCardHtml(data) {
  const day = data.today || data.plan?.today;
  if (!day) return "";
  const meals = enabledMeals(day.meals);
  const canCheckin = day.date === (view.options?.today || day.date);

  return `<section class="card today-card" data-today>
    <div class="row-wrap today-head">
      <h2>${escapeHtml(t("advice.today.title"))}</h2>
      <span class="badge badge-accent">${escapeHtml(t("advice.day", { n: day.index }))}</span>
      <span class="mono muted">${escapeHtml(day.date)}</span>
      <span class="grow"></span>
      <span class="badge">${escapeHtml(fmtDuration(day.duration_minutes))}</span>
    </div>

    <div class="advice-summary">
      <div class="summary-cell">
        <span class="label">🌙 ${escapeHtml(t("records.sleepTime"))}</span>
        <div class="summary-lines">
          <div class="summary-line"><strong class="mono">${escapeHtml(day.sleep_time)}</strong>
            <span>${escapeHtml(shiftText(day.shift_sleep_minutes))}</span></div>
        </div>
      </div>
      <div class="summary-cell">
        <span class="label">☀️ ${escapeHtml(t("records.wakeTime"))}</span>
        <div class="summary-lines">
          <div class="summary-line"><strong class="mono">${escapeHtml(day.wake_time)}</strong>
            <span>${escapeHtml(shiftText(day.shift_wake_minutes))}</span></div>
        </div>
      </div>
      <div class="summary-cell">
        <span class="label">⏱ ${escapeHtml(t("advice.shift"))}</span>
        <div class="summary-lines">
          <div class="summary-line"><strong class="mono">${escapeHtml(fmtMinutes(day.step_minutes))}</strong>
            <span>${escapeHtml(t("advice.today.step", { minutes: day.step_minutes }))}</span></div>
        </div>
      </div>
    </div>

    ${
      meals.length
        ? `<div class="today-meals">
            <div class="row-wrap"><span class="label">${escapeHtml(t("advice.today.meals"))}</span>
              ${mealHintHtml(data.meal_plan)}</div>
            <div class="today-meal-grid" data-meals>${meals
              .map((key) => todayMealHtml(key, day.meals[key], data.meal_plan, canCheckin, day.refusal_preview))
              .join("")}</div>
          </div>`
        : ""
    }

    <div class="today-timeline">
      <div class="row-wrap"><span class="label">${escapeHtml(t("advice.timeline.title"))}</span></div>
      <div class="timeline-slot" data-timeline></div>
    </div>
  </section>`;
}

/* ------------------------------ 步长 / 达标 ------------------------------ */

function stepsCardHtml(data) {
  const steps = data.plan?.steps;
  if (!steps) return "";
  const direction =
    steps.direction === "advance" ? t("advice.steps.direction.advance") : t("advice.steps.direction.delay");
  const curve = (steps.curve_minutes || []).join("/");
  const shortPlan = (data.plan?.notices || []).some((item) => item.code === "advice.notice.short_plan");
  const showSuggested = steps.feasible === false || shortPlan;

  return `<section class="card stack" style="gap:10px">
    <div class="row-wrap">
      <h2 style="margin:0">${escapeHtml(t("advice.steps.title"))}</h2>
      <span class="badge badge-accent">${escapeHtml(direction)}</span>
      <span class="grow"></span>
      <span class="badge ${steps.feasible === false ? "badge-warn" : "badge-ok"}">${escapeHtml(
        steps.feasible === false
          ? tp("advice.steps.infeasible", { days: steps.suggested_days })
          : t("advice.steps.feasible")
      )}</span>
    </div>
    <div class="info-list">
      <div class="info-line"><span>${escapeHtml(t("advice.steps.dailyAverageLabel"))}</span>
        <strong class="mono">${escapeHtml(tp("advice.steps.dailyAverage", { minutes: steps.daily_average_minutes }))}</strong></div>
      <div class="info-line"><span>${escapeHtml(
        tp("advice.steps.caps", { advance: steps.cap_advance_minutes, delay: steps.cap_delay_minutes })
      )}</span><strong class="mono">${escapeHtml(fmtMinutes(steps.hard_cap_minutes))}</strong></div>
      <div class="info-line"><span>${escapeHtml(tp("advice.steps.curve", { curve }))}</span>
        <strong class="mono">${escapeHtml(curve)}</strong></div>
      ${
        showSuggested
          ? `<div class="info-line"><span>${escapeHtml(t("advice.steps.suggestedDaysLabel"))}</span>
              <strong class="mono">${escapeHtml(
                tp("advice.steps.suggestedDays", { days: steps.suggested_days })
              )}</strong></div>`
          : ""
      }
    </div>
  </section>`;
}

function adherenceCardHtml(data) {
  const adherence = data.adherence;
  if (!adherence) return "";
  const rate =
    adherence.hit_rate === null || adherence.hit_rate === undefined
      ? "—"
      : `${Math.round(Number(adherence.hit_rate) * 100)}%`;
  const empty = !adherence.days_recorded;

  return `<section class="card stack" style="gap:10px">
    <div class="row-wrap">
      <h2 style="margin:0">${escapeHtml(t("advice.adherence.title"))}</h2>
      <span class="grow"></span>
      <span class="badge">${escapeHtml(tp("advice.adherence.recorded", {
        recorded: adherence.days_recorded,
        elapsed: adherence.days_elapsed,
      }))}</span>
    </div>
    ${
      empty
        ? `<p class="muted">${escapeHtml(t("advice.adherence.empty"))}</p>`
        : `<div class="adherence-grid">
            <div class="summary-cell">
              <span class="label">${escapeHtml(tp("advice.adherence.hitRate", { rate }))}</span>
              <span class="value mono">${escapeHtml(rate)}</span>
            </div>
            <div class="summary-cell">
              <span class="label">${escapeHtml(
                tp("advice.adherence.streak", { days: adherence.current_streak })
              )}</span>
              <span class="value mono">${escapeHtml(String(adherence.current_streak))}</span>
            </div>
          </div>
          <p class="tiny muted">${escapeHtml(
            tp("advice.adherence.toleranceNote", { minutes: adherence.tolerance_minutes })
          )}</p>`
    }
  </section>`;
}

/* ------------------------------ 每日方案 ------------------------------ */

function mealsHtml(day) {
  const keys = enabledMeals(day.meals);
  if (!keys.length) return "";
  // 午餐提前的解释句带 {streak}/{shift} 两个参数，缺一不可，否则会把占位符直接显示出来
  const streak = Number(view.state?.meal_plan?.breakfast_skipped_streak) || 0;
  return `<div class="day-meals">${keys
    .map((key) => {
      const meal = day.meals[key];
      const shifted = Number(meal.shifted_minutes) || 0;
      const best = meal.best_time || meal.time || "--:--";
      // best_time 是主数字，区间退到次要位置；后端改成提前时用午餐提示说明
      return `<span class="day-meal">
        <span class="dm-name">${mealIcon(key)} ${escapeHtml(mealName(key))}</span>
        <span class="dm-best mono">${escapeHtml(best)}</span>
        <span class="dm-window mono">${escapeHtml(meal.window_start || "--:--")}–${escapeHtml(
          meal.window_end || "--:--"
        )}</span>
        ${
          shifted > 0 && key === "lunch" && streak > 0
            ? `<span class="badge badge-warn">${escapeHtml(
                tp("advice.meal.lunch_advanced", { streak, shift: shifted })
              )}</span>`
            : ""
        }
      </span>`;
    })
    .join("")}</div>`;
}

function dayRowHtml(day) {
  return `<article class="day-card" data-day-card data-date="${escapeHtml(day.date)}">
    <div class="dc-head">
      <span class="badge badge-accent">${escapeHtml(t("advice.day", { n: day.index }))}</span>
      <span class="mono muted">${escapeHtml(day.date)}</span>
      <span class="grow"></span>
      <span class="badge">${escapeHtml(t("advice.today.step", { minutes: day.step_minutes }))}</span>
    </div>
    <div class="dc-times">
      <div class="day-time">
        <span class="label">🌙 ${escapeHtml(t("records.sleepTime"))}</span>
        <span class="value">${escapeHtml(day.sleep_time)}</span>
        <span class="tiny muted">${escapeHtml(shiftText(day.shift_sleep_minutes))}</span>
      </div>
      <div class="day-times-arrow">→</div>
      <div class="day-time">
        <span class="label">☀️ ${escapeHtml(t("records.wakeTime"))}</span>
        <span class="value">${escapeHtml(day.wake_time)}</span>
        <span class="tiny muted">${escapeHtml(shiftText(day.shift_wake_minutes))}</span>
      </div>
      <div class="day-time">
        <span class="label">⏱ ${escapeHtml(t("records.duration"))}</span>
        <span class="value">${escapeHtml(fmtDuration(day.duration_minutes))}</span>
      </div>
    </div>
    ${mealsHtml(day)}
    <div class="timeline-slot mini" data-day-timeline></div>
    ${
      (day.tips || []).length
        ? `<ul class="tips">${day.tips.map((key) => `<li>${escapeHtml(t(key))}</li>`).join("")}</ul>`
        : ""
    }
  </article>`;
}

/* ------------------------------ 计划视图 ------------------------------ */

function planMetaHtml(data) {
  const meta = data.plan_meta || {};
  const parts = [];
  if (meta.saved) parts.push(t("advice.planMeta.saved"));
  if (Number(meta.replans) > 0) parts.push(tp("advice.planMeta.replans", { n: meta.replans }));
  if (Number(meta.postponed_days) > 0) {
    parts.push(tp("advice.planMeta.postponed", { days: meta.postponed_days }));
  }
  if (meta.target_date) {
    parts.push(tp("advice.planMeta.targetDate", { date: meta.target_date }));
  }
  if (data.plan?.generated_at) {
    parts.push(`${t("advice.generatedAt")} ${fmtDateTime(data.plan.generated_at)}`);
  }
  return parts.join(" · ");
}

function renderPlanView(root) {
  const data = view.state;
  const result = qs("#advice-result", root);
  if (!result) return;

  if (!data) {
    result.innerHTML = `<div class="card empty">
      <div class="empty-icon">🌙</div>
      <p>${escapeHtml(t("advice.noPlan"))}</p>
    </div>`;
    return;
  }

  const plan = data.plan || {};
  const sourceBadge =
    plan.source === "custom_target"
      ? `<span class="badge badge-accent">${escapeHtml(t("advice.source.custom_target"))}</span>`
      : `<span class="badge">${escapeHtml(t("advice.source.system"))}</span>`;

  result.innerHTML = `
    ${noticesHtml(plan)}
    ${deviationLineHtml(data)}
    ${todayCardHtml(data)}

    <section class="card stack" style="gap:14px">
      <div class="row-wrap">
        <h2 style="margin:0">${escapeHtml(t("advice.target"))}</h2>
        ${sourceBadge}
        <span class="badge">${escapeHtml(
          plan.mode === "fast" ? t("advice.mode.fast") : t("advice.mode.slow")
        )}</span>
        <span class="badge">${escapeHtml(t("advice.day", { n: plan.days }))}</span>
        <span class="grow"></span>
        <button class="btn btn-sm" id="advice-export">${escapeHtml(t("advice.copy"))}</button>
        ${
          typeof view.navigate === "function" && view.options?.has_custom_target === false
            ? `<button class="btn btn-sm btn-ghost" id="advice-set-target">${escapeHtml(
                t("advice.setTarget")
              )}</button>`
            : ""
        }
        <button class="btn btn-sm btn-ghost" id="advice-regenerate">${escapeHtml(t("advice.regenerate"))}</button>
      </div>
      <div class="advice-summary">
        <div class="summary-cell">
          <span class="label">${escapeHtml(t("advice.basis"))}</span>
          <div class="summary-lines">
            <div class="summary-line"><span>${escapeHtml(t("advice.basis.avgSleep"))}</span>
              <strong class="mono">${escapeHtml(plan.basis?.sleep_time || "--:--")}</strong></div>
            <div class="summary-line"><span>${escapeHtml(t("advice.basis.avgWake"))}</span>
              <strong class="mono">${escapeHtml(plan.basis?.wake_time || "--:--")}</strong></div>
            <div class="summary-line"><span>${escapeHtml(
              t("advice.basis.samples", { n: plan.basis?.samples ?? 0 })
            )}</span>
              <strong class="mono">${escapeHtml(String(plan.basis?.samples ?? 0))}</strong></div>
          </div>
        </div>
        <div class="summary-cell">
          <span class="label">${escapeHtml(t("advice.target"))}</span>
          <div class="summary-lines">
            <div class="summary-line"><span>${escapeHtml(t("advice.basis.avgSleep"))}</span>
              <strong class="mono">${escapeHtml(plan.target?.sleep_time || "--:--")}</strong></div>
            <div class="summary-line"><span>${escapeHtml(t("advice.basis.avgWake"))}</span>
              <strong class="mono">${escapeHtml(plan.target?.wake_time || "--:--")}</strong></div>
            <div class="summary-line"><span>${escapeHtml(t("advice.basis.avgDuration"))}</span>
              <strong class="mono">${escapeHtml(fmtDuration(plan.target?.duration_minutes))}</strong></div>
          </div>
        </div>
      </div>
      <p class="tiny muted">${escapeHtml(planMetaHtml(data))}</p>
    </section>

    ${stepsCardHtml(data)}

    <section class="card stack" style="gap:12px">
      <div class="row-wrap">
        <h2 style="margin:0">${escapeHtml(t("advice.plan"))}</h2>
        <span class="grow"></span>
        <span class="tiny muted">${escapeHtml(t("advice.engine", { engine: plan.engine || "-" }))}</span>
      </div>
      <div class="day-list">${(plan.plan || []).map(dayRowHtml).join("")}</div>
    </section>

    ${adherenceCardHtml(data)}

    ${
      (plan.notes || []).length
        ? `<section class="card stack" style="gap:6px">
            <h2 style="margin:0">${escapeHtml(t("advice.notes"))}</h2>
            <div class="note-list">${plan.notes
              .map((key) => `<div class="note">${escapeHtml(t(key))}</div>`)
              .join("")}</div>
          </section>`
        : ""
    }`;

  mountTimelines(root, data);
  bindPlanActions(root);
}

/** 时间图必须在插入 DOM 之后再挂载（今天的大图带"现在"标记） */
function mountTimelines(root, data) {
  const todaySlot = qs("[data-timeline]", root);
  if (todaySlot && data.timeline) {
    const node = renderTimeline(data.timeline, { now: true });
    todaySlot.innerHTML = "";
    if (node) todaySlot.appendChild(node);
  }
  const plan = data.plan?.plan || [];
  root.querySelectorAll("[data-day-timeline]").forEach((slot, index) => {
    const day = plan[index];
    if (!day) return;
    const node = renderTimeline(day.timeline, { mini: true });
    if (!node) return;
    slot.innerHTML = "";
    slot.appendChild(node);
  });
}

/** 三餐按钮走事件委托：局部重画后重新调用一次即可，不必给每个按钮挂监听 */
function bindMealCheckin(root, scope = root) {
  const mealsBox = qs("[data-meals]", scope);
  if (!mealsBox) return;
  mealsBox.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-meal][data-eaten]");
    if (btn) checkin(root, btn);
  });
}

function bindPlanActions(root) {
  const exportBtn = qs("#advice-export", root);
  if (exportBtn) exportBtn.addEventListener("click", () => exportPlan(view.state.plan));

  const regen = qs("#advice-regenerate", root);
  if (regen) {
    regen.addEventListener("click", () => {
      view.formOpen = true;
      syncForm(root, { collapse: false });
      qs("#advice-form", root)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  const setTarget = qs("#advice-set-target", root);
  if (setTarget) {
    setTarget.addEventListener("click", () => {
      if (typeof view.navigate === "function") view.navigate("settings");
      else window.location.hash = "#/settings";
    });
  }

  root.querySelectorAll("[data-ack]").forEach((btn) => {
    btn.addEventListener("click", () => ackNotice(root, btn));
  });

  bindMealCheckin(root);
}

/* ------------------------------ 表单 ------------------------------ */

function formHtml() {
  return `<aside class="card stack" id="advice-form" style="gap:14px">
    <h2 style="margin:0">${escapeHtml(t("advice.form.title"))}</h2>
    <div class="field">
      <label>${escapeHtml(t("advice.mode"))}</label>
      <div class="segment" id="mode-switch">
        <button type="button" class="segment-btn" data-mode="fast">${escapeHtml(t("advice.mode.fast"))}</button>
        <button type="button" class="segment-btn" data-mode="slow">${escapeHtml(t("advice.mode.slow"))}</button>
      </div>
      <span class="tiny muted" id="mode-hint"></span>
    </div>

    <div class="field" id="days-field">
      <label>${escapeHtml(t("advice.days"))} <span class="mono" id="days-value"></span></label>
      <input type="range" id="days-range" min="2" max="30" step="1" />
      <div class="row-wrap" id="days-presets">
        <button type="button" class="btn btn-sm btn-ghost" data-days="3">3</button>
        <button type="button" class="btn btn-sm btn-ghost" data-days="7">7</button>
        <button type="button" class="btn btn-sm btn-ghost" data-days="14">14</button>
        <button type="button" class="btn btn-sm btn-ghost" data-days="30">30</button>
      </div>
      <span class="tiny muted" id="days-locked"></span>
    </div>

    <div class="field">
      <label>${escapeHtml(t("settings.target"))}</label>
      <label class="checkbox">
        <input type="checkbox" id="use-target" />
        <span>${escapeHtml(t("advice.useTarget"))}</span>
      </label>
      <span class="tiny muted" id="target-hint"></span>
    </div>

    <div class="field">
      <label>${escapeHtml(t("records.meals"))}</label>
      <div class="stack" style="gap:8px">
        <label class="checkbox"><input type="checkbox" id="meal-breakfast" /><span>${escapeHtml(
          t("records.meal.breakfast")
        )}</span></label>
        <label class="checkbox"><input type="checkbox" id="meal-lunch" /><span>${escapeHtml(
          t("records.meal.lunch")
        )}</span></label>
        <label class="checkbox"><input type="checkbox" id="meal-dinner" /><span>${escapeHtml(
          t("records.meal.dinner")
        )}</span></label>
      </div>
    </div>

    <button class="btn btn-primary btn-block" id="generate">${escapeHtml(t("advice.generate"))}</button>
  </aside>`;
}

function syncForm(root, { collapse } = {}) {
  const form = view.form;
  const box = qs("#advice-form", root);
  if (!box) return;
  // 有当前计划时默认折叠成一行按钮；没有计划时永远展开
  const collapsed = collapse ?? (Boolean(view.state) && !view.formOpen);

  box.querySelectorAll("#mode-switch .segment-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === form.mode);
  });

  // 天数：快速调整固定只算今天 1 天（后端直接忽略 days），所以不藏控件而是说明清楚，
  // 否则用户只看到"选不了天数"却不知道原因
  const daysField = qs("#days-field", box);
  const locked = form.mode !== "slow";
  if (daysField) {
    qs("#days-range", daysField)?.classList.toggle("hidden", locked);
    qs("#days-presets", daysField)?.classList.toggle("hidden", locked);
    const lockedNote = qs("#days-locked", daysField);
    if (lockedNote) {
      lockedNote.classList.toggle("hidden", !locked);
      lockedNote.textContent = locked ? t("advice.daysLocked") : "";
    }
  }
  const range = qs("#days-range", box);
  if (range) range.value = String(form.days);
  const daysValue = qs("#days-value", box);
  if (daysValue) {
    daysValue.textContent = locked
      ? t("advice.daysValue", { n: 1 })
      : t("advice.daysValue", { n: form.days });
  }

  const useTarget = qs("#use-target", box);
  if (useTarget) {
    useTarget.checked = form.use_target;
    // 后端约定：没有自定义目标时该请求会被忽略，直接置灰避免误解
    useTarget.disabled = view.options?.has_custom_target === false;
  }
  MEALS.forEach((key) => {
    const input = qs(`#meal-${key}`, box);
    if (input) input.checked = Boolean(form[`meal_${key}`]);
  });

  const modeHint = qs("#mode-hint", box);
  if (modeHint) {
    modeHint.textContent = form.mode === "fast" ? t("advice.mode.fastHint") : t("advice.mode.slowHint");
  }
  const targetHint = qs("#target-hint", box);
  if (targetHint) {
    targetHint.textContent = view.options?.has_custom_target
      ? t("advice.customTargetSet", {
          sleep: view.options.target_sleep_time || "--:--",
          wake: view.options.target_wake_time || "--:--",
        })
      : t("advice.warn.no_custom_target");
  }

  // 折叠只切容器这一个类：CSS 里 form-collapsed 让整张卡不显示，
  // 不必再逐个切子节点的 .hidden（那会把「快速模式藏天数」等正常状态一起搅乱）
  box.classList.toggle("form-collapsed", collapsed);
  // 表单不显示后它就不再是网格项，方案区会掉进左边那条 320px 的窄列 —— 布局也要跟着切单列
  const layout = box.closest(".advice-layout");
  if (layout) layout.classList.toggle("form-folded", collapsed);
}

function formPayload() {
  return {
    mode: view.form.mode,
    days: view.form.mode === "fast" ? 1 : view.form.days,
    use_target: view.form.use_target,
    meal_breakfast: view.form.meal_breakfast,
    meal_lunch: view.form.meal_lunch,
    meal_dinner: view.form.meal_dinner,
  };
}

function bindForm(root) {
  const box = qs("#advice-form", root);
  if (!box) return;

  box.querySelectorAll("#mode-switch .segment-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      view.form.mode = btn.dataset.mode;
      syncForm(root);
    });
  });

  qs("#days-range", box)?.addEventListener("input", (event) => {
    view.form.days = Number(event.target.value);
    const value = qs("#days-value", box);
    if (value) value.textContent = t("advice.daysValue", { n: view.form.days });
  });

  box.querySelectorAll("[data-days]").forEach((btn) => {
    btn.addEventListener("click", () => {
      view.form.days = Number(btn.dataset.days);
      const range = qs("#days-range", box);
      if (range) range.value = String(view.form.days);
      const value = qs("#days-value", box);
      if (value) value.textContent = t("advice.daysValue", { n: view.form.days });
    });
  });

  qs("#use-target", box)?.addEventListener("change", (event) => {
    view.form.use_target = event.target.checked;
  });
  MEALS.forEach((key) => {
    qs(`#meal-${key}`, box)?.addEventListener("change", (event) => {
      view.form[`meal_${key}`] = event.target.checked;
    });
  });

  qs("#generate", box)?.addEventListener("click", () => generate(root));
}

/* ------------------------------ 状态归一化 ------------------------------ */

/**
 * 生成接口直接返回计划负载本身，plan 接口则把它放在 plan 字段里。
 * 统一成"plan + 派生字段"的形状，后面所有渲染都只看这一种结构。
 */
function applyState(body, { generated = false } = {}) {
  const payload = body?.plan && body.plan.plan ? body.plan : body;
  const prev = view.state || {};
  view.state = {
    plan: payload || null,
    today: body?.today || prev.today || null,
    timeline: body?.timeline || payload?.timeline || prev.timeline || null,
    deviation: body?.deviation ?? prev.deviation ?? null,
    adherence: body?.adherence ?? prev.adherence ?? null,
    meal_plan: body?.meal_plan || payload?.meal_plan || prev.meal_plan || null,
    plan_meta: body?.plan_meta || payload?.plan_meta || prev.plan_meta || null,
    acked_notices: body?.acked_notices || prev.acked_notices || [],
    // 刚生成完不算"自动更新"，避免多出一行没有意义的说明
    auto_recomputed: generated ? false : Boolean(body?.auto_recomputed),
  };
  return view.state;
}

/* ------------------------------ 动作 ------------------------------ */

async function generate(root) {
  if (view.pending) return;
  const btn = qs("#generate", root);
  view.pending = true;
  if (btn) {
    btn.disabled = true;
    btn.textContent = t("advice.generating");
  }
  try {
    const data = await adviceApi.generate(formPayload());
    applyState(data, { generated: true });
    view.formOpen = false;
    renderPlanView(root);
    syncForm(root, { collapse: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("error.server"));
  } finally {
    view.pending = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = t("advice.generate");
    }
  }
}

/** 打卡响应里带着重算过的今天：就地合并，避免为一次打卡重拉整页 */
function mergeCheckinState(body) {
  const state = view.state;
  if (!state || !body) return;
  if (body.meal_plan) state.meal_plan = body.meal_plan;
  if (!body.today) return;
  state.today = body.today;
  if (body.timeline) state.timeline = body.timeline;
  // 方案列表第一行就是今天：按日期把同一天替换掉，两处时间才不会打架
  const days = state.plan?.plan || [];
  const index = days.findIndex((day) => day.date === body.today.date);
  if (index >= 0) days[index] = { ...days[index], ...body.today };
}

/* ---------------------- 即时反馈（不改布局、不丢滚动位置） ---------------------- */

const SHIFTED_MEALS = ["lunch", "dinner"];

function mealCardOf(root, key) {
  return qs(`.today-meal[data-meal="${key}"]`, root);
}

/** 记下点击前午餐/晚餐显示的时间，用来做"旧 → 新" */
function mealTimeSnapshot(root) {
  const out = {};
  SHIFTED_MEALS.forEach((key) => {
    const card = mealCardOf(root, key);
    if (!card) return;
    out[key] = card.querySelector("[data-best]")?.textContent.trim() || "";
  });
  return out;
}

/** 点击瞬间先把数字换成后端算好的预测值（网络往返期间用户已经看到结果） */
function applyPreviewTimes(root, preview) {
  if (!preview) return;
  SHIFTED_MEALS.forEach((key) => {
    const card = mealCardOf(root, key);
    const target = preview[key];
    if (!card || !target?.best_time) return;
    const best = card.querySelector("[data-best]");
    if (best) best.textContent = target.best_time;
    const win = card.querySelector("[data-window]");
    if (win) {
      win.textContent = `${target.window_start || "--:--"}–${target.window_end || "--:--"}`;
    }
    card.classList.add("tm-flash");
  });
}

/** 时间真的变了才高亮 2.6 秒，并补一行"旧 → 新"，让变化一眼可见 */
function flashMovedTimes(root, before) {
  SHIFTED_MEALS.forEach((key) => {
    const card = mealCardOf(root, key);
    const now = card?.querySelector("[data-best]")?.textContent.trim() || "";
    const was = before?.[key] || "";
    if (!card || !now || !was || now === was) return;
    card.classList.add("tm-flash");
    const line = document.createElement("p");
    line.className = "tiny meal-moved";
    line.textContent = tp("advice.meal.moved", { from: was, to: now });
    card.appendChild(line);
    window.setTimeout(() => {
      card.classList.remove("tm-flash");
      line.remove();
    }, 2600);
  });
}

/** 重画后把用户刚点的那张卡片拉回视野（block:"nearest" 不跳动、不强制滚动） */
function anchorMealCard(root, meal) {
  mealCardOf(root, meal)?.scrollIntoView({ block: "nearest" });
}

/** 只重画今日卡片与方案列表里今天那一行：整块重画会丢焦点，也把用户拉到页面顶部 */
function renderTodayOnly(root, day) {
  const current = qs("[data-today]", root);
  if (!current || !view.state?.today) return false;
  const holder = document.createElement("div");
  holder.innerHTML = todayCardHtml(view.state).trim();
  const next = holder.firstElementChild;
  if (!next) return false;
  current.replaceWith(next);
  const slot = qs("[data-timeline]", next);
  if (slot && view.state.timeline) {
    slot.innerHTML = "";
    const node = renderTimeline(view.state.timeline, { now: true });
    if (node) slot.appendChild(node);
  }
  bindMealCheckin(root, next);
  if (day?.date) {
    const row = root.querySelector(`.day-card[data-date="${day.date}"]`);
    if (row) {
      const wrap = document.createElement("div");
      wrap.innerHTML = dayRowHtml(day).trim();
      const fresh = wrap.firstElementChild;
      if (fresh) {
        row.replaceWith(fresh);
        const mini = qs("[data-day-timeline]", fresh);
        if (mini && day.timeline) {
          mini.innerHTML = "";
          const node = renderTimeline(day.timeline, { mini: true });
          if (node) mini.appendChild(node);
        }
      }
    }
  }
  return true;
}

/** 三餐一键确认：用响应里的今天 + 三餐统计就地重画，不重新拉整页 */
async function checkin(root, btn) {
  if (view.pending) return;
  const meal = btn.dataset.meal;
  const eaten = btn.dataset.eaten === "1";
  const refused = btn.dataset.refused === "1";
  const date = view.state?.today?.date || view.options?.today || null;
  const before = mealTimeSnapshot(root);
  const preview = view.state?.today?.refusal_preview;
  view.pending = true;
  btn.disabled = true;
  // 点击的那一瞬间先把午餐/晚餐改成预测值：等网络往返时用户已经看到结果，
  // 不用等刷新、也不用往下滚去找那两张卡片。响应回来后再用真实值校准。
  if (refused) applyPreviewTimes(root, preview);
  try {
    const body = await adviceApi.checkin({ meal, eaten, date, refused });
    mergeCheckinState(body);
    renderTodayOnly(root, body.today);
    flashMovedTimes(root, before);
    anchorMealCard(root, meal);
    toastOk(t("advice.meal.checkin.saved"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("advice.checkinFailed"));
    // 失败必须回滚乐观更新的数字：整块重画一次拿服务器状态
    renderPlanView(root);
    anchorMealCard(root, meal);
  } finally {
    view.pending = false;
  }
}

/** 提示条"不再提示"：只更新本地列表，不重新请求整个计划 */
async function ackNotice(root, btn) {
  const code = btn.dataset.ack;
  if (!code || view.pending) return;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = t("common.loading");
  try {
    const body = await adviceApi.ackNotice(code);
    const acked = new Set(body?.acked_notices || [code]);
    (view.state?.plan?.notices || []).forEach((notice) => {
      if (acked.has(notice.code)) notice.acked = true;
    });
    view.state.acked_notices = Array.from(acked);
    renderPlanView(root);
    toastOk(t("advice.notice.ackDone"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    btn.disabled = false;
    btn.textContent = original;
    toastErr(err instanceof Error ? err.message : t("advice.ackFailed"));
  }
}

/** 偏离弹窗：三个策略按钮 + 关闭（关闭不发请求） */
async function askDeviation(root) {
  if (view.deviating) return;
  const deviation = view.state?.deviation;
  if (!deviation || deviation.level !== "ask") return;
  view.deviating = true;

  const minutes = deviation.minutes;
  const directionText =
    deviation.direction === "later" ? t("advice.steps.direction.delay") : t("advice.steps.direction.advance");
  const postpone = (deviation.options || []).find((item) => item.strategy === "postpone");
  const extra = postpone?.extra_days ?? 0;
  const buttons = [
    {
      strategy: "postpone",
      label: tp("advice.deviation.postpone", { extra }),
      cls: postpone?.recommended === false ? "btn" : "btn btn-primary",
    },
    { strategy: "recompute", label: t("advice.deviation.recompute"), cls: "btn" },
    { strategy: "keep", label: t("advice.deviation.keep"), cls: "btn btn-ghost" },
  ];

  const bodyHtml = `
    <p>${escapeHtml(tp("advice.deviation.ask.body", { minutes, direction: directionText }))}</p>
    <div class="deviation-options">
      ${buttons
        .map(
          (item) =>
            `<button class="${item.cls}" data-strategy="${item.strategy}">${escapeHtml(item.label)}</button>`
        )
        .join("")}
    </div>`;

  let chosen = null;
  await openModal({
    title: t("advice.deviation.ask.title"),
    bodyHtml,
    okText: t("advice.deviation.dismiss"),
    cancelText: t("common.close"),
    onMount: (host) => {
      // 三个策略按钮自己结束弹窗；右上角 × 与"关闭"都只关闭，不发请求
      host.querySelectorAll("[data-strategy]").forEach((btn) => {
        btn.addEventListener("click", () => {
          chosen = btn.dataset.strategy;
          host.remove();
        });
      });
    },
  });

  if (chosen) await applyStrategy(root, chosen);
  view.deviating = false;
}

async function applyStrategy(root, strategy) {
  try {
    const body = await adviceApi.replan(strategy);
    applyState(body);
    renderPlanView(root);
    if (strategy !== "keep") toastOk(t("common.ok"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("advice.replanFailed"));
  }
}

/* ------------------------------ 复制方案 ------------------------------ */

function exportPlan(data) {
  if (!data) return;
  const lines = [
    `${t("advice.title")} · ${data.mode === "fast" ? t("advice.mode.fast") : t("advice.mode.slow")}`,
    `${t("advice.basis.avgSleep")}: ${data.basis?.sleep_time || "--:--"} - ${data.basis?.wake_time || "--:--"}`,
    `${t("advice.target")}: ${data.target?.sleep_time || "--:--"} - ${data.target?.wake_time || "--:--"}`,
    "",
  ];
  (data.plan || []).forEach((day) => {
    lines.push(
      `${t("advice.day", { n: day.index })} ${day.date}  ${day.sleep_time} → ${day.wake_time} (${fmtDuration(
        day.duration_minutes
      )})`
    );
    enabledMeals(day.meals).forEach((key) => {
      const meal = day.meals[key];
      lines.push(
        `    ${mealName(key)}: ${meal.best_time || meal.time}（${meal.window_start}–${meal.window_end}）`
      );
    });
  });
  copyText(lines.join("\n"));
}

function copyText(text) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toastOk(t("advice.copied")),
      () => fallbackCopy(text)
    );
    return;
  }
  fallbackCopy(text);
}

function fallbackCopy(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    document.execCommand("copy");
    toastOk(t("advice.copied"));
  } catch (err) {
    toastErr(t("error.server"));
  }
  document.body.removeChild(area);
}

/* ------------------------------ 页面入口 ------------------------------ */

function renderShell(root) {
  root.innerHTML = `
    <div class="page-head">
      <div class="titles">
        <h1>${escapeHtml(t("advice.title"))}</h1>
        <p>${escapeHtml(t("advice.subtitle"))}</p>
      </div>
    </div>
    <div class="advice-layout">
      ${formHtml()}
      <section class="stack" id="advice-result" style="gap:16px"></section>
    </div>`;
}

function renderStatus(root, message, kind) {
  const result = qs("#advice-result", root);
  if (!result) return;
  result.innerHTML = `<div class="card ${kind === "error" ? "inline-error show" : "loading"}">${escapeHtml(
    message
  )}</div>`;
}

/** 语言切换后按新语言重画（切语言时 main.js 也会重跑整页路由） */
function bindLangChange(root) {
  if (view.langBound) return;
  view.langBound = true;
  document.addEventListener("ssp:langchange", () => {
    if (!document.body.contains(root)) return;
    syncForm(root, { collapse: Boolean(view.state) && !view.formOpen });
    renderPlanView(root);
  });
}

export async function renderAdvicePage(root, ctx = {}) {
  view.navigate = ctx.navigate || null;
  view.state = null;
  view.formOpen = false;
  renderShell(root);
  renderStatus(root, t("common.loading"));
  bindForm(root);
  bindLangChange(root);

  const [options, state] = await Promise.all([
    adviceApi.options().catch((err) => {
      if (err instanceof ApiError && err.status === 401) throw err;
      return null;
    }),
    adviceApi.plan().catch((err) => {
      if (err instanceof ApiError && err.status === 401) throw err;
      toastErr(err instanceof Error ? err.message : t("advice.loadError"));
      return null;
    }),
  ]);
  if (ctx.isStale?.()) return;

  view.options = options || { has_custom_target: true, meal_flags: {} };
  // has_plan=false 时给的是空壳（meal_plan/engine），直接落到生成表单
  if (state?.has_plan) applyState(state);
  else view.state = null;

  view.form.use_target = options?.has_custom_target !== false;
  view.form.meal_breakfast = options?.meal_flags?.breakfast !== false;
  view.form.meal_lunch = options?.meal_flags?.lunch !== false;
  view.form.meal_dinner = options?.meal_flags?.dinner !== false;
  if (options?.default_days) view.form.days = options.default_days;

  if (!view.state) {
    // 没有快照：表单展开，结果区给空态提示
    syncForm(root, { collapse: false });
    renderPlanView(root);
    return;
  }

  syncForm(root, { collapse: !view.formOpen });
  renderPlanView(root);
  if (view.state.deviation?.level === "ask") askDeviation(root);
}
