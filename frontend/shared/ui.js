/* ==========================================================================
   共享 UI 小工具：主题、提示条、弹窗、分页、转义
   ========================================================================== */

import { t } from "./i18n.js";

/* ------------------------------- 主题 ------------------------------- */

const THEME_KEY = "ssp.theme";

export function normalizeTheme(value) {
  return value === "light" ? "light" : "dark";
}

export function applyTheme(theme, { persist = true } = {}) {
  const value = normalizeTheme(theme);
  document.documentElement.setAttribute("data-theme", value);
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, value);
    } catch (err) {
      /* 忽略 localStorage 不可用 */
    }
  }
  return value;
}

export function storedTheme(fallback = "dark") {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value ? normalizeTheme(value) : normalizeTheme(fallback);
  } catch (err) {
    return normalizeTheme(fallback);
  }
}

/* ------------------------------- 转义 ------------------------------- */

export function escapeHtml(value) {
  if (value === undefined || value === null) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ------------------------------ 提示条 ------------------------------ */

function toastHost() {
  let host = document.getElementById("toast-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "toast-host";
    document.body.appendChild(host);
  }
  return host;
}

export function toast(message, kind = "info", timeout = 2600) {
  if (!message) return;
  const node = document.createElement("div");
  node.className = `toast ${kind === "error" ? "err" : kind === "success" ? "ok" : ""}`;
  node.textContent = message;
  toastHost().appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity 200ms ease";
    setTimeout(() => node.remove(), 220);
  }, timeout);
}

export const toastOk = (message) => toast(message, "success");
export const toastErr = (message) => toast(message, "error");

/* ------------------------------- 弹窗 ------------------------------- */

/**
 * 通用弹窗。
 * @param {object} options
 * @param {string} options.title   标题（已翻译）
 * @param {string} options.bodyHtml 主体 HTML（调用方负责转义）
 * @param {string|null} [options.okText] 传 null 表示不渲染主按钮（底部只留「取消/关闭」）
 * @param {string} [options.cancelText]
 * @param {boolean} [options.danger]
 * @param {Function} [options.onMount] 挂载后回调 (root, close)
 * @returns {Promise<any>} 确认时 resolve(true) 或 onMount 自定义值；取消时 resolve(false)
 */
export function openModal(options) {
  const {
    title = "",
    bodyHtml = "",
    okText = t("common.confirm"),
    cancelText = t("common.cancel"),
    danger = false,
    onMount = null,
    wide = false,
  } = options || {};

  return new Promise((resolve) => {
    const host = document.createElement("div");
    host.className = "modal-host";
    host.innerHTML = `
      <div class="modal" ${wide ? 'style="width:min(760px,100%)"' : ""} role="dialog" aria-modal="true">
        <div class="spread" style="margin-bottom:12px">
          <h2>${escapeHtml(title)}</h2>
          <button class="icon-btn" data-role="close" title="${escapeHtml(t("common.close"))}">✕</button>
        </div>
        <div data-role="body">${bodyHtml}</div>
        <div class="modal-foot">
          <button class="btn" data-role="cancel">${escapeHtml(cancelText)}</button>
          ${
            okText === null
              ? ""
              : `<button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-role="ok">${escapeHtml(okText)}</button>`
          }
        </div>
      </div>`;

    const close = (value) => {
      host.remove();
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close(false);
    };

    host.addEventListener("click", (event) => {
      if (event.target === host) close(false);
      const role = event.target.closest("[data-role]")?.getAttribute("data-role");
      if (role === "close" || role === "cancel") close(false);
      if (role === "ok") close(true);
    });
    document.addEventListener("keydown", onKey);

    document.body.appendChild(host);
    if (typeof onMount === "function") onMount(host, close);
    const firstInput = host.querySelector("input, select, textarea");
    if (firstInput) firstInput.focus();
  });
}

export async function confirmDialog(options) {
  const result = await openModal({
    okText: t("common.confirm"),
    danger: Boolean(options && options.danger),
    ...options,
  });
  return result === true;
}

/* ------------------------------- 分页 ------------------------------- */

/** 分页控件渲染器；onChange(page) 由调用方重新拉数据 */
export function renderPager(container, { page, pages, total, pageSize, onChange }) {
  if (!container) return;
  const safePages = Math.max(1, pages || 1);
  container.innerHTML = `
    <span class="pages">${escapeHtml(
      t("common.pageInfo", { page, pages: safePages, total: total || 0 })
    )}</span>
    <button class="btn btn-sm" data-role="prev" ${page <= 1 ? "disabled" : ""}>${escapeHtml(t("common.prev"))}</button>
    <button class="btn btn-sm" data-role="next" ${page >= safePages ? "disabled" : ""}>${escapeHtml(t("common.next"))}</button>`;

  const jump = (target) => {
    if (target < 1 || target > safePages || target === page) return;
    onChange(target);
  };
  container.querySelector('[data-role="prev"]')?.addEventListener("click", () => jump(page - 1));
  container.querySelector('[data-role="next"]')?.addEventListener("click", () => jump(page + 1));
}

/* ------------------------------ 小工具 ------------------------------ */

export function fmtDateTime(value) {
  if (!value) return t("common.none");
  const date = new Date(value.endsWith && value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

export function fmtDuration(minutes) {
  const total = Number(minutes) || 0;
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return `${hours}h ${String(mins).padStart(2, "0")}m`;
}

export function fmtShift(minutes) {
  const value = Number(minutes) || 0;
  if (value === 0) return t("advice.shiftNone");
  const sign = value > 0 ? "+" : "-";
  const abs = Math.abs(value);
  const hours = Math.floor(abs / 60);
  const mins = abs % 60;
  const parts = [];
  if (hours) parts.push(`${hours}h`);
  if (mins) parts.push(`${mins}m`);
  return `${sign}${parts.join(" ")}`;
}

export function debounce(fn, wait = 260) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function qs(selector, root = document) {
  return root.querySelector(selector);
}

export function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}
