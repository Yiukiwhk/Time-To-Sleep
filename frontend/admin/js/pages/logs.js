/* ==========================================================================
   系统日志页：级别 / 事件 / 关键字筛选 + 分页 + 手动清理
   ========================================================================== */

import { t } from "/shared/i18n.js";
import { confirmDialog, debounce, escapeHtml, qs, toastErr, toastOk } from "/shared/ui.js";
import { emptyBlock, errorBlock, levelBadge, loadingBlock, renderAdminPager, renderTable } from "../state.js";

const toastOkT = toastOk;

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

const DEFAULT_STATE = { level: "", event: "", q: "", page: 1, page_size: 30, data: null };

export async function renderLogsPage(view, { ctx, navigate, api, isStale }) {
  const store = ctx.listState("logs") || { ...DEFAULT_STATE };
  ctx.setListState("logs", store);

  view.innerHTML = `
    <header class="admin-page-head">
      <div>
        <h1>${t("admin.navLogs")}</h1>
        <p>${t("admin.paginationHint", { size: store.page_size })}</p>
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn" id="l-cleanup">${t("admin.runCleanup")}</button>
    </header>

    <div class="admin-toolbar">
      <div class="field">
        <label for="l-level">${t("admin.logsLevel")}</label>
        <select id="l-level" class="select"></select>
      </div>
      <div class="field">
        <label for="l-event">${t("admin.logsEvent")}</label>
        <input id="l-event" class="input" maxlength="48" value="${escapeHtml(store.event)}" />
      </div>
      <div class="field grow">
        <label for="l-q">${t("admin.logsKeyword")}</label>
        <input id="l-q" class="input" type="search" maxlength="64"
               placeholder="${t("admin.logsKeywordPlaceholder")}" value="${escapeHtml(store.q)}" />
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn btn-sm" id="l-reset">${t("common.reset")}</button>
      <button type="button" class="btn btn-sm" id="l-refresh">${t("common.refresh")}</button>
    </div>

    <div id="l-list">${loadingBlock()}</div>
    <div id="l-pager" class="pager admin-pager"></div>`;

  function syncLevelSelect() {
    const select = qs("#l-level", view);
    if (!select || !store.data) return;
    const counts = store.data.level_counts || {};
    select.innerHTML = [
      `<option value=""${store.level === "" ? " selected" : ""}>${t("admin.logsAllLevels")}</option>`,
      ...LEVELS.map((level) => {
        const count = counts[level] || 0;
        return `<option value="${level}"${store.level === level ? " selected" : ""}>${level} (${count})</option>`;
      }),
    ].join("");
  }

  async function load() {
    const host = qs("#l-list", view);
    if (!host) return;
    if (!store.data) host.innerHTML = loadingBlock();
    const params = { page: store.page, page_size: store.page_size };
    if (store.level) params.level = store.level;
    if (store.event) params.event = store.event;
    if (store.q) params.q = store.q;

    try {
      const data = await api.logs(params);
      if (isStale()) return;
      store.data = data;
      paint();
      syncLevelSelect();
    } catch (err) {
      if (isStale()) return;
      host.innerHTML = errorBlock(err instanceof Error ? err.message : t("error.server"));
    }
  }

  function paint() {
    const host = qs("#l-list", view);
    const pagerHost = qs("#l-pager", view);
    if (!host) return;
    const data = store.data;
    const rows = (data && data.items) || [];
    if (!rows.length) {
      host.innerHTML = emptyBlock();
      if (pagerHost) pagerHost.innerHTML = "";
      return;
    }

    host.innerHTML = renderTable({
      rows,
      rowKey: (row) => String(row.id),
      columns: [
        {
          key: "created_at",
          label: t("admin.col.time"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${escapeHtml(String(row.created_at || "-").replace("T", " "))}</span>`,
        },
        { key: "level", label: t("admin.col.level"), className: "narrow", cell: (row) => levelBadge(row.level) },
        {
          key: "event",
          label: t("admin.col.event"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${escapeHtml(row.event || "-")}</span>`,
        },
        {
          key: "message",
          label: t("admin.col.message"),
          cell: (row) => `<div class="log-message">${escapeHtml(row.message || "")}</div>`,
        },
        {
          key: "username",
          label: t("admin.col.user"),
          className: "narrow",
          cell: (row) => escapeHtml(row.username || (row.user_id ? `#${row.user_id}` : "-")),
        },
        {
          key: "ip",
          label: t("admin.col.ip"),
          className: "narrow",
          cell: (row) => `<span class="mono small">${escapeHtml(row.ip || "-")}</span>`,
        },
      ],
    });

    renderAdminPager(pagerHost, data, (page) => {
      store.page = page;
      load();
    });
  }

  qs("#l-level", view)?.addEventListener("change", (event) => {
    store.level = event.target.value;
    store.page = 1;
    load();
  });

  const eventInput = qs("#l-event", view);
  eventInput?.addEventListener(
    "input",
    debounce(() => {
      store.event = eventInput.value.trim();
      store.page = 1;
      load();
    }, 320)
  );

  const keyword = qs("#l-q", view);
  keyword?.addEventListener(
    "input",
    debounce(() => {
      store.q = keyword.value.trim();
      store.page = 1;
      load();
    }, 320)
  );

  qs("#l-reset", view)?.addEventListener("click", () => {
    store.level = "";
    store.event = "";
    store.q = "";
    store.page = 1;
    store.data = null;
    renderLogsPage(view, { ctx, navigate, api, isStale });
  });

  qs("#l-refresh", view)?.addEventListener("click", () => load());

  qs("#l-cleanup", view)?.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: t("admin.runCleanup"),
      bodyHtml: `<p>${t("admin.cleanupConfirm")}</p>`,
    });
    if (!ok) return;
    try {
      const result = await api.cleanup();
      const data = (result && result.extra && result.extra.result) || {};
      toastOkT(
        t("admin.cleanupResult", {
          records: data.records_removed ?? 0,
          sessions: data.sessions_removed ?? 0,
          attempts: data.attempts_removed ?? 0,
          logs: data.logs_trimmed ?? 0,
        })
      );
      await load();
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  await load();
}
