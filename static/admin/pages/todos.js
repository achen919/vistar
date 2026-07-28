import {
  emptyState,
  errorState,
  escapeHtml,
  formatNumber,
  icon,
  loadingState,
  today,
  unwrap,
} from "../ui.js?v=20260728-console-3";

const DATE_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  month: "long",
  day: "numeric",
  weekday: "short",
});

export async function renderTodos(context) {
  const { api, container, signal, confirm, toast } = context;
  let selectedDate = today();
  let todos = [];
  let daySummary = null;
  let statsPayload = {};
  let statsDays = 14;
  let editingTodo = null;
  let listRequestSequence = 0;
  let statsRequestSequence = 0;
  const busyTodoIds = new Set();

  container.innerHTML = `
    <section class="page-section">
      <div class="page-toolbar">
        <div class="section-header">
          <div>
            <h2>每日 Todo</h2>
            <p id="todoDateDescription">管理今天的待办事项，并记录每日完成情况。</p>
          </div>
        </div>
        <div class="toolbar-actions">
          <button type="button" class="button button-secondary" data-shift-date="-1" aria-label="查看前一天">${icon("arrowLeft", 16)}前一天</button>
          <label class="field">
            <span class="sr-only">选择 Todo 日期</span>
            <input type="date" value="${escapeHtml(selectedDate)}" data-todo-date aria-label="选择 Todo 日期" />
          </label>
          <button type="button" class="button button-secondary" data-today>今天</button>
          <button type="button" class="button button-secondary" data-shift-date="1" aria-label="查看后一天">后一天${icon("chevronRight", 16)}</button>
          <button type="button" class="button button-primary" data-add-todo>${icon("plus", 17)}新增 Todo</button>
        </div>
      </div>

      <div id="todoDayMetrics" class="metric-grid">
        ${metricSkeletons()}
      </div>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2 id="todoListTitle">今日 Todo</h2>
            <p id="todoListDescription">正在读取任务列表…</p>
          </div>
          <button type="button" class="button button-secondary button-sm" data-refresh-todos>${icon("refresh", 16)}刷新</button>
        </div>
        <div id="todoList">${loadingState("正在加载 Todo…")}</div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>每日完成统计</h2>
            <p id="todoStatsDescription">最近 14 天的完成情况</p>
          </div>
          <label class="field">
            <span class="sr-only">统计时间范围</span>
            <select data-stats-days aria-label="Todo 统计时间范围">
              <option value="14" selected>最近 14 天</option>
              <option value="30">最近 30 天</option>
            </select>
          </label>
        </div>
        <div class="panel-body">
          <div id="todoStatsMetrics" class="metric-grid">
            ${metricSkeletons()}
          </div>
        </div>
        <div id="todoStatsTable">${loadingState("正在加载完成统计…")}</div>
      </section>

      <dialog id="todoDialog" class="dialog" aria-labelledby="todoDialogTitle">
        <form id="todoForm" class="dialog-card" novalidate>
          <div class="dialog-header">
            <div>
              <h2 id="todoDialogTitle">新增 Todo</h2>
              <p id="todoDialogDescription">记录一件当天需要完成的事情。</p>
            </div>
            <button type="button" class="icon-button" data-close-todo-dialog aria-label="关闭">${icon("close", 18)}</button>
          </div>
          <div id="todoFormAlert" class="form-alert is-hidden" role="alert"></div>
          <div class="dialog-body">
            <label class="field">
              <span class="field-label">任务内容</span>
              <input name="title" type="text" maxlength="200" autocomplete="off" placeholder="例如：整理本周文章选题" required />
              <span class="field-error is-hidden" data-title-error></span>
            </label>
            <label class="field">
              <span class="field-label">计划日期</span>
              <input name="date" type="date" required />
              <span class="field-error is-hidden" data-date-error></span>
            </label>
          </div>
          <div class="dialog-actions">
            <button type="button" class="button button-secondary" data-close-todo-dialog>取消</button>
            <button type="submit" class="button button-primary" data-save-todo>保存 Todo</button>
          </div>
        </form>
      </dialog>
    </section>
  `;

  const listTarget = container.querySelector("#todoList");
  const dayMetricsTarget = container.querySelector("#todoDayMetrics");
  const statsMetricsTarget = container.querySelector("#todoStatsMetrics");
  const statsTableTarget = container.querySelector("#todoStatsTable");
  const dateInput = container.querySelector("[data-todo-date]");
  const statsDaysSelect = container.querySelector("[data-stats-days]");
  const dialog = container.querySelector("#todoDialog");
  const form = container.querySelector("#todoForm");

  function updateDateHeadings() {
    const isToday = selectedDate === today();
    const dateLabel = formatFriendlyDate(selectedDate);
    container.querySelector("#todoDateDescription").textContent = isToday
      ? "管理今天的待办事项，并记录每日完成情况。"
      : `正在查看 ${dateLabel} 的待办事项。`;
    container.querySelector("#todoListTitle").textContent = isToday ? "今日 Todo" : `${dateLabel} Todo`;
    dateInput.value = selectedDate;
  }

  function renderDayMetrics() {
    const summary = normalizeSummary(daySummary, todos);
    dayMetricsTarget.innerHTML = renderMetricCards([
      {
        label: "当日任务",
        value: summary.total,
        note: formatFriendlyDate(selectedDate),
        iconName: "articles",
      },
      {
        label: "已完成",
        value: summary.completed,
        note: summary.total ? "继续保持节奏" : "还没有任务",
        iconName: "dashboard",
        style: "success",
      },
      {
        label: "待完成",
        value: summary.pending,
        note: summary.pending ? "尚未勾选完成" : "当前没有遗留",
        iconName: "categories",
        style: "warning",
      },
      {
        label: "完成率",
        value: `${summary.rate}%`,
        note: summary.total ? `${summary.completed} / ${summary.total} 项` : "暂无可统计任务",
        iconName: "analytics",
      },
    ]);
  }

  function renderTodoList() {
    const summary = normalizeSummary(daySummary, todos);
    container.querySelector("#todoListDescription").textContent = summary.total
      ? `共 ${summary.total} 项，已完成 ${summary.completed} 项`
      : "这一天还没有安排 Todo";
    renderDayMetrics();

    if (!todos.length) {
      listTarget.innerHTML = emptyState(
        selectedDate === today() ? "今天还没有 Todo" : "这一天还没有 Todo",
        "新增一项任务，完成后可直接勾选。",
        '<button type="button" class="button button-primary button-sm" data-add-todo>新增 Todo</button>',
      );
      return;
    }

    listTarget.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>完成</th>
              <th>任务</th>
              <th>状态</th>
              <th>更新时间</th>
              <th aria-label="操作"></th>
            </tr>
          </thead>
          <tbody>
            ${todos.map((todo) => {
              const id = String(todo.id);
              const busy = busyTodoIds.has(id);
              return `
                <tr data-todo-id="${escapeHtml(id)}">
                  <td>
                    <label class="checkbox">
                      <input
                        type="checkbox"
                        data-toggle-todo="${escapeHtml(id)}"
                        ${todo.completed ? "checked" : ""}
                        ${busy ? "disabled" : ""}
                      />
                      <span class="sr-only">将“${escapeHtml(todo.title)}”标记为${todo.completed ? "未完成" : "已完成"}</span>
                    </label>
                  </td>
                  <td>
                    <span class="table-title">${escapeHtml(todo.title || "未命名 Todo")}</span>
                    <span class="table-subtitle">${escapeHtml(formatFriendlyDate(todo.date || selectedDate))}</span>
                  </td>
                  <td>${completionBadge(todo.completed)}</td>
                  <td>${escapeHtml(formatTimestamp(todo.updatedAt || todo.createdAt))}</td>
                  <td>
                    <div class="table-actions">
                      <button type="button" class="icon-button" data-edit-todo="${escapeHtml(id)}" aria-label="编辑 ${escapeHtml(todo.title)}" ${busy ? "disabled" : ""}>${icon("edit", 16)}</button>
                      <button type="button" class="icon-button" data-delete-todo="${escapeHtml(id)}" aria-label="删除 ${escapeHtml(todo.title)}" ${busy ? "disabled" : ""}>${icon("trash", 16)}</button>
                    </div>
                  </td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderStats() {
    const rows = normalizeStatsRows(statsPayload);
    const visibleRows = rows.slice(-statsDays);
    const summary = statsDays === 30 && statsPayload?.totals
      ? normalizeSummary(statsPayload.totals)
      : summarizeRows(visibleRows);

    container.querySelector("#todoStatsDescription").textContent = `最近 ${statsDays} 天的完成情况`;
    statsMetricsTarget.innerHTML = renderMetricCards([
      {
        label: "累计任务",
        value: summary.total,
        note: `最近 ${statsDays} 天`,
        iconName: "articles",
      },
      {
        label: "累计完成",
        value: summary.completed,
        note: `${summary.total ? summary.rate : 0}% 完成率`,
        iconName: "dashboard",
        style: "success",
      },
      {
        label: "尚未完成",
        value: summary.pending,
        note: "所选统计周期",
        iconName: "categories",
        style: "warning",
      },
      {
        label: "周期完成率",
        value: `${summary.rate}%`,
        note: summary.total ? `${summary.completed} / ${summary.total} 项` : "暂无可统计任务",
        iconName: "analytics",
      },
    ]);

    if (!visibleRows.length) {
      statsTableTarget.innerHTML = emptyState(
        "暂无完成记录",
        "创建并完成 Todo 后，这里会按天展示完成情况。",
      );
      return;
    }

    statsTableTarget.innerHTML = `
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>日期</th>
              <th>全部任务</th>
              <th>已完成</th>
              <th>待完成</th>
              <th>完成率</th>
            </tr>
          </thead>
          <tbody>
            ${[...visibleRows].reverse().map((row) => `
              <tr>
                <td>
                  <span class="table-title">${escapeHtml(formatFriendlyDate(row.date))}</span>
                  <span class="table-subtitle">${escapeHtml(row.date)}</span>
                </td>
                <td>${formatNumber(row.total)}</td>
                <td>${formatNumber(row.completed)}</td>
                <td>${formatNumber(row.pending)}</td>
                <td>${rateBadge(row.rate, row.total)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  async function loadTodos({ showLoading = true } = {}) {
    const requestId = ++listRequestSequence;
    updateDateHeadings();
    if (showLoading) {
      listTarget.innerHTML = loadingState("正在加载 Todo…");
      dayMetricsTarget.innerHTML = metricSkeletons();
      container.querySelector("#todoListDescription").textContent = "正在读取任务列表…";
    }
    try {
      const payload = await api.todos(selectedDate, signal);
      if (signal.aborted || requestId !== listRequestSequence) return;
      const collection = unwrap(payload, "todos", []);
      todos = Array.isArray(collection)
        ? collection.map((todo, index) => normalizeTodo(todo, selectedDate, index))
        : [];
      todos.sort((left, right) => {
        if (left.completed !== right.completed) return Number(left.completed) - Number(right.completed);
        return String(left.createdAt || "").localeCompare(String(right.createdAt || ""));
      });
      daySummary = payload?.summary || null;
      renderTodoList();
    } catch (error) {
      if (error.name === "AbortError" || requestId !== listRequestSequence) return;
      dayMetricsTarget.innerHTML = `
        <div class="panel span-full">${errorState(error.message)}</div>
      `;
      listTarget.innerHTML = errorState(error.message);
      listTarget.querySelector("[data-retry]")?.addEventListener("click", () => loadTodos(), { once: true });
    }
  }

  async function loadStats({ showLoading = true } = {}) {
    const requestId = ++statsRequestSequence;
    if (showLoading) {
      statsMetricsTarget.innerHTML = metricSkeletons();
      statsTableTarget.innerHTML = loadingState("正在加载完成统计…");
    }
    try {
      const payload = await api.todoStats(signal);
      if (signal.aborted || requestId !== statsRequestSequence) return;
      statsPayload = payload && typeof payload === "object" ? payload : {};
      renderStats();
    } catch (error) {
      if (error.name === "AbortError" || requestId !== statsRequestSequence) return;
      statsMetricsTarget.innerHTML = `
        <div class="panel span-full">${errorState(error.message)}</div>
      `;
      statsTableTarget.innerHTML = errorState(error.message);
      statsTableTarget.querySelector("[data-retry]")?.addEventListener("click", () => loadStats(), { once: true });
    }
  }

  function openTodoDialog(todo = null) {
    editingTodo = todo;
    form.reset();
    form.elements.title.value = todo?.title || "";
    form.elements.date.value = todo?.date || selectedDate;
    form.querySelector("#todoDialogTitle").textContent = todo ? "编辑 Todo" : "新增 Todo";
    form.querySelector("#todoDialogDescription").textContent = todo
      ? "修改任务内容或将它安排到其他日期。"
      : "记录一件当天需要完成的事情。";
    form.querySelector("[data-save-todo]").textContent = todo ? "保存修改" : "保存 Todo";
    clearFormErrors();
    dialog.showModal();
    window.setTimeout(() => form.elements.title.focus(), 0);
  }

  function clearFormErrors() {
    form.querySelector("#todoFormAlert").textContent = "";
    form.querySelector("#todoFormAlert").classList.add("is-hidden");
    form.querySelectorAll(".field-error").forEach((element) => {
      element.textContent = "";
      element.classList.add("is-hidden");
    });
    form.elements.title.classList.remove("input-error");
    form.elements.date.classList.remove("input-error");
  }

  function validateForm() {
    clearFormErrors();
    const title = form.elements.title.value.trim();
    const date = form.elements.date.value;
    if (!title) {
      showFieldError("title", "请输入任务内容。");
      return null;
    }
    if (title.length > 200) {
      showFieldError("title", "任务内容不能超过 200 个字符。");
      return null;
    }
    if (!isDateValue(date)) {
      showFieldError("date", "请选择有效日期。");
      return null;
    }
    return { title, date };
  }

  function showFieldError(fieldName, message) {
    const field = form.elements[fieldName];
    const error = form.querySelector(`[data-${fieldName}-error]`);
    field.classList.add("input-error");
    error.textContent = message;
    error.classList.remove("is-hidden");
    field.focus();
  }

  function showFormError(message) {
    const target = form.querySelector("#todoFormAlert");
    target.textContent = message;
    target.classList.remove("is-hidden");
  }

  async function saveTodo(event) {
    event.preventDefault();
    const values = validateForm();
    if (!values) return;
    const button = form.querySelector("[data-save-todo]");
    button.disabled = true;
    button.innerHTML = '<span class="spinner spinner-light" aria-hidden="true"></span><span>正在保存…</span>';
    try {
      if (editingTodo) {
        await api.updateTodo(editingTodo.id, {
          title: values.title,
          date: values.date,
        }, signal);
        toast("Todo 已更新。");
      } else {
        await api.createTodo(values, signal);
        toast("Todo 已创建。");
      }
      if (signal.aborted) return;
      dialog.close();
      await Promise.all([loadTodos(), loadStats()]);
    } catch (error) {
      if (error.name === "AbortError") return;
      showFormError(error.message);
    } finally {
      button.disabled = false;
      button.textContent = editingTodo ? "保存修改" : "保存 Todo";
    }
  }

  async function toggleTodo(id, completed) {
    const todo = findTodo(id);
    if (!todo || busyTodoIds.has(String(id))) return;
    busyTodoIds.add(String(id));
    renderTodoList();
    try {
      await api.updateTodo(todo.id, { completed }, signal);
      if (signal.aborted) return;
      toast(completed ? "Todo 已完成。" : "Todo 已恢复为待完成。");
      await Promise.all([
        loadTodos({ showLoading: false }),
        loadStats({ showLoading: false }),
      ]);
    } catch (error) {
      if (error.name === "AbortError") return;
      toast(error.message, "error");
    } finally {
      busyTodoIds.delete(String(id));
      if (!signal.aborted) renderTodoList();
    }
  }

  async function deleteTodo(id) {
    const todo = findTodo(id);
    if (!todo || busyTodoIds.has(String(id))) return;
    let deleted = false;
    const accepted = await confirm({
      title: "删除这个 Todo？",
      message: `“${todo.title}”将被永久删除，并从每日完成统计中移除。`,
      confirmLabel: "删除 Todo",
    });
    if (!accepted) return;
    busyTodoIds.add(String(id));
    renderTodoList();
    try {
      await api.deleteTodo(todo.id, signal);
      if (signal.aborted) return;
      deleted = true;
      toast("Todo 已删除。");
      await Promise.all([loadTodos(), loadStats()]);
    } catch (error) {
      if (error.name === "AbortError") return;
      toast(error.message, "error");
    } finally {
      busyTodoIds.delete(String(id));
      if (!signal.aborted && !deleted) renderTodoList();
    }
  }

  function findTodo(id) {
    return todos.find((todo) => String(todo.id) === String(id));
  }

  async function selectDate(date) {
    if (!isDateValue(date)) {
      dateInput.value = selectedDate;
      return;
    }
    if (date === selectedDate) return;
    selectedDate = date;
    await loadTodos();
  }

  function handleContainerClick(event) {
    if (event.target.closest("[data-add-todo]")) {
      openTodoDialog();
      return;
    }
    const shiftButton = event.target.closest("[data-shift-date]");
    if (shiftButton) {
      selectDate(shiftDate(selectedDate, Number(shiftButton.dataset.shiftDate)));
      return;
    }
    if (event.target.closest("[data-today]")) {
      selectDate(today());
      return;
    }
    if (event.target.closest("[data-refresh-todos]")) {
      Promise.all([loadTodos(), loadStats()]);
      return;
    }
    const editButton = event.target.closest("[data-edit-todo]");
    if (editButton) {
      const todo = findTodo(editButton.dataset.editTodo);
      if (todo) openTodoDialog(todo);
      return;
    }
    const deleteButton = event.target.closest("[data-delete-todo]");
    if (deleteButton) deleteTodo(deleteButton.dataset.deleteTodo);
  }

  container.addEventListener("click", handleContainerClick);

  listTarget.addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-toggle-todo]");
    if (checkbox) toggleTodo(checkbox.dataset.toggleTodo, checkbox.checked);
  });

  dateInput.addEventListener("change", () => selectDate(dateInput.value));
  statsDaysSelect.addEventListener("change", () => {
    statsDays = Number(statsDaysSelect.value) === 30 ? 30 : 14;
    renderStats();
  });
  form.addEventListener("submit", saveTodo);
  form.addEventListener("input", clearFormErrors);
  dialog.addEventListener("cancel", (event) => {
    if (form.querySelector("[data-save-todo]").disabled) event.preventDefault();
  });
  dialog.querySelectorAll("[data-close-todo-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!form.querySelector("[data-save-todo]").disabled) dialog.close();
    });
  });

  updateDateHeadings();
  await Promise.all([loadTodos(), loadStats()]);

  return () => {
    container.removeEventListener("click", handleContainerClick);
  };
}

function normalizeTodo(todo, fallbackDate, index) {
  const value = todo && typeof todo === "object" ? todo : {};
  return {
    ...value,
    id: value.id ?? value.todoId ?? value.uuid ?? `${fallbackDate}-${index}`,
    title: String(value.title ?? value.text ?? ""),
    date: isDateValue(value.date) ? value.date : fallbackDate,
    completed: Boolean(value.completed ?? value.done ?? value.status === "completed"),
    createdAt: value.createdAt ?? value.created_at ?? "",
    updatedAt: value.updatedAt ?? value.updated_at ?? "",
  };
}

function normalizeStatsRows(payload) {
  const collection = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.daily)
      ? payload.daily
      : Array.isArray(payload?.stats)
        ? payload.stats
        : [];
  return collection
    .filter((row) => row && isDateValue(row.date))
    .map((row) => ({ date: row.date, ...normalizeSummary(row) }))
    .sort((left, right) => left.date.localeCompare(right.date));
}

function normalizeSummary(summary, fallbackTodos = []) {
  const value = summary && typeof summary === "object" ? summary : {};
  const fallback = Array.isArray(fallbackTodos) ? fallbackTodos : [];
  const fallbackCompleted = fallback.filter((todo) => todo.completed).length;
  const total = nonNegativeNumber(
    value.total ?? value.count ?? value.totalTodos ?? value.totalCount,
    fallback.length,
  );
  const completed = Math.min(total, nonNegativeNumber(
    value.completed ?? value.done ?? value.completedTodos ?? value.completedCount,
    fallbackCompleted,
  ));
  const pending = Math.max(0, nonNegativeNumber(
    value.pending ?? value.incomplete ?? value.pendingTodos ?? value.pendingCount,
    total - completed,
  ));
  return {
    total,
    completed,
    pending,
    rate: total ? Math.round(completed / total * 100) : 0,
  };
}

function summarizeRows(rows) {
  const totals = rows.reduce((summary, row) => ({
    total: summary.total + row.total,
    completed: summary.completed + row.completed,
  }), { total: 0, completed: 0 });
  return normalizeSummary(totals);
}

function nonNegativeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : fallback;
}

function completionBadge(completed) {
  return completed
    ? '<span class="badge badge-success"><span class="badge-dot"></span>已完成</span>'
    : '<span class="badge badge-muted"><span class="badge-dot"></span>待完成</span>';
}

function rateBadge(rate, total) {
  if (!total) return '<span class="badge badge-muted">暂无任务</span>';
  if (rate >= 100) return '<span class="badge badge-success">100%</span>';
  if (rate > 0) return `<span class="badge badge-primary">${formatNumber(rate)}%</span>`;
  return '<span class="badge badge-muted">0%</span>';
}

function renderMetricCards(metrics) {
  return metrics.map((metric) => `
    <article class="metric-card">
      <div class="metric-card-head">
        <span>${escapeHtml(metric.label)}</span>
        <span class="metric-icon ${metric.style || ""}">${icon(metric.iconName, 17)}</span>
      </div>
      <p class="metric-value">${escapeHtml(metric.value)}</p>
      <p class="metric-note">${escapeHtml(metric.note)}</p>
    </article>
  `).join("");
}

function metricSkeletons() {
  return Array.from({ length: 4 }, () => (
    '<div class="metric-card"><div class="loading-state"><span class="spinner"></span></div></div>'
  )).join("");
}

function formatFriendlyDate(value) {
  if (!isDateValue(value)) return value || "未知日期";
  return DATE_FORMATTER.format(new Date(`${value}T12:00:00`));
}

function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function shiftDate(value, amount) {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() + amount);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function isDateValue(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const date = new Date(`${value}T12:00:00`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
