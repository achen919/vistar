import { api, ApiError, clearCsrfToken, setUnauthorizedHandler } from "./api.js";
import { escapeHtml, icon } from "./ui.js";
import { renderDashboard } from "./pages/dashboard.js";
import { renderArticles } from "./pages/articles.js";
import { renderEditor } from "./pages/editor.js";
import { renderCategories } from "./pages/categories.js";
import { renderAnalytics } from "./pages/analytics.js";

const app = document.querySelector("#app");
const confirmDialog = document.querySelector("#globalConfirmDialog");
const toastRegion = document.querySelector("#toastRegion");

const routes = [
  { match: /^\/admin\/?$/, title: "仪表盘", description: "博客运营概览", render: renderDashboard },
  { match: /^\/admin\/articles\/?$/, title: "文章管理", description: "管理全部内容与发布状态", render: renderArticles },
  { match: /^\/admin\/articles\/new\/?$/, title: "新建文章", description: "撰写并发布新的博客内容", render: renderEditor },
  { match: /^\/admin\/articles\/edit\/?$/, title: "编辑文章", description: "修改文章内容与发布状态", render: renderEditor },
  { match: /^\/admin\/categories\/?$/, title: "分类管理", description: "维护分类并调整前台展示顺序", render: renderCategories },
  { match: /^\/admin\/analytics\/?$/, title: "数据统计", description: "查看网站访问与内容表现", render: renderAnalytics },
];

let sessionInfo = null;
let pageController = null;
let pageCleanup = null;
let leaveGuard = null;
let currentLocation = `${window.location.pathname}${window.location.search}`;

function authenticated(payload) {
  if (!payload || payload.authenticated === false) return false;
  return payload.authenticated === true || Boolean(payload.user || payload.username || payload.ok);
}

function sessionUser(payload) {
  if (typeof payload?.user === "string") return payload.user;
  return payload?.user?.username || payload?.username || "管理员";
}

function renderLogin(message = "") {
  pageController?.abort();
  pageController = null;
  pageCleanup?.();
  pageCleanup = null;
  leaveGuard = null;
  sessionInfo = null;
  document.body.classList.remove("nav-open");
  document.title = "登录 | 博客管理后台";

  app.innerHTML = `
    <main class="login-screen">
      <section class="login-intro" aria-label="博客管理后台介绍">
        <a class="login-brand" href="/" target="_blank" rel="noopener noreferrer">
          <span class="brand-mark">辰</span>
          <span>
            <strong>阿辰的博客</strong>
            <small>内容管理后台</small>
          </span>
        </a>
        <div class="login-message">
          <p class="eyebrow">CONTENT OPERATIONS</p>
          <h1>专注写作，清晰管理每一篇内容。</h1>
          <p>文章、分类与访问数据统一管理。登录后可进入安全的管理工作区。</p>
        </div>
        <p class="login-copyright">仅限授权管理员访问</p>
      </section>
      <section class="login-panel">
        <form id="loginForm" class="login-card" autocomplete="off">
          <div class="login-card-head">
            <span class="mobile-brand brand-mark">辰</span>
            <p class="eyebrow">WELCOME BACK</p>
            <h2>登录管理后台</h2>
            <p>请输入你的管理员账号和密码。</p>
          </div>
          <div id="loginError" class="form-alert ${message ? "" : "is-hidden"}" role="alert">
            ${message ? escapeHtml(message) : ""}
          </div>
          <label class="field">
            <span class="field-label">用户名</span>
            <input name="username" type="text" autocomplete="off" required autofocus />
          </label>
          <label class="field">
            <span class="field-label">密码</span>
            <input name="password" type="password" autocomplete="off" required />
          </label>
          <button class="button button-primary button-lg button-block" type="submit">
            <span>登录</span>
          </button>
          <p class="login-hint">页面不包含默认账号或密码；凭据仅用于本次登录。</p>
        </form>
      </section>
    </main>
  `;

  const form = app.querySelector("#loginForm");
  const errorBox = app.querySelector("#loginError");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const data = new FormData(form);
    const username = String(data.get("username") || "").trim();
    const password = String(data.get("password") || "");
    if (!username || !password) return;

    button.disabled = true;
    button.innerHTML = '<span class="spinner spinner-light" aria-hidden="true"></span><span>正在登录…</span>';
    errorBox.classList.add("is-hidden");
    try {
      let payload = await api.login({ username, password });
      if (!authenticated(payload)) payload = await api.session();
      if (!authenticated(payload)) throw new ApiError("登录状态无效，请重试。", 401);
      sessionInfo = payload;
      renderShell();
      await renderRoute();
    } catch (error) {
      if (error.name === "AbortError") return;
      errorBox.textContent = error.message || "登录失败，请检查账号和密码。";
      errorBox.classList.remove("is-hidden");
      form.querySelector('input[name="password"]').value = "";
      form.querySelector('input[name="password"]').focus();
    } finally {
      button.disabled = false;
      button.innerHTML = "<span>登录</span>";
    }
  });
}

