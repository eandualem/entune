// Dictum's window: history, dictionary, settings, and recording from here. Plain DOM
// code against the local API; no framework, no build step. Layout and tokens follow
// the design of 2026-09-18; every colour and size lives in tokens.css.

import { createHistory } from "./history.js";

const el = (id) => document.getElementById(id);
const status = el("status");
const modelSelect = el("model");
const fastWrap = el("fast-mode-wrap");
const fastInput = el("fast-mode");
const recordBtn = el("record");
const recTimer = el("rec-timer");
const historyList = el("history");
const emptyState = el("empty");
const stepsList = el("steps");

const ICON = {
  check: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  play: '<svg class="i12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2l7 4-7 4z" fill="currentColor"/></svg>',
  pause: '<svg class="i12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 2h2.5v8H3zM6.5 2H9v8H6.5z" fill="currentColor"/></svg>',
  download: '<svg class="i14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2v8M4.5 6.5L8 10l3.5-3.5M3 13h10"/></svg>',
  attempts: '<svg class="i13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M2 5h12M2 8h12M2 11h8"/></svg>',
  retry: '<svg class="i14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.6-3.7M13 3v3h-3"/></svg>',
  remove: '<svg class="i12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  chevron: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" style="width:0.625rem;height:0.625rem"><path d="M4 6l4 4 4-4"/></svg>',
};

let models = [];
let defaultModel = null; // {id, label, term_limit} from /api/models, or null
let settings = null; // the last /api/settings answer
let shortcuts = { hold: null, toggle: null };
let recordingsCount = 0;

async function api(path, init) {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function flash(target, message, kind) {
  target.textContent = message;
  target.classList.add("save-status", "show");
  target.classList.toggle("ok", kind === "ok");
  target.classList.toggle("err", kind === "err");
  clearTimeout(target._timer);
  target._timer = setTimeout(() => target.classList.remove("show"), kind === "ok" ? 1800 : 8000);
}

const errorText = (err) => String(err?.message ?? err);

// ---- Preferences kept in this window: theme, text size, hints ----
function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  try { theme === "system" ? localStorage.removeItem("theme") : localStorage.setItem("theme", theme); } catch (e) {}
}
{
  let saved = "system";
  try { saved = localStorage.getItem("theme") || "system"; } catch (e) {}
  for (const radio of document.querySelectorAll("input[name=appearance]")) {
    radio.checked = radio.value === saved;
    radio.addEventListener("change", () => applyTheme(radio.value));
  }
}

const SCALES = ["small", "default", "large", "larger"];
const currentScale = () => document.documentElement.dataset.scale ?? "default";
function applyScale(scale) {
  if (scale === "default") delete document.documentElement.dataset.scale;
  else document.documentElement.dataset.scale = scale;
  try { scale === "default" ? localStorage.removeItem("scale") : localStorage.setItem("scale", scale); } catch (e) {}
  for (const radio of document.querySelectorAll("input[name=scale]")) radio.checked = radio.value === scale;
}
for (const radio of document.querySelectorAll("input[name=scale]")) {
  radio.checked = radio.value === currentScale();
  radio.addEventListener("change", () => applyScale(radio.value));
}
document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
  const step = e.key === "=" || e.key === "+" ? 1 : e.key === "-" ? -1 : e.key === "0" ? 0 : null;
  if (step === null) return;
  e.preventDefault();
  const at = SCALES.indexOf(currentScale());
  applyScale(step === 0 ? "default" : SCALES[Math.max(0, Math.min(SCALES.length - 1, at + step))]);
});

const hintsInput = el("hints");
hintsInput.checked = document.documentElement.dataset.hints !== "off";
hintsInput.addEventListener("change", () => {
  if (hintsInput.checked) delete document.documentElement.dataset.hints;
  else document.documentElement.dataset.hints = "off";
  try { hintsInput.checked ? localStorage.removeItem("hints") : localStorage.setItem("hints", "off"); } catch (e) {}
  if (!hintsInput.checked) showHelp(false);
});

// ---- Views and the settings rail ----
function segmentedGroup(buttons, onPick) {
  for (const [name, button] of Object.entries(buttons)) {
    button.addEventListener("click", () => { select(name); onPick(name); });
  }
  function select(name) {
    for (const [key, button] of Object.entries(buttons)) button.setAttribute("aria-selected", String(key === name));
  }
  return select;
}
const views = { history: el("view-history"), dictionary: el("view-dictionary"), settings: el("view-settings") };
const selectTab = segmentedGroup({ history: el("tab-history"), dictionary: el("tab-dictionary"), settings: el("tab-settings") }, showView);
function showView(name) {
  for (const key in views) views[key].toggleAttribute("data-active", key === name);
  views[name].scrollTop = 0;
  if (name === "history") loadHistory().catch((err) => { status.textContent = errorText(err); });
}
function show(name) { selectTab(name); showView(name); }

const sections = { general: el("settings-general"), providers: el("settings-providers"), local: el("settings-local"), agents: el("settings-agents") };
const selectSection = segmentedGroup({ general: el("sec-general"), providers: el("sec-providers"), local: el("sec-local"), agents: el("sec-agents") }, showSection);
function showSection(name) {
  for (const key in sections) sections[key].toggleAttribute("data-active", key === name);
}
function openSettings(section) { show("settings"); selectSection(section); showSection(section); }

