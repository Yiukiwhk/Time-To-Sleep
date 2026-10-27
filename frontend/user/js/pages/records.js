/* ==========================================================================
   睡眠记录页：列表 + 录入 / 修改 / 删除（时间全部使用闹钟式选择器）
   ========================================================================== */

import { recordApi, ApiError } from "/shared/api.js";
import { t } from "/shared/i18n.js";
import { iconSvg } from "/shared/icons.js";

/** 带参数的词条（i18n 只导出 t，建议页也是这么包一层的） */
const tp = (key, params) => t(key, params || {});
import { createTimePicker } from "/shared/timepicker.js";
import { clearDateMarkCache, enhanceDateInputs } from "/user/js/date-picker.js";
import {
  confirmDialog,
  debounce,
  escapeHtml,
  fmtDuration,
  openModal,
  qs,
  renderPager,
  toastErr,
  toastOk,
} from "/shared/ui.js";

const view = {
  page: 1,
  pageSize: 10,
  startDate: "",
  endDate: "",
  total: 0,
  pages: 1,
  items: [],
  selected: new Set(),
  meta: null,
  cooldownUntil: 0,
  overview: [],
};

/* ------------------------------ 渲染辅助 ------------------------------ */

function todayIso() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** 日期字符串加减天数（本地时区，和 todayIso() 口径一致） */
function shiftIso(iso, days) {
  const [year, month, day] = String(iso).split("-").map(Number);
  const target = new Date(year, month - 1, day);
  target.setDate(target.getDate() + days);
  const pad = (n) => String(n).padStart(2, "0");
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}`;
}

/** 默认只看最近 7 天（含今天）；导出给自检脚本复算，避免两处写死 */
export function defaultRange(days = 7) {
  const end = new Date();
  const start = new Date(end.getTime());
  start.setDate(start.getDate() - (days - 1));
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return { start: iso(start), end: iso(end) };
}

/** 把筛选框与 view 一起设成给定区间（初始化与"重置"共用，保证两次行为一致） */
function setRange(root, range) {
  view.startDate = range.start;
  view.endDate = range.end;
  if (root) {
    const startInput = qs("#filter-start", root);
    const endInput = qs("#filter-end", root);
    if (startInput) startInput.value = range.start;
    if (endInput) endInput.value = range.end;
  }
}

function mealCell(record) {
  const flags = [
    ["早", record.meal_breakfast],
    ["午", record.meal_lunch],
    ["晚", record.meal_dinner],
  ];
  return `<span class="meal-dots">${flags
    .map(
      ([label, on]) =>
        `<span class="meal-dot ${on ? "on" : ""}" title="${label}">${label}</span>`
    )
    .join("")}</span>`;
}

function cooldownRemaining() {
  return Math.max(0, Math.ceil((view.cooldownUntil - Date.now()) / 1000));
}

function startCooldown() {
  const seconds = Number(view.meta?.write_cooldown_seconds || 0);
  if (seconds > 0) view.cooldownUntil = Date.now() + seconds * 1000;
}


/* ------------------------------ 记录概览（可视化） ------------------------------ */

/** 概览最多读多少条：够画一整屏，又不至于拖慢页面 */
const OVERVIEW_LIMIT = 60;
/** 时长柱上的参考线（小时） */
const OVERVIEW_REF_HOURS = 8;

/** "HH:MM" → 当天分钟数 */
function minutesOf(value) {
  const [hour, minute] = String(value || "0:0").split(":").map(Number);
  return (Number(hour) || 0) * 60 + (Number(minute) || 0);
}

/** 日期（YYYY-MM-DD）→ 本地当天 00:00 的毫秒数 */
function dayStartMs(iso) {
  const [year, month, day] = String(iso || "").split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day).getTime();
}

/** 一条记录的真实起止时刻：wake_date 优先，缺失时按"起床不晚于入睡即次日"兜底 */
function spanOf(record) {
  const sleepDayMs = dayStartMs(record.record_date);
  if (sleepDayMs === null) return null;
  const sleepTs = sleepDayMs + minutesOf(record.sleep_time) * 60000;
  const wakeDayMs = dayStartMs(record.wake_date || record.record_date) ?? sleepDayMs;
  let wakeTs = wakeDayMs + minutesOf(record.wake_time) * 60000;
  if (wakeTs < sleepTs) wakeTs += 86400000;
  return { sleepTs, wakeTs, minutes: Math.round((wakeTs - sleepTs) / 60000) };
}

/** 时刻落在"18:00 起的夜晚"里的第几个小时（0..24） */
function nightHourOf(ts) {
  const date = new Date(ts);
  const hours = date.getHours() + date.getMinutes() / 60;
  return (hours - 18 + 24) % 24;
}

/** 环形均值（分钟）：睡眠时刻会跨午夜，直接平均会算错 */
function circularMeanMinutes(values) {
  if (!values.length) return null;
  let x = 0;
  let y = 0;
  values.forEach((value) => {
    const angle = (value / 1440) * Math.PI * 2;
    x += Math.cos(angle);
    y += Math.sin(angle);
  });
  if (!x && !y) return null;
  const angle = Math.atan2(y, x);
  return Math.round(((angle / (Math.PI * 2)) * 1440 + 1440) % 1440);
}

function hhmmOf(minutes) {
  const pad = (n) => String(n).padStart(2, "0");
  const value = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${pad(Math.floor(value / 60))}:${pad(value % 60)}`;
}

