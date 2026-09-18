// Dictum's window: history, recording from here, settings. Plain DOM code against
// the local API; no framework, no build step.

const el = (id) => document.getElementById(id);
const recordBtn = el("record");
const recTimer = el("rec-timer");
const modelSelect = el("model");
const status = el("status");
const historyList = el("history");
const historyLabel = el("history-label");
const emptyState = el("empty");
const emptyHint = el("empty-hint");
const settingsForm = el("settings-form");
const keysGroup = el("keys");
const llmKeysGroup = el("llm-keys");
const dictionaryModelInput = el("dictionary-model");
const fastModeInput = el("fast-mode");
const fastModeRow = el("fast-mode-row");
const metricsSection = el("metrics");
const metricsRows = el("metrics-rows");
let streamingProviders = new Set();
const dictionaryModels = el("dictionary-models");
const dictionaryModelChip = el("dictionary-model-chip");
const defaultSelect = el("default-model");
const settingsStatus = el("settings-status");
const shortcutHold = el("shortcut-hold");
const shortcutToggle = el("shortcut-toggle");
const shortcutStatus = el("shortcut-status");

const ICON = {
  copy: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/></svg>',
  check: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  download: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.5v8m-3.5-3L8 11l3.5-3.5M3 13.5h10"/></svg>',
  chevron: '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 2 6.5 5 3.5 8"/></svg>',
  warn: '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 1.5 11 10H1L6 1.5ZM6 5v2.2M6 8.6v.1"/></svg>',
};

let models = [];
let shortcuts = { hold: null, toggle: null };

async function api(path, init) {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ---- Appearance: a per-window preference, applied before first paint by index.html ----
function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  try {
    if (theme === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", theme);
  } catch (e) {}
}
{
  let saved = "system";
  try { saved = localStorage.getItem("theme") || "system"; } catch (e) {}
  for (const radio of settingsForm.querySelectorAll("input[name=appearance]")) {
    radio.checked = radio.value === saved;
    radio.addEventListener("change", () => applyTheme(radio.value));
  }
}

// ---- Tabs ----
const tabs = { history: el("tab-history"), dictionary: el("tab-dictionary"), settings: el("tab-settings") };
const views = { history: el("view-history"), dictionary: el("view-dictionary"), settings: el("view-settings") };
function show(name) {
  for (const key in tabs) {
    tabs[key].setAttribute("aria-selected", String(key === name));
    if (key === name) views[key].setAttribute("data-active", "");
    else views[key].removeAttribute("data-active");
  }
  document.querySelector(".content").scrollTop = 0;
}
tabs.history.addEventListener("click", () => show("history"));
tabs.dictionary.addEventListener("click", () => show("dictionary"));
tabs.settings.addEventListener("click", () => show("settings"));

// ---- Models ----
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
  fillModels(modelSelect, def, "No models: add an API key");
  fillModels(defaultSelect, def, "Add an API key first");
  if (def === null && models.length > 0) {
    defaultSelect.prepend(new Option("Not set", "", true, true));
    modelSelect.prepend(new Option("Default (not set)", "", true, true));
  }
  showFastModeIfSupported();
}

// Fast mode only means something for a provider that takes the audio while it is recorded.
function showFastModeIfSupported() {
  const provider = defaultSelect.value.split("/")[0];
  fastModeRow.hidden = !streamingProviders.has(provider);
}
defaultSelect.addEventListener("change", showFastModeIfSupported);

async function loadMetrics() {
  const rows = await api("/api/metrics");
  metricsSection.hidden = rows.length === 0;
  const cell = (text) => Object.assign(document.createElement("td"), { textContent: text });
  metricsRows.replaceChildren(
    ...rows.map((m) => {
      const tr = document.createElement("tr");
      tr.append(
        cell(`${m.provider} / ${m.model}`),
        cell(m.fast ? "fast" : "plain"),
        cell(m.ok === m.runs ? String(m.runs) : `${m.ok} of ${m.runs} ok`),
        cell(`${Math.round(m.audio_seconds / 60)} min`),
        cell(m.median_wait === null ? "–" : `${m.median_wait.toFixed(1)} s`),
        cell(m.speed === null ? "–" : `${m.speed.toFixed(0)}× realtime`),
      );
      return tr;
    }),
  );
}