// ---- Models: one default, picked in the toolbar; it applies at once ----
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
  defaultModel = models.find((m) => m.default) ?? null;
  fillModels(modelSelect, defaultModel?.id ?? null, "No models: add a key or download one");
  if (!defaultModel && models.length > 0) modelSelect.prepend(new Option("Pick a model", "", true, true));
  const provider = defaultModel?.id.split("/")[0];
  const streams = (settings?.providers ?? []).some((p) => p.id === provider && p.streams);
  fastInput.disabled = !streams;
  fastWrap.classList.toggle("off", !streams);
  fastWrap.title = streams
    ? "Fast mode: upload while recording, so a long dictation is transcribed as soon as you stop"
    : "Fast mode: only AssemblyAI takes the audio while you record; pick it to use fast mode";
  if (!status.textContent || status.textContent === "Ready") status.textContent = defaultModel ? "Ready" : "";
  renderDictionary();
  renderStart();
  // A model change only changes the pickers, never the cards or their audio.
  for (const select of historyList.querySelectorAll(".retry-model")) {
    fillModels(select, select.value, "No models");
    const retry = select.nextElementSibling;
    if (!retry.dataset.busy) retry.disabled = select.disabled;
  }
}

modelSelect.addEventListener("change", async () => {
  const chosen = modelSelect.value || null;
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ defaultModel: chosen }) });
    status.textContent = chosen ? `Using ${modelSelect.selectedOptions[0]?.textContent}` : "";
  } catch (err) {
    status.textContent = errorText(err);
  }
  await loadModels();
});

async function saveSetting(body, statusTarget) {
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ keys: {}, ...body }) });
    if (statusTarget) flash(statusTarget, "Saved", "ok");
    return true;
  } catch (err) {
    if (statusTarget) flash(statusTarget, errorText(err), "err");
    else status.textContent = errorText(err);
    return false;
  }
}
fastInput.addEventListener("change", () => saveSetting({ fastMode: fastInput.checked }, null));

// ---- Performance by model: a popover behind the chart button ----
const metricsPopover = el("metrics");
const metricsToggle = el("metrics-toggle");
const metricsRows = el("metrics-rows");
function showMetrics(open) {
  metricsPopover.hidden = !open;
  metricsToggle.setAttribute("aria-expanded", String(open));
}
metricsToggle.addEventListener("click", () => showMetrics(metricsPopover.hidden));
document.addEventListener("click", (e) => {
  if (!metricsPopover.hidden && !metricsPopover.contains(e.target) && !metricsToggle.contains(e.target)) showMetrics(false);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !metricsPopover.hidden) showMetrics(false); });

async function loadMetrics() {
  const rows = await api("/api/metrics");
  metricsToggle.hidden = rows.length === 0;
  if (rows.length === 0) showMetrics(false);
  const cell = (text) => Object.assign(document.createElement("span"), { textContent: text });
  metricsRows.replaceChildren(
    ...rows.map((m) => {
      const row = document.createElement("div");
      row.className = "perf-grid";
      row.append(
        cell(`${m.provider} / ${m.model}`),
        cell(m.fast ? "fast" : "plain"),
        cell(m.ok === m.runs ? String(m.runs) : `${m.ok} of ${m.runs} ok`),
        cell(`${Math.round(m.audio_seconds / 60)} min`),
        cell(m.median_wait === null ? "–" : `${m.median_wait.toFixed(1)} s`),
        cell(m.speed === null ? "–" : `${m.speed.toFixed(0)}× realtime`),
      );
      return row;
    }),
  );
}

// ---- Settings ----
function keyRow(provider) {
  const row = document.createElement("div");
  row.className = "srow";
  const label = document.createElement("label");
  label.className = "name";
  label.htmlFor = `key-${provider.id}`;
  label.textContent = provider.name;
  const input = document.createElement("input");
  input.className = "input field";
  input.id = `key-${provider.id}`;
  input.type = "password";
  input.name = provider.id;
  input.autocomplete = "off";
  input.placeholder = provider.keyHint ? `saved ${provider.keyHint} · type to replace` : "Not set";
  row.append(label, input);
  return row;
}

const keysForm = el("keys-form");
keysForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const keys = {};
  for (const input of keysForm.querySelectorAll("input[type=password]")) {
    if (input.value.trim()) keys[input.name] = input.value.trim();
  }
  if (Object.keys(keys).length === 0) { flash(el("keys-status"), "Nothing to save", "ok"); return; }
  if (await saveSetting({ keys }, el("keys-status"))) await loadSettings();
});

