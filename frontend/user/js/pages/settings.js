/* ==========================================================================
   个人设置页：外观语言、三餐开关、自定义目标作息、修改密码、账号注销申请
   ========================================================================== */

import { accountApi, ApiError, authApi, settingsApi } from "/shared/api.js";
import { getLang, t } from "/shared/i18n.js";
import { createTimePicker } from "/shared/timepicker.js";
import {
  confirmDialog,
  escapeHtml,
  fmtDateTime,
  qs,
  toastErr,
  toastOk,
} from "/shared/ui.js";

function statusBadge(status) {
  if (status === "pending") return `<span class="badge badge-warn">${escapeHtml(t("settings.deletionPending"))}</span>`;
  if (status === "approved") return `<span class="badge badge-accent">${escapeHtml(t("settings.deletionApproved"))}</span>`;
  if (status === "rejected") return `<span class="badge">${escapeHtml(t("settings.deletionRejected"))}</span>`;
  return `<span class="badge">${escapeHtml(t("settings.deletionNone"))}</span>`;
}

function renderShell(root, ctx) {
  const user = ctx.state?.user || {};
  root.innerHTML = `
    <div class="page-head">
      <div class="titles">
        <h1>${escapeHtml(t("settings.title"))}</h1>
        <p>${escapeHtml(t("settings.subtitle"))}</p>
      </div>
    </div>

    <div class="settings-grid">
      <div class="stack" style="gap:16px">
        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.appearance"))}</h2>
          <div class="field">
            <label>${escapeHtml(t("settings.language"))}</label>
            <div class="segment" id="set-lang">
              <button class="segment-btn" data-lang="zh">中文</button>
              <button class="segment-btn" data-lang="en">English</button>
            </div>
          </div>
          <div class="field">
            <label>${escapeHtml(t("settings.theme"))}</label>
            <div class="segment" id="set-theme">
              <button class="segment-btn" data-theme="dark">🌙 ${escapeHtml(t("theme.dark"))}</button>
              <button class="segment-btn" data-theme="light">☀ ${escapeHtml(t("theme.light"))}</button>
            </div>
            <span class="tiny muted">${escapeHtml(t("settings.themeHint"))}</span>
          </div>
        </section>

        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.meals"))}</h2>
          <div class="stack" style="gap:8px">
            <label class="checkbox"><input type="checkbox" id="set-meal-breakfast" /><span>${escapeHtml(t("records.meal.breakfast"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="set-meal-lunch" /><span>${escapeHtml(t("records.meal.lunch"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="set-meal-dinner" /><span>${escapeHtml(t("records.meal.dinner"))}</span></label>
          </div>
          <span class="tiny muted">${escapeHtml(t("settings.mealsHint"))}</span>
        </section>

        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.target"))}</h2>
          <span class="tiny muted">${escapeHtml(t("settings.targetHint"))}</span>
          <div class="form-row">
            <div class="field">
              <label>${escapeHtml(t("settings.targetSleep"))}</label>
              <div id="set-target-sleep"></div>
            </div>
            <div class="field">
              <label>${escapeHtml(t("settings.targetWake"))}</label>
              <div id="set-target-wake"></div>
            </div>
          </div>
          <div class="row-wrap">
            <button class="btn btn-primary" id="save-target">${escapeHtml(t("common.save"))}</button>
            <button class="btn btn-ghost" id="clear-target">${escapeHtml(t("settings.clearTarget"))}</button>
          </div>
        </section>
      </div>

      <div class="stack" style="gap:16px">
        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.account"))}</h2>
          <div class="info-list">
            <div class="info-line"><span>${escapeHtml(t("settings.username"))}</span><strong class="mono">${escapeHtml(
              user.username || "-"
            )}</strong></div>
            <div class="info-line"><span>${escapeHtml(t("settings.role"))}</span><strong>${escapeHtml(
              user.is_admin ? t("settings.role.admin") : t("settings.role.user")
            )}</strong></div>
            <div class="info-line"><span>${escapeHtml(t("settings.createdAt"))}</span><strong class="mono">${escapeHtml(
              fmtDateTime(user.created_at)
            )}</strong></div>
            <div class="info-line"><span>${escapeHtml(t("settings.lastLogin"))}</span><strong class="mono">${escapeHtml(
              fmtDateTime(user.last_login_at)
            )}</strong></div>
          </div>
        </section>

        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.password"))}</h2>
          <div class="field">
            <label>${escapeHtml(t("settings.currentPassword"))}</label>
            <input class="input" type="password" id="pw-current" autocomplete="current-password" />
          </div>
          <div class="field">
            <label>${escapeHtml(t("settings.newPassword"))}</label>
            <input class="input" type="password" id="pw-new" autocomplete="new-password" />
          </div>
          <div class="field">
            <label>${escapeHtml(t("settings.confirmPassword"))}</label>
            <input class="input" type="password" id="pw-confirm" autocomplete="new-password" />
            <span class="tiny muted">${escapeHtml(t("settings.passwordHint"))}</span>
          </div>
          <div class="inline-error" id="pw-error"></div>
          <button class="btn" id="save-password">${escapeHtml(t("common.save"))}</button>
        </section>

        <section class="card stack danger-card" style="gap:14px">
          <h2>${escapeHtml(t("settings.dangerZone"))}</h2>
          <span class="tiny muted">${escapeHtml(t("settings.deletionHint"))}</span>
          <div id="deletion-state"></div>
          <div id="deletion-actions"></div>
        </section>
      </div>
    </div>`;
}