function renderShell() {
  app.innerHTML = `
    <div class="admin-shell">
      <div class="mobile-overlay" data-close-nav></div>
      <aside class="sidebar" aria-label="管理后台导航">
        <div class="sidebar-head">
          <a class="sidebar-brand" href="/admin/" data-route>
            <span class="brand-mark">辰</span>
            <span>
              <strong>阿辰的博客</strong>
              <small>管理后台</small>
            </span>
          </a>
          <button type="button" class="icon-button sidebar-close" data-close-nav aria-label="关闭导航">${icon("close")}</button>
        </div>
        <nav class="sidebar-nav">
          <p class="nav-section-label">工作台</p>
          <a href="/admin/" data-route data-nav="dashboard">${icon("dashboard")}<span>仪表盘</span></a>
          <p class="nav-section-label">内容管理</p>
          <a href="/admin/articles/" data-route data-nav="articles">${icon("articles")}<span>文章管理</span></a>
          <a href="/admin/categories/" data-route data-nav="categories">${icon("categories")}<span>分类管理</span></a>
          <p class="nav-section-label">运营分析</p>
          <a href="/admin/analytics/" data-route data-nav="analytics">${icon("analytics")}<span>数据统计</span></a>
        </nav>
        <div class="sidebar-foot">
          <a href="/" target="_blank" rel="noopener noreferrer">${icon("external")}<span>查看博客前台</span></a>
          <button type="button" data-logout>${icon("logout")}<span>退出登录</span></button>
        </div>
      </aside>
      <div class="admin-main">
        <header class="topbar">
          <button type="button" class="icon-button menu-button" data-open-nav aria-label="打开导航">${icon("menu")}</button>
          <div class="page-heading">
            <p id="pageDescription"></p>
            <h1 id="pageTitle">管理后台</h1>
          </div>
          <div class="topbar-actions">
            <a class="button button-secondary button-sm desktop-only" href="/" target="_blank" rel="noopener noreferrer">${icon("external", 16)}查看网站</a>
            <div class="user-chip">
              <span class="user-avatar" aria-hidden="true">${escapeHtml(sessionUser(sessionInfo).slice(0, 1).toUpperCase())}</span>
              <span>
                <small>当前账号</small>
                <strong>${escapeHtml(sessionUser(sessionInfo))}</strong>
              </span>
            </div>
          </div>
        </header>
        <main id="pageContent" class="page-content" tabindex="-1"></main>
      </div>
    </div>
  `;

  app.removeEventListener("click", handleShellClick);
  app.addEventListener("click", handleShellClick);
}

async function handleShellClick(event) {
  const routeLink = event.target.closest("a[data-route]");
  if (routeLink) {
    event.preventDefault();
    await navigate(`${routeLink.pathname}${routeLink.search}`);
    return;
  }
  if (event.target.closest("[data-open-nav]")) {
    document.body.classList.add("nav-open");
  }
  if (event.target.closest("[data-close-nav]")) {
    document.body.classList.remove("nav-open");
  }
  if (event.target.closest("[data-logout]")) {
    if (!(await canLeave())) return;
    const confirmed = await confirmAction({
      title: "退出管理后台？",
      message: "退出后需要重新输入管理员账号和密码。",
      confirmLabel: "退出登录",
      danger: false,
    });
    if (!confirmed) return;
    try {
      await api.logout();
    } catch (error) {
      if (error.status !== 401) toast(error.message, "error");
    } finally {
      clearCsrfToken();
      renderLogin();
    }
  }
}

function routeFor(pathname) {
  return routes.find((route) => route.match.test(pathname));
}