/** 一条睡眠落在哪个夜晚：18:00 之前算前一晚（轴是 18:00 → 次日 18:00） */
function nightKeyOf(ts) {
  const d = new Date(ts);
  if (d.getHours() < 18) d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
}

/**
 * 按夜晚分组：同一夜的几条记录（凌晨一觉 + 早上回笼觉）画在同一行，
 * 跨 18:00 的按轴切开、分别落到相邻两行（与今日「一天的样子」同口径）。
 */
function nightsOf(records) {
  const map = new Map();
  [...records].forEach((record) => {
    const span = spanOf(record);
    if (!span) return;
    let cursor = span.sleepTs;
    let guard = 0;
    while (cursor < span.wakeTs && guard < 4) {
      guard += 1;
      const key = nightKeyOf(cursor);
      const windowStart = dayStartMs(key) + 18 * 3600000;
      const windowEnd = windowStart + 86400000;
      const segEnd = Math.min(span.wakeTs, windowEnd);
      if (segEnd <= cursor) break;
      if (!map.has(key)) {
        map.set(key, {
          key,
          startNight: true,
          segments: [],
          records: [],
          duration: 0,
          sleepMinute: null,
          wakeMinute: null,
        });
      }
      const night = map.get(key);
      if (cursor === span.sleepTs) {
        night.startNight = true;
      }
      night.segments.push({
        record,
        left: (cursor - windowStart) / 3600000,
        width: (segEnd - cursor) / 3600000,
      });
      cursor = segEnd;
    }
    const startKey = nightKeyOf(span.sleepTs);
    const night = map.get(startKey);
    if (!night) return;
    if (!night.records.some((row) => row.id === record.id)) night.records.push(record);
    night.duration += span.minutes;
    const sleepMinute = minutesOf(record.sleep_time);
    const wakeMinute = minutesOf(record.wake_time);
    if (night.sleepMinute === null || clockDelta(sleepMinute, night.sleepMinute) > 0) {
      night.sleepMinute = sleepMinute;
    }
    if (night.wakeMinute === null || clockDelta(wakeMinute, night.wakeMinute) < 0) {
      night.wakeMinute = wakeMinute;
    }
  });
  return [...map.values()].sort((a, b) => a.key.localeCompare(b.key));
}

/** 钟点差（分钟，取最短方向）：+60 表示比上一次晚睡/晚起一小时 */
function clockDelta(prevMinutes, currMinutes) {
  let delta = currMinutes - prevMinutes;
  if (delta > 720) delta -= 1440;
  if (delta < -720) delta += 1440;
  return delta;
}

/** 每夜相对前一夜的推移（小时，正数=越来越晚）；中间缺记录时按天摊平 */
function paceSeries(nights) {
  const out = [];
  for (let index = 1; index < nights.length; index += 1) {
    const prev = nights[index - 1];
    const curr = nights[index];
    const dayGap = Math.max(
      1,
      Math.round((dayStartMs(curr.key) - dayStartMs(prev.key)) / 86400000) || 1
    );
    const minutes = clockDelta(prev.wakeMinute, curr.wakeMinute);
    out.push({ date: curr.key, hours: minutes / 60 / dayGap });
  }
  return out;
}

function overviewStats(nights) {
  if (!nights.length) return null;
  const counts = nights.map((night) => night.records.length);
  const sleeps = nights.map((night) => night.sleepMinute).filter((v) => v !== null);
  const wakes = nights.map((night) => night.wakeMinute).filter((v) => v !== null);
  const durations = nights.map((night) => night.duration);
  const paces = paceSeries(nights);
  return {
    count: nights.length,
    records: counts.reduce((sum, value) => sum + value, 0),
    avgSleep: circularMeanMinutes(sleeps),
    avgWake: circularMeanMinutes(wakes),
    avgDuration: Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length),
    avgPace: paces.length ? paces.reduce((sum, item) => sum + item.hours, 0) / paces.length : null,
    paces,
  };
}

function nightLaneHtml(nights) {
  const ticks = [];
  for (let hour = 0; hour <= 24; hour += 3) {
    const label = hhmmOf(((((18 + hour) % 24) + 24) % 24) * 60);
    ticks.push(`<span class="ov-tick" style="left:${(hour / 24) * 100}%">${escapeHtml(label)}</span>`);
  }
  const rows = nights
    .map((night) => {
      const bands = night.segments
        .map(
          (segment) =>
            `<span class="ov-band" data-ov-item="${segment.record.id}" style="left:${(segment.left / 24) * 100}%;width:${(segment.width / 24) * 100}%"></span>`
        )
        .join("");
      const title = night.records
        .map((record) => `${record.record_date} ${record.sleep_time} → ${record.wake_time}`)
        .join(" / ");
      return `<div class="ov-row" data-ov-row="${escapeHtml(night.key)}" title="${escapeHtml(title)}">
          <span class="ov-day mono">${escapeHtml(String(night.key).slice(5))}</span>
          <span class="ov-track">${bands}</span>
        </div>`;
    })
    .join("");
  return `<div class="ov-lane">
      <div class="ov-axis">${ticks.join("")}</div>
      ${rows}
    </div>`;
}

