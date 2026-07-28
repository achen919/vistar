const API_BASE = "/admin/api";

let csrfToken = "";
let unauthorizedHandler = null;

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler;
}

export function clearCsrfToken() {
  csrfToken = "";
}

function captureCsrf(response, payload) {
  const token = response.headers.get("X-CSRF-Token") || payload?.csrfToken;
  if (typeof token === "string" && token) csrfToken = token;
}

async function request(path, options = {}) {
  const {
    method = "GET",
    body,
    signal,
    notifyUnauthorized = true,
  } = options;
  const upperMethod = method.toUpperCase();
  const isWrite = !["GET", "HEAD", "OPTIONS"].includes(upperMethod);
  const headers = new Headers(options.headers || {});

  headers.set("Accept", "application/json");
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (isWrite) headers.set("X-CSRF-Token", csrfToken);

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: upperMethod,
      credentials: "same-origin",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("网络连接失败，请检查网络后重试。");
  }

  const contentType = response.headers.get("Content-Type") || "";
  let payload = null;
  if (response.status !== 204) {
    try {
      payload = contentType.includes("application/json")
        ? await response.json()
        : { error: await response.text() };
    } catch {
      payload = null;
    }
  }
  captureCsrf(response, payload);

  if (!response.ok) {
    const error = new ApiError(payload?.error || `请求失败（${response.status}）`, response.status, payload);
    if (response.status === 401 && notifyUnauthorized && unauthorizedHandler) {
      unauthorizedHandler(error);
    }
    throw error;
  }
  return payload ?? {};
}

export const api = {
  session(signal) {
    return request("/session", { signal, notifyUnauthorized: false });
  },
  login(credentials, signal) {
    return request("/login", {
      method: "POST",
      body: credentials,
      signal,
      notifyUnauthorized: false,
    });
  },
  logout(signal) {
    return request("/logout", { method: "POST", body: {}, signal });
  },
  overview(signal) {
    return request("/overview", { signal });
  },
  posts(signal) {
    return request("/posts", { signal });
  },
  post(slug, signal) {
    return request(`/posts/${encodeURIComponent(slug)}`, { signal });
  },
  createPost(post, signal) {
    return request("/posts", { method: "POST", body: post, signal });
  },
  updatePost(slug, post, signal) {
    return request(`/posts/${encodeURIComponent(slug)}`, {
      method: "PUT",
      body: post,
      signal,
    });
  },
  deletePost(slug, version, signal) {
    return request(`/posts/${encodeURIComponent(slug)}`, {
      method: "DELETE",
      headers: version !== undefined && version !== null
        ? { "If-Match": String(version) }
        : {},
      signal,
    });
  },
  categories(signal) {
    return request("/categories", { signal });
  },
  updateCategories(categories, version, signal) {
    return request("/categories", {
      method: "PUT",
      body: { categories, version },
      signal,
    });
  },
  stats(days, signal) {
    return request(`/stats?days=${encodeURIComponent(days)}`, { signal });
  },
};
