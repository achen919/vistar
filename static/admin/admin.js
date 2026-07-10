const state = {
  categories: [],
};

const el = {
  title: document.querySelector("#title"),
  slug: document.querySelector("#slug"),
  date: document.querySelector("#date"),
  tags: document.querySelector("#tags"),
  overwrite: document.querySelector("#overwrite"),
  content: document.querySelector("#content"),
  preview: document.querySelector("#preview"),
  wordCount: document.querySelector("#wordCount"),
  categoryList: document.querySelector("#categoryList"),
  addCategory: document.querySelector("#addCategory"),
  refreshCategories: document.querySelector("#refreshCategories"),
  publishPost: document.querySelector("#publishPost"),
  status: document.querySelector("#status"),
};

function today() {
  return new Date().toISOString().slice(0, 10);
}

function setStatus(message, isError = false) {
  el.status.textContent = message;
  el.status.classList.toggle("error", isError);
  if (message) {
    window.clearTimeout(setStatus.timer);
    setStatus.timer = window.setTimeout(() => {
      el.status.textContent = "";
      el.status.classList.remove("error");
    }, isError ? 10000 : 5000);
  }
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMarkdown(markdown) {
  const lines = markdown.split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let list = [];
  let inCode = false;
  let code = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      html.push(`<ul>${list.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
      list = [];
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = [];
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      html.push(`<h${heading[1].length}>${inlineMarkdown(heading[2])}</h${heading[1].length}>`);
      continue;
    }
    const item = line.match(/^[-*]\s+(.+)$/);
    if (item) {
      flushParagraph();
      list.push(item[1]);
      continue;
    }
    const quote = line.match(/^>\s+(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      html.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }
    paragraph.push(line.trim());
  }

  flushParagraph();
  flushList();
  if (inCode) {
    html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  }
  return html.join("\n") || "<p>预览会显示在这里。</p>";
}

function updatePreview() {
  const text = el.content.value;
  el.preview.innerHTML = renderMarkdown(text);
  const count = text.replace(/\s/g, "").length;
  el.wordCount.textContent = `${count} 字`;
}

function slugifyTitle(title) {
  return title
    .trim()
    .toLowerCase()
    .replace(/['"]/g, "")
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function categoryRow(category, index) {
  const row = document.createElement("div");
  row.className = "category-row";
  row.innerHTML = `
    <input type="checkbox" class="category-selected" aria-label="选择分类" />
    <label>
      <span>名称</span>
      <input class="category-name" type="text" value="${escapeHtml(category.name)}" />
    </label>
    <label>
      <span>描述</span>
      <input class="category-description" type="text" value="${escapeHtml(category.description || "")}" />
    </label>
    <div class="row-actions">
      <button type="button" class="secondary move-up">上移</button>
      <button type="button" class="secondary move-down">下移</button>
      <button type="button" class="danger remove">删除</button>
    </div>
  `;
  row.querySelector(".category-selected").checked = Boolean(category.selected);
  row.querySelector(".move-up").disabled = index === 0;
  row.querySelector(".move-down").disabled = index === state.categories.length - 1;
  row.querySelector(".move-up").addEventListener("click", () => moveCategory(index, -1));
  row.querySelector(".move-down").addEventListener("click", () => moveCategory(index, 1));
  row.querySelector(".remove").addEventListener("click", () => {
    state.categories.splice(index, 1);
    renderCategories();
  });
  for (const selector of [".category-selected", ".category-name", ".category-description"]) {
    row.querySelector(selector).addEventListener("input", syncCategoriesFromDom);
    row.querySelector(selector).addEventListener("change", syncCategoriesFromDom);
  }
  return row;
}

function renderCategories() {
  el.categoryList.replaceChildren(...state.categories.map(categoryRow));
}

function syncCategoriesFromDom() {
  state.categories = [...el.categoryList.querySelectorAll(".category-row")].map((row) => ({
    selected: row.querySelector(".category-selected").checked,
    name: row.querySelector(".category-name").value.trim(),
    description: row.querySelector(".category-description").value.trim(),
  }));
}

function moveCategory(index, direction) {
  syncCategoriesFromDom();
  const target = index + direction;
  if (target < 0 || target >= state.categories.length) return;
  const [item] = state.categories.splice(index, 1);
  state.categories.splice(target, 0, item);
  renderCategories();
}

async function loadCategories() {
  const response = await fetch("api/categories", { headers: { Accept: "application/json" } });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "分类加载失败");
  state.categories = payload.categories.map((category) => ({ ...category, selected: false }));
  renderCategories();
}

function collectPayload() {
  syncCategoriesFromDom();
  const categories = state.categories.filter((category) => category.name);
  const selected = categories.filter((category) => category.selected).map((category) => category.name);
  return {
    title: el.title.value.trim(),
    slug: el.slug.value.trim(),
    date: el.date.value,
    tags: el.tags.value.split(",").map((item) => item.trim()).filter(Boolean),
    categories: selected,
    categoryCatalog: categories.map(({ name, description }) => ({ name, description })),
    content: el.content.value,
    overwrite: el.overwrite.checked,
    draft: false,
  };
}

async function publish() {
  const payload = collectPayload();
  if (!payload.title || !payload.slug || !payload.content.trim()) {
    setStatus("标题、slug 和正文都必须填写。", true);
    return;
  }
  if (!payload.categories.length) {
    setStatus("至少选择一个分类。", true);
    return;
  }
  el.publishPost.disabled = true;
  setStatus("正在发布，后台会写文章、构建 Hugo 并推送 GitHub...");
  try {
    const response = await fetch("api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "发布失败");
    setStatus(`发布成功：${result.post}，commit ${result.commit}`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    el.publishPost.disabled = false;
  }
}

el.date.value = today();
el.title.addEventListener("input", () => {
  if (!el.slug.dataset.touched) el.slug.value = slugifyTitle(el.title.value);
});
el.slug.addEventListener("input", () => {
  el.slug.dataset.touched = "true";
});
el.content.addEventListener("input", updatePreview);
el.addCategory.addEventListener("click", () => {
  syncCategoriesFromDom();
  state.categories.push({ name: "", description: "", selected: false });
  renderCategories();
});
el.refreshCategories.addEventListener("click", () => {
  loadCategories().then(() => setStatus("分类已刷新。")).catch((error) => setStatus(error.message, true));
});
el.publishPost.addEventListener("click", publish);

updatePreview();
loadCategories().catch((error) => setStatus(error.message, true));
