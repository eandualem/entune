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
const shortcutKeys = el("shortcut-keys");
const shortcutMode = () => settingsForm.querySelector("input[name=shortcut-mode]:checked").value;
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
  if (s.shortcut) {
    settingsForm.querySelector(`input[name=shortcut-mode][value=${s.shortcut.mode}]`).checked = true;
    shortcutKeys.value = s.shortcut.keys;
  }
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
  if (shortcutKeys.value.trim()) body.shortcut = { mode: shortcutMode(), keys: shortcutKeys.value.trim() };
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
  await loadHistory();
}

async function retry(recording, modelId, card) {
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
  await loadHistory();
}

function attemptLabel(t) {
  return `${t.provider} / ${t.model}`;
}

function renderAttempt(t) {
  const box = document.createElement("div");
  box.className = `attempt ${t.status}`;
  if (t.status === "ok") {
    const p = document.createElement("p");
    p.className = t.text ? "text" : "text empty";
    p.textContent = t.text || "(no speech detected)";
    const actions = document.createElement("div");
    actions.className = "row actions";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "Copy";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(t.text ?? "");
        copy.textContent = "Copied";
      } catch (err) {
        copy.textContent = `Copy failed: ${err.message ?? err}`;
      }
      setTimeout(() => (copy.textContent = "Copy"), 2500);
    });
    actions.append(copy);
    box.append(p, actions);
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
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.preload = "none";
  audio.src = `/api/recordings/${r.id}/audio`;
  head.append(time, audio);
  card.append(head);

  const [latest, ...earlier] = r.transcriptions;
  if (latest) {
    const meta = document.createElement("small");
    meta.textContent = attemptLabel(latest);
    card.append(meta, renderAttempt(latest));
    if (latest.status === "error") {
      const row = document.createElement("div");
      row.className = "row retry";
      const select = document.createElement("select");
      const other = models.find((m) => m.id !== `${latest.provider}/${latest.model}`)?.id ?? models[0]?.id ?? null;
      fillModels(select, other, "No models: add an API key in Settings");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Retry with a different model";
      btn.disabled = select.disabled;
      btn.addEventListener("click", () => retry(r, select.value, card));
      const rs = document.createElement("span");
      rs.className = "status retry-status";
      row.append(select, btn, rs);
      card.append(row);
    }
  }
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

async function loadHistory() {
  const recordings = await api("/api/recordings");
  history.replaceChildren(...recordings.map(renderRecording));
  if (recordings.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Nothing recorded yet.";
    history.append(p);
  }
}

await loadSettings();
if (models.length === 0 || location.hash === "#settings") settingsPanel.open = true;
await loadHistory();
