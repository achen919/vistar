import {
  emptyState,
  errorState,
  escapeHtml,
  formatNumber,
  icon,
  loadingState,
  renderTrendChart,
} from "../ui.js?v=20260801-console-5";

export async function renderAnalytics(context) {
  const { api, container, signal } = context;
  let requestSequence = 0;

  container.innerHTML = `
    <section class="page-section">
      <div class="page-toolbar">
        <div class="section-header">
          <div>
            <h2>访问数据</h2>
            <p>统计网站浏览量、独立访客与热门内容。</p>
          </div>
        </div>
        <label class="field">
          <span class="is-hidden">统计时间范围</span>
          <select data-days aria-label="统计时间范围">
            <option value="7">最近 7 天</option>
            <option value="30" selected>最近 30 天</option>
            <option value="90">最近 90 天</option>
          </select>
        </label>
      </div>
      <div id="statsNotice" class="data-notice is-hidden"></div>
      <div id="analyticsMetrics" class="metric-grid">
        ${Array.from({ length: 4 }, () => '<div class="metric-card"><div class="loading-state"><span class="spinner"></span></div></div>').join("")}
      </div>
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>访问趋势</h2>
            <p id="trendDescription">最近 30 天的 PV 与 UV 变化</p>
          </div>
        </div>
        <div id="analyticsTrend" class="panel-body">${loadingState()}</div>
      </section>
      <div class="analytics-grid">
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>热门页面</h2>
              <p>按浏览量排序的内容页面</p>
            </div>
          </div>
          <div id="topPages" class="panel-body">${loadingState()}</div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>访问来源</h2>
              <p>为网站带来访问的来源域名</p>
            </div>
          </div>
          <div id="referrers" class="panel-body">${loadingState()}</div>
        </section>
      </div>
    </section>
  `;

  const daysSelect = container.querySelector("[data-days]");
  const metricsTarget = container.querySelector("#analyticsMetrics");
  const trendTarget = container.querySelector("#analyticsTrend");
  const topPagesTarget = container.querySelector("#topPages");
  const referrersTarget = container.querySelector("#referrers");

  async function load() {
    const requestId = ++requestSequence;
    const days = Number(daysSelect.value);
    metricsTarget.innerHTML = Array.from({ length: 4 }, () => '<div class="metric-card"><div class="loading-state"><span class="spinner"></span></div></div>').join("");
    trendTarget.innerHTML = loadingState();
    topPagesTarget.innerHTML = loadingState();
    referrersTarget.innerHTML = loadingState();
    container.querySelector("#statsNotice").classList.add("is-hidden");
    container.querySelector("#trendDescription").textContent = `最近 ${days} 天的 PV 与 UV 变化`;

    try {
      const payload = await api.stats(days, signal);
      if (signal.aborted || requestId !== requestSequence) return;
      const daily = Array.isArray(payload.daily) ? payload.daily : [];
      renderMetrics(metricsTarget, payload.totals || {}, daily, days);
      trendTarget.innerHTML = renderTrendChart(daily);
      renderTopPages(topPagesTarget, payload.topPages || []);
      renderReferrers(referrersTarget, payload.referrers || []);
      renderSourceNotice(container.querySelector("#statsNotice"), payload.source);
    } catch (error) {
      if (error.name === "AbortError" || requestId !== requestSequence) return;
      metricsTarget.innerHTML = `<div class="panel span-full">${errorState(error.message)}</div>`;
      trendTarget.innerHTML = errorState(error.message);
      topPagesTarget.innerHTML = errorState(error.message);
      referrersTarget.innerHTML = errorState(error.message);
      container.querySelectorAll("[data-retry]").forEach((button) => {
        button.addEventListener("click", load, { once: true });
      });
    }
  }

  daysSelect.addEventListener("change", load);
  await load();
}

function renderMetrics(target, totals, daily, days) {
  const pv = Number(totals.periodPv ?? totals.pv) || 0;
  const uv = Number(totals.periodUv ?? totals.uv) || 0;
  const dataDays = Math.max(1, daily.length || days);
  const metrics = [
    { label: "总浏览量 PV", value: pv, note: `最近 ${days} 天`, icon: "analytics", style: "" },
    { label: "独立访客 UV", value: uv, note: `最近 ${days} 天`, icon: "dashboard", style: "success" },
    { label: "日均浏览量", value: Math.round(pv / dataDays), note: "按有数据日期平均", icon: "articles", style: "" },
    { label: "日均访客数", value: Math.round(uv / dataDays), note: "按有数据日期平均", icon: "categories", style: "warning" },
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

function renderSourceNotice(target, source) {
  if (!source) {
    target.textContent = "";
    target.classList.add("is-hidden");
    return;
  }
  if (source.status !== "ok") {
    target.textContent = source.message || "访问数据源暂时不可用。";
  } else if (source.truncated) {
    target.textContent = "访问日志数据量较大，当前统计仅包含可读取的最近记录。";
  } else if (source.rangeStart && source.rangeEnd) {
    target.textContent = `累计数基于当前可用访问日志（${source.rangeStart} 至 ${source.rangeEnd}），日志轮转会影响可用范围。`;
  } else {
    target.textContent = "统计基于当前可用访问日志，暂时还没有可聚合的页面访问。";
  }
  target.classList.remove("is-hidden");
}

function renderTopPages(target, pages) {
  if (!Array.isArray(pages) || !pages.length) {
    target.innerHTML = emptyState("暂无热门页面", "所选时间范围内还没有页面访问数据。");
    return;
  }
  target.innerHTML = `
    <div class="rank-list">
      ${pages.slice(0, 10).map((page) => `
        <div class="rank-item">
          <div>
            <strong title="${escapeHtml(page.path || "/")}">${escapeHtml(page.path || "/")}</strong>
            <small>${formatNumber(page.uv)} 位访客</small>
          </div>
          <span class="rank-value"><strong>${formatNumber(page.pv)}</strong><small>PV</small></span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderReferrers(target, referrers) {
  if (!Array.isArray(referrers) || !referrers.length) {
    target.innerHTML = emptyState("暂无来源数据", "直接访问或当前时间范围内没有可识别的外部来源。");
    return;
  }
  target.innerHTML = `
    <div class="rank-list">
      ${referrers.slice(0, 10).map((referrer) => `
        <div class="rank-item">
          <div>
            <strong title="${escapeHtml(referrer.host || "直接访问")}">${escapeHtml(referrer.host || "直接访问")}</strong>
            <small>来源域名</small>
          </div>
          <span class="rank-value"><strong>${formatNumber(referrer.pv)}</strong><small>PV</small></span>
        </div>
      `).join("")}
    </div>
  `;
}
