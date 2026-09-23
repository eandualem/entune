// Dictum's window: history, dictionary, settings, and recording from here. Plain DOM
// code against the local API; no framework, no build step. Layout and tokens follow
// the design of 2026-09-18; every colour and size lives in tokens.css.

import { createHistory } from "./history.js";
import { renderCard } from "./history-card.js";
import { createDictionary } from "./dictionary-view.js";
import { createSettings } from "./settings-view.js";
import { createPermissions } from "./permissions-view.js";
import { initRecording } from "./recording.js";
import { ICON, api, el, errorText, fillModels, segmentedGroup, shortModel } from "./ui.js";

const status = el("status");
const modelSelect = el("model");
const fastWrap = el("fast-mode-wrap");
const fastInput = el("fast-mode");
const historyList = el("history");
const emptyState = el("empty");
const stepsList = el("steps");

let models = [];
let defaultModel = null; // {id, label} from /api/models, or null
let settings = null; // the last /api/settings answer
let shortcuts = { hold: null, toggle: null };
let recordingsCount = 0;

// The Mac's real window controls share the toolbar. Browser windows keep their
// own chrome; only the native bridge adds the space and follows text scaling.
// The bridge may be ready before this module runs, so check as well as listen.
function alignWindowButtons() {
  if (!window.pywebview?.api?.layout_titlebar || alignWindowButtons.done) return;
  alignWindowButtons.done = true;
  document.documentElement.classList.add("native-mac");
  const tabs = document.querySelector(".toolbar > .segmented");
  new ResizeObserver(() => {
    const box = tabs.getBoundingClientRect();
    window.pywebview.api.layout_titlebar((box.top + box.height / 2) * 2);
  }).observe(document.querySelector(".toolbar"));
}
window.addEventListener("pywebviewready", alignWindowButtons);
alignWindowButtons();

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
  if (!hintsInput.checked) dictionary.showHelp(false);
});

// ---- Views and the settings rail ----
const views = { history: el("view-history"), dictionary: el("view-dictionary"), settings: el("view-settings") };
const selectTab = segmentedGroup({ history: el("tab-history"), dictionary: el("tab-dictionary"), settings: el("tab-settings") }, showView);
function showView(name) {
  for (const key in views) views[key].toggleAttribute("data-active", key === name);
  views[name].scrollTop = 0;
  if (name === "history") loadHistory().catch((err) => { status.textContent = errorText(err); });
  if (name === "dictionary") dictionary.refreshAudio().catch((err) => { status.textContent = errorText(err); });
  if (name === "settings") settingsView.refreshJev().catch((err) => { status.textContent = errorText(err); });
  if (name === "settings" && sections.agents.hasAttribute("data-active")) settingsView.refreshCorrections();
  permissionsView.setActive(name === "settings" && sections.general.hasAttribute("data-active"));
}
function show(name) { selectTab(name); showView(name); }

const sections = { general: el("settings-general"), providers: el("settings-providers"), local: el("settings-local"), privacy: el("settings-privacy"), agents: el("settings-agents") };
const selectSection = segmentedGroup({ general: el("sec-general"), providers: el("sec-providers"), local: el("sec-local"), privacy: el("sec-privacy"), agents: el("sec-agents") }, showSection);
function showSection(name) {
  for (const key in sections) sections[key].toggleAttribute("data-active", key === name);
  if (name === "agents") settingsView.refreshCorrections();
  permissionsView.setActive(name === "general" && views.settings.hasAttribute("data-active"));
}
function openSettings(section) { show("settings"); selectSection(section); showSection(section); }