function paceLineHtml(paces) {
  if (paces.length < 2) {
    return `<p class="ov-empty">${escapeHtml(t("records.overview.fewer"))}</p>`;
  }
  const width = 100;
  const height = 40;
  const values = paces.map((item) => item.hours);
  const top = Math.max(...values, 1.5, 0) + 0.25;
  const bottom = Math.min(...values, -1.5, 0) - 0.25;
  const span = top - bottom || 1;
  const xOf = (index) => (index / (paces.length - 1)) * width;
  const yOf = (value) => height - ((value - bottom) / span) * height;
  const path = paces
    .map((item, index) => `${index ? "L" : "M"}${xOf(index).toFixed(2)},${yOf(item.hours).toFixed(2)}`)
    .join(" ");
  const dots = paces
    .map((item, index) => {
      const fast = Math.abs(item.hours) > 1.5;
      const x = xOf(index).toFixed(2);
      const y = yOf(item.hours).toFixed(2);
      return `<line x1="${x}" y1="${y}" x2="${x}" y2="${(Number(y) - 3).toFixed(2)}" class="ov-dot${fast ? " is-fast" : ""}"></line>`;
    })
    .join("");
  return `<svg class="ov-pace" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img"
      aria-label="${escapeHtml(t("records.overview.pace"))}">
      <line x1="0" y1="${yOf(0).toFixed(2)}" x2="${width}" y2="${yOf(0).toFixed(2)}" class="ov-zero"></line>
      <path d="${path}" class="ov-line" vector-effect="non-scaling-stroke"></path>
      ${dots}
    </svg>`;
}

function durationBarsHtml(nights) {
  const hours = nights.map((night) => night.duration / 60);
  const maxHours = Math.max(OVERVIEW_REF_HOURS, ...hours, 10);
  const refTop = 100 - (OVERVIEW_REF_HOURS / maxHours) * 100;
  const bars = nights
    .map((night, index) => {
      const value = hours[index];
      const height = Math.max(2, (value / maxHours) * 100);
      const label = String(night.key).slice(5);
      const title = night.records
        .map((record) => `${record.sleep_time} → ${record.wake_time}`)
        .join(" / ");
      return `<span class="ov-bar-wrap" data-ov-item="${night.records[0]?.id ?? ""}" title="${escapeHtml(
        `${label} ${title}`
      )}">
          <span class="ov-bar${value < OVERVIEW_REF_HOURS ? " is-short" : ""}" style="height:${height}%"></span>
          <span class="ov-bar-day mono">${label}</span>
        </span>`;
    })
    .join("");
  return `<div class="ov-bars">
      <span class="ov-ref" style="top:${refTop}%"></span>
      ${bars}
    </div>`;
}

function overviewHtml(nights) {
  const stats = overviewStats(nights);
  if (!stats) {
    return `<div class="row-wrap"><h2 style="margin:0">${escapeHtml(t("records.overview.title"))}</h2></div>
      <p class="ov-empty">${escapeHtml(t("records.overview.empty"))}</p>`;
  }
  const paceValue = stats.avgPace === null ? "—" : `${stats.avgPace >= 0 ? "+" : ""}${stats.avgPace.toFixed(1)}`;
  const fast = stats.avgPace !== null && Math.abs(stats.avgPace) > 1.5;
  return `<div class="row-wrap">
      <h2 style="margin:0">${escapeHtml(t("records.overview.title"))}</h2>
      <span class="badge">${escapeHtml(tp("records.overview.window", { days: stats.count }))}</span>
      <span class="badge">${escapeHtml(tp("records.overview.records", { count: stats.records }))}</span>
      <span class="grow"></span>
      <span class="tiny muted">${escapeHtml(t("records.overview.hint"))}</span>
    </div>
    <div class="ov-stats">
      <div class="ov-tile"><span class="label">${escapeHtml(t("records.overview.avgSleep"))}</span>
        <strong class="mono">${escapeHtml(hhmmOf(stats.avgSleep ?? 0))}</strong></div>
      <div class="ov-tile"><span class="label">${escapeHtml(t("records.overview.avgWake"))}</span>
        <strong class="mono">${escapeHtml(hhmmOf(stats.avgWake ?? 0))}</strong></div>
      <div class="ov-tile"><span class="label">${escapeHtml(t("records.overview.avgDuration"))}</span>
        <strong class="mono">${escapeHtml(fmtDuration(stats.avgDuration))}</strong></div>
      <div class="ov-tile"><span class="label">${escapeHtml(t("records.overview.avgPace"))}</span>
        <strong class="mono${fast ? " is-fast" : ""}">${escapeHtml(
          tp("records.overview.paceValue", { hours: paceValue })
        )}</strong></div>
    </div>
    <div class="ov-split">
      <div class="ov-panel">
        <div class="row-wrap">
          <span class="ov-title">${escapeHtml(t("records.overview.night"))}</span>
          <span class="grow"></span>
          <span class="tiny muted">${escapeHtml(t("records.overview.nightHint"))}</span>
        </div>
        ${nightLaneHtml(nights.slice(-21))}
        <div class="ov-legend">
          <span class="ov-key"><i class="ov-swatch is-band"></i>${escapeHtml(t("records.overview.legendSleep"))}</span>
          <span class="ov-key"><i class="ov-swatch is-fast"></i>${escapeHtml(t("records.overview.legendFast"))}</span>
        </div>
      </div>
      <div class="ov-panel">
        <div class="row-wrap">
          <span class="ov-title">${escapeHtml(t("records.overview.pace"))}</span>
          <span class="grow"></span>
          <span class="tiny muted">${escapeHtml(t("records.overview.paceHint"))}</span>
        </div>
        ${paceLineHtml(stats.paces)}
      </div>
    </div>
    <div class="ov-panel">
      <div class="row-wrap">
        <span class="ov-title">${escapeHtml(t("records.overview.duration"))}</span>
        <span class="grow"></span>
        <span class="tiny muted">${escapeHtml(tp("records.overview.durationHint", { hours: OVERVIEW_REF_HOURS }))}</span>
      </div>
      ${durationBarsHtml(nights)}
    </div>`;
}

