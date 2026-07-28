import {
  emptyState,
  errorState,
  escapeHtml,
  formatDate,
  icon,
  loadingState,
  renderTags,
  statusBadge,
  unwrap,
} from "../ui.js?v=20260728-console-2";

export async function renderArticles(context) {
  const { api, container, signal, confirm, toast } = context;
  let posts = [];
  let filteredPosts = [];

  container.innerHTML = `
    <section class="page-section">
      <div class="page-toolbar">
        <div class="section-header">
          <div>
            <h2>全部文章</h2>
            <p id="articleCount">正在读取文章列表…</p>
          </div>
        </div>
        <div class="toolbar-actions">
          <button type="button" class="button button-secondary" data-refresh>${icon("refresh", 16)}刷新</button>
          <a href="/admin/articles/new/" class="button button-primary" data-route>${icon("plus", 17)}新建文章</a>
        </div>
      </div>
      <section class="panel">
        <div class="panel-header filter-bar">
          <div class="filter-group">
            <label class="search-box">
              ${icon("search", 17)}
              <span class="is-hidden">搜索文章</span>
              <input type="search" placeholder="搜索标题、slug 或标签…" data-search />
            </label>
            <select data-status aria-label="按发布状态筛选">
              <option value="all">全部状态</option>
              <option value="published">已发布</option>
              <option value="draft">草稿</option>
            </select>
            <select data-category aria-label="按分类筛选">
              <option value="all">全部分类</option>
            </select>
          </div>
        </div>
        <div id="articleTable">${loadingState("正在加载文章…")}</div>
      </section>
    </section>
  `;

  const tableTarget = container.querySelector("#articleTable");
  const searchInput = container.querySelector("[data-search]");
  const statusSelect = container.querySelector("[data-status]");
  const categorySelect = container.querySelector("[data-category]");

  function filterPosts() {
    const query = searchInput.value.trim().toLowerCase();
    const status = statusSelect.value;
    const category = categorySelect.value;
    filteredPosts = posts.filter((post) => {
      const searchable = [
        post.title,
        post.slug,
        ...(Array.isArray(post.tags) ? post.tags : []),
        ...(Array.isArray(post.categories) ? post.categories : []),
      ].join(" ").toLowerCase();
      const matchesQuery = !query || searchable.includes(query);
      const matchesStatus = status === "all"
        || (status === "draft" && post.draft)
        || (status === "published" && !post.draft);
      const matchesCategory = category === "all" || post.categories?.includes(category);
      return matchesQuery && matchesStatus && matchesCategory;
    });
    renderTable();
  }

  function renderTable() {
    container.querySelector("#articleCount").textContent = filteredPosts.length === posts.length
      ? `共 ${posts.length} 篇文章`
      : `筛选出 ${filteredPosts.length} / ${posts.length} 篇文章`;
    if (!filteredPosts.length) {
      tableTarget.innerHTML = emptyState(
        posts.length ? "没有匹配的文章" : "还没有文章",
        posts.length ? "尝试更换搜索词或筛选条件。" : "新建第一篇内容，开始维护你的博客。",
        posts.length ? "" : '<a href="/admin/articles/new/" class="button button-primary button-sm" data-route>新建文章</a>',
      );
      return;
    }
    tableTarget.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>文章</th>
              <th>状态</th>
              <th>分类</th>
              <th>标签</th>
              <th>更新日期</th>
              <th aria-label="操作"></th>
            </tr>
          </thead>
          <tbody>
            ${filteredPosts.map((post) => `
              <tr data-post-slug="${escapeHtml(post.slug)}">
                <td>
                  <a class="table-title" href="/admin/articles/edit/?slug=${encodeURIComponent(post.slug)}" data-route>${escapeHtml(post.title || "未命名文章")}</a>
                  <span class="table-subtitle">${escapeHtml(post.slug || "")}</span>
                </td>
                <td>${statusBadge(Boolean(post.draft))}</td>
                <td>${renderTags(post.categories)}</td>
                <td>${renderTags(post.tags)}</td>
                <td>${escapeHtml(formatDate(post.updatedAt || post.date, Boolean(post.updatedAt)))}</td>
                <td>
                  <div class="table-actions">
                    <a class="icon-button" href="/admin/articles/edit/?slug=${encodeURIComponent(post.slug)}" data-route aria-label="编辑 ${escapeHtml(post.title || post.slug)}">${icon("edit", 16)}</a>
                    <button type="button" class="icon-button" data-delete="${escapeHtml(post.slug)}" aria-label="删除 ${escapeHtml(post.title || post.slug)}">${icon("trash", 16)}</button>
                  </div>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderCategoryOptions() {
    const selected = categorySelect.value;
    const categories = [...new Set(posts.flatMap((post) => Array.isArray(post.categories) ? post.categories : []))]
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b, "zh-CN"));
    categorySelect.innerHTML = '<option value="all">全部分类</option>';
    for (const category of categories) {
      const option = document.createElement("option");
      option.value = category;
      option.textContent = category;
      categorySelect.append(option);
    }
    if ([...categorySelect.options].some((option) => option.value === selected)) {
      categorySelect.value = selected;
    }
  }

  async function load() {
    tableTarget.innerHTML = loadingState("正在加载文章…");
    try {
      const payload = await api.posts(signal);
      if (signal.aborted) return;
      const collection = unwrap(payload, "posts", []);
      posts = Array.isArray(collection) ? collection : [];
      posts.sort((a, b) => String(b.updatedAt || b.date || "").localeCompare(String(a.updatedAt || a.date || "")));
      renderCategoryOptions();
      filterPosts();
    } catch (error) {
      if (error.name === "AbortError") return;
      tableTarget.innerHTML = errorState(error.message);
      tableTarget.querySelector("[data-retry]")?.addEventListener("click", load, { once: true });
    }
  }

  async function deletePost(slug) {
    const post = posts.find((item) => item.slug === slug);
    if (!post) return;
    const accepted = await confirm({
      title: "删除这篇文章？",
      message: `“${post.title || post.slug}”将从博客中删除。该操作发布后无法在管理端撤销。`,
      confirmLabel: "删除文章",
    });
    if (!accepted) return;
    const button = container.querySelector(`[data-delete="${CSS.escape(slug)}"]`);
    if (button) button.disabled = true;
    try {
      await api.deletePost(slug, post.version, signal);
      posts = posts.filter((item) => item.slug !== slug);
      renderCategoryOptions();
      filterPosts();
      toast("文章已删除。");
    } catch (error) {
      if (error.name === "AbortError") return;
      const message = error.status === 409
        ? "文章已被其他会话修改，请刷新列表后重试。"
        : error.message;
      toast(message, "error");
      if (button) button.disabled = false;
    }
  }

  searchInput.addEventListener("input", filterPosts);
  statusSelect.addEventListener("change", filterPosts);
  categorySelect.addEventListener("change", filterPosts);
  container.querySelector("[data-refresh]").addEventListener("click", load);
  tableTarget.addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-delete]");
    if (deleteButton) deletePost(deleteButton.dataset.delete);
  });

  await load();
}
