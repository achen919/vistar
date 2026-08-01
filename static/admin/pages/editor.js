import {
  errorState,
  escapeHtml,
  formatDate,
  icon,
  loadingState,
  slugify,
  today,
  unwrap,
} from "../ui.js?v=20260801-console-5";

export async function renderEditor(context) {
  const { api, container, signal, navigate, toast, confirm, setLeaveGuard, setTitle } = context;
  const params = new URLSearchParams(window.location.search);
  let originalSlug = params.get("slug") || "";
  const editing = window.location.pathname.includes("/edit/");
  let version = null;
  let categories = [];
  let post = {
    slug: "",
    title: "",
    date: today(),
    draft: true,
    tags: [],
    categories: [],
    content: "",
    updatedAt: "",
  };

  container.innerHTML = `
    <section class="panel">
      ${loadingState(editing ? "正在加载文章内容…" : "正在准备编辑器…")}
    </section>
  `;

  if (editing && !originalSlug) {
    container.innerHTML = errorState("缺少要编辑的文章 slug。");
    return;
  }

  try {
    const [categoryPayload, postPayload] = await Promise.all([
      api.categories(signal),
      editing ? api.post(originalSlug, signal) : Promise.resolve(null),
    ]);
    if (signal.aborted) return;
    categories = unwrap(categoryPayload, "categories", []);
    if (!Array.isArray(categories)) categories = [];
    if (postPayload) {
      const rawPost = postPayload.post || postPayload;
      post = {
        ...post,
        ...rawPost,
        tags: Array.isArray(rawPost.tags) ? rawPost.tags : [],
        categories: Array.isArray(rawPost.categories) ? rawPost.categories : [],
      };
      version = postPayload.version ?? rawPost.version ?? null;
      originalSlug = rawPost.slug || originalSlug;
      setTitle("编辑文章", `最后更新于 ${formatDate(post.updatedAt || post.date, Boolean(post.updatedAt))}`);
    } else {
      setTitle("新建文章", "撰写并发布新的博客内容");
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    container.innerHTML = errorState(error.message, "返回文章列表");
    container.querySelector("[data-retry]")?.addEventListener("click", () => navigate("/admin/articles/", { force: true }));
    return;
  }

  container.innerHTML = `
    <form id="articleForm" class="editor-page" novalidate>
      <div class="editor-toolbar">
        <a href="/admin/articles/" class="back-link" data-route>${icon("arrowLeft", 17)}返回文章列表</a>
        <span id="saveState" class="save-state">所有更改均已保存</span>
      </div>

      <section class="panel editor-meta">
        <label class="field">
          <span class="field-label">文章标题</span>
          <input class="editor-title-input" name="title" type="text" maxlength="200" value="${escapeHtml(post.title)}" placeholder="输入文章标题…" autocomplete="off" required />
          <span class="field-error is-hidden" data-error-for="title"></span>
        </label>
        <div class="editor-meta-fields">
          <label class="field">
            <span class="field-label">Slug <small>文章 URL 标识</small></span>
            <input name="slug" type="text" maxlength="160" value="${escapeHtml(post.slug)}" placeholder="backend-cache-design" autocomplete="off" required />
            <span class="field-error is-hidden" data-error-for="slug"></span>
          </label>
          <label class="field">
            <span class="field-label">发布日期</span>
            <input name="date" type="date" value="${escapeHtml(post.date || today())}" required />
            <span class="field-error is-hidden" data-error-for="date"></span>
          </label>
          <label class="field">
            <span class="field-label">标签 <small>用英文逗号分隔</small></span>
            <input name="tags" type="text" value="${escapeHtml(post.tags.join(", "))}" placeholder="Hugo, LLM, 随笔" autocomplete="off" />
          </label>
        </div>
        <div class="field category-field">
          <span class="field-label">文章分类 <small>至少选择一个分类</small></span>
          <div class="category-checks">
            ${categories.map((category) => `
              <label class="category-check">
                <input type="checkbox" name="categories" value="${escapeHtml(category.name)}" ${post.categories.includes(category.name) ? "checked" : ""} />
                <span>${escapeHtml(category.name)}</span>
              </label>
            `).join("") || '<p class="text-muted">暂无可用分类，请先到分类管理中新增。</p>'}
          </div>
          <span class="field-error is-hidden" data-error-for="categories"></span>
        </div>
      </section>

      <section class="editor-workspace">
        <div class="panel editor-area">
          <div class="panel-header">
            <div><h2>Markdown 正文</h2><p>支持标题、列表、引用、链接、图片与代码块</p></div>
            <div class="editor-header-actions">
              <span id="wordCount" class="editor-counter">0 字</span>
              <button type="button" class="button button-secondary button-sm" data-insert-image>
                ${icon("image", 16)}插入图片
              </button>
            </div>
          </div>
          <label>
            <span class="is-hidden">Markdown 正文</span>
            <textarea name="content" spellcheck="false" placeholder="从这里开始写作…" required>${escapeHtml(post.content)}</textarea>
          </label>
          <span class="field-error is-hidden" data-error-for="content"></span>
        </div>
        <div class="panel preview-area">
          <div class="panel-header">
            <div><h2>实时预览</h2><p>预览仅用于辅助写作</p></div>
            <span class="badge badge-primary">实时</span>
          </div>
          <article id="markdownPreview" class="markdown-preview"></article>
        </div>
      </section>

      <div id="editorAlert" class="form-alert is-hidden" role="alert"></div>
      <div class="sticky-editor-actions">
        <button type="button" class="button button-secondary" data-save="draft">保存草稿</button>
        <button type="submit" class="button button-primary" data-save="publish">${editing && !post.draft ? "保存并发布" : "发布文章"}</button>
      </div>
    </form>

    <dialog id="imageUploadDialog" class="dialog" aria-labelledby="imageUploadDialogTitle">
      <form id="imageUploadForm" class="dialog-card" novalidate>
        <div class="dialog-header">
          <div>
            <h2 id="imageUploadDialogTitle">插入图片</h2>
            <p>上传完成后，图片 Markdown 会插入到正文当前光标位置。</p>
          </div>
          <button type="button" class="icon-button" data-close-image-dialog aria-label="关闭">${icon("close", 18)}</button>
        </div>
        <div class="dialog-body">
          <input
            class="is-hidden"
            name="imageFile"
            type="file"
            accept="image/jpeg,image/png,image/gif,image/webp"
            data-image-file
          />
          <button type="button" class="image-file-picker" data-choose-image>
            <span class="image-file-picker-icon" aria-hidden="true">${icon("image", 22)}</span>
            <span>
              <strong>选择本地图片</strong>
              <small>支持 PNG、JPEG、GIF、WebP；上传后会安全重编码，不接受 SVG</small>
            </span>
          </button>

          <div class="image-upload-preview is-hidden" data-image-preview>
            <img data-image-preview-img alt="待上传图片预览" />
            <div class="image-upload-file-copy">
              <strong data-image-file-name></strong>
              <small data-image-file-meta></small>
            </div>
            <button type="button" class="button button-secondary button-sm" data-replace-image>更换图片</button>
          </div>

          <label class="field">
            <span class="field-label">图片说明 <small>用于无障碍访问和图片加载失败时展示</small></span>
            <input name="imageAlt" type="text" maxlength="160" autocomplete="off" placeholder="例如：后台访问量趋势图" required />
            <span class="field-error is-hidden" data-image-alt-error></span>
          </label>

          <div class="image-upload-progress is-hidden" data-image-progress role="status" aria-live="polite">
            <div>
              <span data-image-progress-label>准备上传…</span>
              <span data-image-progress-value></span>
            </div>
            <progress max="100" value="0" data-image-progress-bar>0%</progress>
          </div>
          <p class="form-alert is-hidden" data-image-error role="alert"></p>
        </div>
        <div class="dialog-actions">
          <button type="button" class="button button-secondary" data-close-image-dialog>取消</button>
          <button type="submit" class="button button-primary" data-upload-image disabled>上传并插入</button>
        </div>
      </form>
    </dialog>
  `;

  const form = container.querySelector("#articleForm");
  const titleInput = form.elements.title;
  const slugInput = form.elements.slug;
  const contentInput = form.elements.content;
  const saveState = container.querySelector("#saveState");
  const preview = container.querySelector("#markdownPreview");
  const wordCount = container.querySelector("#wordCount");
  const alertBox = container.querySelector("#editorAlert");
  const imageDialog = container.querySelector("#imageUploadDialog");
  const imageForm = container.querySelector("#imageUploadForm");
  const imageFileInput = imageForm.elements.imageFile;
  const imageAltInput = imageForm.elements.imageAlt;
  const imagePreview = imageForm.querySelector("[data-image-preview]");
  const imagePreviewImg = imageForm.querySelector("[data-image-preview-img]");
  const imageError = imageForm.querySelector("[data-image-error]");
  const imageAltError = imageForm.querySelector("[data-image-alt-error]");
  const imageProgress = imageForm.querySelector("[data-image-progress]");
  const imageProgressBar = imageForm.querySelector("[data-image-progress-bar]");
  const imageProgressLabel = imageForm.querySelector("[data-image-progress-label]");
  const imageProgressValue = imageForm.querySelector("[data-image-progress-value]");
  const uploadImageButton = imageForm.querySelector("[data-upload-image]");
  const allowedImageTypes = new Set([
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
  ]);
  let slugTouched = editing || Boolean(post.slug);
  let baseline = snapshot();
  let saving = false;
  let uploadingImage = false;
  let selectedImage = null;
  let imagePreviewUrl = "";
  let uploadController = null;
  let insertionRange = {
    start: contentInput.value.length,
    end: contentInput.value.length,
  };

  function snapshot() {
    return JSON.stringify(collectPost());
  }

  function collectPost() {
    const data = new FormData(form);
    return {
      slug: String(data.get("slug") || "").trim(),
      title: String(data.get("title") || "").trim(),
      date: String(data.get("date") || ""),
      draft: post.draft,
      tags: String(data.get("tags") || "").split(",").map((tag) => tag.trim()).filter(Boolean),
      categories: data.getAll("categories").map((category) => String(category)),
      content: String(data.get("content") || ""),
    };
  }

  function isDirty() {
    return snapshot() !== baseline;
  }

  function syncDirtyState() {
    const dirty = isDirty();
    saveState.textContent = dirty ? "有尚未保存的更改" : "所有更改均已保存";
    saveState.classList.toggle("dirty", dirty);
    setLeaveGuard(dirty
      ? () => confirm({
        title: "离开编辑页面？",
        message: "当前文章有尚未保存的更改，离开后这些更改将丢失。",
        confirmLabel: "放弃更改",
      })
      : null);
  }

  function updatePreview() {
    const text = contentInput.value;
    preview.innerHTML = renderMarkdown(text);
    wordCount.textContent = `${text.replace(/\s/g, "").length} 字`;
  }

  function rememberInsertionRange() {
    if (!Number.isInteger(contentInput.selectionStart) || !Number.isInteger(contentInput.selectionEnd)) return;
    insertionRange = {
      start: contentInput.selectionStart,
      end: contentInput.selectionEnd,
    };
  }

  function revokeImagePreview() {
    if (!imagePreviewUrl) return;
    URL.revokeObjectURL(imagePreviewUrl);
    imagePreviewUrl = "";
  }

  function setImageError(message = "") {
    imageError.textContent = message;
    imageError.classList.toggle("is-hidden", !message);
  }

  function resetImageDialog() {
    uploadController?.abort();
    uploadController = null;
    uploadingImage = false;
    selectedImage = null;
    revokeImagePreview();
    imageForm.reset();
    imagePreview.classList.add("is-hidden");
    imageProgress.classList.add("is-hidden");
    imageProgressBar.value = 0;
    imageProgressLabel.textContent = "准备上传…";
    imageProgressValue.textContent = "";
    imageAltError.textContent = "";
    imageAltError.classList.add("is-hidden");
    setImageError();
    imageForm.querySelectorAll("button, input").forEach((element) => {
      element.disabled = false;
    });
    uploadImageButton.disabled = true;
  }

  function setImageUploadControls(disabled) {
    imageFileInput.disabled = disabled;
    imageAltInput.disabled = disabled;
    imageForm.querySelector("[data-choose-image]").disabled = disabled;
    imageForm.querySelector("[data-replace-image]").disabled = disabled;
    uploadImageButton.disabled = disabled || !selectedImage;
  }

  function closeImageDialog() {
    uploadController?.abort();
    if (imageDialog.open) imageDialog.close();
    resetImageDialog();
    contentInput.focus({ preventScroll: true });
    contentInput.setSelectionRange(insertionRange.start, insertionRange.end);
  }

  function openImageDialog() {
    if (saving || uploadingImage) return;
    rememberInsertionRange();
    resetImageDialog();
    imageDialog.showModal();
    window.setTimeout(() => imageForm.querySelector("[data-choose-image]")?.focus(), 0);
  }

  function selectImage(file) {
    setImageError();
    imageAltError.textContent = "";
    imageAltError.classList.add("is-hidden");
    if (!file) return;
    const looksLikeSvg = file.type === "image/svg+xml" || /\.svgz?$/i.test(file.name);
    if (looksLikeSvg || !allowedImageTypes.has(file.type)) {
      selectedImage = null;
      imageFileInput.value = "";
      uploadImageButton.disabled = true;
      setImageError("仅支持 PNG、JPEG、GIF 或 WebP 图片，不能上传 SVG。");
      return;
    }

    selectedImage = file;
    revokeImagePreview();
    imagePreviewUrl = URL.createObjectURL(file);
    imagePreviewImg.src = imagePreviewUrl;
    imagePreviewImg.alt = imageAltInput.value.trim() || "待上传图片预览";
    imageForm.querySelector("[data-image-file-name]").textContent = file.name;
    imageForm.querySelector("[data-image-file-meta]").textContent = `${formatFileSize(file.size)} · ${file.type}`;
    if (!imageAltInput.value.trim()) {
      imageAltInput.value = file.name.replace(/\.[^.]+$/, "").replaceAll(/[-_]+/g, " ").trim();
    }
    imagePreviewImg.alt = imageAltInput.value.trim() || "待上传图片预览";
    imagePreview.classList.remove("is-hidden");
    uploadImageButton.disabled = false;
    imageAltInput.focus();
    imageAltInput.select();
  }

  function setUploadProgress(value) {
    const progress = Number(value);
    imageProgress.classList.remove("is-hidden");
    if (!Number.isFinite(progress) || progress <= 0) {
      imageProgressBar.removeAttribute("value");
      imageProgressLabel.textContent = "正在上传图片…";
      imageProgressValue.textContent = "";
      return;
    }
    const normalized = Math.min(100, Math.max(0, Math.round(progress)));
    imageProgressBar.value = normalized;
    imageProgressLabel.textContent = normalized >= 100 ? "正在处理图片…" : "正在上传图片…";
    imageProgressValue.textContent = `${normalized}%`;
  }

  function insertImageMarkdown(url, alt) {
    const safeUrl = validateUploadedImageUrl(url);
    const safeAlt = String(alt || "")
      .replaceAll("\\", "\\\\")
      .replaceAll("]", "\\]")
      .replaceAll(/\s+/g, " ")
      .trim();
    const markdown = `![${safeAlt}](${safeUrl})`;
    const start = Math.min(insertionRange.start, contentInput.value.length);
    const end = Math.min(Math.max(insertionRange.end, start), contentInput.value.length);
    const before = contentInput.value.slice(0, start);
    const after = contentInput.value.slice(end);
    const leading = before && !before.endsWith("\n") ? "\n\n" : "";
    const trailing = after && !after.startsWith("\n") ? "\n\n" : "";
    const insertion = `${leading}${markdown}${trailing}`;
    contentInput.setRangeText(insertion, start, end, "end");
    rememberInsertionRange();
    contentInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  async function uploadImage(event) {
    event.preventDefault();
    if (uploadingImage || !selectedImage) return;
    const alt = imageAltInput.value.trim();
    if (!alt) {
      imageAltError.textContent = "请填写能说明图片内容的文字。";
      imageAltError.classList.remove("is-hidden");
      imageAltInput.focus();
      return;
    }

    uploadingImage = true;
    setImageError();
    imageAltError.textContent = "";
    imageAltError.classList.add("is-hidden");
    setImageUploadControls(true);
    uploadImageButton.innerHTML = '<span class="spinner spinner-light" aria-hidden="true"></span>正在上传';
    setUploadProgress(0);

    const controller = new AbortController();
    uploadController = controller;
    const abortUpload = () => controller.abort();
    signal.addEventListener("abort", abortUpload, { once: true });
    try {
      const result = await api.uploadImage(selectedImage, controller.signal, setUploadProgress);
      if (signal.aborted || controller.signal.aborted) return;
      const uploadedUrl = result?.url || extractImageUrl(result?.markdown);
      insertImageMarkdown(uploadedUrl, alt);
      toast("图片已上传并插入正文。");
      closeImageDialog();
    } catch (error) {
      if (error.name === "AbortError") return;
      setImageError(error.message || "图片上传失败，请稍后重试。");
      toast(error.message || "图片上传失败，请稍后重试。", "error");
    } finally {
      signal.removeEventListener("abort", abortUpload);
      uploadingImage = false;
      if (uploadController === controller) uploadController = null;
      if (imageDialog.open) {
        setImageUploadControls(false);
        uploadImageButton.textContent = "上传并插入";
        if (imageProgressBar.value < 100) imageProgress.classList.add("is-hidden");
      }
    }
  }

  function clearErrors() {
    form.querySelectorAll(".input-error").forEach((element) => element.classList.remove("input-error"));
    form.querySelectorAll("[data-error-for]").forEach((element) => {
      element.textContent = "";
      element.classList.add("is-hidden");
    });
    alertBox.classList.add("is-hidden");
  }

  function showFieldError(field, message) {
    const error = form.querySelector(`[data-error-for="${field}"]`);
    if (error) {
      error.textContent = message;
      error.classList.remove("is-hidden");
    }
    form.elements[field]?.classList?.add("input-error");
  }

  function validate(payload) {
    clearErrors();
    let valid = true;
    if (!payload.title) {
      showFieldError("title", "请输入文章标题。");
      valid = false;
    }
    if (!payload.slug) {
      showFieldError("slug", "请输入文章 slug。");
      valid = false;
    } else if (payload.slug.length > 160 || !/^[\p{L}\p{N}_-]+$/u.test(payload.slug)) {
      showFieldError("slug", "Slug 最多 160 个字符，仅支持中文、字母、数字、下划线和中划线。");
      valid = false;
    } else if (new TextEncoder().encode(`${payload.slug}.md`).length > 255) {
      showFieldError("slug", "Slug 的 UTF-8 文件名过长，请缩短中文或其他多字节字符。");
      valid = false;
    }
    const parsedDate = new Date(`${payload.date}T00:00:00Z`);
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(payload.date)
      || Number.isNaN(parsedDate.getTime())
      || parsedDate.toISOString().slice(0, 10) !== payload.date
    ) {
      showFieldError("date", "请选择有效的发布日期。");
      valid = false;
    }
    if (!payload.categories.length) {
      showFieldError("categories", "请至少选择一个分类。");
      valid = false;
    }
    if (!payload.content.trim()) {
      showFieldError("content", "请输入文章正文。");
      valid = false;
    }
    return valid;
  }

  function setSavingState(active) {
    saving = active;
    form.querySelectorAll("[data-save]").forEach((button) => {
      button.disabled = active;
    });
    form.querySelector("[data-insert-image]").disabled = active;
    if (active) saveState.textContent = "正在保存并发布…";
  }

  async function save(asDraft) {
    if (saving) return;
    post.draft = asDraft;
    const payload = collectPost();
    payload.draft = asDraft;
    if (!validate(payload)) {
      post.draft = JSON.parse(baseline).draft;
      form.querySelector(".input-error")?.focus();
      return;
    }
    if (editing) payload.version = version;

    setSavingState(true);
    try {
      const result = editing
        ? await api.updatePost(originalSlug, payload, signal)
        : await api.createPost(payload, signal);
      const savedPost = result.post || result;
      version = result.version ?? savedPost.version ?? version;
      originalSlug = savedPost.slug || payload.slug;
      post = { ...post, ...payload, ...savedPost, draft: asDraft };
      baseline = JSON.stringify({
        slug: originalSlug,
        title: post.title,
        date: post.date,
        draft: post.draft,
        tags: post.tags || [],
        categories: post.categories || [],
        content: post.content || "",
      });
      setLeaveGuard(null);
      toast(asDraft ? "草稿已保存。" : "文章已发布。");

      const nextUrl = `/admin/articles/edit/?slug=${encodeURIComponent(originalSlug)}`;
      if (!editing || window.location.search !== `?slug=${encodeURIComponent(originalSlug)}`) {
        await navigate(nextUrl, { replace: true, force: true });
      } else {
        saveState.textContent = "所有更改均已保存";
        saveState.classList.remove("dirty");
        setTitle("编辑文章", `最后保存于 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`);
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      const message = error.status === 409
        ? "文章已被其他会话修改，请刷新页面后重新编辑。"
        : error.message;
      alertBox.textContent = message;
      alertBox.classList.remove("is-hidden");
      toast(message, "error");
      syncDirtyState();
    } finally {
      setSavingState(false);
    }
  }

  titleInput.addEventListener("input", () => {
    if (!slugTouched) slugInput.value = slugify(titleInput.value);
  });
  slugInput.addEventListener("input", () => {
    slugTouched = true;
  });
  ["click", "keyup", "select"].forEach((eventName) => {
    contentInput.addEventListener(eventName, rememberInsertionRange);
  });
  form.addEventListener("input", () => {
    updatePreview();
    syncDirtyState();
  });
  form.addEventListener("change", syncDirtyState);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save(false);
  });
  form.querySelector('[data-save="draft"]').addEventListener("click", () => save(true));
  form.querySelector("[data-insert-image]").addEventListener("click", openImageDialog);
  imageForm.querySelector("[data-choose-image]").addEventListener("click", () => imageFileInput.click());
  imageForm.querySelector("[data-replace-image]").addEventListener("click", () => imageFileInput.click());
  imageFileInput.addEventListener("change", () => selectImage(imageFileInput.files?.[0]));
  imageAltInput.addEventListener("input", () => {
    imagePreviewImg.alt = imageAltInput.value.trim() || "待上传图片预览";
    imageAltError.textContent = "";
    imageAltError.classList.add("is-hidden");
  });
  imageForm.addEventListener("submit", uploadImage);
  imageDialog.querySelectorAll("[data-close-image-dialog]").forEach((button) => {
    button.addEventListener("click", closeImageDialog);
  });
  imageDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeImageDialog();
  });

  updatePreview();
  baseline = snapshot();
  syncDirtyState();

  return () => {
    uploadController?.abort();
    revokeImagePreview();
  };
}