// ---- Settings ----
function keyRow(provider) {
  const row = document.createElement("div");
  row.className = "row";
  const label = document.createElement("label");
  label.htmlFor = `key-${provider.id}`;
  label.textContent = provider.name;
  const field = document.createElement("div");
  field.className = "field";
  const input = document.createElement("input");
  input.className = "input";
  input.id = `key-${provider.id}`;
  input.type = "password";
  input.name = `key:${provider.id}`;
  input.autocomplete = "off";
  input.placeholder = provider.keyHint ? `saved ${provider.keyHint} · type to replace` : "Not set";
  field.append(input);
  row.append(label, field);
  return row;
}

// A local provider has no key: its models are files, fetched with a button.
let localPoll = null;
function localRow(provider) {
  const row = document.createElement("div");
  row.className = "row top";
  const lbl = document.createElement("div");
  lbl.className = "lbl";
  lbl.innerHTML = `<label>${provider.name}</label><span class="hint">whisper.cpp on this Mac, no key, nothing leaves the machine. Download a model once; it then appears in the model lists.</span>`;
  const field = document.createElement("div");
  field.className = "field local-models";
  field.id = `local-${provider.id}`;
  row.append(lbl, field);
  return row;
}

async function loadLocalModels() {
  const field = document.querySelector(".local-models");
  if (!field) return;
  const list = await api("/api/local/models");
  field.replaceChildren(
    ...list.map((m) => {
      const line = document.createElement("div");
      line.className = "local-model";
      const name = document.createElement("span");
      name.className = "local-name";
      name.textContent = m.label;
      const meta = document.createElement("span");
      meta.className = "local-meta";
      const size = `${(m.size_bytes / 1048576).toFixed(0)} MB`;
      if (m.state === "downloading") meta.textContent = `${Math.round(m.progress * 100)}% of ${size}`;
      else if (m.state === "ready") meta.textContent = `ready · ${size}`;
      else if (m.state === "error") meta.textContent = `failed: ${m.error}`;
      else meta.textContent = `${size} · ${m.note}`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn sm";
      button.dataset.model = m.name;
      if (m.state === "ready") {
        button.textContent = "Remove";
        button.dataset.action = "remove";
      } else {
        button.textContent = m.state === "downloading" ? "Downloading…" : m.state === "error" ? "Retry" : "Download";
        button.dataset.action = "download";
        button.disabled = m.state === "downloading";
      }
      line.append(name, meta, button);
      return line;
    }),
  );
  const busy = list.some((m) => m.state === "downloading");
  if (busy && !localPoll) localPoll = setInterval(() => loadLocalModels().catch(() => {}), 1500);
  if (!busy && localPoll) {
    clearInterval(localPoll);
    localPoll = null;
    await loadModels(); // a model that just finished downloading is now offered
  }
}

keysGroup.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-model]");
  if (!button) return;
  const { model, action } = button.dataset;
  try {
    if (action === "download") await api(`/api/local/models/${model}/download`, { method: "POST" });
    else if (action === "remove") await api(`/api/local/models/${model}`, { method: "DELETE" });
    await loadLocalModels();
    if (action === "remove") await loadModels();
  } catch (err) {
    flash(settingsStatus, String(err.message ?? err), "err");
  }
});

async function loadSettings() {
  const s = await api("/api/settings");
  keysGroup.replaceChildren(...s.providers.map((p) => (p.local ? localRow(p) : keyRow(p))));
  loadLocalModels().catch(() => {});
  llmKeysGroup.replaceChildren(...s.llmProviders.map(keyRow));
  dictionaryModels.replaceChildren(
    ...s.llmProviders.flatMap((p) => p.models.map((m) => new Option(`${m.name}${m.id === p.defaultModel ? " (suggested)" : ""}`, m.id))),
  );
  dictionaryModelInput.value = s.dictionaryModel ?? "";
  fastModeInput.checked = Boolean(s.fastMode);
  streamingProviders = new Set(s.providers.filter((p) => p.streams).map((p) => p.id));
  dictionaryModelChip.textContent = s.dictionaryModel ?? "no model set";
  shortcuts = s.shortcuts;
  shortcutHold.value = s.shortcuts.hold ?? "";
  shortcutToggle.value = s.shortcuts.toggle ?? "";
  updateEmptyHint();
  await Promise.all([loadModels(), loadDictionary()]);
}

function flash(target, message, kind) {
  target.textContent = message;
  target.className = `save-status show ${kind}`;
  clearTimeout(target._timer);
  target._timer = setTimeout(() => target.classList.remove("show"), kind === "ok" ? 1800 : 6000);
}

settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const keys = {};
  for (const input of settingsForm.querySelectorAll("input[type=password]")) {
    if (input.value.trim()) keys[input.name.slice("key:".length)] = input.value.trim();
  }
  const body = { keys, dictionaryModel: dictionaryModelInput.value.trim() || null, fastMode: fastModeInput.checked };
  if (!defaultSelect.disabled) body.defaultModel = defaultSelect.value || null;
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    flash(settingsStatus, "Saved", "ok");
    await loadSettings();
  } catch (err) {
    flash(settingsStatus, String(err.message ?? err), "err");
  }
});

// ---- Dictionary tab ----
// `dict` mirrors dictionary.json: pinned (the user's, never changed by the model) and
// learned (the model's last accepted proposal). Every change is saved whole.
let dict = { pinned: { terms: [], replacements: {} }, agents: { terms: [], replacements: {} }, learned: { terms: [], replacements: {} } };
let proposal = null;
const dictionaryBox = el("dictionary");
const dictionaryStatus = el("dictionary-status");
const buildStatus = el("build-status");
const buildBtn = el("build-dictionary");
const proposalPanel = el("proposal");
const proposalBody = el("proposal-body");

async function loadDictionary() {
  const res = await fetch("/api/dictionary");
  const text = await res.text();
  if (!res.ok) { flash(dictionaryStatus, text, "err"); return; }
  dict = JSON.parse(text);
  renderDictionary(text);
}

async function saveDictionary(next) {
  const res = await fetch("/api/dictionary", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(next) });
  const text = await res.text();
  if (!res.ok) { flash(dictionaryStatus, text, "err"); flash(buildStatus, text, "err"); return false; }
  dict = JSON.parse(text);
  renderDictionary(text);
  return true;
}

function entryRow(section, kind, heard, meant) {
  const row = document.createElement("div");
  row.className = "entry";
  const kindEl = document.createElement("span");
  kindEl.className = "kind";
  kindEl.textContent = kind;
  const what = document.createElement("span");
  what.className = "what";
  if (kind === "term") {
    what.textContent = heard;
  } else {
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = "→";
    const m = document.createElement("span");
    m.className = "meant";
    m.textContent = meant;
    what.append(heard, arrow, m);
  }
  const actions = document.createElement("span");
  actions.className = "actions";
  if (section !== "pinned") {
    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "btn sm";
    pin.textContent = "Pin";
    pin.title = "Keep it: the model will not change it";
    pin.addEventListener("click", () => moveToPinned(section, kind, heard, meant));
    actions.append(pin);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn sm";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => removeEntry(section, kind, heard));
  actions.append(remove);
  row.append(kindEl, what, actions);
  return row;
}

function renderEntries(container, section) {
  const entries = dict[section];
  container.replaceChildren(
    ...entries.terms.map((t) => entryRow(section, "term", t)),
    ...Object.entries(entries.replacements).map(([h, m]) => entryRow(section, "replace", h, m)),
  );
}

function renderDictionary(jsonText) {
  renderEntries(el("pinned-entries"), "pinned");
  renderEntries(el("agents-entries"), "agents");
  renderEntries(el("learned-entries"), "learned");
  const learnedCount = dict.learned.terms.length + Object.keys(dict.learned.replacements).length;
  el("pin-all").hidden = learnedCount === 0;
  buildBtn.textContent = learnedCount ? "Refine from history" : "Build from history";
  if (jsonText !== undefined) dictionaryBox.value = jsonText;
}

function clone() {
  return JSON.parse(JSON.stringify(dict));
}

async function addToPinned(kind, heard, meant) {
  const next = clone();
  if (kind === "term") {
    if (!next.pinned.terms.includes(heard)) next.pinned.terms.push(heard);
  } else {
    next.pinned.replacements[heard] = meant;
  }
  return saveDictionary(next);
}

async function removeEntry(section, kind, heard) {
  const next = clone();
  if (kind === "term") next[section].terms = next[section].terms.filter((t) => t !== heard);
  else delete next[section].replacements[heard];
  await saveDictionary(next);
}

async function moveToPinned(section, kind, heard, meant) {
  const next = clone();
  if (kind === "term") {
    next[section].terms = next[section].terms.filter((t) => t !== heard);
    if (!next.pinned.terms.includes(heard)) next.pinned.terms.push(heard);
  } else {
    delete next[section].replacements[heard];
    next.pinned.replacements[heard] = meant;
  }
  await saveDictionary(next);
}

el("add-term-btn").addEventListener("click", async () => {
  const term = el("add-term").value.trim();
  if (!term) return;
  if (await addToPinned("term", term)) el("add-term").value = "";
});
el("add-term").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el("add-term-btn").click(); } });
el("add-replacement-btn").addEventListener("click", async () => {
  const heard = el("add-heard").value.trim();
  const meant = el("add-meant").value.trim();
  if (!heard || !meant) return;
  if (await addToPinned("replace", heard, meant)) { el("add-heard").value = ""; el("add-meant").value = ""; }
});
el("add-meant").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el("add-replacement-btn").click(); } });
el("pin-all").addEventListener("click", async () => {
  const next = clone();
  for (const t of next.learned.terms) if (!next.pinned.terms.includes(t)) next.pinned.terms.push(t);
  Object.assign(next.pinned.replacements, next.learned.replacements);
  next.learned = { terms: [], replacements: {} };
  await saveDictionary(next);
});

