/* ==========================================================================
   用户详情页：账号信息 + 只读睡眠记录（单条 / 批量删除）+ 账号管理操作
   管理员不能修改用户的睡眠记录内容，只能删除
   ========================================================================== */

import { t } from "/shared/i18n.js";
import { confirmDialog, escapeHtml, fmtDuration, openModal, qs, toastErr, toastOk } from "/shared/ui.js";
import {
  bindTable,
  errorBlock,
  fmtDate,
  fmtDay,
  loadingBlock,
  renderAdminPager,
  renderTable,
  roleBadge,
  statusBadge,
} from "../state.js";

const toastOkT = toastOk;

const listStore = new Map();

function storeFor(userId) {
  if (!listStore.has(userId)) {
    listStore.set(userId, { page: 1, page_size: 15, start_date: "", end_date: "", data: null, selected: new Set() });
  }
  return listStore.get(userId);
}

export async function renderUserDetailPage(view, { ctx, navigate, api, isStale }) {
  const userId = ctx.routeId;
  if (!userId) {
    navigate("users");
    return;
  }

  const store = storeFor(userId);
  view.innerHTML = loadingBlock();

  let user;
  try {
    user = await api.user(userId);
  } catch (err) {
    if (isStale()) return;
    view.innerHTML = errorBlock(err instanceof Error ? err.message : t("error.server"));
    return;
  }
  if (isStale()) return;

  const isSelf = ctx.admin && String(ctx.admin.id) === String(user.id);
  const months = ctx.meta.retention_months || 6;

  view.innerHTML = `
    <header class="admin-page-head">
      <div>
        <h1>${escapeHtml(user.username || "-")}</h1>
        <p>${escapeHtml(t("admin.recordsReadonly"))}</p>
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn btn-sm" id="back-users">${t("admin.backToUsers")}</button>
      <button type="button" class="btn btn-sm" id="open-users">${t("admin.navUsers")}</button>
    </header>

    <section class="admin-panel">
      <h2 class="card-title">
        ${t("admin.userDetail")}
        ${roleBadge(user)}
        ${statusBadge(user)}
        ${isSelf ? `<span class="badge badge-accent">${t("admin.signedInAs")}</span>` : ""}
        <span class="spacer"></span>
        <button type="button" class="btn btn-sm" id="reset-password">${t("admin.resetPassword")}</button>
        ${
          user.locked
            ? `<button type="button" class="btn btn-sm" id="unlock-user">${t("admin.unlock")}</button>`
            : ""
        }
        <button type="button" class="btn btn-sm" id="toggle-active"${isSelf ? " disabled" : ""}>${
          user.is_active ? t("admin.disable") : t("admin.enable")
        }</button>
        <button type="button" class="btn btn-sm btn-danger" id="delete-user"${
          isSelf ? " disabled" : ""
        }>${t("admin.deleteUser")}</button>
      </h2>
      <dl class="admin-kv">
        <div><dt>${t("admin.col.username")}</dt><dd class="mono">${escapeHtml(user.username || "-")}</dd></div>
        <div><dt>${t("admin.col.role")}</dt><dd>${user.is_admin ? t("admin.role.admin") : t("admin.role.user")}</dd></div>
        <div><dt>${t("admin.col.status")}</dt><dd>${user.is_active ? t("admin.status.active") : t("admin.status.disabled")}</dd></div>
        <div><dt>${t("admin.col.records")}</dt><dd class="mono">${escapeHtml(
          String(user.record_count ?? 0)
        )}</dd></div>
        <div><dt>${t("admin.col.createdAt")}</dt><dd class="mono">${escapeHtml(fmtDate(user.created_at))}</dd></div>
        <div><dt>${t("admin.col.lastLogin")}</dt><dd class="mono">${escapeHtml(fmtDate(user.last_login_at))}</dd></div>
        <div><dt>${t("settings.lastLoginIp")}</dt><dd class="mono">${escapeHtml(user.last_login_ip || "-")}</dd></div>
        <div><dt>${t("settings.failedCount")}</dt><dd class="mono">${escapeHtml(
          String(user.failed_login_count ?? 0)
        )}</dd></div>
        <div><dt>${t("settings.lockedUntil")}</dt><dd class="mono">${escapeHtml(
          user.locked_until ? fmtDate(user.locked_until) : t("common.none")
        )}</dd></div>
        <div><dt>${t("settings.targetSchedule")}</dt><dd class="mono">${escapeHtml(
          user.target_sleep_time && user.target_wake_time
            ? `${user.target_sleep_time} - ${user.target_wake_time}`
            : t("settings.targetNone")
        )}</dd></div>
      </dl>
    </section>

    <section class="admin-panel">
      <h2 class="card-title">
        ${escapeHtml(t("admin.recordsOf", { name: user.username || "-" }))}
        <span class="badge">${escapeHtml(t("admin.retentionHint", { months }))}</span>
        <span class="spacer"></span>
        <span class="tiny muted" id="records-total"></span>
      </h2>

      <div class="admin-toolbar">
        <div class="field">
          <label for="r-start">${t("records.startDate")}</label>
          <input id="r-start" class="input" type="date" value="${store.start_date}" />
        </div>
        <div class="field">
          <label for="r-end">${t("records.endDate")}</label>
          <input id="r-end" class="input" type="date" value="${store.end_date}" />
        </div>
        <span class="spacer"></span>
        <button type="button" class="btn btn-sm" id="r-reset">${t("common.reset")}</button>
        <button type="button" class="btn btn-sm" id="r-refresh">${t("common.refresh")}</button>
      </div>

      <div class="admin-bulkbar hidden" id="r-bulkbar">
        <span id="r-selected"></span>
        <span class="spacer"></span>
        <button type="button" class="btn btn-sm btn-danger" id="r-batch-delete">${t(
          "admin.batchDelete"
        )}</button>
      </div>

      <div id="r-list">${loadingBlock()}</div>
      <div id="r-pager" class="pager admin-pager"></div>
    </section>`;

  /* ------------------------------ 记录列表 ------------------------------ */

  async function loadRecords() {
    const host = qs("#r-list", view);
    if (!host) return;
    if (!store.data) host.innerHTML = loadingBlock();
    const params = { page: store.page, page_size: store.page_size };
    if (store.start_date) params.start_date = store.start_date;
    if (store.end_date) params.end_date = store.end_date;

    try {
      const data = await api.userRecords(userId, params);
      if (isStale()) return;
      store.data = data;
      store.selected.clear();
      paintRecords();
    } catch (err) {
      if (isStale()) return;
      host.innerHTML = errorBlock(err instanceof Error ? err.message : t("error.server"));
    }
  }

  function syncBulkbar() {
    const bar = qs("#r-bulkbar", view);
    const label = qs("#r-selected", view);
    const size = store.selected.size;
    if (bar) bar.classList.toggle("hidden", size === 0);
    if (label) label.textContent = t("common.selected", { n: size });
  }

  function paintRecords() {
    const host = qs("#r-list", view);
    const pagerHost = qs("#r-pager", view);
    const totalNode = qs("#records-total", view);
    if (!host) return;
    const data = store.data;
    if (!data || !(data.items || []).length) {
      host.innerHTML = `<div class="empty">${t("admin.emptyRecords")}</div>`;
      if (pagerHost) pagerHost.innerHTML = "";
      if (totalNode) totalNode.textContent = t("admin.recordsTotal", { total: data ? data.total : 0 });
      syncBulkbar();
      return;
    }

    if (totalNode) totalNode.textContent = t("admin.recordsTotal", { total: data.total });

    host.innerHTML = renderTable({
      selectable: true,
      selected: store.selected,
      rows: data.items,
      rowKey: (row) => String(row.id),
      columns: [
        {
          key: "record_date",
          label: t("field.record_date"),
          className: "narrow",
          cell: (row) => `<span class="mono">${escapeHtml(fmtDay(row.record_date))}</span>`,
        },
        {
          key: "wake_date",
          label: t("field.wake_date"),
          className: "narrow",
          cell: (row) => `<span class="mono">${escapeHtml(fmtDay(row.wake_date))}</span>`,
        },
        {
          key: "sleep_time",
          label: t("field.sleep_time"),
          className: "narrow",
          cell: (row) => `<span class="mono">${escapeHtml(row.sleep_time || "-")}</span>`,
        },
        {
          key: "wake_time",
          label: t("field.wake_time"),
          className: "narrow",
          cell: (row) => `<span class="mono">${escapeHtml(row.wake_time || "-")}</span>`,
        },
        {
          key: "duration_minutes",
          label: t("records.duration"),
          className: "num",
          cell: (row) => escapeHtml(fmtDuration(row.duration_minutes)),
        },
        {
          key: "updated_at",
          label: t("records.updatedAt"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${escapeHtml(fmtDate(row.updated_at))}</span>`,
        },
        {
          key: "actions",
          label: t("common.actions"),
          className: "narrow",
          cell: (row) => `<button class="btn btn-danger" data-del="${row.id}">${t(
            "common.delete"
          )}</button>`,
        },
      ],
    });

    bindTable(host, {
      rows: data.items,
      rowKey: (row) => String(row.id),
      onToggle: (key, checked) => {
        if (checked) store.selected.add(key);
        else store.selected.delete(key);
        syncBulkbar();
      },
      onToggleAll: (checked) => {
        store.selected.clear();
        if (checked) data.items.forEach((row) => store.selected.add(String(row.id)));
        paintRecords();
      },
    });

    host.querySelectorAll("button[data-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const ok = await confirmDialog({
          title: t("common.delete"),
          bodyHtml: `<p>${t("admin.deleteRecordConfirm")}</p>`,
          danger: true,
        });
        if (!ok) return;
        try {
          await api.deleteUserRecord(userId, btn.getAttribute("data-del"));
          toastOkT(t("admin.recordDeleted"));
          await loadRecords();
        } catch (err) {
          toastErr(err instanceof Error ? err.message : t("error.server"));
        }
      });
    });

    renderAdminPager(pagerHost, data, (page) => {
      store.page = page;
      loadRecords();
    });
    syncBulkbar();
  }

  qs("#r-start", view)?.addEventListener("change", (event) => {
    store.start_date = event.target.value;
    store.page = 1;
    loadRecords();
  });
  qs("#r-end", view)?.addEventListener("change", (event) => {
    store.end_date = event.target.value;
    store.page = 1;
    loadRecords();
  });
  qs("#r-reset", view)?.addEventListener("click", () => {
    store.start_date = "";
    store.end_date = "";
    store.page = 1;
    const start = qs("#r-start", view);
    const end = qs("#r-end", view);
    if (start) start.value = "";
    if (end) end.value = "";
    loadRecords();
  });
  qs("#r-refresh", view)?.addEventListener("click", () => loadRecords());

  qs("#r-batch-delete", view)?.addEventListener("click", async () => {
    const ids = Array.from(store.selected).map((value) => Number(value)).filter((value) => value > 0);
    if (!ids.length) return toastErr(t("common.nothingSelected"));
    const ok = await confirmDialog({
      title: t("admin.batchDelete"),
      bodyHtml: `<p>${t("admin.confirmDeleteRecords", { n: ids.length })}</p>`,
      danger: true,
    });
    if (!ok) return;
    try {
      const chunks = [];
      for (let i = 0; i < ids.length; i += 500) chunks.push(ids.slice(i, i + 500));
      for (const chunk of chunks) {
        await api.batchDeleteUserRecords(userId, chunk);
      }
      toastOkT(t("admin.recordsDeleted", { n: ids.length }));
      store.selected.clear();
      await loadRecords();
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  /* ------------------------------ 账号操作 ------------------------------ */

  qs("#back-users", view)?.addEventListener("click", () => navigate("users"));
  qs("#open-users", view)?.addEventListener("click", () => navigate("users"));

  qs("#reset-password", view)?.addEventListener("click", async () => {
    const form = { password: "" };
    const ok = await openModal({
      title: t("admin.resetPasswordTitle"),
      okText: t("common.save"),
      bodyHtml: `
        <div class="field">
          <label for="rp-password">${t("admin.newPassword")}</label>
          <input id="rp-password" class="input" type="password" maxlength="72"
                 autocomplete="new-password" />
        </div>
        <p class="form-note">${t("admin.passwordHint")}</p>`,
      onMount(host) {
        const input = qs("#rp-password", host);
        input?.addEventListener("input", () => {
          form.password = input.value;
        });
      },
    });
    if (ok !== true) return;
    if (!form.password) return toastErr(t("error.password_too_short"));
    try {
      await api.resetPassword(userId, { password: form.password });
      toastOkT(t("admin.resetPasswordDone"));
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  qs("#unlock-user", view)?.addEventListener("click", async () => {
    try {
      await api.unlockUser(userId);
      toastOkT(t("admin.unlocked"));
      await renderUserDetailPage(view, { ctx, navigate, api, isStale });
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  qs("#toggle-active", view)?.addEventListener("click", async () => {
    try {
      await api.updateUser(userId, { is_active: !user.is_active });
      toastOkT(user.is_active ? t("admin.disabled") : t("admin.enabled"));
      await renderUserDetailPage(view, { ctx, navigate, api, isStale });
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  qs("#delete-user", view)?.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: t("admin.deleteUser"),
      bodyHtml: `<p>${t("admin.deleteUserConfirm", { name: user.username })}</p>`,
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteUser(userId);
      toastOkT(t("admin.userDeleted"));
      listStore.delete(String(userId));
      navigate("users");
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  await loadRecords();
}
