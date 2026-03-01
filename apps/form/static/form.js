(function () {
  "use strict";

  const MAX_WHAT_HAPPENED = 10000;
  const MIN_WHAT_HAPPENED = 10;
  const PHONE_RE = /^[\d\s+\-.()\sA-Z]+$/i;
  const STORAGE_KEY = "emf_conduct_form_state";

  function getById(id) {
    return document.getElementById(id);
  }

  function setError(fieldId, message) {
    const el = getById(fieldId + "-error");
    const input = getById(fieldId);
    if (el) {
      el.textContent = message;
    }
    if (input) {
      input.classList.toggle("is-invalid", message.length > 0);
      if (message) {
        input.setAttribute("aria-invalid", "true");
      } else {
        input.removeAttribute("aria-invalid");
      }
    }
  }

  function clearError(fieldId) {
    setError(fieldId, "");
  }

  function validatePhone(value) {
    if (!value) return "";
    const upper = value.toUpperCase().trim();
    if (!PHONE_RE.test(upper)) {
      return "Phone number contains invalid characters. Allowed: digits, spaces, +, -, ., (, ), A-Z";
    }
    return "";
  }

  function validateWhatHappened(value) {
    const trimmed = value.trim();
    if (trimmed.length < MIN_WHAT_HAPPENED) {
      return "Please describe what happened (minimum 10 characters, currently " + trimmed.length + ")";
    }
    if (trimmed.length > MAX_WHAT_HAPPENED) {
      return "Description too long (" + trimmed.length + "/" + MAX_WHAT_HAPPENED + " characters)";
    }
    return "";
  }

  function updateCharCount(textarea, countEl) {
    const len = textarea.value.trim().length;
    const remaining = MAX_WHAT_HAPPENED - len;
    countEl.textContent = len + "/" + MAX_WHAT_HAPPENED + " characters";
    countEl.classList.toggle("near-limit", remaining < 500);
  }

  function initCharCount() {
    const textarea = getById("what_happened");
    const countEl = getById("what_happened-count");
    if (!textarea || !countEl) return;
    updateCharCount(textarea, countEl);
    textarea.addEventListener("input", function () {
      updateCharCount(textarea, countEl);
    });
  }

  function initDateTimeDefaults() {
    const dateInput = getById("incident_date");
    const timeInput = getById("incident_time");
    const now = new Date();

    if (dateInput && !dateInput.value) {
      const y = now.getFullYear();
      const m = String(now.getMonth() + 1).padStart(2, "0");
      const d = String(now.getDate()).padStart(2, "0");
      dateInput.value = y + "-" + m + "-" + d;
    }

    if (timeInput && !timeInput.value) {
      const h = String(now.getHours()).padStart(2, "0");
      const min = String(now.getMinutes()).padStart(2, "0");
      timeInput.value = h + ":" + min;
    }
  }

  function getFormState() {
    const form = getById("conduct-form");
    if (!form) return null;
    const data = {};
    const inputs = form.querySelectorAll("input:not([type=hidden]):not([name=website]), textarea, select");
    inputs.forEach(function (el) {
      const input = /** @type {HTMLInputElement} */ (el);
      if (input.type === "radio") {
        if (input.checked) {
          data[input.name] = input.value;
        }
      } else {
        data[input.name] = input.value;
      }
    });
    return data;
  }

  function saveFormState() {
    const state = getFormState();
    if (!state) return;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (_e) {
      // sessionStorage not available
    }
  }

  function restoreFormState() {
    let raw;
    try {
      raw = sessionStorage.getItem(STORAGE_KEY);
    } catch (_e) {
      return;
    }
    if (!raw) return;

    let state;
    try {
      state = JSON.parse(raw);
    } catch (_e) {
      return;
    }

    const form = getById("conduct-form");
    if (!form) return;

    let restored = false;
    Object.keys(state).forEach(function (name) {
      const value = state[name];
      const el = form.querySelector("[name=" + JSON.stringify(name) + "]");
      if (!el) return;
      const input = /** @type {HTMLInputElement} */ (el);
      if (input.type === "radio") {
        const radio = /** @type {HTMLInputElement|null} */ (
          form.querySelector("input[name=" + JSON.stringify(name) + "][value=" + JSON.stringify(value) + "]")
        );
        if (radio) {
          radio.checked = true;
          restored = true;
        }
      } else if (input.tagName !== "SELECT" || value) {
        input.value = value;
        if (value) restored = true;
      }
    });

    if (restored) {
      const statusEl = getById("submit-status");
      if (statusEl) {
        statusEl.textContent = "Your previous answers have been restored.";
      }
    }
  }

  function initStateTracking() {
    const form = getById("conduct-form");
    if (!form) return;
    form.addEventListener("change", saveFormState);
    form.addEventListener("input", saveFormState);
    restoreFormState();
  }

  function buildPayload() {
    const getValue = function (id) {
      const el = getById(id);
      return el ? el.value.trim() : "";
    };

    const getRadioValue = function (name) {
      const el = document.querySelector("input[name=" + JSON.stringify(name) + "]:checked");
      if (!el) return null;
      const input = /** @type {HTMLInputElement} */ (el);
      return input.value || null;
    };

    const canContactRaw = getRadioValue("can_contact");
    let canContact = null;
    if (canContactRaw === "true") canContact = true;
    else if (canContactRaw === "false") canContact = false;

    const latVal = getValue("location_lat");
    const lonVal = getValue("location_lon");
    const locationText = getValue("location_text");

    const location = {};
    if (locationText) location.text = locationText;
    if (latVal) location.lat = parseFloat(latVal);
    if (lonVal) location.lon = parseFloat(lonVal);

    if (!location.text && !location.lat && !location.lon) {
      location.text = null;
      location.lat = null;
      location.lon = null;
    }

    return {
      event_name: getValue("event_name"),
      reporter: {
        name: getValue("reporter_name") || null,
        pronouns: getValue("reporter_pronouns") || null,
        email: getValue("reporter_email") || null,
        phone: getValue("reporter_phone") || null,
        camping_with: getValue("reporter_camping_with") || null,
      },
      what_happened: getValue("what_happened"),
      incident_date: getValue("incident_date"),
      incident_time: getValue("incident_time") + ":00",
      location: location,
      additional_info: getValue("additional_info") || null,
      support_needed: getValue("support_needed") || null,
      urgency: getValue("urgency") || "medium",
      others_involved: getValue("others_involved") || null,
      why_it_happened: getValue("why_it_happened") || null,
      can_contact: canContact,
      anything_else: getValue("anything_else") || null,
      website: getValue("website") || null,
    };
  }

  function validateForm() {
    let valid = true;

    const phone = (getById("reporter_phone") || {}).value || "";
    const phoneErr = validatePhone(phone);
    if (phoneErr) {
      setError("reporter_phone", phoneErr);
      valid = false;
    } else {
      clearError("reporter_phone");
    }

    const whatHappened = (getById("what_happened") || {}).value || "";
    const whatErr = validateWhatHappened(whatHappened);
    if (whatErr) {
      setError("what_happened", whatErr);
      valid = false;
    } else {
      clearError("what_happened");
    }

    return valid;
  }

  function handleSubmit(evt) {
    evt.preventDefault();
    if (!validateForm()) {
      const firstInvalid = document.querySelector(".is-invalid");
      if (firstInvalid) {
        firstInvalid.focus();
      }
      return;
    }

    const submitBtn = getById("submit-btn");
    const submitLabel = getById("submit-label");
    const submitSpinner = getById("submit-spinner");
    const statusEl = getById("submit-status");

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.setAttribute("aria-busy", "true");
    }
    if (submitLabel) submitLabel.textContent = "Submitting...";
    if (submitSpinner) submitSpinner.hidden = false;
    if (statusEl) statusEl.textContent = "Submitting your report, please wait...";

    const payload = buildPayload();

    fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { status: response.status, data: data };
        });
      })
      .then(function (result) {
        const alreadySubmitted = result.status === 200;
        const friendlyId = result.data.friendly_id || "";
        try {
          sessionStorage.removeItem(STORAGE_KEY);
        } catch (_e) {
          // ignore
        }
        window.location.href =
          "/success?friendly_id=" +
          encodeURIComponent(friendlyId) +
          "&already_submitted=" +
          alreadySubmitted;
      })
      .catch(function (err) {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.removeAttribute("aria-busy");
        }
        if (submitLabel) submitLabel.textContent = "Submit report";
        if (submitSpinner) submitSpinner.hidden = true;
        if (statusEl) {
          statusEl.textContent =
            "An error occurred submitting your report. Please try again.";
        }
      });
  }

  function init() {
    initCharCount();
    initDateTimeDefaults();
    initStateTracking();

    const form = getById("conduct-form");
    if (form) {
      form.addEventListener("submit", handleSubmit);
    }

    const phoneInput = getById("reporter_phone");
    if (phoneInput) {
      phoneInput.addEventListener("blur", function () {
        const err = validatePhone(phoneInput.value);
        if (err) {
          setError("reporter_phone", err);
        } else {
          clearError("reporter_phone");
        }
      });
    }

    const whatInput = getById("what_happened");
    if (whatInput) {
      whatInput.addEventListener("blur", function () {
        const err = validateWhatHappened(whatInput.value);
        if (err) {
          setError("what_happened", err);
        } else {
          clearError("what_happened");
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
