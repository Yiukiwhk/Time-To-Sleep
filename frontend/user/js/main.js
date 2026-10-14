/* ==========================================================================
   用户端入口：状态、路由、登录/登出、语言与主题
   ========================================================================== */

import {
  accountApi,
  ApiError,
  authApi,
  settingsApi,
} from "/shared/api.js";
import {
  DICTS,
  detectLang,
  getLang,
  loadLang,
  setLang,
  t,
  translateError,
} from "/shared/i18n.js";
import { applyTheme, escapeHtml, qs, qsa, storedTheme, toastErr, toastOk } from "/shared/ui.js";
import { renderAdvicePage } from "./pages/advice.js";
import { renderRecordsPage } from "./pages/records.js";
import { renderSettingsPage } from "./pages/settings.js";
import { openReferencesModal } from "./references-modal.js";

export const state = {
  user: null,
  route: "records",
  meta: null,
};

/* ------------------------------ 视图切换 ------------------------------ */

function showLogin() {
  qs("#view-login").classList.remove("hidden");
  qs("#view-app").classList.add("hidden");
  closeSidebar();
  const username = qs("#login-username");
  if (username) username.focus();
}

function showApp() {
  qs("#view-login").classList.add("hidden");
  qs("#view-app").classList.remove("hidden");
}

function closeSidebar() {
  qs("#sidebar")?.classList.remove("open");
  qs("#sidebar-backdrop")?.classList.add("hidden");
}

function openSidebar() {
  qs("#sidebar")?.classList.add("open");
  qs("#sidebar-backdrop")?.classList.remove("hidden");
}

/* ------------------------------ 语言 / 主题 ------------------------------ */

/** 把静态 HTML 上带 data-i18n 的节点全部翻译一遍 */
export function applyStaticText(root = document) {
  qsa("[data-i18n]", root).forEach((node) => {
    node.textContent = t(node.getAttribute("data-i18n"));
  });
  qsa("[data-i18n-placeholder]", root).forEach((node) => {
    node.setAttribute("placeholder", t(node.getAttribute("data-i18n-placeholder")));
  });
  qsa("[data-i18n-title]", root).forEach((node) => {
    node.setAttribute("title", t(node.getAttribute("data-i18n-title")));
  });
}

function syncLangButtons() {
  const lang = getLang();
  qsa(".lang-switch button").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-lang") === lang);
  });
  document.title = t("app.userTitle");
}

function syncThemeButtons() {
  const theme = document.documentElement.getAttribute("data-theme");
  qsa("#app-theme, #login-theme, #top-theme").forEach((btn) => {
    if (btn) btn.textContent = theme === "light" ? "☀" : "🌙";
  });
}

export function applyThemeAndSync(theme, { persist = true } = {}) {
  applyTheme(theme, { persist });
  syncThemeButtons();
}

async function changeLanguage(lang, { persistRemote = true } = {}) {
  setLang(lang);
  syncLangButtons();
  applyStaticText();
  renderUserChip();
  renderRoute();
  if (persistRemote && state.user) {
    try {
      await settingsApi.update({ language: lang });
    } catch (err) {
      /* 语言偏好写入失败不阻塞使用（本地已生效） */
    }
  }
}

async function changeTheme(theme, { persistRemote = true } = {}) {
  applyThemeAndSync(theme);
  renderRoute();
  if (persistRemote && state.user) {
    try {
      await settingsApi.update({ theme });
    } catch (err) {
      /* 同上 */
    }
  }
}

/* ------------------------------ 用户信息 ------------------------------ */

function renderUserChip() {
  if (!state.user) return;
  const name = state.user.username || "-";
  const avatar = qs("#user-avatar");
  if (avatar) avatar.textContent = name.slice(0, 1).toUpperCase();
  const nameNode = qs("#user-name");
  if (nameNode) nameNode.textContent = name;
  const roleNode = qs("#user-role");
  if (roleNode) {
    roleNode.textContent = state.user.is_admin ? t("settings.role.admin") : t("settings.role.user");
  }
}

/* ------------------------------ 路由 ------------------------------ */

const ROUTES = {
  records: renderRecordsPage,
  advice: renderAdvicePage,
  settings: renderSettingsPage,
};

export function navigate(route, { push = true } = {}) {
  const target = ROUTES[route] ? route : "records";
  state.route = target;
  if (push) {
    if (window.location.hash !== `#/${target}`) window.location.hash = `#/${target}`;
  }
  qsa("#nav .nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-route") === target);
  });
  if (state.user) renderRoute();
  closeSidebar();
}

let renderToken = 0;

