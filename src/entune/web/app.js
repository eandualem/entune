// Entune's window: history, dictionary, settings, and recording from here. Plain DOM
// code against the local API; no framework, no build step. Layout and tokens follow
// the design of 2026-09-18; every colour and size lives in tokens.css.

import { createHistory } from "./history.js";
import { placeDetails, renderCard } from "./history-card.js";
import { createDictionary } from "./dictionary-view.js";
import { createSettings } from "./settings-view.js";
import { openApp } from "./intro.js";
import { LINUX_LABELS, LINUX_SETUP, copySetup, createPermissions } from "./permissions-view.js";
import { initRecording } from "./recording.js";
import { ICON, THIS_DEVICE, api, el, errorText, figure, fillModels, segmentedGroup } from "./ui.js";

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
let desktop = null; // the desktop app (true) or a browser page (false), once /api/status answers
let system = null; // "macos", "windows", "linux" or "other"
let permissionStates = {};

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

// ---- Words for this computer: the page is written for a Mac ----
// Relabel the page's own text once, before any transcript, dictionary entry or provider
// error is shown: those stay verbatim. Text the views write later uses THIS_DEVICE.
if (THIS_DEVICE !== "this Mac") {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeValue.includes("this Mac")) node.nodeValue = node.nodeValue.replaceAll("this Mac", THIS_DEVICE);
  }
  for (const element of document.querySelectorAll("[placeholder], [aria-label], [title]")) {
    for (const name of ["placeholder", "aria-label", "title"]) {
      const value = element.getAttribute(name);
      if (value?.includes("this Mac")) element.setAttribute(name, value.replaceAll("this Mac", THIS_DEVICE));
    }
  }
}

// ---- Preferences kept in this window: theme and text size ----
function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  try { theme === "system" ? localStorage.removeItem("theme") : localStorage.setItem("theme", theme); } catch (e) {}
  shareTheme();
}
// The Mac app takes the page's theme too, so its translucent material and the recording
// pill match; "system" leaves it to macOS.
function shareTheme() {
  const theme = document.documentElement.dataset.theme ?? "system";
  window.pywebview?.api?.set_appearance?.(theme);
}
window.addEventListener("pywebviewready", shareTheme);
shareTheme();
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

// ---- Views and the settings rail ----
const views = { history: el("view-history"), models: el("view-models"), dictionary: el("view-dictionary"), settings: el("view-settings") };
const selectTab = segmentedGroup({ history: el("tab-history"), models: el("tab-models"), dictionary: el("tab-dictionary"), settings: el("tab-settings") }, showView);
function showView(name) {
  for (const key in views) views[key].toggleAttribute("data-active", key === name);
  views[name].scrollTop = 0;
  if (name === "history") loadHistory().catch((err) => { status.textContent = errorText(err); });
  if (name === "models") loadMetrics().catch((err) => { status.textContent = errorText(err); });
  if (name === "dictionary") dictionary.refreshAudio().catch((err) => { status.textContent = errorText(err); });
  if (name === "models") settingsView.refreshJev().catch((err) => { status.textContent = errorText(err); });
  if (name === "settings" && sections.integrations.hasAttribute("data-active")) settingsView.refreshCorrections();
  permissionsView.setActive(name === "settings" && sections.general.hasAttribute("data-active"));
  permissionsView.setActive(name === "history" && !emptyState.hidden, "start");
}
function show(name) { selectTab(name); showView(name); }

// The Models page: three jobs, one at a time, like Settings.
const MODEL_SECTIONS = ["cloud", "local", "performance"];
const modelSections = Object.fromEntries(MODEL_SECTIONS.map((name) => [name, el(`models-${name}`)]));
const pickModelSection = segmentedGroup(Object.fromEntries(MODEL_SECTIONS.map((name) => [name, el(`msec-${name}`)])), showModelSection);
function showModelSection(name) {
  for (const key in modelSections) modelSections[key].toggleAttribute("data-active", key === name);
}
function openModels(section) { show("models"); pickModelSection(section); showModelSection(section); }

