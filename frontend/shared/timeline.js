/* ==========================================================================
   24 小时时间图：把后端给出的 timeline 数据渲染成"一天"的时间条
   - 轴窗固定为 timeline.axis_start → axis_end（span_minutes），每个条的位置
     就是 start / span 的百分比，不做任何时间换算
   - 跨零点的夜间睡眠条后端已经拆成 morning（轴头）与 night（轴尾）两段，
     这里只负责各画各的，绝不重新推导环绕
   - 宽屏横向、窄屏纵向：两套标记都渲染，由 CSS 媒体查询决定显示哪一套
     （display:none 的节点不占布局，也不需要 JS 监听 resize）
   - 所有文案走 i18n，条上的名称与轴上的时间都是可见文字，不依赖颜色区分
   ========================================================================== */

import { t } from "/shared/i18n.js";
import { escapeHtml } from "/shared/ui.js";

const MEAL_ICONS = { breakfast: "🥣", lunch: "🍱", dinner: "🍲" };
const MEAL_SHORT = {
  breakfast: "advice.meal.short.breakfast",
  lunch: "advice.meal.short.lunch",
  dinner: "advice.meal.short.dinner",
};
const HOUR = 1440;

/** "HH:MM" -> 当天分钟数；非法值返回 null */
export function parseClock(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value ?? "").trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return hour * 60 + minute;
}