el("save-dictionary").addEventListener("click", async () => {
  let parsed;
  try { parsed = JSON.parse(dictionaryBox.value); } catch (err) { flash(dictionaryStatus, `Not valid JSON: ${err.message}`, "err"); return; }
  if (await saveDictionary(parsed)) flash(dictionaryStatus, "Saved", "ok");
});

// Build: the model proposes a learned section; nothing changes until Accept.
function chips(list, cls) {
  const box = document.createElement("div");
  box.className = "chips";
  for (const text of list) {
    const c = document.createElement("span");
    c.className = `chip ${cls}`;
    c.textContent = text;
    box.append(c);
  }
  return box;
}

function renderProposal(p) {
  proposalBody.replaceChildren();
  const sections = [
    ["Added terms", p.added.terms, "add"],
    ["Added replacements", Object.entries(p.added.replacements).map(([h, m]) => `${h} → ${m}`), "add"],
    ["Removed terms", p.removed.terms, "remove"],
    ["Removed replacements", Object.entries(p.removed.replacements).map(([h, m]) => `${h} → ${m}`), "remove"],
  ];
  let any = false;
  for (const [title, items, cls] of sections) {
    if (items.length === 0) continue;
    any = true;
    const h = document.createElement("h3");
    h.textContent = `${title} (${items.length})`;
    proposalBody.append(h, chips(items, cls));
  }
  if (!any) {
    const p2 = document.createElement("p");
    p2.className = "nothing";
    p2.textContent = "The model proposed no changes to what is learned.";
    proposalBody.append(p2);
  }
  proposalPanel.hidden = false;
}

buildBtn.addEventListener("click", async () => {
  buildBtn.disabled = true;
  buildStatus.className = "save-status show";
  buildStatus.textContent = "Asking the model… this can take a minute.";
  try {
    const res = await fetch("/api/dictionary/build", { method: "POST" });
    const text = await res.text();
    if (!res.ok) throw new Error(text);
    proposal = JSON.parse(text);
    buildStatus.classList.remove("show");
    renderProposal(proposal);
  } catch (err) {
    flash(buildStatus, String(err.message ?? err), "err");
  } finally {
    buildBtn.disabled = false;
  }
});
el("accept-proposal").addEventListener("click", async () => {
  if (!proposal) return;
  const next = clone();
  next.learned = proposal.learned;
  if (await saveDictionary(next)) {
    proposal = null;
    proposalPanel.hidden = true;
    flash(buildStatus, "Accepted", "ok");
  }
});
el("discard-proposal").addEventListener("click", () => {
  proposal = null;
  proposalPanel.hidden = true;
});

// ---- Shortcuts: recorded by pressing them ----
// The menu-bar app's global listener captures the keys (it is the only thing that
// can see fn); the page polls for the result and saves it. Without the app, the
// page captures what the browser lets it see.
async function saveShortcuts() {
  const body = { keys: {}, shortcuts: { hold: shortcutHold.value.trim(), toggle: shortcutToggle.value.trim() } };
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    shortcutStatus.textContent = "Saved.";
    shortcuts = { hold: shortcutHold.value.trim() || null, toggle: shortcutToggle.value.trim() || null };
    updateEmptyHint();
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
        const state = await api("/api/capture");
        if (state.state === "done") { keys = state.keys; break; }
        if (state.state === "idle") break;
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
    input.placeholder = "Not set";
    button.disabled = false;
  }
}