const SECTIONS = ["general", "processing", "dictionary", "integrations", "privacy"];
const sections = Object.fromEntries(SECTIONS.map((name) => [name, el(`settings-${name}`)]));
const selectSection = segmentedGroup(Object.fromEntries(SECTIONS.map((name) => [name, el(`sec-${name}`)])), showSection);
function showSection(name) {
  for (const key in sections) sections[key].toggleAttribute("data-active", key === name);
  if (name === "integrations") settingsView.refreshCorrections();
  permissionsView.setActive(name === "general" && views.settings.hasAttribute("data-active"));
}
// Help opens as a modal guide over the page; Escape or the close button dismisses it
// and focus goes back to the button that opened it.
for (const button of document.querySelectorAll("[data-help]")) {
  button.addEventListener("click", () => {
    const guide = el(button.dataset.help);
    guide.showModal();
    guide.querySelector(".guide").scrollTop = 0;
  });
}

function openSettings(section) { show("settings"); selectSection(section); showSection(section); }

// ---- Models: one default, picked in the toolbar; it applies at once ----
async function loadModels() {
  const hadDefault = Boolean(defaultModel);
  models = await api("/api/models");
  defaultModel = models.find((m) => m.default) ?? null;
  // The only model someone has set up is the one they mean to use.
  if (!defaultModel && models.length === 1) {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ defaultModel: models[0].id }) });
    models = await api("/api/models");
    defaultModel = models.find((m) => m.default) ?? null;
  }
  fillModels(models, modelSelect, defaultModel?.id ?? null, "No models: add a key or download one");
  if (!defaultModel && models.length > 0) modelSelect.prepend(new Option("Pick a model", "", true, true));
  fastInput.disabled = !defaultModel || fastSaving;
  fastWrap.classList.toggle("off", !defaultModel);
  fastWrap.title = defaultModel
    ? "Fast mode: transcribe at each pause while you speak, so a long dictation is ready soon after you stop"
    : "Fast mode: choose a speech model first";
  if (!status.textContent || status.textContent === "Ready") status.textContent = defaultModel ? "Ready" : "";
  await dictionary.load();
  renderStart();
  // That was step one of Get started: go back for the next step.
  if (!hadDefault && defaultModel && !emptyState.hidden && views.models.hasAttribute("data-active")) show("history");
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
  await loadModels().catch((err) => { status.textContent = errorText(err); });
});

// One fast-mode save at a time: the switch waits for the server's answer, so a refused
// save is undone exactly and never crosses another.
let fastSaving = false;
fastInput.addEventListener("change", async () => {
  fastSaving = fastInput.disabled = true;
  if (!(await settingsView.save({ fastMode: fastInput.checked }, null))) fastInput.checked = !fastInput.checked;
  fastSaving = false;
  fastInput.disabled = fastWrap.classList.contains("off");
});

// ---- Performance by model: the comparison on the Models page ----
const metricsRows = el("metrics-rows");

