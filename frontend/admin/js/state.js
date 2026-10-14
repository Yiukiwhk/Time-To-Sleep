/* ==========================================================================
   管理后台 · 数据状态与共享视图工具
   页面模块统一从 context() 取状态，避免模块级可变引用
   ========================================================================== */

import { getLang, t } from "/shared/i18n.js";
import { escapeHtml, fmtDateTime, renderPager, qs, qsa } from "/shared/ui.js";

const store = {
  admin: null,
  route: "dashboard",
  routeId: null,
  params: {},
  meta: { stats: null, retention_months: 6, pageSize: 20, adminParts: 3 },
  /** 列表页返回时的现场快照：{ users: {...}, requests: {...}, logs: {...} } */
  listState: {},
  renderToken: 0,
};

export function context() {
  return {
    get admin() {
      return store.admin;
    },
    get route() {
      return store.route;
    },
    get routeId() {
      return store.routeId;
    },
    get params() {
      return store.params;
    },
    get meta() {
      return store.meta;
    },
    setAdmin(admin) {
      store.admin = admin;
    },
    setRoute(route, id) {
      store.route = route;
      store.routeId = id || null;
    },
    setParams(patch) {
      store.params = { ...store.params, ...(patch || {}) };
    },
    setMeta(patch) {
      store.meta = { ...store.meta, ...(patch || {}) };
    },
    nextToken() {
      store.renderToken += 1;
      return store.renderToken;
    },
    isStale(token) {
      return token !== store.renderToken;
    },
    listState(key) {
      return store.listState[key] || null;
    },
    setListState(key, value) {
      store.listState[key] = value;
    },
  };
}

export function clearListState(key) {
  if (key) delete store.listState[key];
  else store.listState = {};
}

/* ------------------------------ 基础片段 ------------------------------ */

export function loadingBlock(message) {
  return `<div class="loading">${escapeHtml(message || t("common.loading"))}</div>`;
}

export function emptyBlock(message) {
  return `<div class="empty">${escapeHtml(message || t("common.empty"))}</div>`;
}

export function errorBlock(message) {
  return `<div class="empty"><span class="inline-error show">${escapeHtml(message)}</span></div>`;
}

/** 后端时间戳是本地无时区的 UTC 字符串，交给 shared/ui.js 统一处理 */
export function fmtDate(value) {
  return fmtDateTime(value);
}

/** 纯日期（YYYY-MM-DD）直接显示 */
export function fmtDay(value) {
  if (!value) return "-";
  return String(value);
}

