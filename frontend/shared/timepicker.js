/* ==========================================================================
   闹钟式时间选择器：上下箭头微调 + 滚轮/拖动调整，禁止自由文本输入
   用法：
     const picker = createTimePicker(document.getElementById("host"), {
       value: "23:30",           // "HH:MM" 或 null
       clearable: true,           // 选填项允许清空
       onChange: (value) => {},   // value: "HH:MM" | null
     });
     picker.value;                // 读取当前值
     picker.setValue("07:00");    // 写值
   ========================================================================== */

import { t } from "./i18n.js";

const MINUTES_PER_STEP = 1;

function pad(n) {
  return String(n).padStart(2, "0");
}

function normalizeHour(hour) {
  return ((Number(hour) % 24) + 24) % 24;
}

function normalizeMinute(minute) {
  return ((Number(minute) % 60) + 60) % 60;
}

function parseValue(value) {
  if (!value || typeof value !== "string") return null;
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return { hour, minute };
}

/**
 * @param {HTMLElement} host
 * @param {object} [options]
 */
export function createTimePicker(host, options = {}) {
  const { clearable = false, onChange = null, initial = "23:00" } = options;
  let state = parseValue(options.value) || (clearable ? null : parseValue(initial) || { hour: 23, minute: 0 });

  host.classList.add("time-wheel");
  host.innerHTML = `
    <div class="time-wheel-cols">
      <div class="time-col" data-col="hour">
        <button type="button" class="time-spin" data-role="hour-up" title="${t("time.stepHint")}">▲</button>
        <div class="time-value" data-role="hour-value" tabindex="0" role="spinbutton"
             aria-label="${t("time.hour")}" aria-valuemin="0" aria-valuemax="23">--</div>
        <button type="button" class="time-spin" data-role="hour-down" title="${t("time.stepHint")}">▼</button>
      </div>
      <div class="time-sep">:</div>
      <div class="time-col" data-col="minute">
        <button type="button" class="time-spin" data-role="minute-up" title="${t("time.stepHint")}">▲</button>
        <div class="time-value" data-role="minute-value" tabindex="0" role="spinbutton"
             aria-label="${t("time.minute")}" aria-valuemin="0" aria-valuemax="59">--</div>
        <button type="button" class="time-spin" data-role="minute-down" title="${t("time.stepHint")}">▼</button>
      </div>
    </div>
    <div class="time-wheel-foot">
      <span class="time-wheel-hint">${t("time.rollHint")}</span>
      ${
        clearable
          ? `<button type="button" class="btn btn-sm btn-ghost" data-role="clear">${t("records.clearOptional")}</button>`
          : ""
      }
    </div>`;

  const hourValue = host.querySelector('[data-role="hour-value"]');
  const minuteValue = host.querySelector('[data-role="minute-value"]');

  function render() {
    const empty = state === null;
    hourValue.textContent = empty ? "--" : pad(state.hour);
    minuteValue.textContent = empty ? "--" : pad(state.minute);
    hourValue.setAttribute("aria-valuenow", empty ? "" : String(state.hour));
    minuteValue.setAttribute("aria-valuenow", empty ? "" : String(state.minute));
    host.classList.toggle("is-empty", empty);
  }

  function commit(next, { notify = true } = {}) {
    if (next === null) {
      state = null;
    } else {
      state = { hour: normalizeHour(next.hour), minute: normalizeMinute(next.minute) };
    }
    render();
    if (notify && typeof onChange === "function") {
      onChange(getValue());
    }
  }

  function getValue() {
    return state === null ? null : `${pad(state.hour)}:${pad(state.minute)}`;
  }

  function ensureValue() {
    if (state === null) state = { hour: 23, minute: 0 };
    return state;
  }

  function bump(field, delta) {
    const base = ensureValue();
    if (field === "hour") {
      let hour = base.hour + delta;
      let minute = base.minute;
      while (hour > 23) {
        hour -= 24;
      }
      while (hour < 0) {
        hour += 24;
      }
      commit({ hour, minute });
    } else {
      let minute = base.minute + delta;
      let hour = base.hour;
      while (minute > 59) {
        minute -= 60;
        hour += 1;
      }
      while (minute < 0) {
        minute += 60;
        hour -= 1;
      }
      commit({ hour, minute });
    }
  }

  function setValue(value) {
    const parsed = parseValue(value);
    commit(parsed, { notify: false });
  }

  host.addEventListener("click", (event) => {
    const role = event.target.closest("[data-role]")?.getAttribute("data-role");
    if (!role) return;
    switch (role) {
      case "hour-up":
        bump("hour", 1);
        break;
      case "hour-down":
        bump("hour", -1);
        break;
      case "minute-up":
        bump("minute", 5);
        break;
      case "minute-down":
        bump("minute", -5);
        break;
      case "clear":
        commit(null);
        break;
      default:
        break;
    }
  });

  host.addEventListener("keydown", (event) => {
    const target = event.target.closest(".time-value");
    if (!target) return;
    const field = target.getAttribute("data-role") === "hour-value" ? "hour" : "minute";
    const step = field === "hour" ? 1 : 5;
    switch (event.key) {
      case "ArrowUp":
        event.preventDefault();
        bump(field, step);
        break;
      case "ArrowDown":
        event.preventDefault();
        bump(field, -step);
        break;
      case "PageUp":
        event.preventDefault();
        bump(field, step * 2);
        break;
      case "PageDown":
        event.preventDefault();
        bump(field, -step * 2);
        break;
      case "Home":
        event.preventDefault();
        commit(field === "hour" ? { hour: 0, minute: ensureValue().minute } : { hour: ensureValue().hour, minute: 0 });
        break;
      default:
        break;
    }
  });

  // 滚轮 / 拖动：模拟滚轮调时
  function attachScroll(node, field) {
    node.classList.add("scrollable");
    node.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const delta = event.deltaY < 0 ? 1 : -1;
        bump(field, delta * (field === "hour" ? 1 : MINUTES_PER_STEP));
      },
      { passive: false }
    );

    let dragging = false;
    let startY = 0;
    let acc = 0;
    const DRAG_STEP = 14;

    node.addEventListener("pointerdown", (event) => {
      dragging = true;
      startY = event.clientY;
      acc = 0;
      node.setPointerCapture(event.pointerId);
    });
    node.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      const diff = startY - event.clientY;
      acc += diff;
      startY = event.clientY;
      while (acc >= DRAG_STEP) {
        bump(field, field === "hour" ? 1 : MINUTES_PER_STEP);
        acc -= DRAG_STEP;
      }
      while (acc <= -DRAG_STEP) {
        bump(field, field === "hour" ? -1 : -MINUTES_PER_STEP);
        acc += DRAG_STEP;
      }
    });
    const stop = (event) => {
      if (!dragging) return;
      dragging = false;
      try {
        node.releasePointerCapture(event.pointerId);
      } catch (err) {
        /* 指针已释放，忽略 */
      }
    };
    node.addEventListener("pointerup", stop);
    node.addEventListener("pointercancel", stop);
  }

  attachScroll(hourValue, "hour");
  attachScroll(minuteValue, "minute");

  render();

  return {
    host,
    get value() {
      return getValue();
    },
    setValue,
    refreshLabels() {
      const hint = host.querySelector(".time-wheel-hint");
      if (hint) hint.textContent = t("time.rollHint");
      const clearBtn = host.querySelector('[data-role="clear"]');
      if (clearBtn) clearBtn.textContent = t("records.clearOptional");
    },
  };
}