function audioLength(seconds) {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
async function loadMetrics() {
  const rows = await api("/api/metrics");
  el("metrics-table").hidden = rows.length === 0;
  el("metrics-empty").hidden = rows.length > 0;
  metricsRows.replaceChildren(
    ...rows.map((m) => {
      const row = document.createElement("div");
      row.className = "perf-grid";
      const name = document.createElement("span");
      name.className = "perf-model";
      name.title = `${m.provider}/${m.model}${m.fast ? " · Fast mode" : ""}`;
      name.append(Object.assign(document.createElement("span"), { className: "cell-main", textContent: m.provider_name }));
      if (m.fast) name.firstChild.append(Object.assign(document.createElement("span"), { className: "tag", textContent: "Fast" }));
      name.append(Object.assign(document.createElement("span"), { className: "cell-sub", textContent: m.model }));
      const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
      const speed = figure(m.seconds_per_minute === null ? "–"
        : `${m.seconds_per_minute < 10 ? m.seconds_per_minute.toFixed(1) : Math.round(m.seconds_per_minute)} s`, "");
      const corrections = m.checked === 0 || m.words === 0
        ? figure("–", "")
        : figure(`${m.replacements ? (100 * m.replacements / m.words).toFixed(1) : "0"} per 100 words`, `${m.corrected} of ${plural(m.checked, "dictation")} changed`);
      const failed = m.runs - m.ok;
      const used = figure(String(m.runs), failed ? `${failed} failed` : "", failed ? "perf-failed" : "");
      const audio = figure(m.audio_seconds ? audioLength(m.audio_seconds) : "–", "");
      row.append(name, speed, corrections, used, audio);
      return row;
    }),
  );
}

// ---- Getting started: the empty history, as steps that tick themselves off ----
// In order: a speech model, then (in the Mac app) the permissions, then a shortcut.
// Input Monitoring last: macOS asks to quit and reopen after it, and the reopened app sees all three.
const PERMISSIONS = { microphone: "Microphone", accessibility: "Accessibility", inputMonitoring: "Input Monitoring" };
function renderStart() {
  const kbd = (keys) => Object.assign(document.createElement("kbd"), { textContent: keys });
  const haveShortcut = Boolean(shortcuts.hold || shortcuts.toggle);
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
  const steps = [
    step(Boolean(defaultModel), "Set up a speech model", ["A cloud service's key, or a model that runs on your computer. The first one becomes your default."], { label: "Models", go: () => openModels("cloud") }),
  ];
  if (desktop) {
    // Not yet reported (the app is still starting) is not the same as all allowed.
    const names = Object.keys(permissionStates);
    const allowed = names.length > 0 && names.every((name) => permissionStates[name] === "granted");
    const li = system === "macos"
      ? step(allowed, "Allow Entune on this Mac", ["The microphone to record; Accessibility and Input Monitoring so your shortcut works in any app and the text is typed there."], null)
      : system === "linux"
        ? step(allowed, "Allow keyboard access", ["So your shortcut works in any app and the text is typed there. Run this once in a terminal, then log out and back in:"], null)
        : step(allowed, "Allow the microphone", ["Windows lets desktop apps use the microphone unless it is turned off in Privacy settings."], null);
    if (!allowed) li.querySelector(".what").append(permissionRows());
    steps.push(li);
  }
  const dictate = [];
  if (desktop && haveShortcut) {
    dictate.push("Anywhere, ");
    if (shortcuts.hold) dictate.push("hold ", kbd(shortcuts.hold));
    if (shortcuts.hold && shortcuts.toggle) dictate.push(" or ");
    if (shortcuts.toggle) dictate.push("press ", kbd(shortcuts.toggle));
    dictate.push(" and speak; the text is typed where you are and copied. Or press Record above.");
  } else if (desktop) {
    dictate.push("Choose the key you hold while you speak, in any app. Or press Record above.");
  } else {
    dictate.push("Press Record above and speak.");
  }
  const needsShortcut = desktop && !haveShortcut;
  steps.push(step(recordingsCount > 0, needsShortcut ? "Set a shortcut and dictate" : "Dictate", dictate, needsShortcut ? { label: "Set a shortcut", go: () => openSettings("general") } : null));
  stepsList.replaceChildren(...steps);
}

// One row per macOS permission, each asking for itself.
function permissionRows() {
  const rows = document.createElement("span");
  rows.className = "permission-steps";
  if (system === "linux") {
    const command = Object.assign(document.createElement("pre"), { className: "code-block", textContent: LINUX_SETUP });
    const copy = Object.assign(document.createElement("button"), { type: "button", className: "btn ghost sm", textContent: "Copy command" });
    copy.addEventListener("click", () => copySetup(copy));
    rows.append(command, copy);
  }
  for (const [name, label] of Object.entries(PERMISSIONS)) {
    if (!(name in permissionStates)) continue; // not something this system asks for
    const row = document.createElement("span");
    row.className = "permission-step";
    row.append(system === "linux" ? LINUX_LABELS[name] : label);
    const state = permissionStates[name];
    if (state === "granted") {
      row.append(Object.assign(document.createElement("span"), { className: "caption", textContent: "Allowed" }));
    } else if (system === "linux") {
      row.append(Object.assign(document.createElement("span"), { className: "caption", textContent: "Not yet allowed" }));
    } else {
      const button = Object.assign(document.createElement("button"), {
        type: "button", className: "btn ghost sm",
        textContent: ["requested", "denied", "restricted"].includes(state) || system !== "macos" ? "Open Settings…" : "Allow…",
      });
      button.addEventListener("click", async () => {
        button.disabled = true;
        try { await permissionsView.request(name); }
        catch (err) { status.textContent = errorText(err); }
        finally { button.disabled = false; }
      });
      row.append(button);
    }
    rows.append(row);
  }
  return rows;
}

// Poll only the visible History page. The controller keeps one bounded page and
// preserves unchanged cards, including playback and a retry already in progress.
const HISTORY_POLL_MS = 3000;
const history = createHistory({
  list: historyList, newer: el("history-newer"), older: el("history-older"), renderCard: (recording) => renderCard(recording, models),
  onChange(recordings) {
    placeDetails(); // closes the details popover if its card was redrawn
    recordingsCount = recordings.length;
    emptyState.hidden = recordings.length > 0;
    if (recordings.length === 0) renderStart();
    permissionsView.setActive(recordings.length === 0 && views.history.hasAttribute("data-active"), "start");
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

// Anonymous mode blurs History for screen sharing; the newest dictation stays readable
// until it is copied. Both choices are remembered in this window.
const anonymous = el("anonymous");
const remembered = (key, value) => { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch { return null; } };
const newestCard = () => el("history-newer").hidden ? historyList.querySelector(".card") : null;
function revealNewest() {
  const on = anonymous.checked;
  document.documentElement.toggleAttribute("data-anonymous", on);
  const newest = newestCard();
  for (const card of historyList.querySelectorAll(".card")) {
    card.toggleAttribute("data-reveal", on && card === newest && card.dataset.id !== remembered("entune.anonymous.copied"));
  }
}
// The newest dictation blurs once it has been copied, by any copy action, also one made
// before anonymous mode was switched on.
function copiedNewest(card) {
  if (card === newestCard()) { remembered("entune.anonymous.copied", card.dataset.id); revealNewest(); }
}
anonymous.checked = remembered("entune.anonymous") === "on";
anonymous.addEventListener("change", () => { remembered("entune.anonymous", anonymous.checked ? "on" : "off"); revealNewest(); });
new MutationObserver(revealNewest).observe(historyList, { childList: true });
revealNewest();

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
      copiedNewest(card);
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
      copiedNewest(card);
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
    // Visible while it runs: a spinner and the model's name, and a light along the card.
    card.classList.add("retrying");
    rowStatus.className = "status working";
    rowStatus.replaceChildren(Object.assign(document.createElement("span"), { className: "spinner" }), `Transcribing with ${label}…`);
    try {
      await api(`/api/recordings/${card.dataset.id}/transcriptions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ model: select.value }),
      });
    } catch (err) {
      card.classList.remove("retrying");
      rowStatus.className = "status err";
      rowStatus.textContent = errorText(err);
      retry.disabled = false;
      delete retry.dataset.busy;
      return;
    }
    card.classList.remove("retrying");
    await loadHistory(true);
  }
});

// ---- Wire the views, then load the saved configuration ----
const dictionary = createDictionary({ getModel: () => defaultModel, getSettings: () => settings, onSettingsChanged: () => settingsView.load(), openSettings });
const permissionsView = createPermissions({
  onChange(answer) { desktop = answer.desktop; system = answer.system; permissionStates = answer.permissions; renderStart(); },
});
const settingsView = createSettings({
  async onLoaded(next) {
    settings = next;
    loadMetrics().catch(() => {});
    await loadModels();
  },
  onModelsChanged: loadModels,
  onShortcutsChanged(next) {
    const had = Boolean(shortcuts.hold || shortcuts.toggle);
    shortcuts = next;
    renderStart();
    // The first shortcut was the last setup step: go back to Get started to dictate.
    if (!had && (next.hold || next.toggle) && !emptyState.hidden && views.settings.hasAttribute("data-active")) show("history");
  },
  onError(message) { status.textContent = message; },
});
initRecording({
  getModelLabel: () => modelSelect.selectedOptions[0]?.textContent ?? "default model",
  onStatus(message) { status.textContent = message; },
  async onUploaded() { show("history"); await history.latest(); },
});
// A first launch opens through the introduction, which covers the loading below.
openApp();
// A failed load is shown; the page still opens its view instead of stopping here.
await settingsView.load().catch((err) => { status.textContent = errorText(err); });
try {
  if (sessionStorage.getItem("entune-reset")) { sessionStorage.removeItem("entune-reset"); status.textContent = "All Entune data was deleted."; }
} catch (e) {}
if (location.hash === "#settings") show("settings");
else if (location.hash === "#models") show("models");
else if (location.hash === "#dictionary") show("dictionary");
else show("history");
