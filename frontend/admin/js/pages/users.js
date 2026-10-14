/* ==========================================================================
   用户列表页：搜索 / 角色与状态筛选 / 分页 / 创建账号
   ========================================================================== */

import { t } from "/shared/i18n.js";
import { debounce, openModal, qs, toastErr, toastOk } from "/shared/ui.js";
import {
  bindTable,
  emptyBlock,
  errorBlock,
  fillSelect,
  loadingBlock,
  readChecked,
  readValue,
  renderAdminPager,
  renderTable,
  roleBadge,
  statusBadge,
} from "../state.js";

const toastOkT = toastOk;

const DEFAULT_STATE = {
  q: "",
  is_admin: "",
  is_active: "",
  page: 1,
  data: null,
  loading: false,
  error: "",
};

export async function renderUsersPage(view, { ctx, navigate, api, isStale }) {
  const store = ctx.listState("users") || { ...DEFAULT_STATE };
  ctx.setListState("users", store);
  store.pageSize = ctx.meta.pageSize;

  view.innerHTML = `
    <header class="admin-page-head">
      <div>
        <h1>${t("admin.navUsers")}</h1>
        <p>${t("admin.paginationHint", { size: store.pageSize })}</p>
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn btn-primary" id="create-user">${t("admin.createUser")}</button>
    </header>

    <div class="admin-toolbar">
      <div class="field" style="min-width: 220px">
        <label for="u-q">${t("admin.searchUser")}</label>
        <input id="u-q" class="input" type="search" maxlength="32"
               placeholder="${t("admin.searchUserPlaceholder")}" value="${store.q}" />
      </div>
      <div class="field">
        <label for="u-role">${t("admin.filterRole")}</label>
        <select id="u-role" class="select"></select>
      </div>
      <div class="field">
        <label for="u-status">${t("admin.filterStatus")}</label>
        <select id="u-status" class="select"></select>
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn btn-sm" id="u-reset">${t("common.reset")}</button>
      <button type="button" class="btn btn-sm" id="u-refresh">${t("common.refresh")}</button>
    </div>

    <div id="u-list">${loadingBlock()}</div>
    <div id="u-pager" class="pager admin-pager"></div>`;

  fillSelect(qs("#u-role", view), [
    { value: "", label: t("admin.roleFilterAll") },
    { value: "1", label: t("admin.role.admin") },
    { value: "0", label: t("admin.role.user") },
  ], store.is_admin);

  fillSelect(qs("#u-status", view), [
    { value: "", label: t("admin.statusFilterAll") },
    { value: "1", label: t("admin.status.active") },
    { value: "0", label: t("admin.status.disabled") },
  ], store.is_active);

  async function load() {
    const list = qs("#u-list", view);
    if (!list) return;
    store.loading = true;
    if (!store.data) list.innerHTML = loadingBlock();
    const params = { page: store.page, page_size: store.pageSize };
    if (store.q) params.q = store.q;
    if (store.is_admin !== "") params.is_admin = store.is_admin;
    if (store.is_active !== "") params.is_active = store.is_active;

    try {
      const data = await api.users(params);
      if (isStale()) return;
      store.data = data;
      store.error = "";
      store.loading = false;
      paint();
    } catch (err) {
      if (isStale()) return;
      store.loading = false;
      store.error = err instanceof Error ? err.message : t("error.server");
      list.innerHTML = errorBlock(store.error);
    }
  }

  function paint() {
    const list = qs("#u-list", view);
    const pagerHost = qs("#u-pager", view);
    if (!list) return;
    const data = store.data;
    if (!data) {
      list.innerHTML = emptyBlock();
      return;
    }

    const rows = data.items || [];
    if (!rows.length) {
      list.innerHTML = emptyBlock();
      if (pagerHost) pagerHost.innerHTML = "";
      return;
    }

    list.innerHTML = renderTable({
      columns: [
        {
          key: "username",
          label: t("admin.col.username"),
          cell: (row) => `<strong>${row.username || "-"}</strong>`,
        },
        { key: "role", label: t("admin.col.role"), cell: (row) => roleBadge(row) },
        { key: "status", label: t("admin.col.status"), cell: (row) => statusBadge(row) },
        {
          key: "record_count",
          label: t("admin.col.records"),
          className: "num",
          cell: (row) => escapeNumber(row.record_count),
        },
        {
          key: "created_at",
          label: t("admin.col.createdAt"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${fmtShort(row.created_at)}</span>`,
        },
        {
          key: "last_login_at",
          label: t("admin.col.lastLogin"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${fmtShort(row.last_login_at)}</span>`,
        },
        {
          key: "actions",
          label: t("common.actions"),
          className: "narrow",
          cell: (row) => `
            <div class="actions">
              <button class="btn" data-action="detail" data-id="${row.id}">${t("common.detail")}</button>
              <button class="btn btn-ghost" data-action="records" data-id="${row.id}">${t(
                "admin.viewRecords"
              )}</button>
            </div>`,
        },
      ],
      rows,
      rowKey: (row) => String(row.id),
    });

    list.querySelectorAll("button[data-action]").forEach((btn) => {
      btn.addEventListener("click", () => navigate("user", btn.getAttribute("data-id")));
    });

    renderAdminPager(pagerHost, data, (page) => {
      store.page = page;
      load();
    });
  }

  // 事件绑定
  const search = qs("#u-q", view);
  search?.addEventListener(
    "input",
    debounce(() => {
      store.q = search.value.trim();
      store.page = 1;
      load();
    }, 320)
  );

  qs("#u-role", view)?.addEventListener("change", (event) => {
    store.is_admin = event.target.value;
    store.page = 1;
    load();
  });

  qs("#u-status", view)?.addEventListener("change", (event) => {
    store.is_active = event.target.value;
    store.page = 1;
    load();
  });

  qs("#u-reset", view)?.addEventListener("click", () => {
    store.q = "";
    store.is_admin = "";
    store.is_active = "";
    store.page = 1;
    renderUsersPage(view, { ctx, navigate, api, isStale });
  });

  qs("#u-refresh", view)?.addEventListener("click", () => load());

  qs("#create-user", view)?.addEventListener("click", () => openCreateUser(ctx, api, load));

  await load();
}

/* ------------------------------ 工具 ------------------------------ */

function escapeNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? String(num) : "0";
}