/** 可选时间选择器：带「启用」开关（用于最晚起床 / 最早入睡等选填项） */
export function createOptionalTimePicker(host, options = {}) {
  const { label = "", value = null, onChange = null } = options;
  host.classList.add("optional-picker");
  host.innerHTML = `
    <div class="field">
      <label class="row" style="gap:8px">
        <span>${label}</span>
        <span class="badge" data-role="state"></span>
      </label>
      <div data-role="picker-host"></div>
    </div>`;

  const pickerHost = host.querySelector('[data-role="picker-host"]');
  const stateBadge = host.querySelector('[data-role="state"]');

  let picker = null;
  const enabled = Boolean(value);

  function mount(withPicker) {
    pickerHost.innerHTML = "";
    if (withPicker) {
      picker = createTimePicker(pickerHost, {
        value: value || "23:00",
        clearable: false,
        onChange: (next) => {
          if (typeof onChange === "function") onChange(next);
        },
      });
    } else {
      picker = null;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm";
      btn.textContent = `+ ${t("common.add")}`;
      btn.addEventListener("click", () => {
        mount(true);
        updateBadge();
        if (typeof onChange === "function") onChange(picker ? picker.value : null);
      });
      pickerHost.appendChild(btn);
    }
    updateBadge();
  }

  function updateBadge() {
    stateBadge.textContent = picker ? t("common.enabled") : t("common.disabled");
    stateBadge.className = `badge ${picker ? "badge-accent" : ""}`;
  }

  mount(enabled);

  return {
    get value() {
      return picker ? picker.value : null;
    },
    disable() {
      mount(false);
    },
  };
}