/** 概览 ↔ 表格联动：点一条，对应的表格行高亮并滚到视野中间 */
function focusOverviewItem(root, id) {
  const row = qs(`#records-body [data-row="${id}"]`, root);
  if (!row) return;
  row.classList.add("is-hot");
  row.scrollIntoView({ block: "center", behavior: "smooth" });
  window.setTimeout(() => row.classList.remove("is-hot"), 1800);
}

function bindOverview(root) {
  const box = qs("#records-overview", root);
  if (!box) return;
  box.addEventListener("click", (event) => {
    const hit = event.target.closest("[data-ov-item], [data-ov-row]");
    if (!hit) return;
    const id = hit.getAttribute("data-ov-item") || hit.getAttribute("data-ov-row");
    if (id) focusOverviewItem(root, id);
  });
}

/** 找「三餐日期」那天已经记过的三餐（列表里最新的一条）。只看内存，不发请求。 */
function findMealRow(day) {
  const rows = [...(view.overview || []), ...(view.items || [])].filter(
    (row) => (row.meal_date || row.record_date) === day
  );
  if (!rows.length) return null;
  return rows.reduce((best, row) => (Number(row.id) > Number(best.id) ? row : best), rows[0]);
}

/** 把一份表单数据换算成绝对毫秒区间（跨零点也成立），用于重叠预检 */
function intervalOf(payload) {
  const sleepDay = dayStartMs(payload.record_date);
  if (sleepDay === null) return null;
  const sleepTs = sleepDay + minutesOf(payload.sleep_time) * 60000;
  const wakeDay = dayStartMs(payload.wake_date) ?? sleepDay;
  const wakeTs = wakeDay + minutesOf(payload.wake_time) * 60000;
  return { sleepTs, wakeTs };
}

/** 在已加载的记录里找与新区间重叠的一条（背靠背不算重叠；后端还会再校验一次） */
function findOverlap(mine, excludeId) {
  if (!mine) return null;
  const rows = [...(view.overview || []), ...(view.items || [])];
  const seen = new Set();
  for (const record of rows) {
    if (!record || seen.has(record.id) || record.id === excludeId) continue;
    seen.add(record.id);
    const span = spanOf(record);
    if (!span) continue;
    if (mine.sleepTs < span.wakeTs && span.sleepTs < mine.wakeTs) return record;
  }
  return null;
}


function renderOverview(root) {
  const box = qs("#records-overview", root);
  if (!box) return;
  const nights = nightsOf(view.overview.length ? view.overview : view.items);
  box.classList.toggle("is-empty", nights.length === 0);
  box.innerHTML = overviewHtml(nights);
  bindOverview(root);
}

/** 概览要看整段区间：当前页的数据不够覆盖区间时，单独拉一次（最多 OVERVIEW_LIMIT 条） */
async function loadOverview(root) {
  const start = view.startDate || defaultRange().start;
  const end = view.endDate || todayIso();
  const inRange = view.items.filter(
    (row) => String(row.record_date) >= start && String(row.record_date) <= end
  );
  const coversRange = view.total <= view.pageSize || inRange.length >= view.total;
  if (coversRange) {
    view.overview = inRange;
    renderOverview(root);
    return;
  }
  try {
    const data = await recordApi.list({ page: 1, page_size: OVERVIEW_LIMIT, start_date: start, end_date: end });
    view.overview = data.items || [];
  } catch (err) {
    view.overview = inRange;
    if (err instanceof ApiError && err.status === 401) throw err;
  }
  renderOverview(root);
}

/* ------------------------------ 渲染 ------------------------------ */

