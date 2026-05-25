(function () {
  const root = document.getElementById("dashboard-root");
  const snapshotNode = document.getElementById("dashboard-snapshot-data");
  const lastCheckedNode = document.getElementById("dashboard-last-checked");

  const setupClarificationControls = () => {
    const forms = document.querySelectorAll(".resume-form");
    forms.forEach((form) => {
      const clarificationField = form.querySelector("[data-clarification-field]");
      const clarificationInput = form.querySelector('textarea[name="clarification_text"]');
      if (!clarificationField || !clarificationInput) {
        return;
      }

      const setClarificationActive = (active) => {
        form.dataset.clarificationActive = active ? "true" : "false";
        if (active) {
          clarificationInput.removeAttribute("disabled");
          return;
        }
        clarificationInput.setAttribute("disabled", "disabled");
      };

      setClarificationActive(form.dataset.clarificationActive === "true");

      form.addEventListener("click", (event) => {
        const button = event.target.closest('button[name="decision_type"]');
        if (!button) {
          return;
        }

        const isClarification = button.value === "ask_clarification";
        if (!isClarification) {
          setClarificationActive(false);
          return;
        }

        if (form.dataset.clarificationActive !== "true") {
          event.preventDefault();
          setClarificationActive(true);
          clarificationInput.focus();
          return;
        }

        if (!clarificationInput.value.trim()) {
          event.preventDefault();
          clarificationInput.focus();
        }
      });
    });
  };

  setupClarificationControls();

  if (!root || !snapshotNode) {
    return;
  }

  const autoRefreshEnabled = root.dataset.autoRefreshEnabled === "true";
  const dashboardMode = root.dataset.dashboardMode || "live";
  if (!autoRefreshEnabled || dashboardMode === "terminal") {
    return;
  }

  const canonicalizeSnapshot = (payload) => {
    try {
      return JSON.stringify(JSON.parse(payload));
    } catch (error) {
      return payload;
    }
  };

  let currentSnapshot = canonicalizeSnapshot(snapshotNode.value || snapshotNode.textContent || "");
  const refreshIntervalMs = 2000;

  const updateLastChecked = () => {
    if (lastCheckedNode) {
      lastCheckedNode.textContent = new Date().toLocaleTimeString();
    }
  };

  const pollSnapshot = async () => {
    try {
      const response = await fetch("/snapshot.json", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }

      const nextSnapshot = await response.text();
      if (canonicalizeSnapshot(nextSnapshot) !== currentSnapshot) {
        window.location.reload();
        return;
      }

      updateLastChecked();
    } catch (error) {
      // Keep polling on transient network issues.
    }
  };

  updateLastChecked();
  pollSnapshot();
  window.setInterval(pollSnapshot, refreshIntervalMs);
})();
