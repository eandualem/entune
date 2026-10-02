import { api, el, errorText } from "./ui.js";

// Linux has no permission prompts: one command, run once, gives Entune the keyboard
// (the input group) and typing (a udev rule for /dev/uinput); the group applies at the
// next login. desktop/linux/permissions.py checks the result.
export const LINUX_SETUP = `echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/70-entune-uinput.rules && sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput && sudo usermod -aG input "$USER"`;
export const LINUX_LABELS = { inputMonitoring: "Keyboard shortcuts", accessibility: "Typing into apps" };

// Copies the command; the button says whether it worked.
export async function copySetup(button) {
  const label = button.textContent;
  try { await navigator.clipboard.writeText(LINUX_SETUP); button.textContent = "Copied"; }
  catch { button.textContent = "Select and copy it"; }
  setTimeout(() => { button.textContent = label; }, 2000);
}

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
  let system = null;

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
      system = result.system;
      const nextStatus = JSON.stringify(result.permissions);
      if (nextStatus === lastStatus) return;
      lastStatus = nextStatus;
      states = result.permissions ?? {};
      const mac = result.system === "macos";
      const linux = result.system === "linux";
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
        button.textContent = linux ? "Copy command" : ["requested", "denied", "restricted"].includes(state) || !mac ? "Open Settings…" : "Allow…";
        if (linux) {
          const row = button.closest(".srow");
          row.querySelector(".name").textContent = LINUX_LABELS[name];
          if (name === "accessibility") row.querySelector(".caption").textContent = "Type the transcript into the focused app.";
        }
        if (granted && name in states) allowed++;
      }
      const ready = allowed === asked.length;
      el("permissions-summary").textContent = ready ? "Permissions ready" : mac ? "Set up Entune on this Mac" : linux ? "Allow keyboard access" : "Allow the microphone";
      el("permissions-help").textContent = ready
        ? mac ? "Entune has access to the microphone, your shortcuts and pasting." : linux ? "Entune can see your shortcut and type the text." : "Entune can use the microphone."
        : mac
          ? "Allow these three permissions so you can dictate in any app. macOS asks for each one separately."
          : linux
            ? "Run this command once in a terminal, then log out and back in. It lets Entune see your shortcut in any app and type the text there."
            : "Windows privacy settings have the microphone turned off for apps like Entune.";
      const command = el("permissions-command");
      command.hidden = ready || !linux;
      command.textContent = LINUX_SETUP;
      status.textContent = ready
        ? "Choose your shortcuts below."
        : mac
          ? `${allowed} of ${asked.length} allowed. Enable Entune in System Settings when asked. If macOS asks you to quit, reopen Entune to continue here.`
          : linux
            ? "Linux applies it at your next login; Entune then shows both as allowed."
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
      if (system === "linux") { await copySetup(button); return; }
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
