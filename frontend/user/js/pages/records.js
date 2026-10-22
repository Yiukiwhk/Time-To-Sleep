/* ==========================================================================
   睡眠记录页：列表 + 录入 / 修改 / 删除（时间全部使用闹钟式选择器）
   ========================================================================== */

import { recordApi, ApiError } from "/shared/api.js";
import { t } from "/shared/i18n.js";
import { createTimePicker } from "/shared/timepicker.js";
import {
  confirmDialog,
  debounce,
  escapeHtml,
  fmtDuration,
  openModal,
  qs,
  renderPager,
  toastErr,
  toastOk,
} from "/shared/ui.js";

const view = {
  page: 1,
  pageSize: 10,
  startDate: "",
  endDate: "",
  total: 0,
  pages: 1,
  items: [],
  selected: new Set(),
  meta: null,
  cooldownUntil: 0,
};

/* ------------------------------ 渲染辅助 ------------------------------ */

function todayIso() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** 日期字符串加减天数（本地时区，和 todayIso() 口径一致） */
function shiftIso(iso, days) {
  const [year, month, day] = String(iso).split("-").map(Number);
  const target = new Date(year, month - 1, day);
  target.setDate(target.getDate() + days);
  const pad = (n) => String(n).padStart(2, "0");
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}`;
}

/** 默认只看最近 7 天（含今天）；导出给自检脚本复算，避免两处写死 */
export function defaultRange(days = 7) {
  const end = new Date();
  const start = new Date(end.getTime());
  start.setDate(start.getDate() - (days - 1));
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return { start: iso(start), end: iso(end) };
}

/** 把筛选框与 view 一起设成给定区间（初始化与"重置"共用，保证两次行为一致） */
function setRange(root, range) {
  view.startDate = range.start;
  view.endDate = range.end;
  if (root) {
    const startInput = qs("#filter-start", root);
    const endInput = qs("#filter-end", root);
    if (startInput) startInput.value = range.start;
    if (endInput) endInput.value = range.end;
  }
}

function mealCell(record) {
  const flags = [
    ["早", record.meal_breakfast],
    ["午", record.meal_lunch],
    ["晚", record.meal_dinner],
  ];
  return `<span class="meal-dots">${flags
    .map(
      ([label, on]) =>
        `<span class="meal-dot ${on ? "on" : ""}" title="${label}">${label}</span>`
    )
    .join("")}</span>`;
}

function cooldownRemaining() {
  return Math.max(0, Math.ceil((view.cooldownUntil - Date.now()) / 1000));
}

function startCooldown() {
  const seconds = Number(view.meta?.write_cooldown_seconds || 0);
  if (seconds > 0) view.cooldownUntil = Date.now() + seconds * 1000;
}

/* ------------------------------ 渲染 ------------------------------ */

function renderShell(root) {
  root.innerHTML = `
    <div class="page-head">
      <div class="titles">
        <h1>${escapeHtml(t("records.title"))}</h1>
        <p>${escapeHtml(t("records.subtitle"))}</p>
      </div>
      <div class="row-wrap">
        <button class="btn btn-primary" id="add-record">＋ ${escapeHtml(t("records.add"))}</button>
      </div>
    </div>

    <div class="card stack" style="gap:12px">
      <div class="toolbar">
        <div class="field">
          <label>${escapeHtml(t("records.startDate"))}</label>
          <input class="input" type="date" id="filter-start" value="${escapeHtml(view.startDate)}" />
        </div>
        <div class="field">
          <label>${escapeHtml(t("records.endDate"))}</label>
          <input class="input" type="date" id="filter-end" value="${escapeHtml(view.endDate)}" />
        </div>
        <button class="btn btn-sm" id="filter-apply">${escapeHtml(t("common.search"))}</button>
        <button class="btn btn-sm btn-ghost" id="filter-reset">${escapeHtml(t("common.reset"))}</button>
        <span class="grow"></span>
        <span class="muted small" id="records-total"></span>
      </div>

      <div id="cooldown-tip" class="note warn hidden"></div>

      <div class="row-wrap" id="batch-bar" style="display:none">
        <span class="badge badge-accent" id="batch-count"></span>
        <button class="btn btn-sm btn-danger" id="batch-delete">${escapeHtml(t("common.delete"))}</button>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width:36px"><input type="checkbox" id="select-all" /></th>
              <th>${escapeHtml(t("records.sleepDate"))}</th>
              <th>${escapeHtml(t("records.wakeDate"))}</th>
              <th>${escapeHtml(t("records.sleepTime"))}</th>
              <th>${escapeHtml(t("records.wakeTime"))}</th>
              <th>${escapeHtml(t("records.duration"))}</th>
              <th>${escapeHtml(t("records.meals"))}</th>
              <th>${escapeHtml(t("records.note"))}</th>
              <th class="right">${escapeHtml(t("common.actions"))}</th>
            </tr>
          </thead>
          <tbody id="records-body"></tbody>
        </table>
        <div id="records-empty" class="empty hidden">${escapeHtml(t("common.empty"))}</div>
      </div>

      <div class="record-cards" id="records-cards"></div>
      <div class="pager" id="records-pager"></div>
      <p class="tiny muted" id="retention-tip"></p>
    </div>`;
}

function renderRows(root) {
  const body = qs("#records-body", root);
  const cards = qs("#records-cards", root);
  const empty = qs("#records-empty", root);
  const hasRows = view.items.length > 0;

  empty.classList.toggle("hidden", hasRows);
  body.innerHTML = view.items
    .map(
      (record) => `
      <tr>
        <td><input type="checkbox" data-pick="${record.id}" ${view.selected.has(record.id) ? "checked" : ""} /></td>
        <td class="mono">${escapeHtml(record.record_date)}</td>
        <td class="mono">${escapeHtml(record.wake_date || "—")}</td>
        <td class="mono">${escapeHtml(record.sleep_time)}</td>
        <td class="mono">${escapeHtml(record.wake_time)}</td>
        <td class="mono record-duration">${escapeHtml(fmtDuration(record.duration_minutes))}</td>
        <td>${mealCell(record)}</td>
        <td class="muted small">${escapeHtml((record.note || "").slice(0, 24))}</td>
        <td class="actions">
          <button class="btn btn-sm" data-edit="${record.id}">${escapeHtml(t("common.edit"))}</button>
          <button class="btn btn-sm btn-danger" data-del="${record.id}">${escapeHtml(t("common.delete"))}</button>
        </td>
      </tr>`
    )
    .join("");

  cards.innerHTML = view.items
    .map(
      (record) => `
      <div class="record-card">
        <div class="rc-head">
          <span class="mono">${escapeHtml(record.record_date)}${record.wake_date ? ` → ${escapeHtml(record.wake_date)}` : ""}</span>
          ${mealCell(record)}
        </div>
        <div class="rc-times">
          <span>🌙 ${escapeHtml(record.sleep_time)}</span>
          <span class="muted">→</span>
          <span>☀️ ${escapeHtml(record.wake_time)}</span>
          <span class="badge">${escapeHtml(fmtDuration(record.duration_minutes))}</span>
        </div>
        <div class="rc-meta">
          ${record.note ? `<span>${escapeHtml(record.note)}</span>` : ""}
        </div>
        <div class="row" style="margin-top:10px; justify-content:flex-end">
          <button class="btn btn-sm" data-edit="${record.id}">${escapeHtml(t("common.edit"))}</button>
          <button class="btn btn-sm btn-danger" data-del="${record.id}">${escapeHtml(t("common.delete"))}</button>
        </div>
      </div>`
    )
    .join("");

  const allChecked = hasRows && view.items.every((row) => view.selected.has(row.id));
  const selectAll = qs("#select-all", root);
  if (selectAll) selectAll.checked = allChecked;

  const batchBar = qs("#batch-bar", root);
  batchBar.style.display = view.selected.size ? "flex" : "none";
  qs("#batch-count", root).textContent = t("common.selected", { n: view.selected.size });
}

function renderPagination(root) {
  renderPager(qs("#records-pager", root), {
    page: view.page,
    pages: view.pages,
    total: view.total,
    pageSize: view.pageSize,
    onChange: async (page) => {
      view.page = page;
      view.selected.clear();
      await load(root);
    },
  });
}

function renderMeta(root) {
  const totalNode = qs("#records-total", root);
  if (totalNode) totalNode.textContent = t("records.stats", { total: view.total });
  const retentionNode = qs("#retention-tip", root);
  if (retentionNode && view.meta) {
    retentionNode.textContent = t("records.retention", {
      months: view.meta.retention_months,
      cutoff: view.meta.retention_cutoff,
    });
  }
  const tip = qs("#cooldown-tip", root);
  if (!tip) return;
  const left = cooldownRemaining();
  tip.classList.toggle("hidden", left <= 0);
  if (left > 0) tip.textContent = t("records.cooldown", { seconds: left });
}

function tick(root) {
  if (cooldownRemaining() > 0) {
    renderMeta(root);
    setTimeout(() => tick(root), 1000);
  }
}

/* ------------------------------ 数据 ------------------------------ */

async function load(root, { silent = false } = {}) {
  if (!silent) {
    qs("#records-body", root).innerHTML = `<tr><td colspan="8" class="loading">${t("common.loading")}</td></tr>`;
  }
  try {
    const data = await recordApi.list({
      page: view.page,
      page_size: view.pageSize,
      start_date: view.startDate || undefined,
      end_date: view.endDate || undefined,
    });
    view.items = data.items || [];
    view.total = data.total || 0;
    view.pages = data.pages || 1;
    view.items.forEach((row) => {
      if (!view.selected.has(row.id)) return;
      if (!view.items.some((item) => item.id === row.id)) view.selected.delete(row.id);
    });
    renderRows(root);
    renderPagination(root);
    renderMeta(root);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("error.server"));
  }
}

async function loadMeta(root) {
  try {
    view.meta = await recordApi.meta();
    if (view.meta?.cooldown_remaining_seconds > 0) {
      view.cooldownUntil = Date.now() + view.meta.cooldown_remaining_seconds * 1000;
      tick(root);
    }
    renderMeta(root);
  } catch (err) {
    /* 元信息失败不影响列表 */
  }
}

/* ------------------------------ 表单弹窗 ------------------------------ */

/* 弹窗关闭后 DOM 会被移除，因此在 onMount 时捕获取值闭包 collector，ok 之后再调用 */
async function openRecordForm(root, record = null) {
  const isEdit = Boolean(record);
  const maxDate = view.meta?.today || todayIso();
  // 入睡日期默认昨天（记录的大多是"昨晚这一觉"）；起床日期默认入睡日期 + 1，可改。
  const sleepDate = record?.record_date || shiftIso(maxDate, -1);
  const wakeDate = record?.wake_date || shiftIso(sleepDate, 1);
  // 三餐记录默认跟着入睡日：入睡与起床同一天（凌晨睡的）说明那晚属于前一天
  const mealDate =
    record?.meal_date ||
    (wakeDate === sleepDate ? shiftIso(sleepDate, -1) : sleepDate);
  let collector = null;

  const ok = await openModal({
    title: isEdit ? t("records.editTitle") : t("records.addTitle"),
    wide: true,
    okText: isEdit ? t("records.submitUpdate") : t("records.submitCreate"),
    bodyHtml: `
      <div class="stack" style="gap:16px">
        <div class="form-row">
          <div class="field">
            <label>${escapeHtml(t("records.sleepDate"))}</label>
            <input class="input" type="date" id="f-date" value="${escapeHtml(sleepDate)}"
                   max="${escapeHtml(maxDate)}" style="max-width:190px" />
          </div>
          <div class="field">
            <label>${escapeHtml(t("records.wakeDate"))}</label>
            <input class="input" type="date" id="f-wake-date" value="${escapeHtml(wakeDate)}"
                   max="${escapeHtml(shiftIso(sleepDate, 1))}" style="max-width:190px" />
            <span class="tiny muted">${escapeHtml(t("records.wakeDateHint"))}</span>
          </div>
        </div>
        <div class="form-row">
          <div class="field">
            <label>${escapeHtml(t("records.sleepTime"))}</label>
            <div id="f-sleep"></div>
          </div>
          <div class="field">
            <label>${escapeHtml(t("records.wakeTime"))}</label>
            <div id="f-wake"></div>
          </div>
        </div>
        <div class="field">
          <div class="row-wrap" style="align-items:center; gap:10px">
            <span class="tiny muted" data-meal-date-hint>${escapeHtml(t("records.mealsDay"))}</span>
            <label class="tiny muted">${escapeHtml(t("records.mealDate"))}</label>
            <input class="input" type="date" id="f-meal-date" value="${escapeHtml(mealDate)}"
                   style="max-width:150px" />
          </div>
          <div class="row-wrap" style="margin-top:8px">
            <label class="checkbox"><input type="checkbox" id="f-breakfast" /><span>${escapeHtml(t("records.meal.breakfast"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="f-lunch" /><span>${escapeHtml(t("records.meal.lunch"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="f-dinner" /><span>${escapeHtml(t("records.meal.dinner"))}</span></label>
          </div>
          <p class="tiny muted" data-meal-late hidden></p>
        </div>
        <div class="field">
          <label>${escapeHtml(t("records.note"))}</label>
          <input class="input" id="f-note" maxlength="200"
                 placeholder="${escapeHtml(t("records.notePlaceholder"))}"
                 value="${escapeHtml(record?.note || "")}" />
        </div>
      </div>`,
    onMount: (modal) => {
      const sleepPicker = createTimePicker(qs("#f-sleep", modal), { value: record?.sleep_time || "23:30" });
      const wakePicker = createTimePicker(qs("#f-wake", modal), { value: record?.wake_time || "07:30" });
      const breakfast = qs("#f-breakfast", modal);
      const lunch = qs("#f-lunch", modal);
      const dinner = qs("#f-dinner", modal);
      breakfast.checked = record ? Boolean(record.meal_breakfast) : true;
      lunch.checked = record ? Boolean(record.meal_lunch) : true;
      dinner.checked = record ? Boolean(record.meal_dinner) : true;

      // 起床日期跟着入睡日期走，直到用户自己改过它（改过就尊重他填的，例如午睡）
      const sleepInput = qs("#f-date", modal);
      const wakeInput = qs("#f-wake-date", modal);
      const mealInput = qs("#f-meal-date", modal);
      const lateHint = qs("[data-meal-late]", modal);
      let wakeTouched = Boolean(
        record?.wake_date && record.wake_date !== shiftIso(record.record_date, 1)
      );
      let mealTouched = Boolean(record?.meal_date);

      // 入睡与起床同一天 = 凌晨睡的那一觉，默认把三餐记到前一天，并明示可以改
      const syncMeal = () => {
        const sameDay = wakeInput.value === sleepInput.value;
        if (!mealTouched) mealInput.value = sameDay ? shiftIso(sleepInput.value, -1) : sleepInput.value;
        if (lateHint) {
          lateHint.hidden = !sameDay;
          lateHint.textContent = sameDay ? t("records.mealDateLate") : "";
        }
      };
      syncMeal();
      mealInput.addEventListener("input", () => {
        mealTouched = true;
      });
      const syncWake = () => {
        wakeInput.max = shiftIso(sleepInput.value, 1);
        if (!wakeTouched) wakeInput.value = shiftIso(sleepInput.value, 1);
        syncMeal();
      };
      sleepInput.addEventListener("change", syncWake);
      sleepInput.addEventListener("input", syncWake);
      wakeInput.addEventListener("input", () => {
        wakeTouched = true;
      });

      collector = () => ({
        record_date: sleepInput.value,
        wake_date: wakeInput.value,
        meal_date: mealInput.value,
        sleep_time: sleepPicker.value,
        wake_time: wakePicker.value,
        meal_breakfast: breakfast.checked,
        meal_lunch: lunch.checked,
        meal_dinner: dinner.checked,
        note: qs("#f-note", modal).value.trim() || null,
      });
    },
  });

  if (ok !== true || !collector) return;
  const payload = collector();
  if (!payload.sleep_time || !payload.wake_time) {
    toastErr(t("error.invalid_time"));
    return;
  }
  if (!payload.record_date || !payload.wake_date) {
    toastErr(t("error.invalid_date"));
    return;
  }
  const dayGap =
    payload.wake_date === payload.record_date
      ? 0
      : payload.wake_date === shiftIso(payload.record_date, 1)
        ? 1
        : -1;
  if (dayGap < 0) {
    toastErr(t("error.invalid_date_range"));
    return;
  }
  if (dayGap === 0 && payload.wake_time <= payload.sleep_time) {
    toastErr(t("error.invalid_time_range"));
    return;
  }
  if (
    !payload.meal_date ||
    (payload.meal_date !== payload.record_date &&
      payload.meal_date !== shiftIso(payload.record_date, -1))
  ) {
    toastErr(payload.meal_date ? t("error.invalid_meal_date") : t("error.invalid_date"));
    return;
  }
  if (cooldownRemaining() > 0) {
    toastErr(t("records.cooldown", { seconds: cooldownRemaining() }));
    return;
  }

  try {
    if (isEdit) {
      await recordApi.update(record.id, payload);
      toastOk(t("records.updated"));
    } else {
      await recordApi.create(payload);
      toastOk(t("records.created"));
      view.page = 1;
    }
    startCooldown();
    await load(root, { silent: true });
    renderMeta(root);
    tick(root);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("error.server"));
  }
}

/* ------------------------------ 事件 ------------------------------ */

function bind(root, ctx) {
  qs("#add-record", root).addEventListener("click", () => openRecordForm(root, null));

  const applyFilter = debounce(async () => {
    view.startDate = qs("#filter-start", root).value;
    view.endDate = qs("#filter-end", root).value;
    view.page = 1;
    view.selected.clear();
    await load(root, { silent: true });
  }, 200);

  qs("#filter-start", root).addEventListener("change", applyFilter);
  qs("#filter-end", root).addEventListener("change", applyFilter);
  qs("#filter-apply", root).addEventListener("click", applyFilter);
  qs("#filter-reset", root).addEventListener("click", async () => {
    // "重置"回到默认的最近 7 天，而不是清空区间（清空会把全部历史拉回来）
    setRange(root, defaultRange());
    view.page = 1;
    view.selected.clear();
    await load(root, { silent: true });
  });

  qs("#select-all", root).addEventListener("change", (event) => {
    if (event.target.checked) {
      view.items.forEach((row) => view.selected.add(row.id));
    } else {
      view.selected.clear();
    }
    renderRows(root);
  });

  qs("#records-body", root).addEventListener("change", (event) => {
    const id = Number(event.target.getAttribute("data-pick"));
    if (!id) return;
    if (event.target.checked) view.selected.add(id);
    else view.selected.delete(id);
    renderRows(root);
  });

  const handleAction = async (target) => {
    const editId = target.closest("[data-edit]")?.getAttribute("data-edit");
    const delId = target.closest("[data-del]")?.getAttribute("data-del");
    if (editId) {
      const record = view.items.find((row) => row.id === Number(editId));
      if (record) await openRecordForm(root, record);
    }
    if (delId) {
      const confirmed = await confirmDialog({
        title: t("common.delete"),
        bodyHtml: `<p>${escapeHtml(t("records.deleteConfirm"))}</p>`,
        danger: true,
        okText: t("common.delete"),
      });
      if (!confirmed) return;
      try {
        await recordApi.remove(Number(delId));
        view.selected.delete(Number(delId));
        toastOk(t("records.deleted"));
        await load(root, { silent: true });
      } catch (err) {
        toastErr(err instanceof Error ? err.message : t("error.server"));
      }
    }
  };

  qs("#records-body", root).addEventListener("click", (event) => handleAction(event.target));
  qs("#records-cards", root).addEventListener("click", (event) => handleAction(event.target));

  qs("#batch-delete", root).addEventListener("click", async () => {
    const ids = Array.from(view.selected);
    if (!ids.length) return;
    const confirmed = await confirmDialog({
      title: t("admin.batchDelete"),
      bodyHtml: `<p>${escapeHtml(t("records.batchDeleteConfirm", { n: ids.length }))}</p>`,
      danger: true,
      okText: t("common.delete"),
    });
    if (!confirmed) return;
    try {
      const result = await recordApi.batchRemove(ids);
      view.selected.clear();
      toastOk(t("records.batchDeleted", { n: result?.extra?.deleted ?? ids.length }));
      await load(root, { silent: true });
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });
}

/* ------------------------------ 入口 ------------------------------ */

export async function renderRecordsPage(root, ctx) {
  renderShell(root);
  // 默认查询最近 7 天，页面一进来就带上区间（用户要求：记录默认只看最近一周）
  setRange(root, defaultRange());
  bind(root, ctx);
  await Promise.all([loadMeta(root), load(root)]);
}
