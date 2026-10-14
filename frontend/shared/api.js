/* ==========================================================================
   共享 API 客户端：统一 fetch 封装 + 错误翻译
   - 会话基于 Cookie，必须带 credentials: "same-origin"
   - 后端错误体 { detail, code, ...extra } 中的 detail 是 i18n key
   ========================================================================== */

import { translateError } from "./i18n.js";

export class ApiError extends Error {
  constructor(status, body, message) {
    super(message || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.code = (body && (body.code || body.detail)) || `http.${status}`;
  }

  get isAuthError() {
    return this.status === 401;
  }
}

/* 站点根路径前缀：既支持部署在域名根目录（/user/... -> /api/...），也支持部署
   在子路径（例如 nginx 的 /sleep/user/... -> /sleep/api/...）。只有当页面本身
   位于 /user/、/admin/、/shared/ 这些应用挂载点下时才取第一段作为前缀，其它
   情况（例如页面挂在站点根 /login 之类）直接按根目录请求，避免生成 /login/api 这种
   不存在的地址。 */
const API_PREFIX = (() => {
  const segments = window.location.pathname.split("/").filter(Boolean);
  const first = segments[0] || "";
  const known = ["user", "admin", "shared"];
  // 子路径部署时 first 是站点前缀（如 sleep），此时第二段才是 user/admin/shared。
  if (segments.length > 1 && known.includes(segments[1])) return `/${first}/api`;
  if (known.includes(first)) return "/api";
  return "/api";
})();

function buildQuery(params) {
  if (!params) return "";
  const search = new URLSearchParams();
  Object.keys(params).forEach((key) => {
    const value = params[key];
    if (value === undefined || value === null || value === "") return;
    search.append(key, String(value));
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

async function parseBody(response) {
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    try {
      return await response.json();
    } catch (err) {
      return null;
    }
  }
  try {
    const text = await response.text();
    return text ? { detail: text } : null;
  } catch (err) {
    return null;
  }
}

async function request(method, path, { body, params, headers } = {}) {
  const init = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(headers || {}) },
  };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(`${API_PREFIX}${path}${buildQuery(params)}`, init);
  } catch (err) {
    throw new ApiError(0, null, translateError({ detail: "error.server" }));
  }

  if (response.status === 204) return null;

  const payload = await parseBody(response);
  if (!response.ok) {
    throw new ApiError(response.status, payload, translateError(payload));
  }
  return payload;
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body) => request("POST", path, { body }),
  put: (path, body) => request("PUT", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
  del: (path, body) => request("DELETE", path, { body }),
};

/* --------------------------- 用户端接口 --------------------------- */

export const authApi = {
  me: () => api.get("/auth/me"),
  login: (payload) => api.post("/auth/login", payload),
  logout: () => api.post("/auth/logout"),
  changePassword: (payload) => api.post("/auth/password", payload),
};

export const recordApi = {
  list: (params) => api.get("/records", params),
  meta: () => api.get("/records/meta"),
  get: (id) => api.get(`/records/${id}`),
  create: (payload) => api.post("/records", payload),
  update: (id, payload) => api.put(`/records/${id}`, payload),
  remove: (id) => api.del(`/records/${id}`),
  batchRemove: (ids) => api.post("/records/batch-delete", { ids }),
};

export const adviceApi = {
  options: () => api.get("/advice/options"),
  generate: (payload) => api.post("/advice", payload),
  plan: () => api.get("/advice/plan"),
  checkin: (payload) => api.post("/advice/checkin", payload),
  ackNotice: (notice) => api.post("/advice/notice-ack", { notice }),
  replan: (strategy) => api.post("/advice/replan", { strategy }),
};

export const settingsApi = {
  get: () => api.get("/settings"),
  update: (payload) => api.put("/settings", payload),
  updateTarget: (payload) => api.put("/settings/target", payload),
};

export const accountApi = {
  deletionStatus: () => api.get("/account/deletion"),
  submitDeletion: (payload) => api.post("/account/deletion", payload),
  cancelDeletion: () => api.del("/account/deletion"),
};

/* --------------------------- 管理端接口 --------------------------- */

export const adminApi = {
  parts: () => api.get("/auth/admin/parts"),
  me: () => api.get("/auth/admin/me"),
  login: (payload) => api.post("/auth/admin/login", payload),
  logout: () => api.post("/auth/admin/logout"),
  stats: () => api.get("/admin/stats"),
  cleanup: () => api.post("/admin/cleanup"),

  users: (params) => api.get("/admin/users", params),
  user: (id) => api.get(`/admin/users/${id}`),
  createUser: (payload) => api.post("/admin/users", payload),
  updateUser: (id, payload) => api.patch(`/admin/users/${id}`, payload),
  resetPassword: (id, payload) => api.post(`/admin/users/${id}/password`, payload),
  unlockUser: (id) => api.post(`/admin/users/${id}/unlock`),
  deleteUser: (id) => api.del(`/admin/users/${id}`),

  userRecords: (id, params) => api.get(`/admin/users/${id}/records`, params),
  deleteUserRecord: (userId, recordId) => api.del(`/admin/users/${userId}/records/${recordId}`),
  batchDeleteUserRecords: (userId, ids) =>
    api.post(`/admin/users/${userId}/records/batch-delete`, { ids }),

  deletionRequests: (params) => api.get("/admin/deletion-requests", params),
  decideDeletion: (id, payload) => api.post(`/admin/deletion-requests/${id}`, payload),

  logs: (params) => api.get("/admin/logs", params),
};
