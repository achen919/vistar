(function () {
  "use strict";

  document.documentElement.className = document.documentElement.className
    .replace(/\bno-js\b/g, "")
    .replace(/\s+/g, " ")
    .replace(/^\s+|\s+$/g, "");

  var completed = false;
  var timeoutId = window.setTimeout(function () {
    showFailure();
  }, 20000);

  function showFailure() {
    if (completed) return;
    completed = true;
    window.clearTimeout(timeoutId);

    var app = document.getElementById("app");
    if (!app) return;
    app.innerHTML = [
      '<main class="boot-screen boot-failure" role="alert">',
      '  <div class="brand-mark" aria-hidden="true">辰</div>',
      "  <h1>管理后台未能启动</h1>",
      "  <p>资源可能仍在更新或被浏览器缓存，请重新加载页面。</p>",
      '  <button type="button" class="button button-primary" data-boot-reload>重新加载</button>',
      "</main>",
    ].join("");

    var reloadButton = app.querySelector("[data-boot-reload]");
    if (reloadButton) {
      reloadButton.addEventListener("click", function () {
        window.location.reload();
      });
    }
  }

  function handleScriptError(event) {
    var target = event && event.target;
    if (target && target.tagName === "SCRIPT") showFailure();
  }

  window.addEventListener("error", handleScriptError, true);
  window.__blogAdminBoot = {
    complete: function () {
      if (completed) return;
      completed = true;
      window.clearTimeout(timeoutId);
      window.removeEventListener("error", handleScriptError, true);
    },
    fail: showFailure,
  };
}());
