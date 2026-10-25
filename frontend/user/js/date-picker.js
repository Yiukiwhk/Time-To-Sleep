/* 自绘日期弹层。
 *
 * 为什么不用原生 <input type="date"> 的日历：那层是浏览器 chrome，样式改不了，
 * 没法把「有记录的日子」染成绿色。所以把原生输入框藏起来（value 仍是唯一真值，
 * 表单收集与探针都照旧读它），在旁边放一个按钮显示当前日期，点开就是我们自己的网格。
 *
 * 用法：`enhanceDateInputs(root)` 会给 root 里每个 `input[type="date"]` 加按钮；
 * 重新渲染后重复调用是安全的（已加过的会跳过）。
 */
import { recordApi } from "/shared/api.js";
import { escapeHtml, qs } from "/shared/ui.js";
import { iconSvg } from "/shared/icons.js";

const WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const WD_LABEL = {
  "zh-CN": ["日", "一", "二", "三", "四", "五", "六"],
  en: ["S", "M", "T", "W", "T", "F", "S"],
};

function locale() {
  const value = String(document.documentElement.lang || "zh-CN").toLowerCase();
  return value.startsWith("en") ? "en" : "zh-CN";
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function isoOf(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function parseIso(value) {
  const text = String(value || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
  const [year, month, day] = text.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

/** 某个月「有记录」的日期集合（按入睡日），带缓存。 */
const markedCache = new Map();

async function markedOf(year, month) {
  const key = `${year}-${pad(month)}`;
  if (markedCache.has(key)) return markedCache.get(key);
  const last = daysInMonth(year, month);
  const first = `${key}-01`;
  const lastIso = `${key}-${pad(last)}`;
  const today = isoOf(new Date());
  let marked = new Set();
  // 整月都在今天之后（或者月底还没到）时不能把整月喂给后端：日期过滤有
  // MAX_FUTURE_DAYS 上限，超了直接 400 error.date_in_future，绿点会全灭。
  // 所以只问到今天为止；未来的月份连请求都不发。
  const end = lastIso < today ? lastIso : today;
  if (first <= end) {
    try {
      const data = await recordApi.list({
        start_date: first,
        end_date: end,
        page: 1,
        page_size: 100,
      });
      const rows = [...(data?.items || []), ...(data?.records || [])];
      marked = new Set(rows.map((row) => row?.record_date).filter(Boolean));
    } catch (error) {
      marked = new Set();
    }
  }
  markedCache.set(key, marked);
  return marked;
}

/** 记录被增删后调用，避免弹层还显示旧的绿点。 */
export function clearDateMarkCache() {
  markedCache.clear();
}

let active = null;

function closeActive() {
  if (!active) return;
  active.remove();
  active = null;
}

function buildPopover(field, input, { min, max }) {
  closeActive();
  const box = document.createElement("div");
  box.className = "dp";
  box.setAttribute("data-date-picker", "1");
  // 优先挂进弹窗容器：模态本身是独立层叠上下文，挂到 body 可能被它盖住。
  document.body.appendChild(box);
  active = box;

  const selected = parseIso(input.value) || new Date();
  let viewYear = selected.getFullYear();
  let viewMonth = selected.getMonth() + 1;

  const rect = field.getBoundingClientRect();
  const width = 268;
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
  const spaceBelow = window.innerHeight - rect.bottom;
  box.style.left = `${left}px`;
  box.style.width = `${width}px`;
  box.style.top = `${spaceBelow > 320 ? rect.bottom + 6 : Math.max(8, rect.top - 320)}px`;

  let markings = new Set();
  function render() {
    const marked = markings;
    const first = new Date(viewYear, viewMonth - 1, 1);
    const lead = first.getDay();
    const total = daysInMonth(viewYear, viewMonth);
    const prevTotal = daysInMonth(viewYear, viewMonth - 1);
    const cells = [];
    for (let index = lead; index > 0; index -= 1) {
      const day = prevTotal - index + 1;
      const date = new Date(viewYear, viewMonth - 2, day);
      cells.push({ date, outside: true });
    }
    for (let day = 1; day <= total; day += 1) {
      cells.push({ date: new Date(viewYear, viewMonth - 1, day), outside: false });
    }
    while (cells.length % 7 !== 0) {
      const day = cells.length - lead - total + 1;
      cells.push({ date: new Date(viewYear, viewMonth, day), outside: true });
    }

    const today = isoOf(new Date());
    const value = input.value;
    const grid = cells
      .map(({ date, outside }) => {
        const iso = isoOf(date);
        const classes = ["dp-day"];
        if (outside) classes.push("is-outside");
        if (marked.has(iso)) classes.push(outside ? "is-marked-soft" : "is-marked");
        if (iso === value) classes.push("is-selected");
        if (iso === today) classes.push("is-today");
        const low = min && iso < min;
        const high = max && iso > max;
        if (low || high) classes.push("is-disabled");
        const label = outside ? date.getDate() : date.getDate();
        return `<button type="button" class="${classes.join(" ")}" data-date="${iso}"${
          low || high ? " disabled" : ""
        }>${label}</button>`;
      })
      .join("");

    const labels = WD_LABEL[locale()] || WD_LABEL["zh-CN"];
    box.innerHTML = `<div class="dp-head">
        <button type="button" class="dp-nav" data-dp-move="-1" aria-label="prev">${iconSvg("chevron-left")}</button>
        <span class="dp-title">${viewYear}.${pad(viewMonth)}</span>
        <button type="button" class="dp-nav" data-dp-move="1" aria-label="next">${iconSvg("chevron-right")}</button>
      </div>
      <div class="dp-week">${labels.map((text) => `<span>${escapeHtml(text)}</span>`).join("")}</div>
      <div class="dp-grid">${grid}</div>`;
  }

  async function paint() {
    render();
    markings = await markedOf(viewYear, viewMonth);
    if (active !== box) return;
    render();
  }

  box.addEventListener("click", (event) => {
    const nav = event.target.closest?.("[data-dp-move]");
    if (nav) {
      viewMonth += Number(nav.dataset.dpMove);
      if (viewMonth < 1) {
        viewMonth = 12;
        viewYear -= 1;
      } else if (viewMonth > 12) {
        viewMonth = 1;
        viewYear += 1;
      }
      paint();
      return;
    }
    const day = event.target.closest?.("[data-date]");
    if (!day || day.disabled) return;
    input.value = day.dataset.date;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    field.dataset.value = day.dataset.date;
    const label = qs("[data-date-label]", field);
    if (label) label.textContent = day.dataset.date;
    closeActive();
  });

  paint();
  return box;
}

function bindOne(input) {
  if (input.dataset.enhanced === "1") {
    const label = input.closest(".date-field")?.querySelector("[data-date-label]");
    if (label && label.textContent !== input.value) label.textContent = input.value;
    return;
  }
  input.dataset.enhanced = "1";
  input.classList.add("date-native");

  const field = document.createElement("div");
  field.className = "date-field";
  field.dataset.dateField = input.id || "date";
  field.dataset.value = input.value || "";

  const button = document.createElement("button");
  button.type = "button";
  button.className = "date-field-btn";
  button.innerHTML = `<span data-date-label>${escapeHtml(input.value || "")}</span>`;
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (active?.__for === field) {
      closeActive();
      return;
    }
    const box = buildPopover(field, input, { min: input.min, max: input.max });
    if (box) box.__for = field;
  });

  input.insertAdjacentElement("afterend", field);
  field.appendChild(button);
  field.appendChild(input);
}

/** 给 root 里每个原生日期输入框配一个自绘弹层按钮（可重复调用）。 */
export function enhanceDateInputs(root) {
  if (!root) return;
  const inputs = root.querySelectorAll('input[type="date"]');
  inputs.forEach((input) => bindOne(input));
}

document.addEventListener("click", (event) => {
  if (!active) return;
  if (active.contains(event.target)) return;
  if (event.target.closest?.(".date-field")) return;
  closeActive();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeActive();
});

// 不用 resize 关弹层：headless/窗口抖动会把它误关；点外面或 Esc 足够。
