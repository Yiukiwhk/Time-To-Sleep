/* ==========================================================================
   概览页：统计卡片 + 保留策略提示 + 手动清理
   ========================================================================== */

import { t } from "/shared/i18n.js";
import { confirmDialog, escapeHtml, qs, toastErr, toastOk } from "/shared/ui.js";
import { emptyBlock, errorBlock, fmtDate, loadingBlock } from "../state.js";

const toastOkT = toastOk;

function statButton(item) {
  return `
    <button type="button" class="admin-stat${item.warn ? " warn" : ""}" data-stat="${escapeHtml(item.key)}">
      <span class="stat-label">${escapeHtml(item.label)}</span>
      <span class="stat-value">${escapeHtml(String(item.value))}</span>
    </button>`;
}

export async function renderDashboardPage(view, { ctx, navigate, api, isStale }) {
  view.innerHTML = loadingBlock();

  let stats;
  try {
    stats = await api.stats();
  } catch (err) {
    if (isStale()) return;
    view.innerHTML = errorBlock(err instanceof Error ? err.message : t("error.server"));
    return;
  }
  if (isStale()) return;

  if (!stats || typeof stats !== "object") stats = {};
  ctx.setMeta({ stats });

  const cards = [
    { key: "users", label: t("admin.stat.users"), value: stats.users ?? 0, route: "users" },
    { key: "records", label: t("admin.stat.records"), value: stats.records ?? 0, route: "users" },
    {
      key: "pending",
      label: t("admin.stat.pending"),
      value: stats.pending_deletions ?? 0,
      route: "requests",
      warn: (stats.pending_deletions ?? 0) > 0,
    },
    { key: "locked", label: t("admin.stat.locked"), value: stats.locked_users ?? 0, route: "users" },
  ];

  const admin = (stats.admin || ctx.admin || {});
  const months = stats.retention_months ?? ctx.meta.retention_months ?? 6;

  view.innerHTML = `
    <header class="admin-page-head">
      <div>
        <h1>${escapeHtml(t("nav.dashboard"))}</h1>
        <p>${escapeHtml(t("admin.navTitle"))} · ${escapeHtml(t("admin.signedInAs"))} ${escapeHtml(
          admin.username || "-"
        )}</p>
      </div>
      <span class="spacer"></span>
      <button type="button" class="btn" id="run-cleanup">${escapeHtml(t("admin.runCleanup"))}</button>
    </header>

    <div class="admin-cards">
      ${cards.map(statButton).join("")}
    </div>

    <section class="admin-panel">
      <h2 class="card-title">${escapeHtml(t("admin.retentionHint", { months }))}</h2>
      <dl class="admin-kv">
        <div><dt>${escapeHtml(t("admin.stat.users"))}</dt><dd>${escapeHtml(String(stats.users ?? 0))}</dd></div>
        <div><dt>${escapeHtml(t("admin.stat.records"))}</dt><dd>${escapeHtml(String(stats.records ?? 0))}</dd></div>
        <div><dt>${escapeHtml(t("admin.stat.pending"))}</dt><dd>${escapeHtml(
          String(stats.pending_deletions ?? 0)
        )}</dd></div>
        <div><dt>${escapeHtml(t("admin.stat.locked"))}</dt><dd>${escapeHtml(
          String(stats.locked_users ?? 0)
        )}</dd></div>
        <div><dt>${escapeHtml(t("admin.col.createdAt"))}</dt><dd>${escapeHtml(
          fmtDate(admin.created_at)
        )}</dd></div>
      </dl>
    </section>

    <section class="admin-panel">
      <h2 class="card-title">${escapeHtml(t("admin.paginationHint", { size: ctx.meta.pageSize }))}</h2>
      <p class="form-note">${escapeHtml(t("admin.navUsers"))} · ${escapeHtml(
        t("admin.navRequests")
      )} · ${escapeHtml(t("admin.navLogs"))}</p>
    </section>`;

  view.querySelectorAll("[data-stat]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = cards.find((card) => card.key === button.getAttribute("data-stat"));
      if (item) navigate(item.route);
    });
  });

  const cleanup = qs("#run-cleanup", view);
  cleanup?.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: t("admin.runCleanup"),
      bodyHtml: `<p>${escapeHtml(t("admin.cleanupConfirm"))}</p>`,
    });
    if (!ok) return;
    cleanup.disabled = true;
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
      await renderDashboardPage(view, { ctx, navigate, api, isStale });
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    } finally {
      cleanup.disabled = false;
    }
  });
}
