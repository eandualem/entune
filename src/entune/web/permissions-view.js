import { api, el, errorText } from "./ui.js";

// macOS owns the grants. Recheck while General or Get started is visible; never store
// a guessed "setup complete" flag that becomes stale after a permission is revoked.
// `onChange` hears every new answer: {desktop, permissions}.
export function createPermissions({ onChange } = {}) {
  const panel = el("permissions");
  const status = el("permissions-status");
  const buttons = [...panel.querySelectorAll("button[data-permission]")];
  let states = {};
  const showing = new Set(); // the views that need fresh answers
  let polling = null;
  let loading = false;
  let lastStatus = "";

  async function refresh() {
    if (showing.size === 0 || document.hidden || loading) return;
    loading = true;
    try {
      const result = await api("/api/status");
      panel.hidden = !result.desktop;
      if (!result.desktop) {
        clearInterval(polling); polling = null;
        if (lastStatus !== "browser") { lastStatus = "browser"; onChange?.({ desktop: false, system: result.system, permissions: {} }); }
        return;
      }
      const nextStatus = JSON.stringify(result.permissions);
      if (nextStatus === lastStatus) return;
      lastStatus = nextStatus;
      states = result.permissions ?? {};
      const mac = result.system === "macos";
      const asked = buttons.filter((button) => button.dataset.permission in states);
      let allowed = 0;
      for (const button of buttons) {
        const name = button.dataset.permission;
        button.closest(".srow").hidden = !(name in states); // Windows asks only for the microphone
        const state = states[name];
        const granted = state === "granted";
        const label = panel.querySelector(`[data-permission-status="${name}"]`);
        label.textContent = granted ? "Allowed" : state === "restricted" ? "Restricted on this computer" : state === "denied" ? "Not allowed" : "Not yet allowed";
        button.hidden = granted;
        button.textContent = ["requested", "denied", "restricted"].includes(state) || !mac ? "Open Settings…" : "Allow…";
        if (granted && name in states) allowed++;
      }
      const ready = allowed === asked.length;
      el("permissions-summary").textContent = ready ? "Permissions ready" : mac ? "Set up Entune on this Mac" : "Allow the microphone";
      el("permissions-help").textContent = ready
        ? mac ? "Entune has access to the microphone, your shortcuts and pasting." : "Entune can use the microphone."
        : mac
          ? "Allow these three permissions so you can dictate in any app. macOS asks for each one separately."
          : "Windows privacy settings have the microphone turned off for apps like Entune.";
      status.textContent = ready
        ? "Choose your shortcuts below."
        : mac
          ? `${allowed} of ${asked.length} allowed. Enable Entune in System Settings when asked. If macOS asks you to quit, reopen Entune to continue here.`
          : "Turn on microphone access for desktop apps in Settings, then come back here.";
      onChange?.({ desktop: true, system: result.system, permissions: states });
    } catch (err) {
      lastStatus = "";
      panel.hidden = false;
      status.textContent = `Could not check permissions: ${errorText(err)}`;
    } finally { loading = false; }
  }

  // Ask macOS, or open its settings once it has asked; either way, show the result.
  async function request(name) {
    await api(`/api/permissions/${name}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ openSettings: ["requested", "denied", "restricted"].includes(states[name]) }),
    });
    await refresh();
  }

  for (const button of buttons) {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await request(button.dataset.permission); }
      catch (err) { status.textContent = errorText(err); }
      finally { button.disabled = false; }
    });
  }

  function poll() {
    clearInterval(polling);
    polling = null;
    if (showing.size > 0 && !document.hidden) {
      polling = setInterval(refresh, 2000);
      refresh();
    }
  }
  // `view` is "settings" or "start"; polling runs while either is visible.
  function setActive(value, view = "settings") {
    if (showing.has(view) === value) return;
    if (value) showing.add(view); else showing.delete(view);
    poll();
  }
  document.addEventListener("visibilitychange", poll);
  window.addEventListener("focus", refresh);
  return { setActive, request };
}
