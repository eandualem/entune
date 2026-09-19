import { api, el, errorText } from "./ui.js";

// macOS owns the grants. Recheck while General is visible; never store a guessed
// "setup complete" flag that becomes stale after a permission is revoked.
export function createPermissions() {
  const panel = el("permissions");
  const status = el("permissions-status");
  const buttons = [...panel.querySelectorAll("button[data-permission]")];
  let states = {};
  let active = false;
  let polling = null;
  let loading = false;
  let lastStatus = "";

  async function refresh() {
    if (!active || document.hidden || loading) return;
    loading = true;
    try {
      const result = await api("/api/status");
      panel.hidden = !result.desktop;
      if (!result.desktop) { clearInterval(polling); polling = null; return; }
      const nextStatus = JSON.stringify(result.permissions);
      if (nextStatus === lastStatus) return;
      lastStatus = nextStatus;
      states = result.permissions ?? {};
      let allowed = 0;
      for (const button of buttons) {
        const name = button.dataset.permission;
        const state = states[name];
        const granted = state === "granted";
        const label = panel.querySelector(`[data-permission-status="${name}"]`);
        label.textContent = granted ? "Allowed" : state === "restricted" ? "Restricted by this Mac" : state === "denied" ? "Not allowed" : "Not yet allowed";
        button.hidden = granted;
        button.textContent = ["requested", "denied", "restricted"].includes(state) ? "Open Settings…" : "Allow…";
        if (granted) allowed++;
      }
      const ready = allowed === buttons.length;
      el("permissions-summary").textContent = ready ? "Permissions ready" : "Set up Dictum on this Mac";
      el("permissions-help").textContent = ready
        ? "Dictum has access to the microphone, your shortcuts and pasting."
        : "Allow these three permissions so you can dictate in any app. macOS asks for each one separately.";
      status.textContent = ready
        ? "Choose your shortcuts below, then add a provider key or download a local model."
        : `${allowed} of 3 allowed. Enable Dictum in System Settings when asked. If macOS asks you to quit, reopen Dictum to continue here.`;
    } catch (err) {
      lastStatus = "";
      panel.hidden = false;
      status.textContent = `Could not check permissions: ${errorText(err)}`;
    } finally { loading = false; }
  }

  for (const button of buttons) {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const name = button.dataset.permission;
        await api(`/api/permissions/${name}`, {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ openSettings: ["requested", "denied", "restricted"].includes(states[name]) }),
        });
        await refresh();
      } catch (err) { status.textContent = errorText(err); }
      finally { button.disabled = false; }
    });
  }

  function setActive(value) {
    active = value;
    clearInterval(polling);
    polling = null;
    if (active && !document.hidden) {
      polling = setInterval(refresh, 2000);
      refresh();
    }
  }
  document.addEventListener("visibilitychange", () => setActive(active));
  window.addEventListener("focus", refresh);
  return { setActive };
}