export async function renderRoute() {
  if (!state.user) return;
  const view = qs("#route-view");
  if (!view) return;
  const renderer = ROUTES[state.route] || ROUTES.records;
  const token = ++renderToken;
  try {
    await renderer(view, {
      state,
      navigate,
      reload: () => renderRoute(),
      isStale: () => token !== renderToken,
      changeLanguage,
      changeTheme,
      logout,
      updateUser: (patch) => {
        state.user = { ...state.user, ...patch };
        renderUserChip();
      },
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      handleAuthLost();
      return;
    }
    view.innerHTML = `<div class="card"><p class="inline-error show">${escapeHtml(
      err instanceof Error ? err.message : translateError(null)
    )}</p></div>`;
  }
}

function handleAuthLost() {
  state.user = null;
  showLogin();
  toastErr(t("error.not_authenticated"));
}

/* ------------------------------ 登录 / 登出 ------------------------------ */

async function doLogin(event) {
  event.preventDefault();
  const errorBox = qs("#login-error");
  const submit = qs("#login-submit");
  errorBox.classList.remove("show");
  submit.disabled = true;
  const original = submit.textContent;
  submit.textContent = t("login.submitting");
  try {
    const user = await authApi.login({
      username: qs("#login-username").value.trim(),
      password: qs("#login-password").value,
      remember: qs("#login-remember").checked,
    });
    await afterLogin(user);
    toastOk(t("login.success"));
  } catch (err) {
    errorBox.textContent = err instanceof Error ? err.message : translateError(null);
    errorBox.classList.add("show");
  } finally {
    submit.disabled = false;
    submit.textContent = original || t("login.submit");
  }
}

export async function afterLogin(user) {
  state.user = user;
  setLang(user.language || getLang());
  syncLangButtons();
  applyThemeAndSync(user.theme || storedTheme(), { persist: true });
  applyStaticText();
  renderUserChip();
  showApp();
  const route = (window.location.hash || "").replace(/^#\/?/, "");
  navigate(ROUTES[route] ? route : "records", { push: true });
}

async function logout() {
  try {
    await authApi.logout();
  } catch (err) {
    /* 即使请求失败也回到登录页 */
  }
  state.user = null;
  qs("#login-form")?.reset();
  showLogin();
}

/* ------------------------------ 启动 ------------------------------ */

function bindChrome() {
  qs("#login-form")?.addEventListener("submit", doLogin);
  qs("#logout-btn")?.addEventListener("click", logout);
  qs("#menu-btn")?.addEventListener("click", openSidebar);
  qs("#sidebar-backdrop")?.addEventListener("click", closeSidebar);

  qsa("#nav .nav-item").forEach((btn) => {
    btn.addEventListener("click", () => navigate(btn.getAttribute("data-route")));
  });

  // 页脚"参考文献"入口：与路由无关，任何页面都能打开
  qs("#references-open")?.addEventListener("click", () => openReferencesModal());

  const toggleTheme = () => {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    changeTheme(next);
  };
  [qs("#app-theme"), qs("#login-theme"), qs("#top-theme")].forEach((btn) => {
    btn?.addEventListener("click", toggleTheme);
  });

  const bindLang = (rootId) => {
    qsa(`#${rootId} button`).forEach((btn) => {
      btn.addEventListener("click", () => changeLanguage(btn.getAttribute("data-lang")));
    });
  };
  bindLang("app-lang");
  bindLang("login-lang");

  window.addEventListener("hashchange", () => {
    const route = (window.location.hash || "").replace(/^#\/?/, "");
    if (ROUTES[route] && route !== state.route) {
      navigate(route, { push: false });
    }
  });
}

async function bootstrap() {
  loadLang();
  detectLang();
  applyTheme(storedTheme(), { persist: false });
  applyStaticText();
  syncLangButtons();
  syncThemeButtons();
  bindChrome();

  try {
    const user = await authApi.me();
    if (user && !user.is_admin) {
      await afterLogin(user);
      return;
    }
    if (user && user.is_admin) {
      // 管理员账号不能进入用户端（后端已拒绝普通登录），直接引导到管理后台
      state.user = null;
      showLogin();
      const box = qs("#login-error");
      box.textContent = t("error.use_admin_login");
      box.classList.add("show");
      const link = document.createElement("p");
      link.className = "auth-switch";
      link.innerHTML = `<a href="/admin/">${escapeHtml(t("adminLogin.title"))} →</a>`;
      box.after(link);
      return;
    }
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) {
      toastErr(err instanceof Error ? err.message : translateError(null));
    }
  }
  showLogin();
}

export { changeLanguage, changeTheme, logout, t, translateError, DICTS };
export const lang = { get: getLang, set: setLang };

bootstrap();