function captureInPage(input) {
  return new Promise((resolve, reject) => {
    const names = { Meta: "cmd", Control: "ctrl", Alt: "alt", Shift: "shift", " ": "space", Enter: "enter", Escape: "esc", Tab: "tab", Backspace: "backspace", ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
    const keys = [];
    const down = new Set();
    const nameOf = (e) => names[e.key] ?? e.key.toLowerCase();
    const onDown = (e) => {
      e.preventDefault();
      const name = nameOf(e);
      if (!keys.includes(name)) keys.push(name);
      down.add(name);
    };
    const onUp = (e) => {
      down.delete(nameOf(e));
      if (keys.length && down.size === 0) { cleanup(); resolve(keys.join("+")); }
    };
    const cleanup = () => { window.removeEventListener("keydown", onDown, true); window.removeEventListener("keyup", onUp, true); };
    window.addEventListener("keydown", onDown, true);
    window.addEventListener("keyup", onUp, true);
    setTimeout(() => { if (keys.length === 0) { cleanup(); reject(new Error("Nothing pressed.")); } }, 15000);
    input.focus();
  });
}

for (const button of settingsForm.querySelectorAll("button.capture")) {
  button.addEventListener("click", () => captureShortcut(el(button.dataset.target), button));
}
for (const button of settingsForm.querySelectorAll("button.clear")) {
  button.addEventListener("click", async () => { el(button.dataset.target).value = ""; await saveShortcuts(); });
}

function updateEmptyHint() {
  const parts = [];
  if (shortcuts.hold) parts.push(`hold ${shortcuts.hold}`);
  if (shortcuts.toggle) parts.push(`press ${shortcuts.toggle}`);
  emptyHint.replaceChildren();
  if (parts.length) {
    emptyHint.append("Anywhere, ");
    parts.forEach((p, i) => {
      const [verb, keys] = p.split(" ");
      const kbd = document.createElement("kbd");
      kbd.textContent = keys;
      emptyHint.append(`${i ? " or " : ""}${verb} `, kbd);
    });
    emptyHint.append(" and speak. Or press Record above.");
  } else {
    emptyHint.textContent = "Press Record above and speak, or set a shortcut in Settings.";
  }
}

// ---- Recording from this window ----
let recorder = null;
let recTick = null;

function setRecording(on) {
  recordBtn.setAttribute("aria-pressed", String(on));
  recordBtn.querySelector(".label").textContent = on ? "Stop" : "Record";
  recTimer.hidden = !on;
  clearInterval(recTick);
  if (on) {
    const start = Date.now();
    recTimer.textContent = "0:00";
    recTick = setInterval(() => {
      const s = Math.floor((Date.now() - start) / 1000);
      recTimer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }, 250);
  }
}

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
    setRecording(false);
    upload(new Blob(chunks, { type }));
  });
  recorder.start();
  setRecording(true);
  status.textContent = "";
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
  show("history");
  await loadHistory(true);
}

// ---- History ----
function attemptLabel(t) {
  return `${t.provider} / ${t.model}`;
}

function chip(text) {
  const span = document.createElement("span");
  span.className = "model-chip";
  span.textContent = text;
  return span;
}

function transcriptBlock(t) {
  const block = document.createElement("div");
  const failed = t.status !== "ok";
  block.className = failed ? "transcript error failed" : t.text ? "transcript" : "transcript empty";
  block.dataset.copy = failed ? t.error ?? "" : t.text ?? "";
  block.title = block.dataset.copy ? "Click to copy" : "";
  if (failed) {
    const label = document.createElement("div");
    label.className = "error-label";
    label.innerHTML = `${ICON.warn} Transcription failed`;
    block.append(label, t.error ?? "");
  } else {
    block.append(t.text || "(no speech detected)");
  }
  const toast = document.createElement("span");
  toast.className = "copied-toast";
  toast.textContent = "Copied";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "btn-icon copy";
  copy.setAttribute("aria-label", failed ? "Copy error" : "Copy transcript");
  copy.innerHTML = ICON.copy;
  copy.disabled = !block.dataset.copy;
  block.append(toast, copy);
  return block;
}

