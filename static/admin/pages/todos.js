import {
  emptyState,
  errorState,
  escapeHtml,
  formatNumber,
  icon,
  loadingState,
  today,
  unwrap,
} from "../ui.js?v=20260801-console-5";

const DATE_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  month: "long",
  day: "numeric",
  weekday: "short",
});
const ALL_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7];
const WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export async function renderTodos(context) {
  const { api, container, signal, confirm, toast } = context;
  let selectedDate = today();
  let todos = [];
  let todoPlans = [];
  let daySummary = null;
  let statsPayload = {};
  let statsLoadError = null;
  let statsDays = 14;
  let editingTodo = null;
  let listRequestSequence = 0;
  let plansRequestSequence = 0;
  let statsRequestSequence = 0;
  const busyTodoIds = new Set();
  const busyPlanIds = new Set();

  container.innerHTML = `
    <section class="page-section">
      <div class="page-toolbar">
        <div class="section-header">
          <div>
            <h2>每日 Todo</h2>
            <p id="todoDateDescription">管理今天的待办事项，并记录每日完成情况。</p>
          </div>
        </div>
        <div class="toolbar-actions todo-toolbar-actions">
          <div class="todo-date-nav">
            <button type="button" class="button button-secondary todo-date-arrow" data-shift-date="-1" aria-label="查看前一天">${icon("arrowLeft", 17)}<span>前一天</span></button>
            <label class="field todo-date-input">
              <span class="sr-only">选择 Todo 日期</span>
              <input type="date" value="${escapeHtml(selectedDate)}" data-todo-date aria-label="选择 Todo 日期" />
            </label>
            <button type="button" class="button button-secondary todo-today-button" data-today>今天</button>
            <button type="button" class="button button-secondary todo-date-arrow" data-shift-date="1" aria-label="查看后一天"><span>后一天</span>${icon("chevronRight", 17)}</button>
          </div>
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

      <section class="panel todo-plans-panel">
        <div class="panel-header">
          <div>
            <h2>重复计划</h2>
            <p id="todoPlansDescription">正在读取固定与自定义重复计划…</p>
          </div>
          <button type="button" class="button button-secondary button-sm" data-add-recurring-plan>${icon("repeat", 16)}新建重复计划</button>
        </div>
        <div id="todoPlans">${loadingState("正在加载重复计划…")}</div>
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

      <dialog id="todoDialog" class="dialog todo-dialog" aria-labelledby="todoDialogTitle" aria-describedby="todoDialogDescription">
        <form id="todoForm" class="dialog-card" novalidate>
          <div class="dialog-header">
            <div>
              <h2 id="todoDialogTitle">新增 Todo</h2>
              <p id="todoDialogDescription">记录一件当天需要完成的事情。</p>
            </div>
            <button type="button" class="icon-button" data-close-todo-dialog aria-label="关闭">${icon("close", 18)}</button>
          </div>
          <div id="todoFormAlert" class="form-alert is-hidden" role="alert"></div>
          <div class="dialog-body todo-dialog-body">
            <label class="field">
              <span class="field-label">任务内容</span>
              <input id="todoTitleInput" name="title" type="text" maxlength="200" autocomplete="off" placeholder="例如：整理本周文章选题" aria-describedby="todoTitleError" required />
              <span id="todoTitleError" class="field-error is-hidden" data-error-for="title"></span>
            </label>

            <fieldset class="todo-schedule-fieldset">
              <legend class="field-label">安排方式</legend>
              <div class="repeat-options">
                <label class="repeat-option">
                  <input type="radio" name="scheduleType" value="once" checked />
                  <span class="repeat-option-icon">${icon("calendar", 19)}</span>
                  <span><strong>仅一次</strong><small>只在指定日期出现</small></span>
                </label>
                <label class="repeat-option">
                  <input type="radio" name="scheduleType" value="daily" />
                  <span class="repeat-option-icon">${icon("repeat", 19)}</span>
                  <span><strong>每天固定</strong><small>每天自动生成任务</small></span>
                </label>
                <label class="repeat-option">
                  <input type="radio" name="scheduleType" value="weekly" />
                  <span class="repeat-option-icon">${icon("calendarRange", 19)}</span>
                  <span><strong>自定义重复</strong><small>选择每周的执行日</small></span>
                </label>
              </div>
            </fieldset>

            <label class="field" data-once-date-field>
              <span class="field-label">计划日期</span>
              <input id="todoDateInput" name="date" type="date" aria-describedby="todoDateError" required />
              <span id="todoDateError" class="field-error is-hidden" data-error-for="date"></span>
            </label>

            <div class="todo-recurrence-fields" data-recurrence-fields hidden>
              <label class="field">
                <span class="field-label">开始日期</span>
                <input id="todoStartDateInput" name="startDate" type="date" aria-describedby="todoStartDateError" />
                <span id="todoStartDateError" class="field-error is-hidden" data-error-for="startDate"></span>
              </label>

              <fieldset class="weekday-fieldset" data-weekday-fields aria-describedby="todoWeekdaysError" hidden>
                <legend class="sr-only">每周重复</legend>
                <div class="weekday-heading">
                  <span class="field-label">每周重复</span>
                  <div class="weekday-presets" aria-label="快速选择星期">
                    <button type="button" class="text-button" data-weekday-preset="workdays">工作日</button>
                    <button type="button" class="text-button" data-weekday-preset="weekend">周末</button>
                    <button type="button" class="text-button" data-weekday-preset="all">每天</button>
                  </div>
                </div>
                <div class="weekday-picker">
                  ${["一", "二", "三", "四", "五", "六", "日"].map((label, index) => `
                    <label class="weekday-choice">
                      <input type="checkbox" name="weekday" value="${index + 1}" />
                      <span>周${label}</span>
                    </label>
                  `).join("")}
                </div>
                <span id="todoWeekdaysError" class="field-error is-hidden" data-error-for="weekdays"></span>
              </fieldset>

              <div class="repeat-end-control">
                <label class="checkbox repeat-end-toggle">
                  <input type="checkbox" name="hasEndDate" />
                  <span>设置截止日期</span>
                </label>
                <label class="field" data-end-date-field hidden>
                  <span class="field-label">重复至（包含当天）</span>
                  <input id="todoEndDateInput" name="endDate" type="date" aria-describedby="todoEndDateError" />
                  <span id="todoEndDateError" class="field-error is-hidden" data-error-for="endDate"></span>
                </label>
              </div>
            </div>

            <div class="recurrence-preview" aria-live="polite">
              <span class="recurrence-preview-icon">${icon("sparkles", 18)}</span>
              <div>
                <strong>计划预览</strong>
                <p data-recurrence-preview>仅安排在所选日期。</p>
              </div>
            </div>

            <div class="todo-series-notice is-hidden" data-series-notice>
              ${icon("warning", 17)}
              <span>正在修改整个重复计划；不再符合新规则的完成记录会被移除。</span>
            </div>
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
  const plansTarget = container.querySelector("#todoPlans");
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
      : `正在查看 ${dateLabel}的待办事项。`;
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
      <div class="todo-list" role="list">
        ${todos.map((todo) => {
          const id = String(todo.id);
          const busy = busyTodoIds.has(id)
            || (todo.recurring && busyPlanIds.has(String(todo.planId)));
          return `
            <article
              class="todo-item ${todo.completed ? "is-completed" : ""} ${busy ? "is-busy" : ""}"
              data-todo-id="${escapeHtml(id)}"
              role="listitem"
              ${busy ? 'aria-busy="true"' : ""}
            >
              <label class="todo-completion-control">
                <input
                  type="checkbox"
                  data-toggle-todo="${escapeHtml(id)}"
                  ${todo.completed ? "checked" : ""}
                  ${busy ? "disabled" : ""}
                />
                <span class="todo-checkmark">${icon("check", 18)}</span>
                <span class="sr-only">将“${escapeHtml(todo.title)}”标记为${todo.completed ? "未完成" : "已完成"}</span>
              </label>
              <div class="todo-item-content">
                <span class="todo-item-title">${escapeHtml(todo.title || "未命名 Todo")}</span>
                <div class="todo-item-meta">
                  ${todo.recurring ? recurrenceBadge(todo) : '<span class="badge badge-muted">单次任务</span>'}
                  <span>${escapeHtml(todoPlanRange(todo))}</span>
                  <span class="todo-updated">更新于 ${escapeHtml(formatTimestamp(todo.updatedAt || todo.createdAt))}</span>
                </div>
              </div>
              <div class="todo-item-actions">
                ${busy ? '<span class="spinner" aria-hidden="true"></span>' : ""}
                <button type="button" class="icon-button" data-edit-todo="${escapeHtml(id)}" aria-label="编辑 ${escapeHtml(todo.title)}" ${busy ? "disabled" : ""}>${icon("edit", 16)}</button>
                <button type="button" class="icon-button" data-delete-todo="${escapeHtml(id)}" aria-label="删除 ${escapeHtml(todo.title)}" ${busy ? "disabled" : ""}>${icon("trash", 16)}</button>
              </div>
            </article>
          `;
        }).join("")}
      </div>
    `;
  }

  function renderTodoPlans() {
    const description = container.querySelector("#todoPlansDescription");
    description.textContent = todoPlans.length
      ? `共 ${formatNumber(todoPlans.length)} 个计划；无论今天是否执行，都可以在这里统一管理。`
      : "固定每日和自定义重复计划会集中显示在这里。";

    if (!todoPlans.length) {
      plansTarget.innerHTML = emptyState(
        "还没有重复计划",
        "创建每天固定或按星期重复的 Todo，之后可以随时在这里修改。",
        `<button type="button" class="button button-primary button-sm" data-add-recurring-plan>${icon("repeat", 16)}新建重复计划</button>`,
      );
      return;
    }

    plansTarget.innerHTML = `
      <div class="todo-plan-list" role="list">
        ${todoPlans.map((plan) => {
          const planId = String(plan.planId || plan.id);
          const busy = busyPlanIds.has(planId);
          return `
            <article
              class="todo-plan-item ${busy ? "is-busy" : ""}"
              data-todo-plan-id="${escapeHtml(planId)}"
              role="listitem"
              ${busy ? 'aria-busy="true"' : ""}
            >
              <span class="todo-plan-icon" aria-hidden="true">${icon("repeat", 18)}</span>
              <div class="todo-plan-content">
                <span class="todo-plan-title">${escapeHtml(plan.title || "未命名重复计划")}</span>
                <div class="todo-plan-meta">
                  ${recurrenceBadge(plan)}
                  ${planStatusBadge(plan)}
                  <span>${escapeHtml(todoPlanRange(plan))}</span>
                  <span class="todo-updated">更新于 ${escapeHtml(formatTimestamp(plan.updatedAt || plan.createdAt))}</span>
                </div>
              </div>
              <div class="todo-plan-actions">
                ${busy ? '<span class="spinner" aria-hidden="true"></span>' : ""}
                <button type="button" class="icon-button" data-edit-todo-plan="${escapeHtml(planId)}" aria-label="编辑重复计划 ${escapeHtml(plan.title)}" ${busy ? "disabled" : ""}>${icon("edit", 16)}</button>
                <button type="button" class="icon-button" data-delete-todo-plan="${escapeHtml(planId)}" aria-label="删除重复计划 ${escapeHtml(plan.title)}" ${busy ? "disabled" : ""}>${icon("trash", 16)}</button>
              </div>
            </article>
          `;
        }).join("")}
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

  async function loadTodoPlans({ showLoading = true } = {}) {
    const requestId = ++plansRequestSequence;
    if (showLoading) {
      plansTarget.innerHTML = loadingState("正在加载重复计划…");
      container.querySelector("#todoPlansDescription").textContent = "正在读取固定与自定义重复计划…";
    }
    try {
      const payload = await api.todoPlans(signal);
      if (signal.aborted || requestId !== plansRequestSequence) return;
      const collection = unwrap(payload, "plans", []);
      todoPlans = Array.isArray(collection)
        ? collection.map((plan, index) => normalizeTodo(
          plan,
          isDateValue(plan?.startDate) ? plan.startDate : selectedDate,
          index,
        ))
        : [];
      todoPlans.sort((left, right) => {
        const statusDifference = planSortRank(left) - planSortRank(right);
        if (statusDifference) return statusDifference;
        return String(left.recurrence?.startDate || "").localeCompare(
          String(right.recurrence?.startDate || ""),
        );
      });
      renderTodoPlans();
    } catch (error) {
      if (error.name === "AbortError" || requestId !== plansRequestSequence) return;
      container.querySelector("#todoPlansDescription").textContent = "重复计划暂时无法读取。";
      plansTarget.innerHTML = errorState(error.message);
      plansTarget.querySelector("[data-retry]")?.addEventListener(
        "click",
        () => loadTodoPlans(),
        { once: true },
      );
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
      statsLoadError = null;
      renderStats();
    } catch (error) {
      if (error.name === "AbortError" || requestId !== statsRequestSequence) return;
      statsLoadError = error;
      statsMetricsTarget.innerHTML = `
        <div class="panel span-full">${errorState(error.message)}</div>
      `;
      statsTableTarget.innerHTML = errorState(error.message);
      statsTableTarget.querySelector("[data-retry]")?.addEventListener("click", () => loadStats(), { once: true });
    }
  }

  function openTodoDialog(todo = null, initialScheduleType = "once") {
    editingTodo = todo;
    form.reset();
    form.elements.title.value = todo?.title || "";
    form.elements.date.value = todo?.date || selectedDate;
    form.elements.startDate.value = todo?.recurrence?.startDate || selectedDate;
    const scheduleType = todo?.recurring
      ? todo.recurrence.type
      : (initialScheduleType === "daily" || initialScheduleType === "weekly"
        ? initialScheduleType
        : "once");
    form.querySelector(`input[name="scheduleType"][value="${scheduleType}"]`).checked = true;
    const weekdays = todo?.recurrence?.weekdays?.length
      ? todo.recurrence.weekdays
      : [isoWeekday(todo?.recurrence?.startDate || selectedDate)];
    setSelectedWeekdays(weekdays);
    const endDate = todo?.recurrence?.endDate || "";
    form.elements.hasEndDate.checked = Boolean(endDate);
    form.elements.endDate.value = endDate;
    form.querySelectorAll('input[name="scheduleType"]').forEach((input) => {
      input.disabled = Boolean(todo) && (
        todo.recurring ? input.value === "once" : input.value !== "once"
      );
    });
    form.querySelector("#todoDialogTitle").textContent = todo
      ? (todo.recurring ? "编辑重复计划" : "编辑 Todo")
      : "新增 Todo";
    form.querySelector("#todoDialogDescription").textContent = todo
      ? (todo.recurring
        ? "本次修改会应用到这项任务的整个重复计划。"
        : "修改任务内容或将它安排到其他日期。")
      : "一次设置，让每天的计划按节奏自动出现。";
    form.querySelector("[data-series-notice]").classList.toggle("is-hidden", !todo?.recurring);
    clearFormErrors();
    updateFormPresentation();
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
    form.querySelectorAll(".input-error").forEach((element) => {
      element.classList.remove("input-error");
    });
    form.querySelectorAll('[aria-invalid="true"]').forEach((element) => {
      element.removeAttribute("aria-invalid");
    });
    form.querySelector(".weekday-picker").classList.remove("input-error");
  }

  function setSelectedWeekdays(weekdays) {
    const selected = new Set(weekdays.map(Number));
    form.querySelectorAll('input[name="weekday"]').forEach((input) => {
      input.checked = selected.has(Number(input.value));
    });
  }

  function selectedWeekdays() {
    return [...form.querySelectorAll('input[name="weekday"]:checked')]
      .map((input) => Number(input.value))
      .sort((left, right) => left - right);
  }

  function updateFormPresentation() {
    const scheduleType = form.elements.scheduleType.value || "once";
    const recurring = scheduleType !== "once";
    const hasEndDate = recurring && form.elements.hasEndDate.checked;
    form.querySelector("[data-once-date-field]").hidden = recurring;
    form.querySelector("[data-recurrence-fields]").hidden = !recurring;
    form.querySelector("[data-weekday-fields]").hidden = scheduleType !== "weekly";
    form.querySelector("[data-end-date-field]").hidden = !hasEndDate;
    form.elements.date.required = !recurring;
    form.elements.startDate.required = recurring;
    form.elements.endDate.required = hasEndDate;
    form.elements.endDate.min = form.elements.startDate.value || "2000-01-01";
    form.querySelector("[data-save-todo]").textContent = editingTodo
      ? "保存修改"
      : (recurring ? "创建重复计划" : "创建 Todo");
    updateRecurrencePreview();
  }

  function updateRecurrencePreview() {
    const scheduleType = form.elements.scheduleType.value || "once";
    const date = scheduleType === "once"
      ? form.elements.date.value
      : form.elements.startDate.value;
    const weekdays = scheduleType === "daily" ? ALL_WEEKDAYS : selectedWeekdays();
    const hasEndDate = form.elements.hasEndDate.checked;
    const endDate = hasEndDate ? form.elements.endDate.value : null;
    const description = describeSchedule({
      scheduleType,
      date,
      weekdays,
      endDate,
      hasEndDate,
    });
    const preview = form.querySelector("[data-recurrence-preview]");
    if (preview.textContent !== description) preview.textContent = description;
  }

  function validateForm() {
    clearFormErrors();
    const title = form.elements.title.value.trim();
    if (!title) {
      showFieldError("title", "请输入任务内容。");
      return null;
    }
    if (title.length > 200) {
      showFieldError("title", "任务内容不能超过 200 个字符。");
      return null;
    }
    const scheduleType = form.elements.scheduleType.value || "once";
    if (scheduleType === "once") {
      const date = form.elements.date.value;
      if (!isDateValue(date)) {
        showFieldError("date", "请选择有效日期。");
        return null;
      }
      return { title, scheduleType, date };
    }
    const startDate = form.elements.startDate.value;
    if (!isDateValue(startDate)) {
      showFieldError("startDate", "请选择有效的开始日期。");
      return null;
    }
    const weekdays = scheduleType === "daily" ? ALL_WEEKDAYS : selectedWeekdays();
    if (!weekdays.length) {
      showFieldError("weekdays", "至少选择一个重复日。");
      return null;
    }
    let endDate = null;
    if (form.elements.hasEndDate.checked) {
      endDate = form.elements.endDate.value;
      if (!isDateValue(endDate)) {
        showFieldError("endDate", "请选择有效的截止日期。");
        return null;
      }
      if (endDate < startDate) {
        showFieldError("endDate", "截止日期不能早于开始日期。");
        return null;
      }
      if (!firstMatchingDate(startDate, weekdays, endDate)) {
        showFieldError("weekdays", "这个日期范围内没有符合所选星期的任务。");
        return null;
      }
    }
    return { title, scheduleType, startDate, endDate, weekdays };
  }

  function showFieldError(fieldName, message) {
    const weekdayFieldset = form.querySelector("[data-weekday-fields]");
    const field = fieldName === "weekdays" ? weekdayFieldset : form.elements[fieldName];
    const visualField = fieldName === "weekdays"
      ? form.querySelector(".weekday-picker")
      : field;
    const error = form.querySelector(`[data-error-for="${fieldName}"]`);
    visualField.classList.add("input-error");
    field.setAttribute("aria-invalid", "true");
    error.textContent = message;
    error.classList.remove("is-hidden");
    if (fieldName === "weekdays") {
      form.querySelector('input[name="weekday"]')?.focus();
    } else {
      field.focus();
    }
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
    if (editingTodo?.recurring && recurrenceScheduleChanged(editingTodo, values)) {
      const accepted = await confirm({
        title: "确认修改整个重复计划？",
        message: `“${editingTodo.title}”的新重复规则会应用到整个计划；不再符合规则的历史完成记录会被永久清理，并影响完成统计。`,
        confirmLabel: "确认修改规则",
      });
      if (!accepted || signal.aborted) return;
    }
    const button = form.querySelector("[data-save-todo]");
    button.disabled = true;
    button.innerHTML = '<span class="spinner spinner-light" aria-hidden="true"></span><span>正在保存…</span>';
    const changesPlan = Boolean(editingTodo?.recurring || (!editingTodo && values.scheduleType !== "once"));
    try {
      if (editingTodo) {
        if (editingTodo.recurring) {
          await api.updateTodoPlan(editingTodo.planId, planPayload(values), signal);
          toast("重复计划已更新。");
        } else {
          await api.updateTodo(editingTodo.id, {
            title: values.title,
            date: values.date,
          }, signal);
          toast("Todo 已更新。");
        }
      } else if (values.scheduleType === "once") {
        await api.createTodo({ title: values.title, date: values.date }, signal);
        toast("Todo 已创建。");
      } else {
        await api.createTodoPlan(planPayload(values), signal);
        toast("重复计划已创建，之后会按设置自动出现。");
      }
      if (signal.aborted) return;
      dialog.close();
      const reloads = [loadTodos()];
      if (changesPlan) reloads.push(loadTodoPlans());
      await Promise.all(reloads);
      void loadStats({ showLoading: false });
    } catch (error) {
      if (error.name === "AbortError") return;
      showFormError(error.message);
    } finally {
      button.disabled = false;
      updateFormPresentation();
    }
  }

  function planPayload(values) {
    return {
      title: values.title,
      repeatType: values.scheduleType,
      startDate: values.startDate,
      endDate: values.endDate,
      weekdays: values.weekdays,
    };
  }

  function recurrenceScheduleChanged(todo, values) {
    const recurrence = todo?.recurrence || {};
    const previousWeekdays = Array.isArray(recurrence.weekdays)
      ? recurrence.weekdays.map(Number).sort((left, right) => left - right)
      : [];
    const nextWeekdays = Array.isArray(values.weekdays)
      ? values.weekdays.map(Number).sort((left, right) => left - right)
      : [];
    return recurrence.type !== values.scheduleType
      || recurrence.startDate !== values.startDate
      || (recurrence.endDate || null) !== (values.endDate || null)
      || previousWeekdays.length !== nextWeekdays.length
      || previousWeekdays.some((weekday, index) => weekday !== nextWeekdays[index]);
  }

  async function toggleTodo(id, completed) {
    const todo = findTodo(id);
    if (!todo || busyTodoIds.has(String(id))) return;
    const operationDate = selectedDate;
    const previousCompleted = todo.completed;
    todo.completed = completed;
    daySummary = summarizeTodoCollection(todos);
    busyTodoIds.add(String(id));
    renderTodoList();
    let succeeded = false;
    try {
      let payload;
      if (todo.recurring) {
        payload = await api.updateTodoOccurrence(todo.planId, todo.date, completed, signal);
      } else {
        payload = await api.updateTodo(todo.id, { completed }, signal);
      }
      if (signal.aborted) return;
      succeeded = true;
      busyTodoIds.delete(String(id));
      if (selectedDate === operationDate) {
        const current = findTodo(id);
        if (current && payload?.id) {
          Object.assign(current, normalizeTodo(payload, operationDate, 0));
        }
        daySummary = summarizeTodoCollection(todos);
        renderTodoList();
      }
      toast(todo.recurring
        ? (completed
          ? `${formatFriendlyDate(todo.date)}这一次已完成，后续计划不受影响。`
          : `${formatFriendlyDate(todo.date)}这一次已恢复为待完成。`)
        : (completed ? "Todo 已完成。" : "Todo 已恢复为待完成。"));
      void loadStats({ showLoading: false });
    } catch (error) {
      if (error.name === "AbortError") return;
      if (selectedDate === operationDate) {
        const current = findTodo(id);
        if (current) current.completed = previousCompleted;
        daySummary = summarizeTodoCollection(todos);
      }
      toast(error.message, "error");
    } finally {
      busyTodoIds.delete(String(id));
      if (!signal.aborted && !succeeded && selectedDate === operationDate) renderTodoList();
    }
  }

  async function deleteTodo(id) {
    const todo = findTodo(id);
    if (!todo || busyTodoIds.has(String(id))) return;
    if (todo.recurring) {
      await deleteTodoPlan(todo);
      return;
    }
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
      todos = todos.filter((item) => String(item.id) !== String(id));
      daySummary = summarizeTodoCollection(todos);
      renderTodoList();
      toast("Todo 已删除。");
      void loadStats({ showLoading: false });
    } catch (error) {
      if (error.name === "AbortError") return;
      toast(error.message, "error");
    } finally {
      busyTodoIds.delete(String(id));
      if (!signal.aborted && !deleted) renderTodoList();
    }
  }

  async function deleteTodoPlan(plan) {
    const planId = String(plan?.planId || plan?.id || "");
    if (!planId || busyPlanIds.has(planId)) return;
    const accepted = await confirm({
      title: "删除整个重复计划？",
      message: `“${plan.title}”的整个重复计划和已有完成记录都会被永久删除。此操作不是只删除某一次任务。`,
      confirmLabel: "删除整个计划",
    });
    if (!accepted || signal.aborted) return;
    busyPlanIds.add(planId);
    renderTodoPlans();
    renderTodoList();
    try {
      await api.deleteTodoPlan(planId, signal);
      if (signal.aborted) return;
      todoPlans = todoPlans.filter((item) => String(item.planId || item.id) !== planId);
      todos = todos.filter((item) => String(item.planId || "") !== planId);
      daySummary = summarizeTodoCollection(todos);
      toast("重复计划已删除。");
      void loadStats({ showLoading: false });
    } catch (error) {
      if (error.name === "AbortError") return;
      toast(error.message, "error");
    } finally {
      busyPlanIds.delete(planId);
      if (!signal.aborted) {
        renderTodoPlans();
        renderTodoList();
      }
    }
  }

  function findTodo(id) {
    return todos.find((todo) => String(todo.id) === String(id));
  }

  function findTodoPlan(id) {
    return todoPlans.find((plan) => String(plan.planId || plan.id) === String(id));
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
    if (event.target.closest("[data-add-recurring-plan]")) {
      openTodoDialog(null, "daily");
      return;
    }
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
      Promise.all([loadTodos(), loadTodoPlans(), loadStats()]);
      return;
    }
    const presetButton = event.target.closest("[data-weekday-preset]");
    if (presetButton) {
      const preset = presetButton.dataset.weekdayPreset;
      setSelectedWeekdays(
        preset === "workdays" ? [1, 2, 3, 4, 5]
          : preset === "weekend" ? [6, 7]
            : ALL_WEEKDAYS,
      );
      clearFormErrors();
      updateRecurrencePreview();
      return;
    }
    const editButton = event.target.closest("[data-edit-todo]");
    if (editButton) {
      const todo = findTodo(editButton.dataset.editTodo);
      if (todo) openTodoDialog(todo);
      return;
    }
    const editPlanButton = event.target.closest("[data-edit-todo-plan]");
    if (editPlanButton) {
      const plan = findTodoPlan(editPlanButton.dataset.editTodoPlan);
      if (plan) openTodoDialog(plan);
      return;
    }
    const deleteButton = event.target.closest("[data-delete-todo]");
    if (deleteButton) {
      deleteTodo(deleteButton.dataset.deleteTodo);
      return;
    }
    const deletePlanButton = event.target.closest("[data-delete-todo-plan]");
    if (deletePlanButton) {
      const plan = findTodoPlan(deletePlanButton.dataset.deleteTodoPlan);
      if (plan) deleteTodoPlan(plan);
    }
  }

  container.addEventListener("click", handleContainerClick);

  listTarget.addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-toggle-todo]");
    if (checkbox) toggleTodo(checkbox.dataset.toggleTodo, checkbox.checked);
  });

  dateInput.addEventListener("change", () => selectDate(dateInput.value));
  statsDaysSelect.addEventListener("change", () => {
    statsDays = Number(statsDaysSelect.value) === 30 ? 30 : 14;
    if (statsLoadError) loadStats();
    else renderStats();
  });
  form.addEventListener("submit", saveTodo);
  form.addEventListener("input", (event) => {
    clearFormErrors();
    if (event.target.matches?.(
      'input[name="scheduleType"], input[name="date"], input[name="startDate"], input[name="weekday"], input[name="hasEndDate"], input[name="endDate"]',
    )) {
      updateFormPresentation();
    }
  });
  dialog.addEventListener("cancel", (event) => {
    if (form.querySelector("[data-save-todo]").disabled) event.preventDefault();
  });
  dialog.querySelectorAll("[data-close-todo-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!form.querySelector("[data-save-todo]").disabled) dialog.close();
    });
  });

  updateDateHeadings();
  await Promise.all([loadTodos(), loadTodoPlans(), loadStats()]);

  return () => {
    container.removeEventListener("click", handleContainerClick);
  };
}

function normalizeTodo(todo, fallbackDate, index) {
  const value = todo && typeof todo === "object" ? todo : {};
  const recurrenceValue = value.recurrence && typeof value.recurrence === "object"
    ? value.recurrence
    : {};
  const recurring = Boolean(
    value.recurring
    || value.kind === "recurring"
    || value.planId
    || value.plan_id,
  );
  const recurrenceType = recurrenceValue.type ?? recurrenceValue.repeatType
    ?? value.repeatType ?? value.repeat_type;
  const weekdays = Array.isArray(recurrenceValue.weekdays)
    ? recurrenceValue.weekdays
      .map(Number)
      .filter((weekday) => Number.isInteger(weekday) && weekday >= 1 && weekday <= 7)
      .sort((left, right) => left - right)
    : [];
  return {
    ...value,
    id: value.id ?? value.todoId ?? value.uuid ?? `${fallbackDate}-${index}`,
    planId: value.planId ?? value.plan_id ?? null,
    title: String(value.title ?? value.text ?? ""),
    date: isDateValue(value.date) ? value.date : fallbackDate,
    completed: Boolean(value.completed ?? value.done ?? value.status === "completed"),
    createdAt: value.createdAt ?? value.created_at ?? "",
    updatedAt: value.updatedAt ?? value.updated_at ?? "",
    recurring,
    recurrence: recurring ? {
      type: recurrenceType === "daily" ? "daily" : "weekly",
      startDate: recurrenceValue.startDate ?? recurrenceValue.start_date ?? value.startDate ?? value.start_date ?? fallbackDate,
      endDate: recurrenceValue.endDate ?? recurrenceValue.end_date ?? value.endDate ?? value.end_date ?? null,
      weekdays,
    } : null,
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

function summarizeTodoCollection(items) {
  const collection = Array.isArray(items) ? items : [];
  const completed = collection.filter((todo) => todo.completed).length;
  return {
    total: collection.length,
    completed,
    pending: collection.length - completed,
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

function rateBadge(rate, total) {
  if (!total) return '<span class="badge badge-muted">暂无任务</span>';
  if (rate >= 100) return '<span class="badge badge-success">100%</span>';
  if (rate > 0) return `<span class="badge badge-primary">${formatNumber(rate)}%</span>`;
  return '<span class="badge badge-muted">0%</span>';
}

function recurrenceBadge(todo) {
  const recurrence = todo.recurrence || {};
  const label = recurrence.type === "daily"
    ? "每天固定"
    : `每周 ${formatWeekdays(recurrence.weekdays, true)}`;
  return `<span class="badge badge-primary todo-recurrence-badge">${icon("repeat", 13)}${escapeHtml(label)}</span>`;
}

function planLifecycle(plan, referenceDate = today()) {
  const recurrence = plan?.recurrence || {};
  if (isDateValue(recurrence.startDate) && recurrence.startDate > referenceDate) {
    return { label: "即将开始", className: "badge-muted", rank: 1 };
  }
  if (isDateValue(recurrence.endDate) && recurrence.endDate < referenceDate) {
    return { label: "已结束", className: "badge-muted", rank: 2 };
  }
  return { label: "进行中", className: "badge-success", rank: 0 };
}

function planStatusBadge(plan) {
  const status = planLifecycle(plan);
  return `<span class="badge ${status.className}">${escapeHtml(status.label)}</span>`;
}

function planSortRank(plan) {
  return planLifecycle(plan).rank;
}

function todoPlanRange(todo) {
  if (!todo.recurring) return formatFriendlyDate(todo.date);
  const recurrence = todo.recurrence || {};
  const start = formatFriendlyDate(recurrence.startDate || todo.date);
  return recurrence.endDate
    ? `从 ${start}起，至 ${formatFriendlyDate(recurrence.endDate)}`
    : `从 ${start}起，长期重复`;
}

function formatWeekdays(weekdays, compact = false) {
  const values = Array.isArray(weekdays) ? weekdays : [];
  return values
    .filter((weekday) => Number.isInteger(weekday) && weekday >= 1 && weekday <= 7)
    .map((weekday) => compact ? WEEKDAY_LABELS[weekday - 1].slice(1) : WEEKDAY_LABELS[weekday - 1])
    .join(compact ? " · " : "、");
}

function describeSchedule({ scheduleType, date, weekdays, endDate, hasEndDate = false }) {
  if (!isDateValue(date)) {
    return scheduleType === "once" ? "选择日期后即可查看安排。" : "选择开始日期后即可查看重复节奏。";
  }
  if (scheduleType === "once") {
    return `仅安排在 ${formatFriendlyDate(date)}。`;
  }
  const ending = hasEndDate
    ? (endDate && isDateValue(endDate)
      ? `，至 ${formatFriendlyDate(endDate)}（包含当天）`
      : "，请选择截止日期")
    : "，不设截止日期";
  if (scheduleType === "daily") {
    return `从 ${formatFriendlyDate(date)}起，每天重复${ending}。`;
  }
  if (!weekdays.length) {
    return "请选择一周中需要执行这项 Todo 的日期。";
  }
  const firstDate = firstMatchingDate(date, weekdays, endDate || null);
  const firstHint = firstDate && firstDate !== date
    ? `首次任务会出现在 ${formatFriendlyDate(firstDate)}。`
    : "";
  return `从 ${formatFriendlyDate(date)}起，每${formatWeekdays(weekdays)}重复${ending}。${firstHint}`;
}

function firstMatchingDate(startDate, weekdays, endDate = null) {
  if (!isDateValue(startDate)) return null;
  const selected = new Set(weekdays.map(Number));
  for (let offset = 0; offset < 7; offset += 1) {
    const candidate = shiftDate(startDate, offset);
    if (endDate && candidate > endDate) return null;
    if (selected.has(isoWeekday(candidate))) return candidate;
  }
  return null;
}

function isoWeekday(value) {
  if (!isDateValue(value)) return 1;
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day, 12).getDay() || 7;
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
  if (!isDateValue(value)) return value;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  date.setDate(date.getDate() + amount);
  return [
    String(date.getFullYear()).padStart(4, "0"),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function isDateValue(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const [year, month, day] = String(value).split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  return !Number.isNaN(date.getTime())
    && date.getFullYear() === year
    && date.getMonth() + 1 === month
    && date.getDate() === day;
}
