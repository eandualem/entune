// The history page: settings, recording, history with retry and copy.
// Plain DOM code against the local API; no framework, no build step.

const el = (id) => document.getElementById(id);
const recordBtn = el("record");
const modelSelect = el("model");
const status = el("status");
const history = el("history");
const settingsForm = el("settings-form");
const keysDiv = el("keys");
const defaultSelect = el("default-model");
const settingsStatus = el("settings-status");
const shortcutHold = el("shortcut-hold");
const shortcutToggle = el("shortcut-toggle");
const shortcutStatus = el("shortcut-status");

// Recording a shortcut: the menu-bar app's global listener captures the keys
// (it is the only thing that can see fn), the page polls for the result and
// saves it. Without the app, the page captures what the browser lets it see.
async function saveShortcuts() {
  const body = { keys: {}, shortcuts: { hold: shortcutHold.value.trim(), toggle: shortcutToggle.value.trim() } };
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    shortcutStatus.textContent = "Saved.";
  } catch (err) {
    shortcutStatus.textContent = String(err.message ?? err);
  }
}

async function captureShortcut(input, button) {
  const previous = input.value;
  input.value = "";
  input.placeholder = "Press keys…";
  button.disabled = true;
  shortcutStatus.textContent = "";
  try {
    const res = await fetch("/api/capture", { method: "POST" });
    if (res.status === 409) {
      input.value = await captureInPage(input);
    } else if (!res.ok) {
      throw new Error(await res.text());
    } else {
      const deadline = Date.now() + 15000;
      let keys = null;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 120));
        const status = await api("/api/capture");
        if (status.state === "done") { keys = status.keys; break; }
        if (status.state === "idle") break;
      }
      if (keys === null) {
        await fetch("/api/capture", { method: "DELETE" });
        throw new Error("Nothing pressed.");
      }
      input.value = keys;
    }
    await saveShortcuts();
  } catch (err) {
    input.value = previous;
    shortcutStatus.textContent = String(err.message ?? err);
  } finally {
    input.placeholder = "not set";
    button.disabled = false;
  }
}

// Browser-only capture (no menu-bar app): keys the page can see, in press order.
function captureInPage(input) {
  return new Promise((resolve, reject) => {
    const names = { Meta: "cmd", Control: "ctrl", Alt: "alt", Shift: "shift", " ": "space", Enter: "enter", Escape: "esc", Tab: "tab", Backspace: "backspace", ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
    const keys = [];
    const down = new Set();
    const onDown = (e) => {
      e.preventDefault();
      const name = names[e.key] ?? (e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase());
      if (!keys.includes(name)) keys.push(name);
      down.add(name);
    };
    const onUp = (e) => {
      const name = names[e.key] ?? e.key.toLowerCase();
      down.delete(name);
      if (keys.length && down.size === 0) { cleanup(); resolve(keys.join("+")); }
    };
    const cleanup = () => { window.removeEventListener("keydown", onDown, true); window.removeEventListener("keyup", onUp, true); };
    window.addEventListener("keydown", onDown, true);
    window.addEventListener("keyup", onUp, true);
    setTimeout(() => { if (down.size === 0 && keys.length === 0) { cleanup(); reject(new Error("Nothing pressed.")); } }, 15000);
    input.focus();
  });
}

for (const button of settingsForm.querySelectorAll("button.capture")) {
  button.addEventListener("click", () => captureShortcut(el(button.dataset.target), button));
}
for (const button of settingsForm.querySelectorAll("button.clear")) {
  button.addEventListener("click", async () => { el(button.dataset.target).value = ""; await saveShortcuts(); });
}
const settingsPanel = el("settings");
const themeSelect = el("theme");

// The settings panel is a <details> for its toggle button; the form lives outside it so it can be a card.
settingsPanel.addEventListener("toggle", () => (settingsForm.hidden = !settingsPanel.open));

// Appearance is a per-page preference, kept in this browser (or window) only.
function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  try {
    if (theme === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", theme);
  } catch (e) {}
}
try {
  themeSelect.value = localStorage.getItem("theme") || "system";
} catch (e) {}
themeSelect.addEventListener("change", () => applyTheme(themeSelect.value));

let models = [];

async function api(path, init) {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fillModels(select, selected, emptyLabel) {
  select.replaceChildren();
  if (models.length === 0) {
    select.append(new Option(emptyLabel, "", true, true));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const m of models) select.append(new Option(m.label, m.id, false, m.id === selected));
}

async function loadModels() {
  models = await api("/api/models");
  const def = models.find((m) => m.default)?.id ?? null;
  fillModels(modelSelect, def, "No models: add an API key in Settings");
  fillModels(defaultSelect, def, "Add an API key first");
  if (def === null && models.length > 0) {
    defaultSelect.prepend(new Option("Not set", "", true, true));
    modelSelect.prepend(new Option("Default (not set)", "", true, true));
  }
}

async function loadSettings() {
  const s = await api("/api/settings");
  keysDiv.replaceChildren(
    ...s.providers.map((p) => {
      const label = document.createElement("label");
      label.textContent = `${p.name} API key `;
      const input = document.createElement("input");
      input.type = "password";
      input.name = `key:${p.id}`;
      input.autocomplete = "off";
      input.placeholder = p.keyHint ? `saved (${p.keyHint}); type to replace` : "not set";
      label.append(input);
      return label;
    }),
  );
  shortcutHold.value = s.shortcuts.hold ?? "";
  shortcutToggle.value = s.shortcuts.toggle ?? "";
  await loadModels();
}

settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const keys = {};
  for (const input of settingsForm.querySelectorAll("input[type=password]")) {
    if (input.value.trim()) keys[input.name.slice("key:".length)] = input.value.trim();
  }
  const body = { keys };
  if (!defaultSelect.disabled) body.defaultModel = defaultSelect.value || null;
  body.shortcuts = { hold: shortcutHold.value.trim(), toggle: shortcutToggle.value.trim() };
  settingsStatus.textContent = "Saving…";
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    settingsStatus.textContent = "Saved.";
    await loadSettings();
  } catch (err) {
    settingsStatus.textContent = String(err.message ?? err);
  }
});