function renderShell(root) {
  root.innerHTML = `
    <div class="page-head">
      <div class="titles">
        <h1>${escapeHtml(t("records.title"))}</h1>
        <p>${escapeHtml(t("records.subtitle"))}</p>
      </div>
      <div class="row-wrap">
        <button class="btn btn-primary" id="add-record">＋ ${escapeHtml(t("records.add"))}</button>
      </div>
    </div>

    <section class="card stack records-overview" id="records-overview"></section>

    <div class="card stack" style="gap:12px">
      <div class="toolbar">
        <div class="field">
          <label>${escapeHtml(t("records.startDate"))}</label>
          <input class="input" type="date" id="filter-start" value="${escapeHtml(view.startDate)}" />
        </div>
        <div class="field">
          <label>${escapeHtml(t("records.endDate"))}</label>
          <input class="input" type="date" id="filter-end" value="${escapeHtml(view.endDate)}" />
        </div>
        <button class="btn btn-sm" id="filter-apply">${escapeHtml(t("common.search"))}</button>
        <button class="btn btn-sm btn-ghost" id="filter-reset">${escapeHtml(t("common.reset"))}</button>
        <span class="grow"></span>
        <span class="muted small" id="records-total"></span>
      </div>

      <div id="cooldown-tip" class="note warn hidden"></div>

      <div class="row-wrap" id="batch-bar" style="display:none">
        <span class="badge badge-accent" id="batch-count"></span>
        <button class="btn btn-sm btn-danger" id="batch-delete">${escapeHtml(t("common.delete"))}</button>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width:36px"><input type="checkbox" id="select-all" /></th>
              <th>${escapeHtml(t("records.sleepDate"))}</th>
              <th>${escapeHtml(t("records.wakeDate"))}</th>
              <th>${escapeHtml(t("records.sleepTime"))}</th>
              <th>${escapeHtml(t("records.wakeTime"))}</th>
              <th>${escapeHtml(t("records.duration"))}</th>
              <th>${escapeHtml(t("records.meals"))}</th>
              <th>${escapeHtml(t("records.note"))}</th>
              <th class="right">${escapeHtml(t("common.actions"))}</th>
            </tr>
          </thead>
          <tbody id="records-body"></tbody>
        </table>
        <div id="records-empty" class="empty hidden">${escapeHtml(t("common.empty"))}</div>
      </div>

      <div class="record-cards" id="records-cards"></div>
      <div class="pager" id="records-pager"></div>
      <p class="tiny muted" id="retention-tip"></p>
    </div>`;
}

function renderRows(root) {
  const body = qs("#records-body", root);
  const cards = qs("#records-cards", root);
  const empty = qs("#records-empty", root);
  const hasRows = view.items.length > 0;

  empty.classList.toggle("hidden", hasRows);
  body.innerHTML = view.items
    .map(
      (record) => `
      <tr data-row="${record.id}">
        <td><input type="checkbox" data-pick="${record.id}" ${view.selected.has(record.id) ? "checked" : ""} /></td>
        <td class="mono">${escapeHtml(record.record_date)}</td>
        <td class="mono">${escapeHtml(record.wake_date || "—")}</td>
        <td class="mono">${escapeHtml(record.sleep_time)}</td>
        <td class="mono">${escapeHtml(record.wake_time)}</td>
        <td class="mono record-duration">${escapeHtml(fmtDuration(record.duration_minutes))}</td>
        <td>${mealCell(record)}</td>
        <td class="muted small">${escapeHtml((record.note || "").slice(0, 24))}</td>
        <td class="actions">
          <button class="btn btn-sm" data-edit="${record.id}">${escapeHtml(t("common.edit"))}</button>
          <button class="btn btn-sm btn-danger" data-del="${record.id}">${escapeHtml(t("common.delete"))}</button>
        </td>
      </tr>`
    )
    .join("");

  cards.innerHTML = view.items
    .map(
      (record) => `
      <div class="record-card">
        <div class="rc-head">
          <span class="mono">${escapeHtml(record.record_date)}${record.wake_date ? ` → ${escapeHtml(record.wake_date)}` : ""}</span>
          ${mealCell(record)}
        </div>
        <div class="rc-times">
          <span>${iconSvg("moon")} ${escapeHtml(record.sleep_time)}</span>
          <span class="muted">${iconSvg("arrow-right")}</span>
          <span>${iconSvg("sun")} ${escapeHtml(record.wake_time)}</span>
          <span class="badge">${escapeHtml(fmtDuration(record.duration_minutes))}</span>
        </div>
        <div class="rc-meta">
          ${record.note ? `<span>${escapeHtml(record.note)}</span>` : ""}
        </div>
        <div class="row" style="margin-top:10px; justify-content:flex-end">
          <button class="btn btn-sm" data-edit="${record.id}">${escapeHtml(t("common.edit"))}</button>
          <button class="btn btn-sm btn-danger" data-del="${record.id}">${escapeHtml(t("common.delete"))}</button>
        </div>
      </div>`
    )
    .join("");

  const allChecked = hasRows && view.items.every((row) => view.selected.has(row.id));
  const selectAll = qs("#select-all", root);
  if (selectAll) selectAll.checked = allChecked;

  const batchBar = qs("#batch-bar", root);
  batchBar.style.display = view.selected.size ? "flex" : "none";
  qs("#batch-count", root).textContent = t("common.selected", { n: view.selected.size });
}

function renderPagination(root) {
  renderPager(qs("#records-pager", root), {
    page: view.page,
    pages: view.pages,
    total: view.total,
    pageSize: view.pageSize,
    onChange: async (page) => {
      view.page = page;
      view.selected.clear();
      await load(root);
    },
  });
}

function renderMeta(root) {
  enhanceDateInputs(root);
  const totalNode = qs("#records-total", root);
  if (totalNode) totalNode.textContent = t("records.stats", { total: view.total });
  const retentionNode = qs("#retention-tip", root);
  if (retentionNode && view.meta) {
    retentionNode.textContent = t("records.retention", {
      months: view.meta.retention_months,
      cutoff: view.meta.retention_cutoff,
    });
  }
  const tip = qs("#cooldown-tip", root);
  if (!tip) return;
  const left = cooldownRemaining();
  tip.classList.toggle("hidden", left <= 0);
  if (left > 0) tip.textContent = t("records.cooldown", { seconds: left });
}

function tick(root) {
  if (cooldownRemaining() > 0) {
    renderMeta(root);
    setTimeout(() => tick(root), 1000);
  }
}