function fmtShort(value) {
  if (!value) return "-";
  const text = String(value);
  const date = new Date(text.endsWith("Z") ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return text;
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

/* ------------------------------ 创建账号 ------------------------------ */

export async function openCreateUser(ctx, api, onDone) {
  const parts = Math.max(1, Number(ctx.meta.adminParts) || 3);
  const extra = Math.max(0, parts - 1);
  const form = {
    username: "",
    password: "",
    language: "zh",
    theme: "dark",
    is_admin: false,
    admin_passwords: [],
  };

  const extraFields = Array.from({ length: extra }, (_, index) => {
    const n = index + 2;
    return `
      <div class="field">
        <label for="nu-part-${n}">${t("admin.adminPart", { n })}</label>
        <input id="nu-part-${n}" class="input" type="password" maxlength="72" data-admin-part="${n}"
               autocomplete="off" placeholder="${t("admin.partPlaceholder")}" />
      </div>`;
  }).join("");

  const confirmed = await openModal({
    title: t("admin.createUserTitle"),
    wide: true,
    okText: t("common.create"),
    bodyHtml: `
      <p class="form-note" style="margin-bottom:12px">${t("admin.createSubtitle")}</p>
      <div class="admin-form">
        <div class="field">
          <label for="nu-username">${t("field.username")} <span class="muted tiny">${t(
      "admin.usernameHint"
    )}</span></label>
          <input id="nu-username" class="input" maxlength="32" autocomplete="off"
                 placeholder="${t("admin.usernamePlaceholder")}" />
        </div>
        <div class="field">
          <label for="nu-password">${t("admin.initialPassword")} <span class="muted tiny">${t(
      "admin.passwordHint"
    )}</span></label>
          <input id="nu-password" class="input" type="password" maxlength="72" autocomplete="new-password" />
        </div>
        <div class="field">
          <label for="nu-language">${t("common.language")}</label>
          <select id="nu-language" class="select">
            <option value="zh" selected>${t("common.lang.zh")}</option>
            <option value="en">${t("common.lang.en")}</option>
          </select>
        </div>
        <div class="field">
          <label for="nu-theme">${t("common.theme")}</label>
          <select id="nu-theme" class="select">
            <option value="dark" selected>${t("common.theme.dark")}</option>
            <option value="light">${t("common.theme.light")}</option>
          </select>
        </div>
        <div class="full">
          <label class="checkbox" for="nu-admin">
            <input id="nu-admin" type="checkbox" />
            <span>${t("admin.createAsAdmin")}</span>
          </label>
        </div>
        <div class="full hidden" id="nu-admin-box">
          <div class="form-note" style="margin-bottom:8px">${t("admin.adminPartsHint", {
            n: extra,
          })}</div>
          <div class="admin-form">${extraFields}</div>
        </div>
      </div>`,
    onMount(host) {
      const adminBox = qs("#nu-admin", host);
      const adminFields = qs("#nu-admin-box", host);

      // 弹窗关闭后 DOM 会被移除，因此在挂载阶段把表单值同步到 form
      const capture = () => {
        form.username = readValue(host, "#nu-username");
        form.password = qs("#nu-password", host)?.value || "";
        form.language = readValue(host, "#nu-language") || "zh";
        form.theme = readValue(host, "#nu-theme") || "dark";
        form.is_admin = readChecked(host, "#nu-admin");
        form.admin_passwords = Array.from(host.querySelectorAll("input[data-admin-part]")).map(
          (node) => node.value
        );
      };

      host.addEventListener("input", capture);
      host.addEventListener("change", capture);

      adminBox?.addEventListener("change", () => {
        adminFields?.classList.toggle("hidden", !adminBox.checked);
      });
      capture();
    },
  });

  if (confirmed !== true) return;

  const payload = {
    username: form.username,
    password: form.password,
    is_admin: form.is_admin,
    language: form.language,
    theme: form.theme,
  };
  if (form.is_admin) payload.admin_passwords = form.admin_passwords;

  if (!payload.username || !payload.password) {
    toastErr(t("error.invalid_input"));
    return;
  }

  try {
    await api.createUser(payload);
    toastOkT(t("admin.userCreated"));
    await onDone();
  } catch (err) {
    toastErr(err instanceof Error ? err.message : t("error.server"));
  }
}
