/* ==========================================================================
   注销审核页：待审核 / 全部列表 + 通过或拒绝（通过后清除账号与全部数据）
   ========================================================================== */

import { t } from "/shared/i18n.js";
import { debounce, escapeHtml, openModal, qs, toastErr, toastOk } from "/shared/ui.js";
import { emptyBlock, errorBlock, fmtDate, loadingBlock, renderAdminPager, renderTable } from "../state.js";

const toastOkT = toastOk;

const DEFAULT_STATE = { status: "pending", q: "", page: 1, data: null };

function requestStatus(request) {
  const status = request.status || "pending";
  const style =
    status === "approved"
      ? "background:rgba(53,196,141,.15);color:var(--ok)"
      : status === "rejected"
        ? "background:rgba(242,85,90,.15);color:var(--danger)"
        : "background:rgba(240,177,55,.15);color:var(--warn)";
  const label =
    {
      pending: t("admin.requestStatus.pending"),
      approved: t("admin.requestStatus.approved"),
      rejected: t("admin.requestStatus.rejected"),
      cancelled: t("admin.requestStatus.cancelled"),
      deleted: t("admin.requestStatus.deleted"),
    }[status] || status;
  return `<span class="badge" style="${style}">${escapeHtml(label)}</span>`;
}

export async function renderRequestsPage(view, { ctx, navigate, api, isStale }) {
  const store = ctx.listState("requests") || { ...DEFAULT_STATE };
  ctx.setListState("requests", store);

  view.innerHTML = `
    <header class="admin-page-head">
      <div>
        <h1>${t("admin.navRequests")}</h1>
        <p>${t("admin.paginationHint", { size: ctx.meta.pageSize })}</p>
      </div>
    </header>

    <div class="admin-toolbar">
      <div class="field" style="min-width: 220px">
        <label for="d-q">${t("admin.searchUser")}</label>
        <input id="d-q" class="input" type="search" maxlength="32"
               placeholder="${t("admin.searchUserPlaceholder")}" value="${store.q}" />
      </div>
      <div class="field">
        <label for="d-status">${t("admin.filterStatus")}</label>
        <select id="d-status" class="select">
          <option value="pending"${store.status === "pending" ? " selected" : ""}>${t(
    "admin.requestsPending"
  )}</option>
          <option value="all"${store.status === "all" ? " selected" : ""}>${t("admin.requestsAll")}</option>
          <option value="approved"${store.status === "approved" ? " selected" : ""}>${t(
    "admin.requestStatus.approved"
  )}</option>
          <option value="rejected"${store.status === "rejected" ? " selected" : ""}>${t(
    "admin.requestStatus.rejected"
  )}</option>
        </select>
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn btn-sm" id="d-refresh">${t("common.refresh")}</button>
    </div>

    <div id="d-list">${loadingBlock()}</div>
    <div id="d-pager" class="pager admin-pager"></div>`;

  async function load() {
    const host = qs("#d-list", view);
    if (!host) return;
    if (!store.data) host.innerHTML = loadingBlock();
    const params = { page: store.page, page_size: ctx.meta.pageSize, status: store.status };
    if (store.q) params.username = store.q;
    try {
      const data = await api.deletionRequests(params);
      if (isStale()) return;
      store.data = data;
      paint();
    } catch (err) {
      if (isStale()) return;
      host.innerHTML = errorBlock(err instanceof Error ? err.message : t("error.server"));
    }
  }

  function paint() {
    const host = qs("#d-list", view);
    const pagerHost = qs("#d-pager", view);
    if (!host) return;
    const data = store.data;
    const rows = (data && data.items) || [];
    if (!rows.length) {
      host.innerHTML = emptyBlock(t("admin.requestsEmpty"));
      if (pagerHost) pagerHost.innerHTML = "";
      return;
    }

    host.innerHTML = renderTable({
      rows,
      rowKey: (row) => String(row.id),
      columns: [
        {
          key: "username",
          label: t("admin.col.username"),
          cell: (row) =>
            `<strong>${escapeHtml(row.username || (row.user_id ? `#${row.user_id}` : "-"))}</strong>`,
        },
        { key: "status", label: t("admin.col.status"), cell: (row) => requestStatus(row) },
        {
          key: "reason",
          label: t("admin.requestReason"),
          cell: (row) => `<span class="small">${escapeHtml(row.reason || t("common.none"))}</span>`,
        },
        {
          key: "requested_at",
          label: t("admin.requestedAt"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${escapeHtml(fmtDate(row.requested_at))}</span>`,
        },
        {
          key: "handled_at",
          label: t("settings.handledAt"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${escapeHtml(fmtDate(row.handled_at))}</span>`,
        },
        {
          key: "handled_by_username",
          label: t("admin.col.handledBy"),
          className: "narrow",
          cell: (row) => escapeHtml(row.handled_by_username || "-"),
        },
        {
          key: "handle_note",
          label: t("admin.handleNote"),
          cell: (row) => `<span class="small muted">${escapeHtml(row.handle_note || "-")}</span>`,
        },
        {
          key: "actions",
          label: t("common.actions"),
          className: "narrow",
          cell: (row) =>
            row.status === "pending"
              ? `<div class="actions">
                   <button class="btn btn-primary" data-decide="approve" data-id="${row.id}">${t(
                  "admin.approve"
                )}</button>
                   <button class="btn" data-decide="reject" data-id="${row.id}">${t("admin.reject")}</button>
                 </div>`
              : `<span class="muted small">${t("common.none")}</span>`,
        },
      ],
    });

    host.querySelectorAll("button[data-decide]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = btn.getAttribute("data-decide");
        const request = rows.find((row) => String(row.id) === btn.getAttribute("data-id"));
        if (request) decide(action, request);
      });
    });

    renderAdminPager(pagerHost, data, (page) => {
      store.page = page;
      load();
    });
  }

  async function decide(action, request) {
    const name = request.username || (request.user_id ? `#${request.user_id}` : "");
    const approve = action === "approve";
    const note = { value: "" };

    const confirmed = await openModal({
      title: approve ? t("admin.approve") : t("admin.reject"),
      danger: approve,
      okText: approve ? t("admin.approve") : t("admin.reject"),
      bodyHtml: `
        <p>${escapeHtml(
          approve ? t("admin.approveConfirm", { name }) : t("admin.rejectConfirm", { name })
        )}</p>
        <div class="field" style="margin-top:12px">
          <label for="decide-note">${t("admin.handleNote")}</label>
          <input id="decide-note" class="input" maxlength="200"
                 placeholder="${t("admin.handleNotePlaceholder")}" />
        </div>`,
      onMount(host) {
        const input = qs("#decide-note", host);
        input?.addEventListener("input", () => {
          note.value = input.value;
        });
      },
    });
    if (confirmed !== true) return;

    try {
      await api.decideDeletion(request.id, { action, note: note.value || undefined });
      toastOkT(approve ? t("admin.approved") : t("admin.rejected"));
      await load();
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  }

  qs("#d-status", view)?.addEventListener("change", (event) => {
    store.status = event.target.value;
    store.page = 1;
    store.data = null;
    load();
  });

  const search = qs("#d-q", view);
  search?.addEventListener(
    "input",
    debounce(() => {
      store.q = search.value.trim();
      store.page = 1;
      load();
    }, 320)
  );

  qs("#d-refresh", view)?.addEventListener("click", () => load());

  await load();
}