/* ------------------------------ 数据 ------------------------------ */

async function load(root, { silent = false } = {}) {
  if (!silent) {
    qs("#records-body", root).innerHTML = `<tr><td colspan="8" class="loading">${t("common.loading")}</td></tr>`;
  }
  try {
    const data = await recordApi.list({
      page: view.page,
      page_size: view.pageSize,
      start_date: view.startDate || undefined,
      end_date: view.endDate || undefined,
    });
    // 记录有增删改，日历上的“已记录”绿点要重新算
    clearDateMarkCache();
    view.items = data.items || [];
    view.total = data.total || 0;
    view.pages = data.pages || 1;
    view.items.forEach((row) => {
      if (!view.selected.has(row.id)) return;
      if (!view.items.some((item) => item.id === row.id)) view.selected.delete(row.id);
    });
    renderRows(root);
    renderPagination(root);
    renderMeta(root);
    await loadOverview(root);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("error.server"));
  }
}

async function loadMeta(root) {
  try {
    view.meta = await recordApi.meta();
    if (view.meta?.cooldown_remaining_seconds > 0) {
      view.cooldownUntil = Date.now() + view.meta.cooldown_remaining_seconds * 1000;
      tick(root);
    }
    renderMeta(root);
  } catch (err) {
    /* 元信息失败不影响列表 */
  }
}

/* ------------------------------ 表单弹窗 ------------------------------ */

