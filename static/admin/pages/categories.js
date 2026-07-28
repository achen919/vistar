import {
  emptyState,
  errorState,
  escapeHtml,
  formatNumber,
  icon,
  loadingState,
} from "../ui.js?v=20260728-console-4";

export async function renderCategories(context) {
  const { api, container, signal, confirm, toast, setLeaveGuard } = context;
  let categories = [];
  let version = null;
  let baseline = "";
  let draggedIndex = null;
  let editingIndex = null;

  container.innerHTML = `
    <section class="page-section">
      <div class="page-toolbar">
        <div class="section-header">
          <div>
            <h2>文章分类</h2>
            <p>拖动手柄调整前台展示顺序；也可用方向键或上下按钮排序。</p>
          </div>
        </div>
        <div class="toolbar-actions">
          <button type="button" class="button button-secondary" data-refresh>${icon("refresh", 16)}刷新</button>
          <button type="button" class="button button-primary" data-add-category>${icon("plus", 17)}新增分类</button>
        </div>
      </div>

      <div id="categoryAlert" class="form-alert is-hidden" role="alert"></div>
      <section id="categoryPanel" class="panel category-panel">
        ${loadingState("正在加载分类…")}
      </section>
      <p id="sortStatus" class="sr-only" aria-live="polite"></p>
      <div id="categorySavebar" class="category-savebar is-hidden">
        <p>分类有尚未保存的更改。保存后将同步更新博客分类与排序。</p>
        <div class="toolbar-actions">
          <button type="button" class="button button-secondary button-sm" data-discard>撤销更改</button>
          <button type="button" class="button button-primary button-sm" data-save-categories>保存更改</button>
        </div>
      </div>

      <dialog id="categoryDialog" class="dialog" aria-labelledby="categoryDialogTitle">
        <form id="categoryForm" class="dialog-card" novalidate>
          <div class="dialog-header">
            <div>
              <h2 id="categoryDialogTitle">新增分类</h2>
              <p>分类名称会显示在文章与博客前台。</p>
            </div>
            <button type="button" class="icon-button" data-close-category-dialog aria-label="关闭">${icon("close", 18)}</button>
          </div>
          <div class="dialog-body">
            <label class="field">
              <span class="field-label">分类名称</span>
              <input name="name" type="text" maxlength="80" autocomplete="off" placeholder="例如：技术-前端开发" required />
              <span class="field-error is-hidden" data-category-error></span>
            </label>
            <label class="field">
              <span class="field-label">分类描述</span>
              <textarea name="description" rows="4" maxlength="240" placeholder="简要描述该分类涵盖的内容…"></textarea>
            </label>
          </div>
          <div class="dialog-actions">
            <button type="button" class="button button-secondary" data-close-category-dialog>取消</button>
            <button type="submit" class="button button-primary">确认</button>
          </div>
        </form>
      </dialog>
    </section>
  `;

  const panel = container.querySelector("#categoryPanel");
  const savebar = container.querySelector("#categorySavebar");
  const alertBox = container.querySelector("#categoryAlert");
  const dialog = container.querySelector("#categoryDialog");
  const dialogForm = container.querySelector("#categoryForm");

  function categorySnapshot(items = categories) {
    return JSON.stringify(items.map((category) => ({
      id: category.id ?? null,
      name: category.name,
      description: category.description || "",
    })));
  }

  function isDirty() {
    return categorySnapshot() !== baseline;
  }

  function syncDirtyState() {
    const dirty = isDirty();
    savebar.classList.toggle("is-hidden", !dirty);
    setLeaveGuard(dirty
      ? () => confirm({
        title: "放弃分类更改？",
        message: "分类名称、描述或排序有尚未保存的更改。",
        confirmLabel: "放弃更改",
      })
      : null);
  }

  function renderList() {
    if (!categories.length) {
      panel.innerHTML = emptyState(
        "还没有分类",
        "新增分类后，可在文章编辑器中选择它。",
        '<button type="button" class="button button-primary button-sm" data-add-category>新增分类</button>',
      );
      syncDirtyState();
      return;
    }
    panel.innerHTML = `
      <div class="category-list-head" aria-hidden="true">
        <span>排序</span>
        <span>分类名称</span>
        <span>描述</span>
        <span>文章数</span>
        <span>操作</span>
      </div>
      <div id="categoryList" role="list" aria-label="分类排序列表">
        ${categories.map((category, index) => `
          <article class="category-row" role="listitem" data-category-index="${index}">
            <button type="button" class="icon-button drag-handle" draggable="true" data-drag-handle aria-label="拖动 ${escapeHtml(category.name)} 调整顺序" title="拖动排序；聚焦后可按上下方向键">${icon("grip", 20)}</button>
            <div>
              <p class="category-name">${escapeHtml(category.name)}</p>
            </div>
            <p class="category-description">${escapeHtml(category.description || "暂无描述")}</p>
            <p class="usage-count">${formatNumber(category.usageCount)} 篇</p>
            <div class="category-actions">
              <div class="sort-buttons">
                <button type="button" class="icon-button" data-move="-1" data-index="${index}" aria-label="上移 ${escapeHtml(category.name)}" ${index === 0 ? "disabled" : ""}>${icon("chevronUp", 16)}</button>
                <button type="button" class="icon-button" data-move="1" data-index="${index}" aria-label="下移 ${escapeHtml(category.name)}" ${index === categories.length - 1 ? "disabled" : ""}>${icon("chevronDown", 16)}</button>
              </div>
              <button type="button" class="icon-button" data-edit-category="${index}" aria-label="编辑 ${escapeHtml(category.name)}">${icon("edit", 16)}</button>
              <button type="button" class="icon-button" data-delete-category="${index}" aria-label="删除 ${escapeHtml(category.name)}">${icon("trash", 16)}</button>
            </div>
          </article>
        `).join("")}
      </div>
    `;
    syncDirtyState();
  }

  async function load({ confirmDiscard = false } = {}) {
    if (confirmDiscard && isDirty()) {
      const accepted = await confirm({
        title: "刷新分类数据？",
        message: "刷新会丢弃当前尚未保存的分类更改。",
        confirmLabel: "刷新并放弃",
      });
      if (!accepted) return;
    }
    panel.innerHTML = loadingState("正在加载分类…");
    savebar.classList.add("is-hidden");
    alertBox.classList.add("is-hidden");
    setLeaveGuard(null);
    try {
      const payload = await api.categories(signal);
      if (signal.aborted) return;
      categories = Array.isArray(payload.categories)
        ? payload.categories.map((category) => ({ ...category }))
        : [];
      version = payload.version ?? null;
      baseline = categorySnapshot();
      renderList();
    } catch (error) {
      if (error.name === "AbortError") return;
      panel.innerHTML = errorState(error.message);
      panel.querySelector("[data-retry]")?.addEventListener("click", () => load(), { once: true });
    }
  }

  function openCategoryDialog(index = null) {
    editingIndex = index;
    const category = index === null ? null : categories[index];
    dialog.querySelector("#categoryDialogTitle").textContent = category ? "编辑分类" : "新增分类";
    dialogForm.elements.name.value = category?.name || "";
    dialogForm.elements.description.value = category?.description || "";
    const error = dialogForm.querySelector("[data-category-error]");
    error.textContent = "";
    error.classList.add("is-hidden");
    dialog.showModal();
    window.setTimeout(() => dialogForm.elements.name.focus(), 0);
  }

  function saveCategoryFromDialog(event) {
    event.preventDefault();
    const name = dialogForm.elements.name.value.trim();
    const description = dialogForm.elements.description.value.trim();
    const error = dialogForm.querySelector("[data-category-error]");
    if (!name) {
      error.textContent = "请输入分类名称。";
      error.classList.remove("is-hidden");
      dialogForm.elements.name.focus();
      return;
    }
    if (/[/\\\0]/.test(name) || name === "." || name === "..") {
      error.textContent = "分类名称不能包含路径分隔符。";
      error.classList.remove("is-hidden");
      dialogForm.elements.name.focus();
      return;
    }
    if (new TextEncoder().encode(name).length > 240) {
      error.textContent = "分类名称的 UTF-8 文件名过长，请缩短后重试。";
      error.classList.remove("is-hidden");
      dialogForm.elements.name.focus();
      return;
    }
    const duplicate = categories.some((category, index) => index !== editingIndex && category.name === name);
    if (duplicate) {
      error.textContent = "已存在同名分类。";
      error.classList.remove("is-hidden");
      dialogForm.elements.name.focus();
      return;
    }

    if (editingIndex === null) {
      categories.push({ name, description, usageCount: 0 });
    } else {
      categories[editingIndex] = {
        ...categories[editingIndex],
        name,
        description,
      };
    }
    dialog.close();
    renderList();
  }

  function moveCategory(from, to) {
    if (from === to || from < 0 || to < 0 || from >= categories.length || to >= categories.length) return;
    const [category] = categories.splice(from, 1);
    categories.splice(to, 0, category);
    renderList();
    container.querySelector("#sortStatus").textContent = `${category.name} 已移动到第 ${to + 1} 位。`;
    panel.querySelector(`[data-category-index="${to}"] [data-drag-handle]`)?.focus();
  }

  async function deleteCategory(index) {
    const category = categories[index];
    if (!category) return;
    if (categories.length === 1) {
      await confirm({
        title: "需要保留至少一个分类",
        message: "请先新增一个替代分类，再删除当前分类。",
        confirmLabel: "知道了",
        danger: false,
      });
      return;
    }
    if (Number(category.usageCount) > 0) {
      await confirm({
        title: "该分类暂时不能删除",
        message: `“${category.name}”仍被 ${category.usageCount} 篇文章使用。请先将这些文章迁移到其他分类，再回来删除。`,
        confirmLabel: "知道了",
        danger: false,
      });
      return;
    }
    const accepted = await confirm({
      title: "删除这个分类？",
      message: `“${category.name}”将在保存更改后从博客分类中移除。`,
      confirmLabel: "删除分类",
    });
    if (!accepted) return;
    categories.splice(index, 1);
    renderList();
  }

  async function saveCategories() {
    const button = savebar.querySelector("[data-save-categories]");
    button.disabled = true;
    alertBox.classList.add("is-hidden");
    const payload = categories.map((category) => ({
      ...(category.id !== undefined && category.id !== null ? { id: category.id } : {}),
      name: category.name,
      description: category.description || "",
    }));
    try {
      const result = await api.updateCategories(payload, version, signal);
      if (signal.aborted) return;
      categories = Array.isArray(result.categories)
        ? result.categories.map((category) => ({ ...category }))
        : categories;
      version = result.version ?? version;
      baseline = categorySnapshot();
      renderList();
      toast("分类与排序已保存。");
    } catch (error) {
      if (error.name === "AbortError") return;
      const usageConflict = error.status === 409 && /使用|引用|文章|迁移/.test(error.message || "");
      const message = usageConflict
        ? `${error.message} 请先迁移相关文章后重试。`
        : error.status === 409
          ? "分类已被其他会话修改，请刷新后重试。"
          : error.message;
      alertBox.textContent = message;
      alertBox.classList.remove("is-hidden");
      toast(message, "error");
    } finally {
      button.disabled = false;
    }
  }

  panel.addEventListener("click", (event) => {
    if (event.target.closest("[data-add-category]")) {
      openCategoryDialog();
      return;
    }
    const moveButton = event.target.closest("[data-move]");
    if (moveButton) {
      const index = Number(moveButton.dataset.index);
      moveCategory(index, index + Number(moveButton.dataset.move));
      return;
    }
    const editButton = event.target.closest("[data-edit-category]");
    if (editButton) {
      openCategoryDialog(Number(editButton.dataset.editCategory));
      return;
    }
    const deleteButton = event.target.closest("[data-delete-category]");
    if (deleteButton) deleteCategory(Number(deleteButton.dataset.deleteCategory));
  });

  panel.addEventListener("keydown", (event) => {
    const handle = event.target.closest("[data-drag-handle]");
    if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const row = handle.closest("[data-category-index]");
    const index = Number(row.dataset.categoryIndex);
    moveCategory(index, index + (event.key === "ArrowUp" ? -1 : 1));
  });

  panel.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("[data-drag-handle]");
    if (!handle) {
      event.preventDefault();
      return;
    }
    const row = handle.closest("[data-category-index]");
    draggedIndex = Number(row.dataset.categoryIndex);
    row.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(draggedIndex));
  });

  panel.addEventListener("dragover", (event) => {
    const row = event.target.closest("[data-category-index]");
    if (!row || draggedIndex === null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    panel.querySelectorAll(".drag-over").forEach((item) => item.classList.remove("drag-over"));
    row.classList.add("drag-over");
  });

  panel.addEventListener("drop", (event) => {
    const row = event.target.closest("[data-category-index]");
    if (!row || draggedIndex === null) return;
    event.preventDefault();
    const targetIndex = Number(row.dataset.categoryIndex);
    const sourceIndex = draggedIndex;
    draggedIndex = null;
    moveCategory(sourceIndex, targetIndex);
  });

  panel.addEventListener("dragend", () => {
    draggedIndex = null;
    panel.querySelectorAll(".is-dragging, .drag-over").forEach((item) => {
      item.classList.remove("is-dragging", "drag-over");
    });
  });

  container.querySelector("[data-add-category]").addEventListener("click", () => openCategoryDialog());
  container.querySelector("[data-refresh]").addEventListener("click", () => load({ confirmDiscard: true }));
  container.querySelector("[data-discard]").addEventListener("click", () => load({ confirmDiscard: true }));
  container.querySelector("[data-save-categories]").addEventListener("click", saveCategories);
  dialogForm.addEventListener("submit", saveCategoryFromDialog);
  dialog.querySelectorAll("[data-close-category-dialog]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });

  await load();
}