// The dictionary model: a summary line with Change, then provider, key and model.
const dm = { edit: el("dm-edit"), provider: el("dm-provider"), key: el("dm-key"), model: el("dm-model"), custom: el("dm-model-custom") };
function renderDictionaryModel() {
  const [providerId, , modelId] = splitRef(settings.dictionaryModel);
  const provider = settings.llmProviders.find((p) => p.id === providerId);
  const anyKey = settings.llmProviders.find((p) => p.keyHint);
  if (provider) {
    el("dm-title").textContent = `${provider.name} · ${modelId}`;
    el("dm-caption").textContent = provider.keyHint ? `key saved ${provider.keyHint} · runs at high reasoning effort` : `no key for ${provider.name} yet: add one to build the dictionary`;
  } else {
    el("dm-title").textContent = "No model";
    el("dm-caption").textContent = anyKey ? "" : "Add an Anthropic or OpenAI key to build the dictionary from your history.";
  }
}
function splitRef(ref) {
  const i = (ref ?? "").indexOf(":");
  return i < 0 ? [ref ?? "", "", ""] : [ref.slice(0, i), ":", ref.slice(i + 1)];
}
function fillDictionaryModelForm() {
  const [providerId, , modelId] = splitRef(settings.dictionaryModel);
  dm.provider.replaceChildren(...settings.llmProviders.map((p) => new Option(p.name, p.id, false, p.id === providerId)));
  if (!providerId) dm.provider.value = settings.llmProviders.find((p) => p.keyHint)?.id ?? settings.llmProviders[0]?.id ?? "";
  fillDictionaryModelChoices(modelId);
}
function fillDictionaryModelChoices(chosen) {
  const provider = settings.llmProviders.find((p) => p.id === dm.provider.value);
  if (!provider) return;
  dm.key.value = "";
  dm.key.placeholder = provider.keyHint ? `saved ${provider.keyHint} · type to replace` : "Not set";
  const known = provider.models.map((m) => m.id);
  const listed = chosen && !known.includes(`${provider.id}:${chosen}`) ? [{ id: `${provider.id}:${chosen}`, name: chosen }] : [];
  dm.model.replaceChildren(
    ...[...listed, ...provider.models].map((m) => new Option(`${m.name}${m.id === provider.defaultModel ? " (suggested)" : ""}`, m.id.slice(provider.id.length + 1))),
    new Option("Custom…", "__custom__"),
  );
  dm.model.value = chosen && dm.model.querySelector(`option[value="${CSS.escape(chosen)}"]`) ? chosen : provider.defaultModel.slice(provider.id.length + 1);
  dm.custom.hidden = true;
  dm.custom.value = "";
}
dm.provider.addEventListener("change", () => fillDictionaryModelChoices(null));
dm.model.addEventListener("change", () => {
  dm.custom.hidden = dm.model.value !== "__custom__";
  if (!dm.custom.hidden) dm.custom.focus();
});
el("dm-change").addEventListener("click", () => {
  fillDictionaryModelForm();
  el("dm-summary").hidden = true;
  dm.edit.hidden = false;
});
el("dm-cancel").addEventListener("click", () => { dm.edit.hidden = true; el("dm-summary").hidden = false; });
dm.edit.addEventListener("submit", async (e) => {
  e.preventDefault();
  const modelId = dm.model.value === "__custom__" ? dm.custom.value.trim() : dm.model.value;
  if (!modelId) { flash(el("dm-status"), "Give the model's id", "err"); return; }
  const keys = dm.key.value.trim() ? { [dm.provider.value]: dm.key.value.trim() } : {};
  if (await saveSetting({ keys, dictionaryModel: `${dm.provider.value}:${modelId}` }, el("dm-status"))) {
    dm.edit.hidden = true;
    el("dm-summary").hidden = false;
    await loadSettings();
  }
});

// Local models: a card per local provider, rows with size, state and one button.
let localPoll = null;
let localList = [];
const localSearch = el("local-search");
localSearch.addEventListener("input", () => renderLocalModels());

async function loadLocalModels() {
  const providers = (settings?.providers ?? []).filter((p) => p.local);
  if (providers.length === 0) return;
  localList = await api("/api/local/models");
  renderLocalModels();
  const busy = localList.some((m) => m.state === "downloading");
  if (busy && !localPoll) localPoll = setInterval(() => loadLocalModels().catch(() => {}), 1500);
  if (!busy && localPoll) {
    clearInterval(localPoll);
    localPoll = null;
    await loadModels(); // a model that just finished downloading is now offered
  }
}