function formatFileSize(bytes) {
  const size = Number(bytes);
  if (!Number.isFinite(size) || size < 0) return "未知大小";
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 ** 2).toFixed(1)} MB`;
}

function extractImageUrl(markdown) {
  const match = String(markdown || "").match(/^!\[[^\]]*\]\(([^)]+)\)$/);
  return match?.[1] || "";
}

function validateUploadedImageUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) throw new Error("上传接口没有返回图片地址。");
  const url = new URL(raw, window.location.origin);
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("上传接口返回了无效的图片地址。");
  }
  return url.origin === window.location.origin
    ? `${url.pathname}${url.search}${url.hash}`
    : url.href;
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let list = [];
  let orderedList = [];
  let inCode = false;
  let code = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushLists = () => {
    if (list.length) {
      html.push(`<ul>${list.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
      list = [];
    }
    if (orderedList.length) {
      html.push(`<ol>${orderedList.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ol>`);
      orderedList = [];
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
        flushLists();
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
      flushLists();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushLists();
      html.push(`<h${heading[1].length}>${inlineMarkdown(heading[2])}</h${heading[1].length}>`);
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      orderedList = [];
      list.push(bullet[1]);
      continue;
    }
    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      list = [];
      orderedList.push(ordered[1]);
      continue;
    }
    const quote = line.match(/^>\s+(.+)$/);
    if (quote) {
      flushParagraph();
      flushLists();
      html.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }
    paragraph.push(line.trim());
  }

  flushParagraph();
  flushLists();
  if (inCode) html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  return html.join("\n") || '<p class="text-muted">正文预览会显示在这里。</p>';
}

function inlineMarkdown(value) {
  const imageTokens = [];
  const withImageTokens = escapeHtml(value).replace(
    /!\[((?:\\.|[^\]])*)\]\(((?:https?:\/\/|\/)[^)\s]+)\)/g,
    (_, rawAlt, source) => {
      const token = `\uE000${imageTokens.length}\uE001`;
      const alt = rawAlt.replace(/\\([\\\]])/g, "$1");
      imageTokens.push(`<img src="${source}" alt="${alt}" loading="lazy" decoding="async">`);
      return token;
    },
  );
  return withImageTokens
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\uE000(\d+)\uE001/g, (_, index) => imageTokens[Number(index)] || "");
}
