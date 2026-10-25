/**
 * 全站图标：一套手绘的 24×24 描边图标，直接内联成 SVG。
 *
 * 为什么不用 emoji：emoji 是「彩色位图字体」，颜色写死在字形里（🌙 是黄的、📋 是蓝绿的），
 * 深色/浅色主题都只能凑合用；形状还随系统变（Windows 的 Segoe UI Emoji 和 Android 的
 * Noto Color Emoji 完全两套），和文字混排时基线与字重也对不齐。
 *
 * 这里的图标全是 `stroke="currentColor"` + `fill="none"`：
 *   - 颜色继承父元素（`.nav-item.active` 变蓝时图标跟着变蓝），无需为亮/暗主题各写一套；
 *   - 矢量、任意缩放不糊；不依赖任何字体和第三方库；
 *   - 一个 <svg> 就是几个字节，内联进 HTML 也不多发请求。
 *
 * 用法：
 *   1) 静态 HTML：<span class="nav-icon" data-icon="records"></span>，
 *      页面启动时调用一次 hydrateIcons()。
 *   2) JS 动态内容：setIcon(host, "sun")，host 是任意元素。
 *   3) 想自己拼字符串：iconSvg("moon", 18) 返回一段 <svg> 文本。
 *
 * 想加图标：往 ICONS 里加一条 path 即可（网格 24×24，描边 1.7，圆角端点）。
 */