const gb = (bytes) => (bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(1)} GB` : `${(bytes / 1048576).toFixed(0)} MB`);

function renderLocalModels() {
  const query = localSearch.value.trim().toLowerCase();
  const ready = localList.filter((m) => m.state === "ready");
  const onDisk = ready.reduce((n, m) => n + m.size_bytes, 0);
  el("local-summary").textContent = localList.length ? `${ready.length} of ${localList.length} downloaded · ${gb(onDisk)} on disk` : "";
  const cards = [];
  for (const provider of settings.providers.filter((p) => p.local)) {
    const mine = localList.filter((m) => m.provider === provider.id && (!query || `${m.label} ${m.note}`.toLowerCase().includes(query)));
    if (query && mine.length === 0) continue;
    const card = document.createElement("div");
    card.className = "scard";
    const head = document.createElement("div");
    head.className = "scard-head";
    const isParakeet = provider.id === "parakeet";
    head.innerHTML = isParakeet
      ? `<div class="name strong">Parakeet <span class="caption">· NVIDIA on Apple MLX</span></div><div class="caption hint">The most accurate offline model. Its engine is installed outside Dictum, once; Dictum then finds it.</div>`
      : `<div class="name strong">Whisper <span class="caption">· whisper.cpp</span></div><div class="caption hint">Downloaded inside Dictum with one click. Runs on this machine; nothing leaves it.</div>`;
    card.append(head, ...mine.map((m) => localRow(m, isParakeet)));
    const missing = mine.find((m) => m.state === "unavailable");
    if (isParakeet && missing) card.append(engineNote(missing));
    else if (isParakeet && mine.some((m) => m.state !== "unavailable")) {
      const found = document.createElement("div");
      found.className = "engine";
      found.innerHTML = `<span class="dot ok"></span>Engine found.`;
      card.append(found);
    }
    cards.push(card);
  }
  el("local-cards").replaceChildren(...cards);
}

function localRow(m, isParakeet) {
  const row = document.createElement("div");
  row.className = "lrow";
  const name = document.createElement("div");
  name.innerHTML = `<div class="name"></div><div class="note"></div>`;
  name.querySelector(".name").textContent = m.label;
  name.querySelector(".note").textContent = isParakeet ? `${m.note} · takes no words, replacements only` : m.note;
  const size = document.createElement("span");
  size.className = "size";
  size.textContent = gb(m.size_bytes);
  const state = document.createElement("div");
  state.className = "state";
  const dot = document.createElement("span");
  dot.className = "dot";
  const text = document.createElement("span");
  if (m.state === "ready") { dot.classList.add("ok"); text.textContent = "ready"; }
  else if (m.state === "downloading") { dot.classList.add("busy"); text.textContent = `${Math.round(m.progress * 100)}%`; }
  else if (m.state === "error") { dot.classList.add("err"); text.textContent = "failed"; }
  else if (m.state === "unavailable") { dot.classList.add("busy"); text.textContent = "setup required"; state.classList.add("setup"); }
  else text.textContent = "not downloaded";
  state.append(dot, text);
  if (m.state === "downloading") {
    const bar = document.createElement("span");
    bar.className = "bar";
    bar.innerHTML = `<span style="width:${Math.round(m.progress * 100)}%"></span>`;
    state.append(bar);
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn sm";
  button.dataset.model = m.name;
  if (m.state === "ready") { button.textContent = "Remove"; button.dataset.action = "remove"; }
  else if (m.state === "downloading") { button.textContent = "Downloading…"; button.disabled = true; }
  else if (m.state === "unavailable") { button.textContent = "Check installation"; button.classList.add("setup"); button.dataset.action = "check"; }
  else { button.textContent = m.state === "error" ? "Retry" : "Download"; button.dataset.action = "download"; }
  row.append(name, size, state, button);
  if (m.state === "error" && m.error) {
    const wrap = document.createElement("div");
    wrap.append(row);
    const why = document.createElement("div");
    why.className = "engine";
    why.innerHTML = `<span class="dot err"></span>`;
    why.append(m.error);
    wrap.append(why);
    return wrap;
  }
  return row;
}

let engineStepsOpen = false;
function engineNote(m) {
  const wrap = document.createElement("div");
  const line = document.createElement("div");
  line.className = "engine";
  line.innerHTML = `<span>Engine not found. Install it in Terminal, then check again.</span><span class="spacer"></span><button type="button" class="btn link steps-toggle">${ICON.chevron}Installation steps</button>`;
  const steps = document.createElement("div");
  steps.className = "engine-steps";
  steps.hidden = !engineStepsOpen;
  steps.innerHTML = `<span class="n">1</span><div>Install the engine, once:<pre>uv tool install parakeet-mlx</pre></div>
    <span class="n">2</span><div>Come back and press <b>Check installation</b>. Dictum looks for <code>parakeet-mlx</code> where uv installs tools.</div>
    <span class="n">3</span><div>Then press <b>Download</b> here to fetch the model (about ${gb(m.size_bytes)}).</div>`;
  line.querySelector(".steps-toggle").addEventListener("click", () => { engineStepsOpen = !engineStepsOpen; steps.hidden = !engineStepsOpen; });
  wrap.append(line, steps);
  return wrap;
}

el("local-cards").addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-model]");
  if (!button) return;
  const { model, action } = button.dataset;
  try {
    if (action === "download") await api(`/api/local/models/${model}/download`, { method: "POST" });
    else if (action === "remove") await api(`/api/local/models/${model}`, { method: "DELETE" });
    await loadLocalModels();
    if (action === "remove") await loadModels();
  } catch (err) {
    el("local-summary").textContent = errorText(err);
  }
});

// Agents: the local API, and what arrived through it.
function renderAgents() {
  const endpoint = `${location.origin}/api/dictionary/corrections`;
  el("agent-endpoint").textContent = endpoint;
  el("agent-curl").textContent = `curl -s -m 2 -X POST ${endpoint} \\\n  -H 'content-type: application/json' \\\n  -d '{"replacements": {"cloud code": "Claude Code"}, "terms": ["Dictum"], "source": "my-agent"}'`;
}
for (const button of document.querySelectorAll(".copy-btn")) {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(el(button.dataset.copy).textContent);
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = "Copy"; }, 1400);
    } catch (err) {
      button.textContent = "Copy failed";
    }
  });
}
async function loadCorrections() {
  const rows = await api("/api/dictionary/corrections");
  const list = el("corrections-rows");
  if (rows.length === 0) {
    list.innerHTML = `<div class="crow none">Nothing received yet.</div>`;
    return;
  }
  list.replaceChildren(
    ...rows.map((c) => {
      const row = document.createElement("div");
      row.className = "crow";
      const ts = document.createElement("span");
      ts.className = "ts";
      ts.textContent = whenLabel(c.created_at);
      const what = document.createElement("span");
      what.className = "what";
      if (c.meant) {
        what.innerHTML = `<span class="heard"></span><span class="arrow">→</span><span class="meant"></span>`;
        what.querySelector(".heard").textContent = c.heard;
        what.querySelector(".meant").textContent = c.meant;
      } else what.textContent = c.heard;
      const from = document.createElement("span");
      from.className = "from";
      from.textContent = c.source ?? "";
      row.append(ts, what, from);
      return row;
    }),
  );
}

async function loadSettings() {
  settings = await api("/api/settings");
  el("keys").replaceChildren(...settings.providers.filter((p) => !p.local).map(keyRow));
  fastInput.checked = Boolean(settings.fastMode);
  shortcuts = settings.shortcuts;
  el("shortcut-hold").textContent = settings.shortcuts.hold ?? "";
  el("shortcut-toggle").textContent = settings.shortcuts.toggle ?? "";
  renderDictionaryModel();
  renderAgents();
  loadLocalModels().catch(() => {});
  loadCorrections().catch(() => {});
  loadMetrics().catch(() => {});
  await Promise.all([loadModels(), loadDictionary()]);
}

// ---- Shortcuts: recorded by pressing them ----
const shortcutStatus = el("shortcut-status");
function showShortcutStatus(text) {
  shortcutStatus.textContent = text;
  el("shortcut-status-row").hidden = !text;
}
async function saveShortcuts() {
  const hold = el("shortcut-hold").textContent.trim();
  const toggle = el("shortcut-toggle").textContent.trim();
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ keys: {}, shortcuts: { hold, toggle } }) });
    showShortcutStatus("");
    shortcuts = { hold: hold || null, toggle: toggle || null };
    renderStart();
  } catch (err) {
    showShortcutStatus(errorText(err));
  }
}

async function captureShortcut(display, button) {
  const previous = display.textContent;
  display.textContent = "";
  display.dataset.empty = "Press keys…";
  button.disabled = true;
  showShortcutStatus("");
  try {
    const res = await fetch("/api/capture", { method: "POST" });
    if (res.status === 409) {
      display.textContent = await captureInPage();
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
      display.textContent = keys;
    }
    await saveShortcuts();
  } catch (err) {
    display.textContent = previous;
    showShortcutStatus(errorText(err));
  } finally {
    display.dataset.empty = "Not set";
    button.disabled = false;
  }
}

function captureInPage() {
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
  });
}

for (const button of document.querySelectorAll("button.capture")) {
  button.addEventListener("click", () => captureShortcut(el(button.dataset.target), button));
}
for (const button of document.querySelectorAll("button.clear")) {
  button.addEventListener("click", async () => { el(button.dataset.target).textContent = ""; await saveShortcuts(); });
}

// ---- Getting started: the empty history, as steps that tick themselves off ----
function renderStart() {
  const kbd = (keys) => Object.assign(document.createElement("kbd"), { textContent: keys });
  const haveModel = models.length > 0;
  const haveDefault = Boolean(defaultModel);
  const dictate = [];
  if (shortcuts.hold || shortcuts.toggle) {
    dictate.push("Anywhere, ");
    if (shortcuts.hold) dictate.push("hold ", kbd(shortcuts.hold));
    if (shortcuts.hold && shortcuts.toggle) dictate.push(" or ");
    if (shortcuts.toggle) dictate.push("press ", kbd(shortcuts.toggle));
    dictate.push(" and speak; the text is typed where you are and copied. Or press Record above.");
  } else {
    dictate.push("Press Record above and speak. A shortcut in Settings lets you dictate into any app.");
  }
  const step = (done, what, how, action) => {
    const li = document.createElement("li");
    li.className = done ? "step done" : "step";
    const mark = document.createElement("span");
    mark.className = "mark";
    mark.innerHTML = ICON.check;
    const text = document.createElement("span");
    text.className = "what";
    text.append(what);
    const hint = document.createElement("span");
    hint.className = "how";
    hint.append(...how);
    text.append(hint);
    li.append(mark, text);
    if (action && !done) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn ghost sm";
      button.textContent = action.label;
      button.addEventListener("click", action.go);
      li.append(button);
    }
    return li;
  };
  stepsList.replaceChildren(
    step(haveModel, "Add a provider", ["A key, or a downloaded model."], { label: "Providers", go: () => openSettings("providers") }),
    step(haveDefault, "Pick the default model", ["The picker in the toolbar; it applies at once."], null),
    step(recordingsCount > 0, "Dictate", dictate, shortcuts.hold || shortcuts.toggle ? null : { label: "Set a shortcut", go: () => openSettings("general") }),
  );
}

// ---- Recording from this window ----
let recorder = null;
let recTick = null;
let starting = false; // between the click and the microphone answering

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

// The page records raw PCM and wraps it as WAV, like the shortcut does, so every
// recording is one container that every provider, cloud or local, takes without a
// decoder. (MediaRecorder would give WebM, which the local engines cannot read.)
recordBtn.addEventListener("click", async () => {
  if (recorder) { recorder.stop(); return; }
  if (starting) return; // a second click before permission resolves would open a second mic
  starting = true;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    starting = false;
    status.textContent = `Microphone unavailable: ${errorText(err)}`;
    return;
  }
  starting = false;
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const tap = context.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  tap.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(tap);
  tap.connect(context.destination);
  recorder = {
    stop() {
      tap.disconnect();
      source.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      const rate = context.sampleRate;
      context.close();
      recorder = null;
      setRecording(false);
      upload(wavBlob(chunks, rate));
    },
  };
  setRecording(true);
  status.textContent = "Recording…";
});

function wavBlob(chunks, rate) {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => [...text].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  ascii(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (const sample of chunk) {
      const s = Math.max(-1, Math.min(1, sample));
      view.setInt16(offset, s < 0 ? s * 32768 : s * 32767, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function upload(audio) {
  const form = new FormData();
  form.append("audio", audio, "clip");
  const label = modelSelect.selectedOptions[0]?.textContent ?? "default model";
  status.textContent = `Transcribing with ${label}…`;
  try {
    await api("/api/recordings", { method: "POST", body: form });
    status.textContent = "Ready";
  } catch (err) {
    status.textContent = errorText(err);
  }
  show("history");
  await history.latest();
}

// ---- History ----
function whenLabel(iso) {
  const d = new Date(iso);
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, now)) return `Today ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return `Yesterday ${time}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}
const attemptLabel = (t) => (t.provider === "none" ? "no model set" : `${t.provider} / ${t.model}`);
const clock = (s) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "–:––");

function player(url, label, seconds) {
  const box = document.createElement("div");
  box.className = "player";
  const audio = document.createElement("audio");
  audio.preload = "none";
  audio.setAttribute("aria-label", label);
  const play = document.createElement("button");
  play.type = "button";
  play.className = "btn-icon";
  play.title = "Play";
  play.innerHTML = ICON.play;
  const now = document.createElement("span");
  now.textContent = "0:00";
  const track = document.createElement("div");
  track.className = "track";
  track.innerHTML = "<span></span>";
  const total = document.createElement("span");
  total.textContent = clock(seconds);
  for (const event of ["loadedmetadata", "durationchange"]) audio.addEventListener(event, () => { total.textContent = clock(audio.duration); });
  audio.addEventListener("timeupdate", () => {
    now.textContent = clock(audio.currentTime);
    track.firstChild.style.width = audio.duration ? `${(audio.currentTime / audio.duration) * 100}%` : "0";
  });
  audio.addEventListener("play", () => { play.innerHTML = ICON.pause; play.title = "Pause"; });
  audio.addEventListener("pause", () => { play.innerHTML = ICON.play; play.title = "Play"; });
  audio.addEventListener("ended", () => { audio.currentTime = 0; });
  play.addEventListener("click", async () => {
    if (!audio.src) audio.src = url;
    try { audio.paused ? await audio.play() : audio.pause(); }
    catch (err) { play.title = `Playback failed: ${errorText(err)}`; }
  });
  track.addEventListener("click", (e) => {
    const rect = track.getBoundingClientRect();
    if (audio.duration) audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
  });
  box.append(audio, play, now, track, total);
  return box;
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
  when.textContent = whenLabel(r.created_at);
  when.title = new Date(r.created_at).toLocaleString();
  const model = document.createElement("span");
  model.className = "model";
  model.textContent = latest ? attemptLabel(latest) : "";
  const spacer = document.createElement("span");
  spacer.className = "spacer";
  const copied = document.createElement("span");
  copied.className = "copied-tag";
  copied.textContent = "Copied";
  head.append(when, model, spacer, copied);
  card.append(head);

  if (latest && latest.status !== "ok") {
    const failed = document.createElement("div");
    failed.className = "failed";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = "FAILED";
    const pre = document.createElement("pre");
    pre.textContent = latest.error ?? "";
    failed.append(label, pre);
    card.append(failed);
    card.dataset.copy = latest.error ?? "";
  } else if (latest) {
    const block = document.createElement("div");
    block.className = latest.text ? "transcript" : "transcript empty";
    block.textContent = latest.text || "(no speech detected)";
    block.title = latest.text ? "Click to copy" : "";
    card.append(block);
    card.dataset.copy = latest.text ?? "";
  }

  const row = document.createElement("div");
  row.className = "card-row";
  row.append(player(`/api/recordings/${r.id}/audio`, `Recording ${when.textContent}`, latest?.audio_seconds));
  const download = document.createElement("a");
  download.className = "btn-icon";
  download.href = `/api/recordings/${r.id}/audio`;
  download.download = "";
  download.title = "Download audio";
  download.setAttribute("aria-label", "Download audio");
  download.innerHTML = ICON.download;
  const rowStatus = document.createElement("span");
  rowStatus.className = "status";
  const rowSpacer = document.createElement("span");
  rowSpacer.className = "spacer";
  row.append(download, rowStatus, rowSpacer);
  let attempts = null;
  if (earlier.length > 0) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn ghost attempts-toggle";
    toggle.title = earlier.length === 1 ? `1 earlier attempt · ${attemptLabel(earlier[0])}` : `${earlier.length} earlier attempts`;
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = `${ICON.attempts}${earlier.length}`;
    attempts = document.createElement("div");
    attempts.hidden = true;
    for (const t of earlier) {
      const attempt = document.createElement("div");
      attempt.className = "attempt";
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `${attemptLabel(t)} · ${whenLabel(t.created_at)}`;
      const text = document.createElement("div");
      text.className = t.status === "ok" ? "text" : "text err";
      text.textContent = t.status === "ok" ? t.text || "(no speech detected)" : t.error ?? "";
      attempt.append(meta, text);
      attempts.append(attempt);
    }
    toggle.addEventListener("click", () => {
      attempts.hidden = !attempts.hidden;
      toggle.setAttribute("aria-expanded", String(!attempts.hidden));
    });
    row.append(toggle);
  }
  const select = document.createElement("select");
  select.className = "select quiet retry-model";
  select.title = "Transcribe again with…";
  select.setAttribute("aria-label", "Model for re-transcription");
  const current = latest ? `${latest.provider}/${latest.model}` : null;
  fillModels(select, models.find((m) => m.id !== current)?.id ?? models[0]?.id ?? null, "No models");
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "btn-icon retry";
  retry.title = latest?.status === "error" ? "Try again" : "Transcribe again";
  retry.setAttribute("aria-label", retry.title);
  retry.innerHTML = ICON.retry;
  retry.disabled = select.disabled;
  row.append(select, retry);
  card.append(row);
  if (attempts) card.append(attempts);
  return card;
}

// Poll only the visible History page. The controller keeps one bounded page and
// preserves unchanged cards, including playback and a retry already in progress.
const HISTORY_POLL_MS = 3000;
const history = createHistory({
  list: historyList, newer: el("history-newer"), older: el("history-older"), renderCard,
  onChange(recordings) {
    loadMetrics().catch(() => {});
    recordingsCount = recordings.length;
    emptyState.hidden = recordings.length > 0;
    if (recordings.length === 0) renderStart();
  },
  onError(err) { status.textContent = errorText(err); },
});
const loadHistory = (force = false) => history.refresh(force);
const historyVisible = () => document.visibilityState === "visible" && views.history.hasAttribute("data-active");

setInterval(() => {
  if (historyVisible()) loadHistory().catch(() => {});
}, HISTORY_POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (historyVisible()) loadHistory().catch(() => {});
});

// Copy (click on the transcript) and re-transcribe, delegated so re-renders need no rebinding.
historyList.addEventListener("click", async (e) => {
  const block = e.target.closest(".transcript, .failed");
  if (block) {
    const card = block.closest(".card");
    if (!card.dataset.copy) return;
    const tag = card.querySelector(".copied-tag");
    try {
      await navigator.clipboard.writeText(card.dataset.copy);
      tag.textContent = "Copied";
    } catch (err) {
      tag.textContent = `Copy failed: ${errorText(err)}`;
    }
    card.classList.add("copied");
    clearTimeout(card._copied);
    card._copied = setTimeout(() => card.classList.remove("copied"), 1400);
    return;
  }
  const retry = e.target.closest(".retry");
  if (retry) {
    const card = retry.closest(".card");
    const rowStatus = card.querySelector(".card-row .status");
    const select = card.querySelector(".retry-model");
    const label = select.selectedOptions[0]?.textContent ?? "";
    retry.disabled = true;
    retry.dataset.busy = "true";
    rowStatus.className = "status";
    rowStatus.textContent = `Transcribing with ${label}…`;
    try {
      await api(`/api/recordings/${card.dataset.id}/transcriptions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ model: select.value }),
      });
    } catch (err) {
      rowStatus.className = "status err";
      rowStatus.textContent = errorText(err);
      retry.disabled = false;
      delete retry.dataset.busy;
      return;
    }
    await loadHistory(true);
  }
});