function renderCard(r) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.id = r.id;
  const [latest, ...earlier] = r.transcriptions;

  const head = document.createElement("div");
  head.className = "card-head";
  const when = document.createElement("time");
  when.className = "when";
  when.dateTime = r.created_at;
  when.textContent = new Date(r.created_at).toLocaleString();
  head.append(when);
  if (latest) head.append(chip(attemptLabel(latest)));
  const spacer = document.createElement("span");
  spacer.className = "spacer";
  const audioRow = document.createElement("div");
  audioRow.className = "audio-row";
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.preload = "none";
  audio.src = `/api/recordings/${r.id}/audio`;
  audio.setAttribute("aria-label", `Recording ${when.textContent}`);
  const download = document.createElement("a");
  download.className = "btn-icon";
  download.href = `/api/recordings/${r.id}/audio`;
  download.download = "";
  download.title = "Download audio";
  download.setAttribute("aria-label", "Download audio");
  download.innerHTML = ICON.download;
  audioRow.append(audio, download);
  head.append(spacer, audioRow);
  card.append(head);

  if (latest) card.append(transcriptBlock(latest));

  // Any recording can be transcribed again with another model.
  const foot = document.createElement("div");
  foot.className = "card-foot";
  const select = document.createElement("select");
  select.className = "select sm retry-model";
  select.setAttribute("aria-label", "Model for re-transcription");
  const current = latest ? `${latest.provider}/${latest.model}` : null;
  fillModels(select, models.find((m) => m.id !== current)?.id ?? models[0]?.id ?? null, "No models: add an API key");
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "btn sm retry";
  retry.textContent = latest?.status === "error" ? "Try again" : "Transcribe again";
  retry.disabled = select.disabled;
  const foot_status = document.createElement("span");
  foot_status.className = "status";
  foot.append(select, retry, foot_status);
  card.append(foot);

  if (earlier.length > 0) {
    const details = document.createElement("details");
    details.className = "attempts";
    const summary = document.createElement("summary");
    summary.innerHTML = `${ICON.chevron} ${earlier.length} earlier attempt${earlier.length === 1 ? "" : "s"}`;
    details.append(summary);
    for (const t of earlier) {
      const attempt = document.createElement("div");
      attempt.className = "attempt";
      const p = document.createElement("p");
      p.className = t.status === "ok" ? "" : "err";
      p.textContent = t.status === "ok" ? t.text || "(no speech detected)" : t.error ?? "";
      attempt.append(chip(attemptLabel(t)), p);
      details.append(attempt);
    }
    card.append(details);
  }
  return card;
}

// Dictations made with the shortcut arrive while this page is open, so it keeps
// itself current: it re-reads the history every few seconds while visible and
// re-renders only when something changed.
let historySnapshot = "";
const HISTORY_POLL_MS = 3000;

async function loadHistory(force = false) {
  const recordings = await api("/api/recordings");
  const snapshot = JSON.stringify(recordings);
  if (!force && snapshot === historySnapshot) return;
  historySnapshot = snapshot;
  historyList.replaceChildren(...recordings.map(renderCard));
  loadMetrics().catch(() => {});
  emptyState.hidden = recordings.length > 0;
  historyLabel.hidden = recordings.length === 0;
}

setInterval(() => {
  if (document.visibilityState === "visible") loadHistory().catch(() => {});
}, HISTORY_POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadHistory().catch(() => {});
});

// Copy (whole block or its icon) and re-transcribe, delegated so re-renders need no rebinding.
historyList.addEventListener("click", async (e) => {
  const block = e.target.closest(".transcript");
  if (block && block.dataset.copy) {
    const button = block.querySelector(".copy");
    const toast = block.querySelector(".copied-toast");
    try {
      await navigator.clipboard.writeText(block.dataset.copy);
      toast.textContent = "Copied";
      button.innerHTML = ICON.check;
    } catch (err) {
      toast.textContent = `Copy failed: ${err.message ?? err}`;
    }
    block.classList.add("copied");
    setTimeout(() => { block.classList.remove("copied"); button.innerHTML = ICON.copy; }, 1500);
    return;
  }
  const retry = e.target.closest(".retry");
  if (retry) {
    const card = retry.closest(".card");
    const foot_status = card.querySelector(".card-foot .status");
    const select = card.querySelector(".retry-model");
    const label = select.selectedOptions[0]?.textContent ?? "";
    retry.disabled = true;
    foot_status.className = "status";
    foot_status.textContent = `Transcribing with ${label}…`;
    try {
      await api(`/api/recordings/${card.dataset.id}/transcriptions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ model: select.value }),
      });
    } catch (err) {
      foot_status.className = "status err";
      foot_status.textContent = err.message ?? String(err);
      retry.disabled = false;
      return;
    }
    await loadHistory(true);
  }
});

// ---- Start ----
await loadSettings();
if (models.length === 0 || location.hash === "#settings") show("settings");
else if (location.hash === "#dictionary") show("dictionary");
await loadHistory(true);
