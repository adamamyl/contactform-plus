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

  function initAssigneeForm() {
    var form = document.getElementById("assignee-form");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = form.querySelector("button[type=submit]");
      var caseId = form.dataset.caseId;
      var val = (form.querySelector("#assignee").value || "").trim() || null;
      jsonPatch("/api/cases/" + caseId + "/assignee", { assignee: val })
        .then(function (r) { flashBtn(btn, r.ok ? "Saved" : "Error", r.ok); })
        .catch(function () { flashBtn(btn, "Error", false); });
    });
  }

  function initTagsForm() {
    var form = document.getElementById("tags-form");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = form.querySelector("button[type=submit]");
      var caseId = form.dataset.caseId;
      var raw = form.querySelector("#tags").value || "";
      var tags = raw.split(",").map(function (t) { return t.trim(); }).filter(Boolean);
      jsonPatch("/api/cases/" + caseId + "/tags", { tags: tags })
        .then(function (r) { flashBtn(btn, r.ok ? "Saved" : "Error", r.ok); })
        .catch(function () { flashBtn(btn, "Error", false); });
    });
  }

  function initStatusForms() {
    document.querySelectorAll(".status-form").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var btn = form.querySelector("button[type=submit]");
        var caseId = form.dataset.caseId;
        var status = form.querySelector("input[name=status]").value;
        jsonPatch("/api/cases/" + caseId + "/status", { status: status })
          .then(function (r) { flashBtn(btn, r.ok ? "Done" : "Error", r.ok); })
          .catch(function () { flashBtn(btn, "Error", false); });
      });
    });
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

    document.querySelectorAll("[data-action=ack]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.disabled = true;
        var caseId = btn.dataset.caseId;
        fetch("/api/dispatcher/ack/" + caseId + "?token=" + token, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: '{"acked_by":"dispatcher"}',
        }).then(function () { btn.textContent = "✅"; });
      });
    });

    document.querySelectorAll("[data-action=trigger]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.disabled = true;
        var caseId = btn.dataset.caseId;
        fetch("/api/dispatcher/trigger/" + caseId + "?token=" + token, {
          method: "POST",
        }).then(function () { btn.textContent = "📞 Sent"; });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initAssigneeForm();
    initTagsForm();
    initStatusForms();
    initDispatcherShare();
    initDispatcher();
  });
})();
