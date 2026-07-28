const NUMBER_FORMATTER = new Intl.NumberFormat("zh-CN");
const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function formatNumber(value) {
  const number = Number(value);
  return NUMBER_FORMATTER.format(Number.isFinite(number) ? number : 0);
}

export function formatDate(value, includeTime = false) {
  if (!value) return "—";
  if (!includeTime && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return includeTime ? DATE_TIME_FORMATTER.format(date) : date.toISOString().slice(0, 10);
}

export function today() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

export function slugify(value) {
  return String(value)
    .trim()
    .toLowerCase()
    .replace(/['"]/g, "")
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

export function icon(name, size = 20) {
  const paths = {
    dashboard: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    articles: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h6"/>',
    categories: '<path d="M20.6 13.7 12 22.3 1.7 12V2h10.3z"/><circle cx="7" cy="7" r="1.2"/>',
    analytics: '<path d="M4 19V9M10 19V5M16 19v-7M22 19V2"/><path d="M2 19h22"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v5M14 11v5"/>',
    logout: '<path d="M10 17l5-5-5-5M15 12H3"/><path d="M14 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    arrowLeft: '<path d="m15 18-6-6 6-6"/>',
    chevronUp: '<path d="m18 15-6-6-6 6"/>',
    chevronDown: '<path d="m6 9 6 6 6-6"/>',
    chevronRight: '<path d="m9 18 6-6-6-6"/>',
    grip: '<circle cx="9" cy="5" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="19" r="1"/>',
    refresh: '<path d="M20 6v6h-6"/><path d="M4 18v-6h6"/><path d="M6.5 8a7 7 0 0 1 11.7-2L20 8M4 16l1.8 2a7 7 0 0 0 11.7-2"/>',
    external: '<path d="M14 3h7v7M10 14 21 3"/><path d="M21 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h6"/>',
    warning: '<path d="M10.3 3.7 1.8 18.2A2 2 0 0 0 3.5 21h17a2 2 0 0 0 1.7-2.8L13.7 3.7a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  };
  return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || ""}</svg>`;
}

export function statusBadge(draft) {
  return draft
    ? '<span class="badge badge-muted"><span class="badge-dot"></span>草稿</span>'
    : '<span class="badge badge-success"><span class="badge-dot"></span>已发布</span>';
}

export function renderTags(items, className = "tag") {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) return '<span class="text-muted">—</span>';
  return `<div class="tag-list">${values.map((item) => `<span class="${className}">${escapeHtml(item)}</span>`).join("")}</div>`;
}

export function loadingState(label = "正在加载数据…") {
  return `
    <div class="loading-state" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <span>${escapeHtml(label)}</span>
    </div>
  `;
}

export function emptyState(title, description, action = "") {
  return `
    <div class="empty-state">
      <div class="empty-icon" aria-hidden="true">${icon("articles", 28)}</div>
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(description)}</p>
      ${action}
    </div>
  `;
}

export function errorState(message, retryLabel = "重新加载") {
  return `
    <div class="error-state" role="alert">
      <div>${icon("warning", 24)}</div>
      <div>
        <h3>数据加载失败</h3>
        <p>${escapeHtml(message)}</p>
      </div>
      <button type="button" class="button button-secondary button-sm" data-retry>${escapeHtml(retryLabel)}</button>
    </div>
  `;
}

export function renderTrendChart(rows, options = {}) {
  const data = Array.isArray(rows) ? rows : [];
  const width = 760;
  const height = 260;
  const padding = { top: 20, right: 18, bottom: 42, left: 48 };
  if (!data.length) {
    return emptyState("暂无趋势数据", "所选时间范围内还没有可展示的访问记录。");
  }

  const values = data.flatMap((row) => [Number(row.pv) || 0, Number(row.uv) || 0]);
  const max = Math.max(1, ...values);
  const usableWidth = width - padding.left - padding.right;
  const usableHeight = height - padding.top - padding.bottom;
  const x = (index) => padding.left + (data.length === 1 ? usableWidth / 2 : index * usableWidth / (data.length - 1));
  const y = (value) => padding.top + usableHeight - (Number(value) || 0) / max * usableHeight;
  const pvPoints = data.map((row, index) => `${x(index)},${y(row.pv)}`).join(" ");
  const uvPoints = data.map((row, index) => `${x(index)},${y(row.uv)}`).join(" ");
  const gridLines = [0, 0.5, 1].map((ratio) => {
    const gridY = padding.top + usableHeight * ratio;
    const label = Math.round(max * (1 - ratio));
    return `<line x1="${padding.left}" y1="${gridY}" x2="${width - padding.right}" y2="${gridY}" class="chart-grid"/><text x="${padding.left - 10}" y="${gridY + 4}" text-anchor="end" class="chart-label">${formatNumber(label)}</text>`;
  }).join("");
  const labelIndexes = [...new Set([0, Math.floor((data.length - 1) / 2), data.length - 1])];
  const xLabels = labelIndexes.map((index) => `<text x="${x(index)}" y="${height - 14}" text-anchor="middle" class="chart-label">${escapeHtml(String(data[index]?.date || "").slice(5))}</text>`).join("");
  const dots = data.map((row, index) => `
    <circle cx="${x(index)}" cy="${y(row.pv)}" r="3" class="chart-dot chart-dot-pv"><title>${escapeHtml(row.date)} PV ${formatNumber(row.pv)}</title></circle>
    <circle cx="${x(index)}" cy="${y(row.uv)}" r="3" class="chart-dot chart-dot-uv"><title>${escapeHtml(row.date)} UV ${formatNumber(row.uv)}</title></circle>
  `).join("");

  return `
    <div class="chart-wrap">
      <div class="chart-legend">
        <span><i class="legend-line pv"></i>${escapeHtml(options.pvLabel || "浏览量 PV")}</span>
        <span><i class="legend-line uv"></i>${escapeHtml(options.uvLabel || "访客数 UV")}</span>
      </div>
      <svg class="trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="访问量趋势图">
        ${gridLines}
        ${xLabels}
        <polyline points="${pvPoints}" class="chart-line chart-line-pv"/>
        <polyline points="${uvPoints}" class="chart-line chart-line-uv"/>
        ${dots}
      </svg>
    </div>
  `;
}

export function unwrap(payload, key, fallback) {
  if (payload && Object.hasOwn(payload, key)) return payload[key];
  return payload ?? fallback;
}