// ---- Dictionary ----
// `dict` mirrors dictionary.json: pinned (the user's, including what their agents sent;
// never changed by the model) and learned (the last accepted proposal, keyed by the
// speech model it was learned for). The table shows pinned and the default model's
// learned list, filtered; every change is saved whole.
let dict = { pinned: { terms: [], replacements: {} }, learned: {} };
let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
let proposal = null;
let filter = "all";
const dictionaryBox = el("dictionary");
const buildBtn = el("build-dictionary");
const buildStatus = el("build-status");
const proposalPanel = el("proposal");
const proposalBody = el("proposal-body");
const empty = () => ({ terms: [], replacements: {} });

const selectFilter = segmentedGroup({ all: el("filter-all"), pinned: el("filter-pinned"), learned: el("filter-learned") }, (name) => { filter = name; renderDictionary(); });

async function loadDictionary() {
  const res = await fetch("/api/dictionary");
  const text = await res.text();
  if (!res.ok) { dictVersion = null; flash(el("dictionary-status"), text, "err"); el("json-editor").hidden = false; return; }
  dict = JSON.parse(text);
  dictVersion = res.headers.get("etag");
  renderDictionary(text);
}

// Every edit sends the whole document, named with the version it was made on. The
// server refuses a save on a stale version (an agent or a hand edit got there first)
// and the fresh document is shown instead.
async function saveDictionary(next) {
  const headers = { "content-type": "application/json" };
  if (dictVersion) headers["if-match"] = dictVersion;
  const res = await fetch("/api/dictionary", { method: "PUT", headers, body: JSON.stringify(next) });
  const text = await res.text();
  if (res.status === 409) {
    await loadDictionary();
    flash(el("dictionary-status"), `${text} Reloaded; please redo that change.`, "err");
    return false;
  }
  if (!res.ok) { flash(el("dictionary-status"), text, "err"); flash(buildStatus, text, "err"); return false; }
  dict = JSON.parse(text);
  dictVersion = res.headers.get("etag");
  renderDictionary(text);
  return true;
}

