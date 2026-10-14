/* ==========================================================================
   管理后台入口：多重密码登录、路由、分页列表壳
   仅 PC 端使用（布局 min-width: 1024px）
   ========================================================================== */

import { adminApi, ApiError } from "/shared/api.js";
import { detectLang, getLang, loadLang, setLang, t, translateError } from "/shared/i18n.js";
import { applyTheme, escapeHtml, qs, qsa, storedTheme, toastErr, toastOk } from "/shared/ui.js";

import { context } from "./state.js";
import { renderDashboardPage } from "./pages/dashboard.js";
import { renderLogsPage } from "./pages/logs.js";
import { renderRequestsPage } from "./pages/requests.js";
import { renderUserDetailPage } from "./pages/userDetail.js";
import { renderUsersPage } from "./pages/users.js";

const toastOkT = toastOk;

const ROUTES = {
  dashboard: renderDashboardPage,
  users: renderUsersPage,
  user: renderUserDetailPage,
  requests: renderRequestsPage,
  logs: renderLogsPage,
};

const NAV_ROUTE = { user: "users" };

let renderToken = 0;

/* ------------------------------ 视图切换 ------------------------------ */

function showLogin() {
  qs("#view-login").classList.remove("hidden");
  qs("#view-admin").classList.add("hidden");
}

function showConsole() {
  qs("#view-login").classList.add("hidden");
  qs("#view-admin").classList.remove("hidden");
}

/* ------------------------------ 语言 / 主题 ------------------------------ */

function applyStaticText(root = document) {
  qsa("[data-i18n]", root).forEach((node) => {
    node.textContent = t(node.getAttribute("data-i18n"));
  });
  qsa("[data-i18n-placeholder]", root).forEach((node) => {
    node.setAttribute("placeholder", t(node.getAttribute("data-i18n-placeholder")));
  });
}

function syncLangButtons() {
  const lang = getLang();
  qsa(".lang-switch button").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-lang") === lang);
  });
}

function syncThemeButtons() {
  const theme = document.documentElement.getAttribute("data-theme");
  qsa("#login-theme, #top-theme").forEach((btn) => {
    if (btn) btn.textContent = theme === "light" ? "☀" : "🌙";
  });
}

function setAdminName() {
  const ctx = context();
  const node = qs("#admin-name");
  if (node) node.textContent = (ctx.admin && ctx.admin.username) || "-";
}

/** 登录后把账号里的语言 / 主题偏好应用到本地界面 */
function applyAccountPrefs(admin) {
  if (!admin) return;
  if (admin.language) setLang(admin.language);
  const theme = admin.theme === "light" ? "light" : admin.theme === "dark" ? "dark" : storedTheme();
  applyTheme(theme, { persist: true });
}

async function changeLanguage(lang) {
  setLang(lang);
  syncLangButtons();
  applyStaticText();
  await reload();
}

async function changeTheme(theme) {
  applyTheme(theme, { persist: true });
  syncThemeButtons();
  await reload();
}

async function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  await changeTheme(next);
}

/* ------------------------------ 路由 ------------------------------ */

function parseHash() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "");
  const [route, id] = raw.split("/");
  return { route: ROUTES[route] ? route : "dashboard", id: id || null };
}

function crumbFor(route) {
  if (route === "user") return t("admin.userDetail");
  if (route === "users") return t("admin.navUsers");
  if (route === "requests") return t("admin.navRequests");
  if (route === "logs") return t("admin.navLogs");
  return t("nav.dashboard");
}

function highlightNav(route) {
  const target = NAV_ROUTE[route] || route;
  qsa("#nav .nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-route") === target);
  });
}

export function navigate(route, id) {
  const path = id ? `${route}/${id}` : route;
  if (window.location.hash === `#/${path}`) {
    renderRoute();
    return;
  }
  window.location.hash = `#/${path}`;
}

function applyFilterParams(route, params) {
  if (!params) return;
  navigate(route);
}

