(() => {
  const container = document.querySelector("[data-site-stats]");
  if (!container) return;

  fetch("/analytics/summary", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  })
    .then((response) => {
      if (!response.ok) throw new Error("Site statistics are unavailable");
      return response.json();
    })
    .then(({ pv, uv }) => {
      const formatter = new Intl.NumberFormat("zh-CN");
      container.querySelector("[data-site-pv]").textContent = formatter.format(pv || 0);
      container.querySelector("[data-site-uv]").textContent = formatter.format(uv || 0);
      container.hidden = false;
    })
    .catch(() => {
      // Statistics are optional content; keep the footer quiet if the API is down.
    });
})();