const ICONS = {
  /** 月亮：品牌标记 / 深色主题 */
  moon:
    '<path d="M20 14.4A8.4 8.4 0 0 1 9.6 4 8.4 8.4 0 1 0 20 14.4Z"/>',
  /** 太阳：浅色主题 */
  sun:
    '<circle cx="12" cy="12" r="3.8"/>' +
    '<path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2' +
    'M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4L17 7M7 17l-1.6 1.6"/>',
  /** 清单：睡眠记录 */
  records:
    '<path d="M9 6.2h11M9 12h11M9 17.8h11"/>' +
    '<circle cx="4.6" cy="6.2" r="1.15" fill="currentColor" stroke="none"/>' +
    '<circle cx="4.6" cy="12" r="1.15" fill="currentColor" stroke="none"/>' +
    '<circle cx="4.6" cy="17.8" r="1.15" fill="currentColor" stroke="none"/>',
  /** 灯泡：作息建议（罗盘针在小尺寸下会糊成一团，换成灯泡更清楚） */
  advice:
    '<path d="M12 3.6a5.9 5.9 0 0 0-3.4 10.7c.6.5 1 1.2 1 2v.3h4.8v-.3c0-.8.4-1.5 1-2A5.9 5.9 0 0 0 12 3.6Z"/>' +
    '<path d="M9.7 19.4h4.6M10.7 21.6h2.6"/>',
  /** 滑杆：个人设置 */
  settings:
    '<path d="M4 7.2h7.6M17.4 7.2H20M4 16.8h2.6M12.4 16.8H20"/>' +
    '<circle cx="14.5" cy="7.2" r="2.1"/><circle cx="9.5" cy="16.8" r="2.1"/>',
  /** 汉堡菜单：移动端侧栏开关 */
  menu: '<path d="M4.5 7h15M4.5 12h15M4.5 17h15"/>',
  /** 关闭：弹层右上角 */
  close: '<path d="M6.2 6.2l11.6 11.6M17.8 6.2L6.2 17.8"/>',
  /** 日历：日期字段 / 记录 */
  calendar:
    '<rect x="3.5" y="5.5" width="17" height="15" rx="2.5"/>' +
    '<path d="M8 3.5v4M16 3.5v4M3.5 10h17"/>',
  /** 时钟：时间字段 / 时长 */
  clock: '<circle cx="12" cy="12" r="8.4"/><path d="M12 7.4V12l3.1 2"/>',
  /** 左箭头：日历翻月 */
  "chevron-left": '<path d="M14.4 6.2L8.6 12l5.8 5.8"/>',
  /** 右箭头：日历翻月 */
  "chevron-right": '<path d="M9.6 6.2L15.4 12l-5.8 5.8"/>',
  /** 右向箭头：入睡 → 起床 */
  "arrow-right": '<path d="M4.6 12h14.2"/><path d="M13.2 6.4l5.6 5.6-5.6 5.6"/>',
  /** 上三角：时间滚轮加一档 */
  "caret-up": '<path d="M6.6 14.6L12 9.2l5.4 5.4"/>',
  /** 下三角：时间滚轮减一档 */
  "caret-down": '<path d="M6.6 9.4L12 14.8l5.4-5.4"/>',
  /** 循环箭头：自动重算 */
  refresh:
    '<path d="M19.8 12a7.8 7.8 0 1 1-2.3-5.5"/>' +
    '<path d="M19.9 4.5V10h-5.4"/>',
  /** 盾牌：管理后台品牌 */
  shield: '<path d="M12 3.3l7 2.6v5.2c0 4.2-2.9 7.6-7 9.6-4.1-2-7-5.4-7-9.6V5.9l7-2.6Z"/>',
  /** 柱状图：管理后台概览 */
  dashboard:
    '<path d="M4 20.2h16"/><path d="M7.4 20.2v-6.4M12 20.2V7.8M16.6 20.2v-4.2"/>',
  /** 两个人：用户管理 */
  users:
    '<circle cx="9.2" cy="8.4" r="3.3"/>' +
    '<path d="M3.6 19.4c.8-3.1 2.9-4.9 5.6-4.9s4.8 1.8 5.6 4.9"/>' +
    '<path d="M15.6 5.5a3.3 3.3 0 0 1 0 5.9M17.2 14.9c2.1.5 3.5 2.1 4.2 4.5"/>',
  /** 收件盘：账号申请 */
  inbox:
    '<path d="M3.8 13.4h4.4l1.5 2.5h4.6l1.5-2.5h4.4"/>' +
    '<path d="M6.1 5.1h11.8l2.1 8.3v4.2a2 2 0 0 1-2 2H5.9a2 2 0 0 1-2-2v-4.2l2.2-8.3Z"/>',
  /** 文档：操作日志 */
  document:
    '<path d="M6.8 3.6h6.6L18.2 8.4v12H6.8z"/>' +
    '<path d="M13.2 3.7v4.8h5"/><path d="M9.4 12.1h5.2M9.4 15.6h5.2"/>',
  /** 碗：早餐 */
  "meal-breakfast":
    '<path d="M4.2 12.2h15.6a7.8 7.8 0 0 1-15.6 0Z"/>' +
    '<path d="M9.4 8.4c0-1.3 1.3-1.3 1.3-2.6M13.3 8.4c0-1.3 1.3-1.3 1.3-2.6"/>',
  /** 便当盒：午餐 */
  "meal-lunch":
    '<rect x="3.6" y="6.6" width="16.8" height="10.8" rx="2.4"/>' +
    '<path d="M11.2 6.6v10.8M3.6 12h7.6"/>',
  /** 汤锅：晚餐 */
  "meal-dinner":
    '<path d="M4.8 11.2h14.4v4a3.6 3.6 0 0 1-3.6 3.6H8.4a3.6 3.6 0 0 1-3.6-3.6v-4Z"/>' +
    '<path d="M8.6 11.2V8.6M12 11.2V8.2M15.4 11.2V8.6"/>',
  /** 盘子：没识别出具体餐次时的兜底 */
  meal: '<circle cx="12" cy="12" r="8.3"/><circle cx="12" cy="12" r="4.6"/>',
};

export const ICON_NAMES = Object.keys(ICONS);

/**
 * @param {string} name   ICONS 里的键
 * @param {number} [size] 缺省 24；样式表里的 width/height 优先级更高，可覆盖
 * @returns {string} 一段完整的 <svg>（名字不存在时返回空串，调用方不必判断）
 */
export function iconSvg(name, size = 24) {
  const body = ICONS[name];
  if (!body) return "";
  return (
    `<svg class="ic" viewBox="0 0 24 24" width="${size}" height="${size}"` +
    ' fill="none" stroke="currentColor" stroke-width="1.7"' +
    ' stroke-linecap="round" stroke-linejoin="round"' +
    ' aria-hidden="true" focusable="false">' +
    body +
    "</svg>"
  );
}

/** 把 host 的内容换成某个图标（host 里的文字会被替换掉）。 */
export function setIcon(host, name) {
  if (!host) return false;
  const svg = iconSvg(name, 24);
  if (!svg) return false;
  host.innerHTML = svg;
  return true;
}

/** 渲染 root 下所有 [data-icon]（默认整页）；重复调用无害。 */
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((host) => {
    setIcon(host, host.dataset.icon);
  });
}