// ---- Models: one default, picked in the toolbar; it applies at once ----
async function loadModels() {
  models = await api("/api/models");
  defaultModel = models.find((m) => m.default) ?? null;
  fillModels(models, modelSelect, defaultModel?.id ?? null, "No models: add a key or download one");
  if (!defaultModel && models.length > 0) modelSelect.prepend(new Option("Pick a model", "", true, true));
  const provider = defaultModel?.id.split("/")[0];
  const streams = (settings?.providers ?? []).some((p) => p.id === provider && p.streams);
  fastInput.disabled = !streams;
  fastWrap.classList.toggle("off", !streams);
  fastWrap.title = streams
    ? "Fast mode: upload while recording, so a long dictation is transcribed as soon as you stop"
    : "Fast mode: only AssemblyAI takes the audio while you record; pick it to use fast mode";
  if (!status.textContent || status.textContent === "Ready") status.textContent = defaultModel ? "Ready" : "";
  await dictionary.load();
  renderStart();
  // A model change only changes the pickers, never the cards or their audio.
  for (const select of historyList.querySelectorAll(".retry-model")) {
    fillModels(models, select, defaultModel?.id ?? select.value, "No models");
    const retry = select.closest(".card-row").querySelector(".retry");
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

fastInput.addEventListener("change", () => settingsView.save({ fastMode: fastInput.checked }, null));

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

function audioLength(seconds) {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
async function loadMetrics() {
  const rows = await api("/api/metrics");
  metricsToggle.hidden = rows.length === 0;
  if (rows.length === 0) showMetrics(false);
  const cell = (text, title = "") => Object.assign(document.createElement("span"), { textContent: text, title });
  metricsRows.replaceChildren(
    ...rows.map((m) => {
      const row = document.createElement("div");
      row.className = "perf-grid";
      const name = cell(shortModel(m.provider_name, m.provider, m.model), `${m.provider}/${m.model}${m.fast ? " · Fast mode" : ""}`);
      name.className = "perf-model";
      if (m.fast) name.append(Object.assign(document.createElement("span"), { className: "tag", textContent: "Fast" }));
      const speed = m.seconds_per_minute === null
        ? cell("–", "No successful run with a known length yet")
        : cell(`1 min → ${m.seconds_per_minute < 10 ? m.seconds_per_minute.toFixed(1) : Math.round(m.seconds_per_minute)} s`,
          `Transcription wait per minute of audio, from ${m.timed_runs} run${m.timed_runs === 1 ? "" : "s"} with a known length. Longer and shorter clips vary.`);
      const corrections = m.checked === 0 || m.words === 0
        ? cell("–", "No dictation with the dictionary on yet")
        : cell(`${m.replacements ? (100 * m.replacements / m.words).toFixed(1) : "0"} / 100 words`,
          `${m.replacements} replacement${m.replacements === 1 ? "" : "s"} in ${m.corrected} of ${m.checked} dictations checked by the dictionary`);
      const failed = m.runs - m.ok;
      const used = cell(`${m.runs} run${m.runs === 1 ? "" : "s"} · ${m.audio_seconds ? audioLength(m.audio_seconds) : "length unknown"}`,
        failed ? `${failed} failed` : "");
      if (failed) used.append(Object.assign(document.createElement("span"), { className: "perf-failed", textContent: ` · ${failed} failed` }));
      row.append(name, speed, corrections, used);
      return row;
    }),
  );
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

// Poll only the visible History page. The controller keeps one bounded page and
// preserves unchanged cards, including playback and a retry already in progress.
const HISTORY_POLL_MS = 3000;
const history = createHistory({
  list: historyList, newer: el("history-newer"), older: el("history-older"), renderCard: (recording) => renderCard(recording, models),
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
  const recovery = e.target.closest(".safe-copy");
  if (recovery) {
    const card = recovery.closest(".card"), message = card.querySelector(".card-row .status");
    recovery.disabled = true;
    try {
      // WebKit only allows a clipboard write that starts in the click, so hand it the pending text.
      const request = api(`/api/recordings/${card.dataset.id}/transcriptions/${recovery.dataset.attempt}/safe-copy`, { method: "POST" });
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": request.then((r) => new Blob([r.text], { type: "text/plain" })) })]);
      const result = await request;
      message.textContent = `Copied · ${result.replacements} direct mappings · ${result.unresolved} unresolved`;
    } catch (err) { message.textContent = errorText(err); }
    finally { recovery.disabled = false; }
    return;
  }
  const block = e.target.closest(".transcript, .failed, .copy-raw");
  if (block) {
    const card = block.closest(".card");
    const text = block.matches(".copy-raw") ? card.dataset.raw : card.dataset.copy;
    if (text === undefined) return;
    const tag = card.querySelector(".copied-tag");
    try {
      await navigator.clipboard.writeText(text);
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

// ---- Wire the views, then load the saved configuration ----
const dictionary = createDictionary({ getModel: () => defaultModel, getSettings: () => settings, onSettingsChanged: () => settingsView.load() });
const permissionsView = createPermissions();
const settingsView = createSettings({
  async onLoaded(next) {
    settings = next;
    loadMetrics().catch(() => {});
    await loadModels();
  },
  onModelsChanged: loadModels,
  onShortcutsChanged(next) { shortcuts = next; renderStart(); },
  onError(message) { status.textContent = message; },
});
initRecording({
  getModelLabel: () => modelSelect.selectedOptions[0]?.textContent ?? "default model",
  onStatus(message) { status.textContent = message; },
  async onUploaded() { show("history"); await history.latest(); },
});
await settingsView.load();
if (location.hash === "#settings") show("settings");
else if (location.hash === "#dictionary") show("dictionary");
else show("history");
