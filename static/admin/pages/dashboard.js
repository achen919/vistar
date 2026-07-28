import {
  emptyState,
  errorState,
  escapeHtml,
  formatDate,
  formatNumber,
  icon,
  loadingState,
  renderTags,
  renderTrendChart,
  statusBadge,
} from "../ui.js";

export async function renderDashboard(context) {
  const { api, container, signal } = context;

  container.innerHTML = `
    <section class="page-section">
      <div id="dashboardMetrics" class="metric-grid">
        ${Array.from({ length: 4 }, () => '<div class="metric-card"><div class="loading-state"><span class="spinner"></span></div></div>').join("")}
      </div>
      <div class="dashboard-grid">
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>访问趋势</h2>
              <p>近期网站浏览量与独立访客</p>
            </div>
            <a href="/admin/analytics/" class="button button-ghost button-sm" data-route>查看完整数据</a>
          </div>
          <div id="dashboardTrend" class="panel-body">${loadingState()}</div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>快捷操作</h2>
              <p>常用内容管理入口</p>
            </div>
          </div>
          <div class="panel-body quick-actions">
            <a class="quick-action" href="/admin/articles/new/" data-route aria-label="新建文章，进入独立编辑器开始写作">
              <span class="metric-icon">${icon("plus", 18)}</span>
              <span><strong>新建文章</strong><p>进入独立编辑器开始写作</p></span>
              ${icon("chevronRight", 16)}
            </a>
            <a class="quick-action" href="/admin/categories/" data-route aria-label="管理分类，新增分类或调整展示顺序">
              <span class="metric-icon success">${icon("categories", 18)}</span>
              <span><strong>管理分类</strong><p>新增分类或调整展示顺序</p></span>
              ${icon("chevronRight", 16)}
            </a>
            <a class="quick-action" href="/" target="_blank" rel="noopener noreferrer" aria-label="访问博客前台">
              <span class="metric-icon warning">${icon("external", 18)}</span>
              <span><strong>访问博客</strong><p>查看前台最新发布效果</p></span>
              ${icon("chevronRight", 16)}
            </a>
          </div>
        </section>
      </div>
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>最近更新</h2>
            <p>最近创建或编辑的文章</p>
          </div>
          <a href="/admin/articles/" class="button button-secondary button-sm" data-route>全部文章</a>
        </div>
        <div id="recentPosts">${loadingState("正在加载近期文章…")}</div>
      </section>
    </section>
  `;

  async function load() {
    try {
      const payload = await api.overview(signal);
      if (signal.aborted) return;
      renderMetrics(container.querySelector("#dashboardMetrics"), payload.totals || {});
      container.querySelector("#dashboardTrend").innerHTML = renderTrendChart(payload.trend || []);
      renderRecentPosts(container.querySelector("#recentPosts"), payload.recentPosts || []);
    } catch (error) {
      if (error.name === "AbortError") return;
      container.querySelector("#dashboardMetrics").innerHTML = `
        <div class="panel span-full">${errorState(error.message)}</div>
      `;
      container.querySelector("#dashboardTrend").innerHTML = errorState(error.message);
      container.querySelector("#recentPosts").innerHTML = errorState(error.message);
      container.querySelectorAll("[data-retry]").forEach((button) => {
        button.addEventListener("click", () => {
          container.querySelector("#dashboardTrend").innerHTML = loadingState();
          container.querySelector("#recentPosts").innerHTML = loadingState();
          load();
        }, { once: true });
      });
    }
  }

  await load();
}

function renderMetrics(target, totals) {
  const metrics = [
    {
      label: "文章总数",
      value: totals.posts,
      note: "已发布与草稿内容",
      icon: "articles",
      style: "",
    },
    {
      label: "分类数量",
      value: totals.categories,
      note: "前台启用的内容分类",
      icon: "categories",
      style: "success",
    },
    {
      label: "日志范围浏览量",
      value: totals.pv,
      note: `今日 ${formatNumber(totals.todayPv)} 次浏览`,
      icon: "analytics",
      style: "",
    },
    {
      label: "日志范围访客数",
      value: totals.uv,
      note: `今日 ${formatNumber(totals.todayUv)} 位访客`,
      icon: "dashboard",
      style: "warning",
    },
  ];
  target.innerHTML = metrics.map((metric) => `
    <article class="metric-card">
      <div class="metric-card-head">
        <span>${escapeHtml(metric.label)}</span>
        <span class="metric-icon ${metric.style}">${icon(metric.icon, 17)}</span>
      </div>
      <p class="metric-value">${formatNumber(metric.value)}</p>
      <p class="metric-note">${escapeHtml(metric.note)}</p>
    </article>
  `).join("");
}

function renderRecentPosts(target, posts) {
  if (!Array.isArray(posts) || !posts.length) {
    target.innerHTML = emptyState(
      "还没有文章",
      "创建第一篇文章后，它会显示在这里。",
      '<a href="/admin/articles/new/" class="button button-primary button-sm" data-route>新建文章</a>',
    );
    return;
  }

  target.innerHTML = `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>文章</th>
            <th>状态</th>
            <th>分类</th>
            <th>日期</th>
            <th aria-label="操作"></th>
          </tr>
        </thead>
        <tbody>
          ${posts.slice(0, 6).map((post) => `
            <tr>
              <td>
                <a class="table-title" href="/admin/articles/edit/?slug=${encodeURIComponent(post.slug)}" data-route>${escapeHtml(post.title || "未命名文章")}</a>
                <span class="table-subtitle">${escapeHtml(post.slug || "")}</span>
              </td>
              <td>${statusBadge(Boolean(post.draft))}</td>
              <td>${renderTags(post.categories)}</td>
              <td>${escapeHtml(formatDate(post.updatedAt || post.date, Boolean(post.updatedAt)))}</td>
              <td>
                <div class="table-actions">
                  <a class="icon-button" href="/admin/articles/edit/?slug=${encodeURIComponent(post.slug)}" data-route aria-label="编辑 ${escapeHtml(post.title || post.slug)}">${icon("edit", 16)}</a>
                </div>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}
