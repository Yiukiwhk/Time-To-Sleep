/* ==========================================================================
   参考文献弹窗：数据来自 /shared/references.js（只读，不在这里改动）
   - 每条渲染 authors / year / title / journal / volume / pages
   - doi 链接到 url（新窗口打开），有 pmid / pmcid 时一并显示
   - summary 按当前语言取，缺英文时回退中文；弹窗底部不再放校对说明小字（按用户要求移除）
   - 语言切换时重绘：i18n 的 setLang() 会广播 ssp:langchange
   ========================================================================== */

import { getLang, t } from "/shared/i18n.js";
import { REFERENCES } from "/shared/references.js";
import { escapeHtml, openModal } from "/shared/ui.js";

/** 取当前语言的字段，缺失时回退中文 */
function pick(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  return value[getLang()] || value.zh || "";
}

function referenceHtml(item) {
  const pages = item.pages ? `: ${escapeHtml(String(item.pages))}` : "";
  const volume = item.volume ? ` ${escapeHtml(String(item.volume))}` : "";
  const journal = item.journal ? `<em>${escapeHtml(item.journal)}</em>${volume}${pages}` : "";
  const summary = pick(item.summary);

  const ids = [
    item.pmid ? `${escapeHtml(t("references.pmid"))} ${escapeHtml(String(item.pmid))}` : "",
    item.pmcid ? `${escapeHtml(t("references.pmcid"))} ${escapeHtml(String(item.pmcid))}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return `<li class="ref-item">
    <p class="ref-line">${escapeHtml(item.authors || "")} (${escapeHtml(String(item.year || ""))}). ${escapeHtml(
      item.title || ""
    )}. ${journal}</p>
    <p class="ref-links">
      ${
        item.doi
          ? `<a href="${escapeHtml(item.url || `https://doi.org/${item.doi}`)}" target="_blank" rel="noopener">${escapeHtml(
              t("references.doi")
            )} ${escapeHtml(item.doi)}</a>`
          : ""
      }
      ${ids ? `<span class="ref-ids">${ids}</span>` : ""}
    </p>
    ${summary ? `<p class="ref-summary">${escapeHtml(summary)}</p>` : ""}
  </li>`;
}

function bodyHtml() {
  return `<ul class="ref-list">${REFERENCES.map(referenceHtml).join("")}</ul>`;
}

/** 打开参考文献弹窗（纯阅读，只留一个关闭按钮） */
export function openReferencesModal() {
  return openModal({
    title: t("references.title"),
    bodyHtml: bodyHtml(),
    okText: t("references.close"),
    wide: true,
    onMount: (host, close) => {
      const cancel = host.querySelector('[data-role="cancel"]');
      if (cancel) cancel.remove();
      host.querySelector('[data-role="ok"]')?.addEventListener("click", () => close(true));

      // 语言切换：就地重绘标题/正文/按钮，弹窗保持打开；弹窗关掉后自动解绑
      const redraw = () => {
        if (!host.isConnected) {
          document.removeEventListener("ssp:langchange", redraw);
          return;
        }
        const title = host.querySelector("h2");
        if (title) title.textContent = t("references.title");
        const body = host.querySelector('[data-role="body"]');
        if (body) body.innerHTML = bodyHtml();
        const ok = host.querySelector('[data-role="ok"]');
        if (ok) ok.textContent = t("references.close");
      };
      document.addEventListener("ssp:langchange", redraw);
    },
  });
}