// Recording: click to start, click again to stop, then upload and transcribe.
let recorder = null;

recordBtn.addEventListener("click", async () => {
  if (recorder) {
    recorder.stop();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    status.textContent = `Microphone unavailable: ${err.message ?? err}`;
    return;
  }
  const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
  const chunks = [];
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  recorder.addEventListener("dataavailable", (e) => chunks.push(e.data));
  recorder.addEventListener("stop", () => {
    const type = recorder.mimeType;
    stream.getTracks().forEach((t) => t.stop());
    recorder = null;
    recordBtn.textContent = "Record";
    recordBtn.classList.remove("recording");
    upload(new Blob(chunks, { type }));
  });
  recorder.start();
  recordBtn.textContent = "Stop";
  recordBtn.classList.add("recording");
  status.textContent = "Recording…";
});

async function upload(audio) {
  const form = new FormData();
  form.append("audio", audio, "clip");
  if (modelSelect.value) form.append("model", modelSelect.value);
  const label = modelSelect.selectedOptions[0]?.textContent ?? "default model";
  status.textContent = `Transcribing with ${label}…`;
  try {
    await api("/api/recordings", { method: "POST", body: form });
    status.textContent = "";
  } catch (err) {
    status.textContent = err.message ?? String(err);
  }
  await loadHistory(true);
}

