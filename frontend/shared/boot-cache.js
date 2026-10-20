/**
 * 首帧缓存（sessionStorage，只活在这个标签页里）
 *
 * 目的：刷新页面时浏览器已经把 DOM 丢掉了，想让"上一屏还在"只有一条路——
 * 在 JS 启动的第一时间把上一次的界面/登录身份重新贴回屏幕，再去后台核对。
 * 这就是 GitHub 那种"刷新不黑屏"在前端渲染应用里的等价做法。
 *
 * 存两样东西：
 *   ssp.boot.user         上一次 `GET /api/auth/me` 的结果（只存 id/username/is_admin）
 *   ssp.boot.page.<route> 上一次这个路由渲染完成时的 #route-view.innerHTML
 *
 * 约束：
 *   - 带版本号（VERSION）与写入时间（TTL），过期或版本不符就当没有；
 *   - 页面快照带 uid，换账号直接不认，避免把上一个用户的界面贴给下一个人；
 *   - 退出登录 / 401 一律 clearBootCache()，不给共享电脑留残影；
 *   - sessionStorage 不可用（隐私模式、被禁用）时全部静默降级为"不缓存"。
 */

const VERSION = 1;
const TTL_MS = 12 * 60 * 60 * 1000;
const KEY_USER = "ssp.boot.user";
const KEY_PAGE = "ssp.boot.page.";
const PREFIX = "ssp.boot.";

function store() {
  try {
    return window.sessionStorage;
  } catch (err) {
    return null;
  }
}

function readBox(key) {
  const box = store();
  if (!box) return null;
  try {
    const raw = box.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.v !== VERSION) return null;
    if (typeof parsed.at === "number" && Date.now() - parsed.at > TTL_MS) return null;
    return parsed;
  } catch (err) {
    return null;
  }
}

function writeBox(key, data) {
  const box = store();
  if (!box) return;
  try {
    box.setItem(key, JSON.stringify({ v: VERSION, at: Date.now(), data }));
  } catch (err) {
    /* 配额满或被禁用：放弃缓存，页面照常走网络 */
  }
}

/** 上一次的登录身份（没缓存过或已过期 → null）。 */
export function readBootUser() {
  const box = readBox(KEY_USER);
  const user = box?.data;
  if (!user || typeof user.id !== "number" || !user.username) return null;
  return { id: user.id, username: user.username, is_admin: Boolean(user.is_admin) };
}

/** 登录成功 / 校验通过后记住身份，供下次刷新先出壳。 */
export function writeBootUser(user) {
  if (!user || typeof user.id !== "number") return;
  writeBox(KEY_USER, {
    id: user.id,
    username: user.username,
    is_admin: Boolean(user.is_admin),
  });
}

/** 取某个路由上次渲染完成的 HTML（uid 对不上就返回 null）。 */
export function readPage(uid, route) {
  const box = readBox(KEY_PAGE + route);
  const data = box?.data;
  if (!data || data.uid !== uid || typeof data.html !== "string" || !data.html) return null;
  return data.html;
}

/** 渲染完成后把 #route-view 的 HTML 存下来，下次刷新先贴这一屏。 */
export function writePage(uid, route, html) {
  if (!uid || typeof html !== "string" || !html) return;
  writeBox(KEY_PAGE + route, { uid, html });
}

/** 退出登录、401、换账号时全清。 */
export function clearBootCache() {
  const box = store();
  if (!box) return;
  try {
    const keys = [];
    for (let i = 0; i < box.length; i += 1) {
      const key = box.key(i);
      if (key && key.startsWith(PREFIX)) keys.push(key);
    }
    keys.forEach((key) => box.removeItem(key));
  } catch (err) {
    /* 清不掉也不影响正确性：uid 不匹配时读取端会自己拒绝 */
  }
}

export const bootCacheKeys = { USER: KEY_USER, PAGE: KEY_PAGE };