/** 分钟数 -> "HH:MM"（自动绕回当天） */
export function formatClock(minutes) {
  const wrapped = ((Math.round(minutes) % HOUR) + HOUR) % HOUR;
  return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`;
}

/** 轴上的时间刻度间隔：横向最多 8 个，小图少一些免得挤 */
function tickStep(span) {
  for (const step of [180, 120, 240, 90, 360, 480]) {
    if (span / step <= 8) return step;
  }
  return 720;
}

const pct = (value) => `${Math.max(0, Math.min(100, Number(value) || 0)).toFixed(3)}%`;

function barLabel(item) {
  return item.kind === "sleep" ? t("advice.timeline.sleep") : t(`records.meal.${item.meal}`);
}

function barHint(item) {
  const label = barLabel(item);
  const range = item.start_time && item.end_time ? `${item.start_time}–${item.end_time}` : "";
  return range ? `${label} ${range}` : label;
}

/**
 * 渲染时间图。
 * @param {object} timeline 后端 timeline 对象
 * @param {object} [opts]
 * @param {boolean} [opts.mini]  小号变体（方案列表里的每日行）
 * @param {boolean} [opts.now]   画"现在"标记（只在今天这张卡上传 true）
 * @param {string}  [opts.title] aria-label，默认用图例文案
 * @returns {HTMLElement|null}
 */
export function renderTimeline(timeline, opts = {}) {
  const span = Number(timeline?.span_minutes) || 0;
  const items = Array.isArray(timeline?.items) ? timeline.items : [];
  if (!span || !items.length) return null;

  const mini = Boolean(opts.mini);
  const wrap = document.createElement("div");
  wrap.className = `timeline${mini ? " timeline-mini" : ""}`;
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label", opts.title || t("advice.timeline.legend"));

  const bars = items
    .map((item) => {
      const start = Number(item.start) || 0;
      const end = Number(item.end) || 0;
      const kind = item.kind === "meal" ? `meal meal-${item.meal}` : `sleep sleep-${item.segment || "night"}`;
      const icon = item.kind === "meal" ? MEAL_ICONS[item.meal] || "" : "🌙";
      // 小图里只给三餐写名字：睡眠条在轴上的徽标已经写了时段，
      // 而且睡眠条很窄，写名字只会被裁掉（task 里报的"文字重叠/显示 records.meal.…"就是这个）
      let name = "";
      if (mini) {
        if (item.kind === "meal") name = t(MEAL_SHORT[item.meal] || `records.meal.${item.meal}`);
      } else {
        name = barLabel(item);
      }
      const label = name
        ? `<span class="tl-item-label">${icon ? `<span class="tl-icon">${icon}</span>` : ""}<span class="tl-name">${escapeHtml(
            name
          )}</span></span>`
        : "";
      return (
        `<span class="tl-item ${kind}" style="--pos:${pct((start / span) * 100)};--len:${pct(
          ((end - start) / span) * 100
        )}" title="${escapeHtml(barHint(item))}">${label}</span>`
      );
    })
    .join("");

  // 轴窗内的整点刻度：文字位置与条的位置用同一套百分比，保证对齐
  const axisStart = parseClock(timeline.axis_start) ?? 0;
  const step = tickStep(span);
  const ticks = [];
  const gridLines = [];
  for (let offset = step; offset < span; offset += step) {
    const position = (offset / span) * 100;
    if (position < 4 || position > 96) continue; // 太靠边会和首尾标签重叠
    ticks.push(
      `<span class="tl-tick" style="--pos:${pct(position)}">${escapeHtml(formatClock(axisStart + offset))}</span>`
    );
    gridLines.push(`<i class="tl-grid-line" style="--pos:${pct(position)}"></i>`);
  }

  // "现在"标记：当前时间落在轴窗内才画（跨零点的轴窗用 >= axis_start 判断即可）
  let nowMark = "";
  if (opts.now) {
    const clock = new Date();
    const nowMinutes = clock.getHours() * 60 + clock.getMinutes();
    if (nowMinutes >= axisStart && nowMinutes <= axisStart + span) {
      const hint = `${t("advice.timeline.now")} ${formatClock(nowMinutes)}`;
      nowMark =
        `<span class="tl-now" style="--pos:${pct(((nowMinutes - axisStart) / span) * 100)}" ` +
        `title="${escapeHtml(hint)}"><i class="tl-now-line"></i>` +
        `<i class="tl-now-text">${escapeHtml(t("advice.timeline.now"))}</i></span>`;
    }
  }

  const axisHtml = `
    <div class="tl-axis">
      <span class="tl-axis-end tl-axis-first">${escapeHtml(timeline.axis_start || "")}</span>
      ${ticks.join("")}
      <span class="tl-axis-sleep">${escapeHtml(t("advice.timeline.sleep"))} ${escapeHtml(
        timeline.sleep_label || ""
      )}</span>
      <span class="tl-axis-end tl-axis-last">${escapeHtml(timeline.axis_end || "")}</span>
    </div>`;

  const trackHtml = `
    <div class="tl-track">
      ${gridLines.join("")}
      ${nowMark}
      ${bars}
    </div>`;

  const mealLegend = ["breakfast", "lunch", "dinner"]
    .filter((key) => items.some((item) => item.kind === "meal" && item.meal === key))
    .map(
      (key) =>
        `<span class="tl-legend-item">${MEAL_ICONS[key]} ${escapeHtml(t(`records.meal.${key}`))}</span>`
    )
    .join("");

  // 纵向变体不需要轴上的时间刻度：轴的文字在上（第一个条之上）、在区间里、在下，
  // 纵向的睡眠标签用 title 里的时间跨度代替，避免和刻度重叠
  const axisVHtml = `
    <div class="tl-axis tl-axis-v">
      <span class="tl-axis-end">${escapeHtml(timeline.axis_start || "")}</span>
      <span class="tl-axis-sleep">${escapeHtml(timeline.sleep_label || "")}</span>
      <span class="tl-axis-end">${escapeHtml(timeline.axis_end || "")}</span>
    </div>`;

  wrap.innerHTML = `
    <div class="tl-h">${trackHtml}${axisHtml}</div>
    <div class="tl-v">${trackHtml}${axisVHtml}</div>
    ${
      mini
        ? ""
        : `<div class="tl-legend"><span class="tl-legend-item">🌙 ${escapeHtml(
            t("advice.timeline.sleep")
          )}</span>${mealLegend}</div>`
    }`;

  return wrap;
}

export { MEAL_ICONS as TIMELINE_MEAL_ICONS };
