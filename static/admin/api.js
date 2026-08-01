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
      if (contentType.includes("application/json")) {
        payload = await response.json();
      } else {
        await response.text();
      }
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

function reportUploadProgress(callback, value) {
  if (typeof callback !== "function") return;
  try {
    callback(value);
  } catch {
    // Upload progress is presentational and must not break the request.
  }
}

async function uploadImage(file, signal, onProgress) {
  if (!file || typeof file.name !== "string") {
    throw new ApiError("请选择要上传的图片。");
  }
  const form = new FormData();
  form.append("file", file, file.name);
  reportUploadProgress(onProgress, 0);

  let response;
  try {
    response = await fetch(`${API_BASE}/uploads/images`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: form,
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("图片上传失败，请检查网络后重试。");
  }

  const contentType = response.headers.get("Content-Type") || "";
  let payload = null;
  try {
    if (contentType.includes("application/json")) {
      payload = await response.json();
    } else {
      await response.text();
    }
  } catch {
    payload = null;
  }
  captureCsrf(response, payload);

  if (!response.ok) {
    const error = new ApiError(
      payload?.error || `图片上传失败（${response.status}）`,
      response.status,
      payload,
    );
    if (response.status === 401 && unauthorizedHandler) {
      unauthorizedHandler(error);
    }
    throw error;
  }
  reportUploadProgress(onProgress, 100);
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
  uploadImage(file, signal, onProgress) {
    return uploadImage(file, signal, onProgress);
  },
  todos(date, signal) {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request(`/todos${query}`, { signal });
  },
  todoStats(signal, days = 30, endDate = "") {
    const params = new URLSearchParams({ days: String(days) });
    if (endDate) params.set("endDate", endDate);
    return request(`/todos/stats?${params}`, { signal });
  },
  createTodo(todo, signal) {
    return request("/todos", { method: "POST", body: todo, signal });
  },
  updateTodo(identifier, todo, signal) {
    return request(`/todos/${encodeURIComponent(identifier)}`, {
      method: "PUT",
      body: todo,
      signal,
    });
  },
  deleteTodo(identifier, signal) {
    return request(`/todos/${encodeURIComponent(identifier)}`, {
      method: "DELETE",
      signal,
    });
  },
  todoPlans(signal) {
    return request("/todo-plans", { signal });
  },
  createTodoPlan(plan, signal) {
    return request("/todo-plans", { method: "POST", body: plan, signal });
  },
  updateTodoPlan(identifier, plan, signal) {
    return request(`/todo-plans/${encodeURIComponent(identifier)}`, {
      method: "PUT",
      body: plan,
      signal,
    });
  },
  deleteTodoPlan(identifier, signal) {
    return request(`/todo-plans/${encodeURIComponent(identifier)}`, {
      method: "DELETE",
      signal,
    });
  },
  updateTodoOccurrence(identifier, date, completed, signal) {
    return request(
      `/todo-plans/${encodeURIComponent(identifier)}/occurrences/${encodeURIComponent(date)}`,
      { method: "PUT", body: { completed }, signal },
    );
  },
};