/* 弹窗关闭后 DOM 会被移除，因此在 onMount 时捕获取值闭包 collector，ok 之后再调用 */
async function openRecordForm(root, record = null) {
  const isEdit = Boolean(record);
  const maxDate = view.meta?.today || todayIso();
  // 入睡日期默认昨天（记录的大多是"昨晚这一觉"）；起床日期默认入睡日期 + 1，可改。
  const sleepDate = record?.record_date || shiftIso(maxDate, -1);
  const wakeDate = record?.wake_date || shiftIso(sleepDate, 1);
  // 三餐记录默认跟着入睡日：入睡与起床同一天（凌晨睡的）说明那晚属于前一天
  const mealDate =
    record?.meal_date ||
    (wakeDate === sleepDate ? shiftIso(sleepDate, -1) : sleepDate);
  let collector = null;

  await openModal({
    title: isEdit ? t("records.editTitle") : t("records.addTitle"),
    wide: true,
    okText: null,
    bodyHtml: `
      <div class="stack" style="gap:16px">
        <div class="form-row">
          <div class="field">
            <label>${escapeHtml(t("records.sleepDate"))}</label>
            <input class="input" type="date" id="f-date" value="${escapeHtml(sleepDate)}"
                   max="${escapeHtml(maxDate)}" style="max-width:190px" />
          </div>
          <div class="field">
            <label>${escapeHtml(t("records.wakeDate"))}</label>
            <input class="input" type="date" id="f-wake-date" value="${escapeHtml(wakeDate)}"
                   max="${escapeHtml(shiftIso(sleepDate, 1))}" style="max-width:190px" />
            <span class="tiny muted">${escapeHtml(t("records.wakeDateHint"))}</span>
          </div>
        </div>
        <div class="form-row">
          <div class="field">
            <label>${escapeHtml(t("records.sleepTime"))}</label>
            <div id="f-sleep"></div>
          </div>
          <div class="field">
            <label>${escapeHtml(t("records.wakeTime"))}</label>
            <div id="f-wake"></div>
          </div>
        </div>
        <div class="field">
          <div class="row-wrap" style="align-items:center; gap:10px">
            <span class="tiny muted" data-meal-date-hint>${escapeHtml(t("records.mealsDay"))}</span>
            <label class="tiny muted">${escapeHtml(t("records.mealDate"))}</label>
            <input class="input" type="date" id="f-meal-date" value="${escapeHtml(mealDate)}"
                   style="max-width:150px" />
          </div>
          <div class="row-wrap" style="margin-top:8px">
            <label class="checkbox"><input type="checkbox" id="f-breakfast" /><span>${escapeHtml(t("records.meal.breakfast"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="f-lunch" /><span>${escapeHtml(t("records.meal.lunch"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="f-dinner" /><span>${escapeHtml(t("records.meal.dinner"))}</span></label>
          </div>
          <p class="tiny muted" data-meals-from hidden></p>
          <p class="tiny muted" data-meal-late hidden></p>
        </div>
        <div class="field">
          <label>${escapeHtml(t("records.note"))}</label>
          <input class="input" id="f-note" maxlength="200"
                 placeholder="${escapeHtml(t("records.notePlaceholder"))}"
                 value="${escapeHtml(record?.note || "")}" />
        </div>
        <p class="form-error" data-form-error hidden></p>
        <div class="row-wrap">
          <span class="grow"></span>
          <button type="button" class="btn btn-primary" data-save>${escapeHtml(
            isEdit ? t("records.submitUpdate") : t("records.submitCreate")
          )}</button>
        </div>
      </div>`,
    onMount: (modal, close) => {
      enhanceDateInputs(modal);

      // 保存按钮挪到底栏、和"取消"并排：底栏常驻，手机上不用滚到表单末尾才够得着。
      const foot = modal.querySelector(".modal-foot");
      const saveButton = qs("[data-save]", modal);
      if (foot && saveButton) {
        const saveRow = saveButton.closest(".row-wrap");
        foot.appendChild(saveButton);
        if (saveRow && !saveRow.querySelector("button")) saveRow.remove();
      }
      const sleepPicker = createTimePicker(qs("#f-sleep", modal), { value: record?.sleep_time || "23:30" });
      const wakePicker = createTimePicker(qs("#f-wake", modal), { value: record?.wake_time || "07:30" });
      const breakfast = qs("#f-breakfast", modal);
      const lunch = qs("#f-lunch", modal);
      const dinner = qs("#f-dinner", modal);
      breakfast.checked = record ? Boolean(record.meal_breakfast) : true;
      lunch.checked = record ? Boolean(record.meal_lunch) : true;
      dinner.checked = record ? Boolean(record.meal_dinner) : true;

      // 新建记录时，三餐默认跟随「三餐日期」那天已有的记录（建议页今天卡片勾过、
      // 或者更早填过一条记录）；用户只要自己动过勾选，就不再覆盖他。
      const mealsHint = qs("[data-meals-from]", modal);
      const mealBoxes = { breakfast, lunch, dinner };
      let mealsTouched = false;
      const applyMeals = (meals) => {
        Object.keys(mealBoxes).forEach((key) => {
          const state = meals?.[key]?.state;
          if (state === "eaten") mealBoxes[key].checked = true;
          else if (state === "skipped") mealBoxes[key].checked = false;
        });
      };
      // 新建记录时，三餐默认沿用「三餐日期」那天已经记过的三餐（列表里已有的记录）。
      // 只读内存里的列表数据，不发请求：弹窗打开是纯同步的，表单能立刻出现。
      const loadMeals = () => {
        if (record || mealsTouched) return;
        const day = qs("#f-meal-date", modal).value;
        if (!day) return;
        const row = findMealRow(day);
        if (!row) {
          if (mealsHint) mealsHint.hidden = true;
          return;
        }
        applyMeals({
          breakfast: { state: row.meal_breakfast ? "eaten" : "skipped" },
          lunch: { state: row.meal_lunch ? "eaten" : "skipped" },
          dinner: { state: row.meal_dinner ? "eaten" : "skipped" },
        });
        if (mealsHint) {
          mealsHint.hidden = false;
          mealsHint.textContent = tp("records.mealsFromRecord", { date: day });
        }
      };
      Object.values(mealBoxes).forEach((box) => {
        box.addEventListener("change", () => {
          mealsTouched = true;
        });
      });

      // 起床日期跟着入睡日期走，直到用户自己改过它（改过就尊重他填的，例如午睡）
      const sleepInput = qs("#f-date", modal);
      const wakeInput = qs("#f-wake-date", modal);
      const mealInput = qs("#f-meal-date", modal);
      const lateHint = qs("[data-meal-late]", modal);
      let wakeTouched = Boolean(
        record?.wake_date && record.wake_date !== shiftIso(record.record_date, 1)
      );
      let mealTouched = Boolean(record?.meal_date);

      // 入睡与起床同一天 = 凌晨睡的那一觉，默认把三餐记到前一天，并明示可以改
      const syncMeal = () => {
        const sameDay = wakeInput.value === sleepInput.value;
        if (!mealTouched) mealInput.value = sameDay ? shiftIso(sleepInput.value, -1) : sleepInput.value;
        if (lateHint) {
          lateHint.hidden = !sameDay;
          lateHint.textContent = sameDay ? t("records.mealDateLate") : "";
        }
      };
      syncMeal();
      loadMeals();
      mealInput.addEventListener("input", () => {
        mealTouched = true;
        loadMeals();
      });
      const syncWake = () => {
        wakeInput.max = shiftIso(sleepInput.value, 1);
        if (!wakeTouched) wakeInput.value = shiftIso(sleepInput.value, 1);
        syncMeal();
        loadMeals();
      };
      sleepInput.addEventListener("change", syncWake);
      sleepInput.addEventListener("input", syncWake);
      wakeInput.addEventListener("input", () => {
        wakeTouched = true;
      });

      collector = () => ({
        record_date: sleepInput.value,
        wake_date: wakeInput.value,
        meal_date: mealInput.value,
        sleep_time: sleepPicker.value,
        wake_time: wakePicker.value,
        meal_breakfast: breakfast.checked,
        meal_lunch: lunch.checked,
        meal_dinner: dinner.checked,
        note: qs("#f-note", modal).value.trim() || null,
      });

      // 保存：按钮进入禁用态、错误留在弹窗里，成功才 close(true) 关窗
      const errorBox = qs("[data-form-error]", modal);
      const saveBtn = qs("[data-save]", modal);
      const showError = (message) => {
        if (!errorBox) {
          toastErr(message);
          return;
        }
        errorBox.textContent = message;
        errorBox.hidden = false;
        errorBox.scrollIntoView({ block: "nearest" });
      };
      const clearError = () => {
        if (!errorBox) return;
        errorBox.hidden = true;
        errorBox.textContent = "";
      };
      const busy = (on) => {
        if (!saveBtn) return;
        saveBtn.disabled = on;
        saveBtn.textContent = on
          ? t("records.saving")
          : isEdit
            ? t("records.submitUpdate")
            : t("records.submitCreate");
      };
      const submit = async () => {
        clearError();
        const payload = collector();
        if (!payload.sleep_time || !payload.wake_time) return showError(t("error.invalid_time"));
        if (!payload.record_date || !payload.wake_date) return showError(t("error.invalid_date"));
        const dayGap =
          payload.wake_date === payload.record_date
            ? 0
            : payload.wake_date === shiftIso(payload.record_date, 1)
              ? 1
              : -1;
        if (dayGap < 0) return showError(t("error.invalid_date_range"));
        if (dayGap === 0 && payload.wake_time <= payload.sleep_time) {
          return showError(t("error.invalid_time_range"));
        }
        if (
          !payload.meal_date ||
          (payload.meal_date !== payload.record_date &&
            payload.meal_date !== shiftIso(payload.record_date, -1))
        ) {
          return showError(payload.meal_date ? t("error.invalid_meal_date") : t("error.invalid_date"));
        }
        if (cooldownRemaining() > 0) {
          return showError(t("records.cooldown", { seconds: cooldownRemaining() }));
        }
        const other = findOverlap(intervalOf(payload), record?.id);
        if (other) {
          return showError(
            tp("error.overlapping_record", {
              date: other.record_date,
              sleep_time: other.sleep_time,
              wake_time: other.wake_time,
            })
          );
        }
        busy(true);
        try {
          if (isEdit) {
            await recordApi.update(record.id, payload);
            toastOk(t("records.updated"));
          } else {
            await recordApi.create(payload);
            toastOk(t("records.created"));
            view.page = 1;
          }
          startCooldown();
          await load(root, { silent: true });
          renderMeta(root);
          tick(root);
          close(true);
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) throw err;
          showError(err instanceof Error ? err.message : t("error.server"));
        } finally {
          busy(false);
        }
      };
      saveBtn?.addEventListener("click", (event) => {
        event.preventDefault();
        submit();
      });
      [sleepInput, wakeInput, mealInput].forEach((node) => node?.addEventListener("input", clearError));
    },
  });

}

