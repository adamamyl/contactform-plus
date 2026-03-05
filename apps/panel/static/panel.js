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

  document.addEventListener("DOMContentLoaded", function () {
    initAssigneeForm();
    initTagsForm();
    initStatusForms();
  });
})();
