/* ==========================================================================
   24 小时时间图：把后端给出的 timeline 数据渲染成"一天"的时间条
   - 轴窗固定为 timeline.axis_start → axis_end（span_minutes），每个条的位置
     就是 start / span 的百分比，不做任何时间换算
   - 跨零点的夜间睡眠条后端已经拆成 morning（轴头）与 night（轴尾）两段，
     这里只负责各画各的，绝不重新推导环绕；两截分别标成"睡眠前段/后段"，
     并在轴上的睡眠徽标里补一句"跨零点，轴两端相接"，免得被当成睡了两觉
   - 宽屏横向、窄屏纵向：两套标记都渲染，由 CSS 媒体查询决定显示哪一套
     （display:none 的节点不占布局，也不需要 JS 监听 resize）
   - 窄屏不再是"把竖轨按时间比例压缩"：20 小时轴窗里 30 分钟的三餐只有几个像素高，
     文字还会被 overflow:hidden 裁光（用户报的"手机版很多不显示"）。改成一行一条：
     行高由 CSS 保证，比例只体现在中间那根小条的长度上，名称与时间区间都排成横排
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

// 跨零点的睡眠会被轴窗切成两截：贴入睡那截在轴尾（segment: "night"），贴起床那截在轴头
// （segment: "morning"）。两截各自标成"前段/后段"，轴上再用一句"两端相接"说明它们是同一觉，
// 否则用户会以为是"睡了两觉"。data-tl-part 给验收脚本一个稳定的钩子（不依赖中文文案）。
const SLEEP_PART_LABEL = {
  night: "advice.timeline.sleepFirst",
  morning: "advice.timeline.sleepLast",
};
const SLEEP_PART_ATTR = { night: "first", morning: "last" };

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
  if (item.kind !== "sleep") return t(`records.meal.${item.meal}`);
  const partKey = item.partial ? SLEEP_PART_LABEL[item.segment] : "";
  return partKey ? t(partKey) : t("advice.timeline.sleep");
}

function barHint(item) {
  const label = barLabel(item);
  const range = item.start_time && item.end_time ? `${item.start_time}–${item.end_time}` : "";
  const base = range ? `${label} ${range}` : label;
  // 只有被切开的睡眠条需要补一句"它和轴另一端是同一觉"
  return item.kind === "sleep" && item.partial ? `${base} · ${t("advice.timeline.sleepSplit")}` : base;
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
      // 名称容器始终渲染，哪怕是空字符串：窄屏的行布局靠它撑出左侧文字列，
      // 少一个容器那一行的小条就会错位（睡眠条在迷你图里没有名字）
      const label =
        `<span class="tl-item-label">` +
        (name
          ? `${icon ? `<span class="tl-icon">${icon}</span>` : ""}<span class="tl-name">${escapeHtml(name)}</span>`
          : "") +
        `</span>`;
      // 窄屏（.tl-v）一行一条：名称列 + 按时间比例的小条 + 时间区间。
      // 宽屏用不到这两个节点，由 CSS 隐藏（见 app.css 的 .tl-h/.tl-v 区块）。
      // 迷你图里的时间在上方餐次块已经写了，行里不再重复。
      const range = item.start_time && item.end_time ? `${item.start_time}–${item.end_time}` : "";
      const row =
        `<span class="tl-rowtrack"><span class="tl-bar"></span></span>` +
        (range && !mini ? `<span class="tl-item-time">${escapeHtml(range)}</span>` : "");
      // 被切开的睡眠条标出自己属于前段还是后段（迷你图里睡眠条不写名字，两者互不影响）
      const part = item.kind === "sleep" ? SLEEP_PART_ATTR[item.segment] || "" : "";
      const partAttr = part ? ` data-tl-part="${part}"` : "";
      return (
        `<span class="tl-item ${kind}"${partAttr} style="--pos:${pct((start / span) * 100)};--len:${pct(
          ((end - start) / span) * 100
        )}" title="${escapeHtml(barHint(item))}">${label}${row}</span>`
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

  // "现在"标记：当前时间落在轴窗内才画。轴窗会跨零点（例如 06:04 起、02:04 止），这时"现在"
  // 若是凌晨，钟点数值反而比 axis_start 小，必须补一天再比较，否则凌晨那几个小时没有标记
  let nowMark = "";
  if (opts.now) {
    const clock = new Date();
    const wallMinutes = clock.getHours() * 60 + clock.getMinutes();
    const nowMinutes = wallMinutes < axisStart ? wallMinutes + 24 * 60 : wallMinutes;
    if (nowMinutes <= axisStart + span) {
      const hint = `${t("advice.timeline.now")} ${formatClock(wallMinutes)}`;
      nowMark =
        `<span class="tl-now" style="--pos:${pct(((nowMinutes - axisStart) / span) * 100)}" ` +
        `title="${escapeHtml(hint)}"><i class="tl-now-line"></i>` +
        `<i class="tl-now-text">${escapeHtml(t("advice.timeline.now"))}</i></span>`;
    }
  }

  // 睡眠被轴窗切成两截时，在轴上的睡眠徽标里说明"两端相接"：
  // 用户看到的两条睡眠其实是一觉（入睡那截排在轴尾，起床那截排在轴头）。
  const sleepHalves = items.filter((item) => item.kind === "sleep" && item.partial);
  const splitSleep = sleepHalves.length > 1;
  const joinAttr = splitSleep ? ' data-tl-join="1"' : "";
  const joinNote = splitSleep ? ` · ${t("advice.timeline.sleepSplit")}` : "";
  const sleepAxisText = `${t("advice.timeline.sleep")} ${timeline.sleep_label || ""}${joinNote}`;

  const axisHtml = `
    <div class="tl-axis">
      <span class="tl-axis-end tl-axis-first">${escapeHtml(timeline.axis_start || "")}</span>
      ${ticks.join("")}
      <span class="tl-axis-sleep"${joinAttr}>${escapeHtml(sleepAxisText)}</span>
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
      <span class="tl-axis-sleep"${joinAttr}>${escapeHtml(
        `${timeline.sleep_label || ""}${joinNote}`
      )}</span>
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