// The default model's learned list, made if absent. Pinned is shared by every model.
function learnedOf(doc) {
  if (!defaultModel) return empty();
  return (doc.learned[defaultModel.id] ??= empty());
}
const clone = () => JSON.parse(JSON.stringify(dict));

function entryRow(source, kind, heard, meant) {
  const row = document.createElement("div");
  row.className = "entry-row";
  const kindEl = document.createElement("span");
  kindEl.className = "kind";
  kindEl.textContent = kind === "term" ? "TERM" : "REPLACE";
  const what = document.createElement("span");
  what.className = "what";
  if (kind === "term") what.textContent = heard;
  else {
    what.innerHTML = `<span class="heard"></span><span class="arrow">→</span><span class="meant"></span>`;
    what.querySelector(".heard").textContent = heard;
    what.querySelector(".meant").textContent = meant;
  }
  const src = document.createElement("span");
  src.className = source === "learned" ? "source learned" : "source";
  src.textContent = source === "learned" ? "Learned" : "Pinned";
  const actions = document.createElement("span");
  actions.className = "actions";
  if (source === "learned") {
    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "btn ghost sm";
    pin.textContent = "Pin";
    pin.title = "Pin: keep it for every model";
    pin.addEventListener("click", () => moveToPinned(kind, heard, meant));
    actions.append(pin);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn-icon";
  remove.title = "Remove";
  remove.setAttribute("aria-label", "Remove");
  remove.innerHTML = ICON.remove;
  remove.addEventListener("click", () => removeEntry(source, kind, heard));
  actions.append(remove);
  row.append(kindEl, what, src, actions);
  return row;
}

function renderDictionary(jsonText) {
  const learned = learnedOf(dict);
  const rows = [];
  if (filter !== "learned") {
    rows.push(...dict.pinned.terms.map((t) => entryRow("pinned", "term", t)));
    rows.push(...Object.entries(dict.pinned.replacements).map(([h, m]) => entryRow("pinned", "replace", h, m)));
  }
  if (filter !== "pinned") {
    rows.push(...learned.terms.map((t) => entryRow("learned", "term", t)));
    rows.push(...Object.entries(learned.replacements).map(([h, m]) => entryRow("learned", "replace", h, m)));
  }
  if (rows.length === 0) {
    const none = document.createElement("div");
    none.className = "entry-row none";
    none.textContent = filter === "learned" ? "Nothing learned for this model yet. Build from history proposes a list." : "Nothing here yet.";
    rows.push(none);
  }
  el("dict-rows").replaceChildren(...rows);
  const learnedCount = learned.terms.length + Object.keys(learned.replacements).length;
  el("filter-learned").textContent = defaultModel ? `Learned · ${defaultModel.label.replace(" / ", " · ")}` : "Learned";
  el("pin-all").hidden = learnedCount === 0;
  buildBtn.textContent = learnedCount ? "Refine from history" : "Build from history";
  buildBtn.title = settings?.dictionaryModel
    ? `Send this model's recent transcripts to ${settings.dictionaryModel} and review a proposal; nothing is saved before Accept`
    : "Needs an Anthropic or OpenAI key in Settings › Providers";
  const budget = termBudgetText(learned);
  el("term-budget").textContent = budget;
  el("term-budget-row").hidden = !budget;
  if (jsonText !== undefined) dictionaryBox.value = jsonText;
}

function termBudgetText(learned) {
  if (!defaultModel) return "";
  const limit = defaultModel.term_limit;
  const pinned = dict.pinned.terms.length;
  const inUse = pinned + learned.terms.length;
  if (limit === null) return `${defaultModel.label.split(" / ")[0]} takes no terms · replacements only · ${inUse} pinned words unused here`;
  const line = `${inUse} of ${limit} terms in use · ${pinned} pinned`;
  if (pinned >= limit) return `${line} · full: remove pinned terms to make room`;
  if (inUse >= limit) return `${line} · full`;
  return line;
}

async function addToPinned(kind, heard, meant) {
  const next = clone();
  if (kind === "term") { if (!next.pinned.terms.includes(heard)) next.pinned.terms.push(heard); }
  else next.pinned.replacements[heard] = meant;
  return saveDictionary(next);
}
async function removeEntry(source, kind, heard) {
  const next = clone();
  const from = source === "pinned" ? next.pinned : learnedOf(next);
  if (kind === "term") from.terms = from.terms.filter((t) => t !== heard);
  else delete from.replacements[heard];
  await saveDictionary(next);
}
async function moveToPinned(kind, heard, meant) {
  const next = clone();
  const from = learnedOf(next);
  if (kind === "term") {
    from.terms = from.terms.filter((t) => t !== heard);
    if (!next.pinned.terms.includes(heard)) next.pinned.terms.push(heard);
  } else {
    delete from.replacements[heard];
    next.pinned.replacements[heard] = meant;
  }
  await saveDictionary(next);
}
el("pin-all").addEventListener("click", async () => {
  if (!defaultModel) return;
  const next = clone();
  const learned = learnedOf(next);
  for (const t of learned.terms) if (!next.pinned.terms.includes(t)) next.pinned.terms.push(t);
  Object.assign(next.pinned.replacements, learned.replacements);
  delete next.learned[defaultModel.id];
  await saveDictionary(next);
});

// Add: a word, or a heard → meant fix; both are pinned.
segmentedGroup({ word: el("add-word-mode"), fix: el("add-fix-mode") }, (name) => {
  el("add-word-row").hidden = name !== "word";
  el("add-fix-row").hidden = name !== "fix";
});
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

// JSON editor and help panel
el("json-toggle").addEventListener("click", () => {
  const editor = el("json-editor");
  editor.hidden = !editor.hidden;
  el("json-toggle").setAttribute("aria-expanded", String(!editor.hidden));
});
el("save-dictionary").addEventListener("click", async () => {
  let parsed;
  try { parsed = JSON.parse(dictionaryBox.value); } catch (err) { flash(el("dictionary-status"), `Not valid JSON: ${err.message}`, "err"); return; }
  if (await saveDictionary(parsed)) flash(el("dictionary-status"), "Saved", "ok");
});
function showHelp(open) {
  el("help-panel").hidden = !open;
  el("help-toggle").setAttribute("aria-expanded", String(open));
}
el("help-toggle").addEventListener("click", () => showHelp(el("help-panel").hidden));
el("help-more-toggle").addEventListener("click", () => {
  const more = el("help-more");
  more.hidden = !more.hidden;
  el("help-more-toggle").textContent = more.hidden ? "More detail" : "Less";
});

// Build: the model proposes a learned list for the default speech model, from that
// model's transcripts; nothing changes until Accept.
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
  const groups = [
    ["Added terms", p.added.terms, "add"],
    ["Added replacements", Object.entries(p.added.replacements).map(([h, m]) => `${h} → ${m}`), "add"],
    ["Removed terms", p.removed.terms, "remove"],
    ["Removed replacements", Object.entries(p.removed.replacements).map(([h, m]) => `${h} → ${m}`), "remove"],
  ];
  let any = false;
  for (const [title, items, cls] of groups) {
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
  if (p.dropped_terms > 0) {
    const note = document.createElement("p");
    note.className = "nothing";
    note.textContent = `${p.dropped_terms} more proposed terms did not fit: this model takes ${p.budget.limit} and ${p.budget.pinned} are pinned. Remove some to make room.`;
    proposalBody.append(note);
  }
  proposalPanel.hidden = false;
}
buildBtn.addEventListener("click", async () => {
  buildBtn.disabled = true;
  buildStatus.className = "caption save-status show";
  buildStatus.textContent = "Asking the model… this can take a minute.";
  try {
    const res = await fetch("/api/dictionary/build", { method: "POST" });
    const text = await res.text();
    if (!res.ok) throw new Error(text);
    proposal = JSON.parse(text);
    buildStatus.classList.remove("show");
    renderProposal(proposal);
  } catch (err) {
    flash(buildStatus, errorText(err), "err");
  } finally {
    buildBtn.disabled = false;
  }
});
el("accept-proposal").addEventListener("click", async () => {
  if (!proposal) return;
  const next = clone();
  next.learned[proposal.model] = proposal.learned;
  if (await saveDictionary(next)) {
    proposal = null;
    proposalPanel.hidden = true;
    flash(buildStatus, "Accepted", "ok");
  }
});
el("discard-proposal").addEventListener("click", () => { proposal = null; proposalPanel.hidden = true; });

// ---- Start ----
await loadSettings();
if (location.hash === "#settings") show("settings");
else if (location.hash === "#dictionary") show("dictionary");
else show("history");
