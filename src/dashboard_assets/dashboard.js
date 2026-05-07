(function () {
  const root = document.getElementById("dashboard-root");
  const snapshotNode = document.getElementById("dashboard-snapshot-data");
  const lastCheckedNode = document.getElementById("dashboard-last-checked");

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