function applyStaticState(root, ctx) {
  const user = ctx.state?.user || {};
  const lang = getLang();
  root.querySelectorAll("#set-lang .segment-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
  const theme = document.documentElement.getAttribute("data-theme");
  root.querySelectorAll("#set-theme .segment-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.theme === theme);
  });
  qs("#set-meal-breakfast", root).checked = user.meal_breakfast !== false;
  qs("#set-meal-lunch", root).checked = user.meal_lunch !== false;
  qs("#set-meal-dinner", root).checked = user.meal_dinner !== false;
}

function renderDeletion(root, ctx, data) {
  const stateBox = qs("#deletion-state", root);
  const actionBox = qs("#deletion-actions", root);
  const isAdmin = Boolean(ctx.state?.user?.is_admin);
  const request = data?.request || null;

  stateBox.innerHTML = `
    <div class="row-wrap">
      ${statusBadge(data?.status || "none")}
      ${
        request
          ? `<span class="tiny muted">${escapeHtml(t("records.date"))}: ${escapeHtml(
              fmtDateTime(request.requested_at)
            )}</span>`
          : ""
      }
    </div>
    ${
      request
        ? `<div class="stack" style="gap:6px; margin-top:8px">
             ${request.reason ? `<span class="small">${escapeHtml(request.reason)}</span>` : ""}
             ${
               request.handled_at
                 ? `<span class="tiny muted">${escapeHtml(t("settings.handledAt"))}: ${escapeHtml(
                     fmtDateTime(request.handled_at)
                   )} · ${escapeHtml(t("settings.handledBy"))}: ${escapeHtml(
                     request.handled_by_username || "-"
                   )}</span>`
                 : ""
             }
           </div>`
        : ""
    }`;

  if (isAdmin) {
    actionBox.innerHTML = `<span class="tiny muted">${escapeHtml(t("settings.adminNote"))}</span>`;
    return;
  }

  if (data?.pending) {
    actionBox.innerHTML = `<button class="btn btn-ghost" id="cancel-deletion">${escapeHtml(
      t("settings.cancelDeletion")
    )}</button>`;
    qs("#cancel-deletion", root).addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: t("settings.cancelDeletion"),
        bodyHtml: `<p>${escapeHtml(t("settings.cancelDeletionConfirm"))}</p>`,
        okText: t("settings.cancelDeletion"),
      });
      if (!ok) return;
      try {
        await accountApi.cancelDeletion();
        toastOk(t("settings.deletionCancelled"));
        await loadDeletion(root, ctx);
      } catch (err) {
        toastErr(err instanceof Error ? err.message : t("error.server"));
      }
    });
    return;
  }

  actionBox.innerHTML = `
    <div class="field">
      <label>${escapeHtml(t("settings.deletionReason"))}</label>
      <input class="input" id="deletion-reason" maxlength="200"
             placeholder="${escapeHtml(t("settings.deletionReasonPlaceholder"))}" />
    </div>
    <button class="btn btn-danger" id="submit-deletion">${escapeHtml(t("settings.submitDeletion"))}</button>`;

  qs("#submit-deletion", root).addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: t("settings.dangerZone"),
      bodyHtml: `<p>${escapeHtml(t("settings.deletionHint"))}</p>`,
      danger: true,
      okText: t("settings.submitDeletion"),
    });
    if (!ok) return;
    try {
      await accountApi.submitDeletion({ reason: qs("#deletion-reason", root).value.trim() || null });
      toastOk(t("settings.deletionSubmitted"));
      await loadDeletion(root, ctx);
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });
}