/* ------------------------------ 事件 ------------------------------ */

function bind(root, ctx) {
  qs("#add-record", root).addEventListener("click", () => openRecordForm(root, null));

  const applyFilter = debounce(async () => {
    view.startDate = qs("#filter-start", root).value;
    view.endDate = qs("#filter-end", root).value;
    view.page = 1;
    view.selected.clear();
    await load(root, { silent: true });
  }, 200);

  qs("#filter-start", root).addEventListener("change", applyFilter);
  qs("#filter-end", root).addEventListener("change", applyFilter);
  qs("#filter-apply", root).addEventListener("click", applyFilter);
  qs("#filter-reset", root).addEventListener("click", async () => {
    // "重置"回到默认的最近 7 天，而不是清空区间（清空会把全部历史拉回来）
    setRange(root, defaultRange());
    view.page = 1;
    view.selected.clear();
    await load(root, { silent: true });
  });

  qs("#select-all", root).addEventListener("change", (event) => {
    if (event.target.checked) {
      view.items.forEach((row) => view.selected.add(row.id));
    } else {
      view.selected.clear();
    }
    renderRows(root);
  });

  qs("#records-body", root).addEventListener("change", (event) => {
    const id = Number(event.target.getAttribute("data-pick"));
    if (!id) return;
    if (event.target.checked) view.selected.add(id);
    else view.selected.delete(id);
    renderRows(root);
  });

  const handleAction = async (target) => {
    const editId = target.closest("[data-edit]")?.getAttribute("data-edit");
    const delId = target.closest("[data-del]")?.getAttribute("data-del");
    if (editId) {
      const record = view.items.find((row) => row.id === Number(editId));
      if (record) await openRecordForm(root, record);
    }
    if (delId) {
      const confirmed = await confirmDialog({
        title: t("common.delete"),
        bodyHtml: `<p>${escapeHtml(t("records.deleteConfirm"))}</p>`,
        danger: true,
        okText: t("common.delete"),
      });
      if (!confirmed) return;
      try {
        await recordApi.remove(Number(delId));
        view.selected.delete(Number(delId));
        toastOk(t("records.deleted"));
        await load(root, { silent: true });
      } catch (err) {
        toastErr(err instanceof Error ? err.message : t("error.server"));
      }
    }
  };

  qs("#records-body", root).addEventListener("click", (event) => handleAction(event.target));
  qs("#records-cards", root).addEventListener("click", (event) => handleAction(event.target));

  qs("#batch-delete", root).addEventListener("click", async () => {
    const ids = Array.from(view.selected);
    if (!ids.length) return;
    const confirmed = await confirmDialog({
      title: t("admin.batchDelete"),
      bodyHtml: `<p>${escapeHtml(t("records.batchDeleteConfirm", { n: ids.length }))}</p>`,
      danger: true,
      okText: t("common.delete"),
    });
    if (!confirmed) return;
    try {
      const result = await recordApi.batchRemove(ids);
      view.selected.clear();
      toastOk(t("records.batchDeleted", { n: result?.extra?.deleted ?? ids.length }));
      await load(root, { silent: true });
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });
}

/* ------------------------------ 入口 ------------------------------ */

export async function renderRecordsPage(root, ctx) {
  renderShell(root);
  // 默认查询最近 7 天，页面一进来就带上区间（用户要求：记录默认只看最近一周）
  setRange(root, defaultRange());
  bind(root, ctx);
  await Promise.all([loadMeta(root), load(root)]);
}
