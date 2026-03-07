(function () {
  "use strict";

  function jsonPatch(url, body) {
    return fetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function flashBtn(btn, message, ok) {
    var orig = btn.textContent;
    btn.textContent = message;
    btn.disabled = true;
    setTimeout(function () {
      if (ok) {
        window.location.reload();
      } else {
        btn.textContent = orig;
        btn.disabled = false;
      }
    }, 600);
  }

  function initPatchForm(formId, endpoint, getBody, successMsg) {
    var form = document.getElementById(formId);
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = form.querySelector("button[type=submit]");
      jsonPatch("/api/cases/" + form.dataset.caseId + "/" + endpoint, getBody(form))
        .then(function (r) { flashBtn(btn, r.ok ? successMsg : "Error", r.ok); })
        .catch(function () { flashBtn(btn, "Error", false); });
    });
  }

  // Delegated handler for status transition forms (rendered per-case).
  // Hoisted to module level so it is registered exactly once regardless of
  // how many times initCaseForms() might be called.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches(".status-form")) return;
    e.preventDefault();
    var btn = form.querySelector("button[type=submit]");
    jsonPatch(
      "/api/cases/" + form.dataset.caseId + "/status",
      { status: form.querySelector("input[name=status]").value }
    )
      .then(function (r) { flashBtn(btn, r.ok ? "Done" : "Error", r.ok); })
      .catch(function () { flashBtn(btn, "Error", false); });
  });

  function initCaseForms() {
    initPatchForm("assignee-form", "assignee", function (f) {
      return { assignee: (f.querySelector("#assignee").value || "").trim() || null };
    }, "Saved");

    initPatchForm("tags-form", "tags", function (f) {
      var raw = f.querySelector("#tags").value || "";
      return { tags: raw.split(",").map(function (t) { return t.trim(); }).filter(Boolean) };
    }, "Saved");
  }

  function initDispatcherShare() {
    var genBtn = document.getElementById("generate-session-btn");
    var copyBtn = document.getElementById("copy-url-btn");
    var errEl = document.getElementById("gen-error");
    var resultEl = document.getElementById("result");
    var urlInput = document.getElementById("session-url");
    var expiryNote = document.getElementById("expiry-note");
    if (!genBtn) return;

    genBtn.addEventListener("click", function () {
      if (errEl) errEl.hidden = true;
      var sendTo = (document.getElementById("send_to") || {}).value || null;
      fetch("/api/dispatcher-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ send_to: sendTo || null }),
      })
        .then(function (res) {
          if (!res.ok) {
            throw new Error("HTTP " + res.status);
          }
          return res.json();
        })
        .then(function (data) {
          if (urlInput) urlInput.value = data.url;
          if (expiryNote) expiryNote.textContent = "Expires in " + data.expires_in_hours + " hours.";
          if (resultEl) resultEl.hidden = false;
        })
        .catch(function (e) {
          if (errEl) {
            errEl.textContent = "Failed to generate session: " + e.message;
            errEl.hidden = false;
          }
        });
    });

    if (copyBtn && urlInput) {
      copyBtn.addEventListener("click", function () {
        urlInput.select();
        navigator.clipboard.writeText(urlInput.value);
      });
    }
  }

  function initDispatcher() {
    var main = document.getElementById("dispatcher-main");
    if (!main) return;
    var token = main.dataset.token || "";

    main.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-action]");
      if (!btn || btn.disabled) return;
      var caseId = btn.dataset.caseId;
      var action = btn.dataset.action;
      btn.disabled = true;
      if (action === "ack") {
        fetch("/api/dispatcher/ack/" + caseId + "?token=" + token, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: '{"acked_by":"dispatcher"}',
        }).then(function () { btn.textContent = "✅"; });
      } else if (action === "trigger") {
        fetch("/api/dispatcher/trigger/" + caseId + "?token=" + token, {
          method: "POST",
        }).then(function () { btn.textContent = "📞 Sent"; });
      } else {
        btn.disabled = false;
      }
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action=admin-ack],[data-action=admin-trigger]");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    var caseId = btn.dataset.caseId;
    var action = btn.dataset.action;
    var url = action === "admin-ack"
      ? "/api/cases/" + caseId + "/ack"
      : "/api/cases/" + caseId + "/trigger-call";
    fetch(url, { method: "POST" })
      .then(function (r) {
        btn.textContent = r.ok ? (action === "admin-ack" ? "\u2705 ACKed" : "\u{1F4DE} Sent") : "Error";
      })
      .catch(function () { btn.textContent = "Error"; });
  });

  function relativeTime(ms) {
    var diff = (Date.now() - ms) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.round(diff / 60) + " min ago";
    if (diff < 86400) return Math.round(diff / 3600) + " hr ago";
    return Math.round(diff / 86400) + " days ago";
  }

  function initRelativeTimes() {
    document.querySelectorAll("time.relative-time").forEach(function (el) {
      var dt = new Date(el.getAttribute("datetime"));
      if (isNaN(dt.getTime())) return;
      el.textContent = el.textContent.trim() + " (" + relativeTime(dt.getTime()) + ")";
    });
  }

  function initAssigneeList() {
    var dl = document.getElementById("assignee-options");
    if (!dl) return;
    fetch("/api/assignees")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (names) {
        names.forEach(function (name) {
          var opt = document.createElement("option");
          opt.value = name;
          dl.appendChild(opt);
        });
      })
      .catch(function () {});
  }

  function initTheme() {
    var stored = localStorage.getItem("emf-theme");
    if (stored) {
      document.documentElement.setAttribute("data-theme", stored);
    }
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("emf-theme", next);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initRelativeTimes();
    initAssigneeList();
    initCaseForms();
    initDispatcherShare();
    initDispatcher();
  });
})();