async function transcribeAgain(recording, modelId, card) {
  card.querySelector(".retry-status").textContent = "Transcribing…";
  try {
    await api(`/api/recordings/${recording.id}/transcriptions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: modelId }),
    });
  } catch (err) {
    card.querySelector(".retry-status").textContent = err.message ?? String(err);
  }
  await loadHistory(true);
}

function attemptLabel(t) {
  return `${t.provider} / ${t.model}`;
}

const COPY_ICON = `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 5.5V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>`;
const DOWNLOAD_ICON = `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 2.5v8m0 0L4.8 7.3M8 10.5l3.2-3.2M3 13.5h10" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

async function copyText(text, feedback) {
  try {
    await navigator.clipboard.writeText(text);
    feedback("Copied");
  } catch (err) {
    feedback(`Copy failed: ${err.message ?? err}`);
  }
}

function renderAttempt(t) {
  const box = document.createElement("div");
  box.className = `attempt ${t.status}`;
  if (t.status === "ok") {
    // The transcript is a copyable block: click it, or the icon in its corner.
    const block = document.createElement("div");
    block.className = t.text ? "transcript" : "transcript empty";
    block.title = t.text ? "Click to copy" : "";
    const p = document.createElement("p");
    p.className = "text";
    p.textContent = t.text || "(no speech detected)";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "icon copy";
    copy.title = "Copy transcript";
    copy.setAttribute("aria-label", "Copy transcript");
    copy.innerHTML = COPY_ICON;
    const note = document.createElement("span");
    note.className = "copy-note";
    const feedback = (message) => {
      note.textContent = message;
      setTimeout(() => (note.textContent = ""), 1800);
    };
    if (t.text) {
      copy.addEventListener("click", (e) => { e.stopPropagation(); copyText(t.text, feedback); });
      block.addEventListener("click", () => copyText(t.text, feedback));
    } else {
      copy.disabled = true;
    }
    block.append(p, copy, note);
    box.append(block);
  } else {
    const pre = document.createElement("pre");
    pre.className = "error";
    const title = document.createElement("b");
    title.textContent = `${attemptLabel(t)} failed`;
    pre.append(title, t.error ?? "");
    box.append(pre);
  }
  return box;
}

function renderRecording(r) {
  const card = document.createElement("article");
  card.className = "recording card";
  const head = document.createElement("div");
  head.className = "row head";
  const time = document.createElement("time");
  time.dateTime = r.created_at;
  time.textContent = new Date(r.created_at).toLocaleString();
  const media = document.createElement("div");
  media.className = "row media";
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.preload = "none";
  audio.src = `/api/recordings/${r.id}/audio`;
  const download = document.createElement("a");
  download.className = "icon";
  download.href = `/api/recordings/${r.id}/audio`;
  download.download = "";
  download.title = "Download audio";
  download.setAttribute("aria-label", "Download audio");
  download.innerHTML = DOWNLOAD_ICON;
  media.append(audio, download);
  head.append(time, media);
  card.append(head);

  const [latest, ...earlier] = r.transcriptions;
  if (latest) {
    const meta = document.createElement("small");
    meta.textContent = attemptLabel(latest);
    card.append(meta, renderAttempt(latest));
  }

  // Any recording can be transcribed again with another model, not only a failed one.
  const row = document.createElement("div");
  row.className = "row retry";
  const select = document.createElement("select");
  const current = latest ? `${latest.provider}/${latest.model}` : null;
  const other = models.find((m) => m.id !== current)?.id ?? models[0]?.id ?? null;
  fillModels(select, other, "No models: add an API key in Settings");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = latest?.status === "error" ? "Retry with this model" : "Transcribe with this model";
  btn.disabled = select.disabled;
  btn.addEventListener("click", () => transcribeAgain(r, select.value, card));
  const rs = document.createElement("span");
  rs.className = "status retry-status";
  row.append(select, btn, rs);
  card.append(row);

  if (earlier.length > 0) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${earlier.length} earlier attempt${earlier.length === 1 ? "" : "s"}`;
    details.append(summary);
    for (const t of earlier) {
      const meta = document.createElement("small");
      meta.textContent = attemptLabel(t);
      details.append(meta, renderAttempt(t));
    }
    card.append(details);
  }
  return card;
}

// Dictations made with the shortcut arrive while this page is open, so it keeps
// itself current: it re-reads the history every few seconds while visible and
// re-renders only when something changed.
let historySnapshot = "";

async function loadHistory(force = false) {
  const recordings = await api("/api/recordings");
  const snapshot = JSON.stringify(recordings);
  if (!force && snapshot === historySnapshot) return;
  historySnapshot = snapshot;
  history.replaceChildren(...recordings.map(renderRecording));
  if (recordings.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Nothing recorded yet.";
    history.append(p);
  }
}

const HISTORY_POLL_MS = 3000;
setInterval(() => {
  if (document.visibilityState === "visible") loadHistory().catch(() => {});
}, HISTORY_POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadHistory().catch(() => {});
});

await loadSettings();
if (models.length === 0 || location.hash === "#settings") settingsPanel.open = true;
await loadHistory(true);