function updateNavigation(route) {
  const section = route?.match === routes[0].match
    ? "dashboard"
    : route?.match === routes[4].match
      ? "categories"
      : route?.match === routes[5].match
        ? "analytics"
        : "articles";
  app.querySelectorAll("[data-nav]").forEach((link) => {
    const active = link.dataset.nav === section;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

async function renderRoute() {
  const route = routeFor(window.location.pathname);
  const content = app.querySelector("#pageContent");
  if (!content) return;

  pageController?.abort();
  pageCleanup?.();
  pageCleanup = null;
  leaveGuard = null;
  pageController = new AbortController();
  document.body.classList.remove("nav-open");
  currentLocation = `${window.location.pathname}${window.location.search}`;

  if (!route) {
    app.querySelector("#pageTitle").textContent = "页面不存在";
    app.querySelector("#pageDescription").textContent = "404";
    document.title = "页面不存在 | 博客管理后台";
    content.innerHTML = `
      <section class="not-found">
        <p>404</p>
        <h2>没有找到这个管理页面</h2>
        <a href="/admin/" class="button button-primary" data-route>返回仪表盘</a>
      </section>
    `;
    updateNavigation(null);
    return;
  }

  app.querySelector("#pageTitle").textContent = route.title;
  app.querySelector("#pageDescription").textContent = route.description;
  document.title = `${route.title} | 博客管理后台`;
  updateNavigation(route);
  content.innerHTML = '<div class="page-loader"><span class="spinner"></span><span>正在加载页面…</span></div>';

  const context = {
    api,
    container: content,
    signal: pageController.signal,
    navigate,
    toast,
    confirm: confirmAction,
    setLeaveGuard(guard) {
      leaveGuard = typeof guard === "function" ? guard : null;
    },
    setTitle(title, description) {
      if (title) {
        app.querySelector("#pageTitle").textContent = title;
        document.title = `${title} | 博客管理后台`;
      }
      if (description) app.querySelector("#pageDescription").textContent = description;
    },
  };

  try {
    const cleanup = await route.render(context);
    if (!pageController.signal.aborted && typeof cleanup === "function") pageCleanup = cleanup;
    content.focus({ preventScroll: true });
  } catch (error) {
    if (error.name === "AbortError" || error.status === 401) return;
    content.innerHTML = `
      <section class="fatal-page-error">
        ${icon("warning", 30)}
        <h2>页面暂时无法显示</h2>
        <p>${escapeHtml(error.message || "发生了未知错误。")}</p>
        <button type="button" class="button button-primary" data-page-reload>重新加载</button>
      </section>
    `;
    content.querySelector("[data-page-reload]").addEventListener("click", () => renderRoute());
  }
}

async function canLeave() {
  if (!leaveGuard) return true;
  return Boolean(await leaveGuard());
}

async function navigate(target, options = {}) {
  const url = new URL(target, window.location.origin);
  if (url.origin !== window.location.origin) {
    window.location.assign(url.href);
    return;
  }
  if (!options.force && !(await canLeave())) return;
  if (options.replace) history.replaceState({}, "", `${url.pathname}${url.search}`);
  else history.pushState({}, "", `${url.pathname}${url.search}`);
  await renderRoute();
}

function toast(message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast toast-${type}`;
  item.setAttribute("role", type === "error" ? "alert" : "status");
  item.innerHTML = `
    <span class="toast-indicator" aria-hidden="true"></span>
    <span>${escapeHtml(message)}</span>
    <button type="button" aria-label="关闭提示">${icon("close", 16)}</button>
  `;
  item.querySelector("button").addEventListener("click", () => item.remove());
  toastRegion.append(item);
  window.setTimeout(() => {
    item.classList.add("toast-out");
    window.setTimeout(() => item.remove(), 220);
  }, type === "error" ? 7000 : 4000);
}

function confirmAction(options = {}) {
  const {
    title = "确认操作",
    message = "此操作无法撤销。",
    confirmLabel = "确认",
    danger = true,
  } = options;
  confirmDialog.querySelector("#confirmDialogTitle").textContent = title;
  confirmDialog.querySelector("[data-dialog-message]").textContent = message;
  confirmDialog.querySelector("[data-dialog-confirm]").textContent = confirmLabel;
  confirmDialog.querySelector("[data-dialog-confirm]").className = `button ${danger ? "button-danger" : "button-primary"}`;
  confirmDialog.querySelector("[data-dialog-icon]").classList.toggle("dialog-icon-neutral", !danger);
  confirmDialog.returnValue = "";
  confirmDialog.showModal();
  return new Promise((resolve) => {
    confirmDialog.addEventListener("close", () => resolve(confirmDialog.returnValue === "confirm"), { once: true });
  });
}

setUnauthorizedHandler(() => {
  clearCsrfToken();
  renderLogin("登录已过期，请重新登录。");
});

window.addEventListener("popstate", async () => {
  const nextLocation = `${window.location.pathname}${window.location.search}`;
  if (!(await canLeave())) {
    history.pushState({}, "", currentLocation);
    return;
  }
  currentLocation = nextLocation;
  await renderRoute();
});

window.addEventListener("beforeunload", (event) => {
  if (!leaveGuard) return;
  event.preventDefault();
  event.returnValue = "";
});

async function bootstrap() {
  try {
    const payload = await api.session();
    if (!authenticated(payload)) {
      renderLogin();
      return;
    }
    sessionInfo = payload;
    renderShell();
    await renderRoute();
  } catch (error) {
    if (error.status === 401) renderLogin();
    else renderLogin(`暂时无法验证登录状态：${error.message}`);
  }
}

bootstrap();