export function fmtClock(value) {
  if (!value) return "-";
  const text = String(value);
  const date = new Date(text.endsWith("Z") ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return text;
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function emptyCell(value) {
  const text = value === null || value === undefined || value === "" ? "-" : String(value);
  return escapeHtml(text);
}

/* ------------------------------ 表格 ------------------------------ */

/**
 * 渲染一张数据表。
 * @param {object} options
 *  - columns: [{ key, label, className, cell(row), raw(row) }]
 *  - rows: 数据数组
 *  - rowKey: (row) => 唯一键
 *  - selectable: 是否显示复选框列
 *  - selected: Set<string>
 *  - onToggle: (key, checked, row) => void
 *  - onToggleAll: (checked) => void
 *  - empty: 空数据提示
 */
export function renderTable(options) {
  const {
    columns = [],
    rows = [],
    rowKey = (row) => String(row.id),
    selectable = false,
    selected = null,
    empty = t("common.empty"),
  } = options;

  if (!rows.length) return emptyBlock(empty);

  const allChecked =
    selectable && selected && rows.length > 0 && rows.every((row) => selected.has(rowKey(row)));

  const head = columns
    .map((col) => `<th class="${col.className || ""}">${escapeHtml(col.label)}</th>`)
    .join("");

  const body = rows
    .map((row, index) => {
      const key = rowKey(row);
      const cells = columns
        .map((col) => {
          const content = typeof col.cell === "function" ? col.cell(row, index) : emptyCell(row[col.key]);
          return `<td class="${col.className || ""}">${content}</td>`;
        })
        .join("");
      const check = selectable
        ? `<td class="select-col"><input type="checkbox" data-row-key="${escapeHtml(key)}"${
            selected && selected.has(key) ? " checked" : ""
          } /></td>`
        : "";
      return `<tr data-key="${escapeHtml(key)}">${check}${cells}</tr>`;
    })
    .join("");

  const headCheck = selectable
    ? `<th class="select-col"><input type="checkbox" data-check-all${allChecked ? " checked" : ""} /></th>`
    : "";

  return `
    <div class="admin-table-wrap">
      <table class="admin-table">
        <thead><tr>${headCheck}${head}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

/** 表格渲染后统一绑定复选框事件 */
export function bindTable(root, options = {}) {
  const { rowKey = (row) => String(row.id), rows = [], onToggle, onToggleAll } = options;
  const byKey = new Map(rows.map((row) => [rowKey(row), row]));

  qsa("tbody input[data-row-key]", root).forEach((box) => {
    box.addEventListener("change", () => {
      if (typeof onToggle === "function") {
        onToggle(box.getAttribute("data-row-key"), box.checked, byKey.get(box.getAttribute("data-row-key")));
      }
    });
  });

  const all = qs("thead input[data-check-all]", root);
  if (all) {
    all.addEventListener("change", () => {
      if (typeof onToggleAll === "function") onToggleAll(all.checked);
    });
  }
}

/**
 * 渲染分页器到指定容器（复用 shared/ui.js 的 renderPager）。
 * @param {HTMLElement} container
 * @param {{page:number,pages:number,total:number,page_size:number}} data
 * @param {(page:number)=>void} onChange
 */
export function renderAdminPager(container, data, onChange) {
  if (!container) return;
  renderPager(container, {
    page: data.page,
    pages: data.pages,
    total: data.total,
    pageSize: data.page_size,
    onChange,
  });
  container.classList.add("admin-pager");
}

/* ------------------------------ 表单读取 ------------------------------ */

export function readValue(root, selector) {
  const node = qs(selector, root);
  return node ? String(node.value || "").trim() : "";
}

export function readChecked(root, selector) {
  const node = qs(selector, root);
  return !!(node && node.checked);
}

export function fillSelect(node, items, current) {
  if (!node) return;
  node.innerHTML = items
    .map(
      (item) =>
        `<option value="${escapeHtml(String(item.value))}"${
          String(item.value) === String(current) ? " selected" : ""
        }>${escapeHtml(item.label)}</option>`
    )
    .join("");
}

/** 统一的 busy 包装：禁用按钮 -> 执行 -> 恢复 */
export async function withBusy(button, task) {
  if (!button) return task();
  const original = button.textContent;
  button.disabled = true;
  button.textContent = t("common.loading");
  try {
    return await task();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

/** 状态徽标 */
export function statusBadge(user) {
  if (user.locked) {
    return `<span class="badge" style="background:rgba(240,177,55,.15);color:var(--warn)">${escapeHtml(
      t("admin.status.locked")
    )}</span>`;
  }
  if (!user.is_active) {
    return `<span class="badge" style="background:rgba(242,85,90,.15);color:var(--danger)">${escapeHtml(
      t("admin.status.disabled")
    )}</span>`;
  }
  return `<span class="badge" style="background:rgba(53,196,141,.15);color:var(--ok)">${escapeHtml(
    t("admin.status.active")
  )}</span>`;
}

export function roleBadge(user) {
  return user.is_admin
    ? `<span class="badge badge-accent">${escapeHtml(t("admin.role.admin"))}</span>`
    : `<span class="badge">${escapeHtml(t("admin.role.user"))}</span>`;
}

export function levelBadge(level) {
  return `<span class="log-level ${escapeHtml(String(level || "INFO"))}">${escapeHtml(String(level || ""))}</span>`;
}

export { getLang };