export async function renderRoute() {
  const ctx = context();
  if (!ctx.admin) return;

  const view = qs("#route-view");
  if (!view) return;

  const { route, id } = parseHash();
  ctx.setRoute(route, id);

  const crumb = qs("#crumb");
  if (crumb) crumb.textContent = crumbFor(route);
  highlightNav(route);
  document.title = `${crumbFor(route)} · ${t("app.adminTitle")}`;

  const renderer = ROUTES[route] || ROUTES.dashboard;
  const token = ++renderToken;
  view.innerHTML = `<div class="loading">${escapeHtml(t("common.loading"))}</div>`;

  try {
    await renderer(view, {
      ctx,
      navigate,
      render: renderRoute,
      reload: reload,
      isStale: () => token !== renderToken,
      changeLanguage,
      changeTheme,
      logout,
      api: adminApi,
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      handleAuthLost();
      return;
    }
    view.innerHTML = `<div class="admin-panel"><span class="inline-error show">${escapeHtml(
      err instanceof Error ? err.message : translateError(null)
    )}</span></div>`;
  }
}

/** 强制重渲染当前路由（语言/主题切换、操作完成后刷新） */
export function reload() {
  return renderRoute();
}

function handleAuthLost() {
  const ctx = context();
  ctx.setAdmin(null);
  showLogin();
  toastErr(t("error.not_authenticated"));
}

function handleHashChange() {
  const ctx = context();
  if (!ctx.admin) return;
  renderRoute();
}

/* ------------------------------ 登录 / 登出 ------------------------------ */

async function renderPartFields() {
  const box = qs("#parts-box");
  const note = qs("#parts-note");
  if (!box) return 3;

  let parts = 3;
  try {
    const data = await adminApi.parts();
    const value = Number(data && data.parts);
    if (Number.isFinite(value) && value > 0) parts = value;
  } catch (err) {
    /* 取不到段数时退回默认 3 段 */
  }

  const fields = [];
  for (let index = 1; index <= parts; index += 1) {
    const label = index === 1 ? t("adminLogin.part1") : t("adminLogin.partN", { n: index });
    fields.push(`
      <div class="field">
        <label for="login-part-${index}">${escapeHtml(label)}</label>
        <input id="login-part-${index}" class="input" type="password" maxlength="72"
               data-part="${index}" autocomplete="off" required />
      </div>`);
  }
  box.innerHTML = fields.join("");
  if (note) note.textContent = t("adminLogin.partsHint");
  return parts;
}

async function doLogin(event) {
  event.preventDefault();
  const errorBox = qs("#login-error");
  const submit = qs("#login-submit");
  if (errorBox) errorBox.classList.remove("show");
  if (submit) submit.disabled = true;

  const parts = qsa("#parts-box input[data-part]");
  const payload = {
    username: (qs("#login-username")?.value || "").trim(),
    passwords: parts.map((node) => node.value),
    remember: !!qs("#login-remember")?.checked,
  };

  if (!payload.username || payload.passwords.some((value) => !value)) {
    if (errorBox) {
      errorBox.textContent = t("error.invalid_input");
      errorBox.classList.add("show");
    }
    if (submit) submit.disabled = false;
    return;
  }

  try {
    const admin = await adminApi.login(payload);
    await afterLogin(admin);
    toastOkT(t("login.success"));
  } catch (err) {
    if (errorBox) {
      errorBox.textContent = err instanceof Error ? err.message : translateError(null);
      errorBox.classList.add("show");
    }
  } finally {
    if (submit) submit.disabled = false;
  }
}

export async function afterLogin(admin) {
  const ctx = context();
  ctx.setAdmin(admin);
  applyAccountPrefs(admin);
  syncLangButtons();
  syncThemeButtons();
  applyStaticText();
  setAdminName();
  showConsole();
  // 统计信息同时用于概览与列表分页
  try {
    const stats = await adminApi.stats();
    const months = Number(stats && stats.retention_months);
    ctx.setMeta({ stats, retention_months: Number.isFinite(months) && months > 0 ? months : 6 });
  } catch (err) {
    /* 概览页会自行重试 */
  }
  try {
    const data = await adminApi.parts();
    const parts = Number(data && data.parts);
    if (Number.isFinite(parts) && parts > 0) ctx.setMeta({ adminParts: parts });
  } catch (err) {
    /* 创建管理员时按默认段数处理 */
  }
  await renderRoute();
}

async function logout() {
  try {
    await adminApi.logout();
  } catch (err) {
    /* 即使请求失败也回到登录页 */
  }
  const ctx = context();
  ctx.setAdmin(null);
  const form = qs("#login-form");
  if (form) form.reset();
  showLogin();
}

/* ------------------------------ 启动 ------------------------------ */

function bindChrome() {
  qs("#login-form")?.addEventListener("submit", doLogin);
  qs("#logout-btn")?.addEventListener("click", logout);

  qsa("#nav .nav-item").forEach((btn) => {
    btn.addEventListener("click", () => navigate(btn.getAttribute("data-route")));
  });

  qs("#login-theme")?.addEventListener("click", toggleTheme);
  qs("#top-theme")?.addEventListener("click", toggleTheme);

  qsa("#app-lang button, #login-lang button").forEach((btn) => {
    btn.addEventListener("click", () => changeLanguage(btn.getAttribute("data-lang")));
  });

  window.addEventListener("hashchange", handleHashChange);
}

async function bootstrap() {
  loadLang();
  detectLang();
  applyTheme(storedTheme(), { persist: false });
  applyStaticText();
  syncLangButtons();
  syncThemeButtons();
  bindChrome();
  await renderPartFields();

  try {
    const admin = await adminApi.me();
    if (admin && admin.is_admin) {
      await afterLogin(admin);
      return;
    }
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) {
      toastErr(err instanceof Error ? err.message : translateError(null));
    }
  }
  showLogin();
}

export { t, translateError };
bootstrap();