async function loadDeletion(root, ctx) {
  try {
    const data = await accountApi.deletionStatus();
    renderDeletion(root, ctx, data);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("error.server"));
  }
}

export async function renderSettingsPage(root, ctx) {
  renderShell(root, ctx);
  applyStaticState(root, ctx);

  const targetSleep = createTimePicker(qs("#set-target-sleep", root), {
    value: ctx.state.user?.target_sleep_time || "23:00",
  });
  const targetWake = createTimePicker(qs("#set-target-wake", root), {
    value: ctx.state.user?.target_wake_time || "07:00",
  });

  root.querySelectorAll("#set-lang .segment-btn").forEach((btn) => {
    btn.addEventListener("click", () => ctx.changeLanguage?.(btn.dataset.lang));
  });
  root.querySelectorAll("#set-theme .segment-btn").forEach((btn) => {
    btn.addEventListener("click", () => ctx.changeTheme?.(btn.dataset.theme));
  });

  ["breakfast", "lunch", "dinner"].forEach((key) => {
    qs(`#set-meal-${key}`, root).addEventListener("change", async (event) => {
      const payload = { [`meal_${key}`]: event.target.checked };
      try {
        const user = await settingsApi.update(payload);
        ctx.updateUser?.(user);
        toastOk(t("settings.preferenceSaved"));
      } catch (err) {
        event.target.checked = !event.target.checked;
        toastErr(err instanceof Error ? err.message : t("error.server"));
      }
    });
  });

  qs("#save-target", root).addEventListener("click", async () => {
    const sleep = targetSleep.value;
    const wake = targetWake.value;
    if (!sleep || !wake) {
      toastErr(t("advice.targetBothRequired"));
      return;
    }
    try {
      const user = await settingsApi.updateTarget({ target_sleep_time: sleep, target_wake_time: wake });
      ctx.updateUser?.(user);
      toastOk(t("settings.targetSaved"));
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  qs("#clear-target", root).addEventListener("click", async () => {
    try {
      const user = await settingsApi.updateTarget({ target_sleep_time: null, target_wake_time: null });
      ctx.updateUser?.(user);
      toastOk(t("settings.targetCleared"));
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  qs("#save-password", root).addEventListener("click", async () => {
    const errorBox = qs("#pw-error", root);
    errorBox.classList.remove("show");
    const current = qs("#pw-current", root).value;
    const next = qs("#pw-new", root).value;
    const confirm = qs("#pw-confirm", root).value;
    if (!current || !next) {
      errorBox.textContent = t("error.password_too_short");
      errorBox.classList.add("show");
      return;
    }
    if (next !== confirm) {
      errorBox.textContent = t("settings.passwordMismatch");
      errorBox.classList.add("show");
      return;
    }
    try {
      await authApi.changePassword({ current_password: current, new_password: next });
      toastOk(t("settings.passwordChanged"));
      ctx.logout?.();
    } catch (err) {
      errorBox.textContent = err instanceof Error ? err.message : t("error.server");
      errorBox.classList.add("show");
    }
  });

  await loadDeletion(root, ctx);
}
