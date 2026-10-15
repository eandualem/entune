import { THIS_DEVICE, api, el, errorText, flash, modelName, segmentedGroup } from "./ui.js";
import { createAudioOnboarding, duration } from "./audio-onboarding.js";
import { createHistoryReuse } from "./history-reuse.js";
import { createDictionaryBuild } from "./dictionary-build.js";

// The dictionary owns its document, revision and pending proposal. Model selection
// stays in the app; getters read the current selection when an action is made.
export function createDictionary({ getModel, getSettings, onSettingsChanged, openSettings }) {
  // The words the person uses, each defined once and shared by every speech model.
  let dict = { version: 4, words: [] };
  let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
  let savedText = ""; // the JSON view's last loaded or saved text
  let draftBase = null; // the revision the JSON view's unsaved text was started from
  let importing = false;
  let building = false;
  let run = { phase: "idle" };
  let filter = "all"; // or "check": only the words still to check
  let query = "";
  let draft = null; // the word open in the editor: its ID and its JSON as opened (null for a new one)
  let saving = false; // one editor save at a time
  let proposalChanges = [];
  let missingKey = false; // the saved dictionary model's provider has no key
  const modelSelect = el("dictionary-model");
  const buildStatus = el("build-status");
  const proposalBody = el("proposal-body");
  const wordDrawer = el("word-drawer");
  const suggestDrawer = el("suggest-drawer");
  const jsonBox = el("dictionary");
  // The suggestion model's reasoning effort, by the provider's level names, remembered in
  // this browser. High is the default; see drawEffect.
  const effortButtons = Object.fromEntries([...el("suggest-effort").children].map((b) => [b.dataset.effort, b]));
  let effort = "high";
  try {
    const saved = localStorage.getItem("entune.suggest.effort");
    if (saved in effortButtons) effort = saved;
  } catch { /* storage unavailable: the default stands */ }
  const runSettings = () => ({ effort });
  const onboarding = createAudioOnboarding({
    getModel, getSettings, getRunSettings: runSettings,
    onBuild(selection) { return builds.start("audio", { ...selection, ...runSettings() }); },
    onBusy(value) { importing = value; gate(); },
    onChange: () => drawSheet(),
  });
  const historyReuse = createHistoryReuse({ getSettings, getRunSettings: runSettings, onChange: () => drawSheet() });

  const builds = createDictionaryBuild({
    onBusy(value) { building = value; gate(); onboarding.setBuildBusy(value); lockEditors(); },
    onState(value) { showRun(value); },
    // A proposal is reviewed beside the dictionary as it is now: an agent may have
    // changed it since this page loaded it.
    async onProposal(value) { if (value) { await loadDictionary(false); renderProposal(value); } else { proposalChanges = []; el("proposal").hidden = true; } },
    onAccepted: () => loadDictionary(false),
    // The included changes, as the review left them; the ones left out are dismissed.
    getSelected: () => proposalChanges.filter((c) => c.included).map((c) => ({ id: c.id, after: c.after })),
    getRunSettings: runSettings,
  });
  // A radio group drawn as a segmented control: the selected segment is also the checked radio.
  const checked = (buttons) => { for (const b of Object.values(buttons)) b.setAttribute("aria-checked", b.getAttribute("aria-selected")); };
  const selectEffort = segmentedGroup(effortButtons, (name) => {
    effort = name;
    checked(effortButtons);
    try { localStorage.setItem("entune.suggest.effort", effort); } catch { /* not remembered */ }
    drawEffect();
    onboarding.redraw();
    historyReuse.redraw();
    drawAdvanced(stage(run) === "setup" && setupSource === "history");
  });
  selectEffort(effort);
  checked(effortButtons);
  // What the choice does is in Help; only a ChatGPT plan's limit per reply is said here.
  function drawEffect() {
    const model = getSettings()?.dictionaryModel ?? "";
    el("run-effect").textContent = model.startsWith("chatgpt:")
      ? "On a ChatGPT plan each reply must finish within about 15 minutes."
      : "";
    el("run-effect").hidden = !el("run-effect").textContent;
  }
  // The speech model that transcribes audio: the same choice as the toolbar's default model.
  const speechSelect = el("suggest-speech");
  function fillSpeech() {
    const toolbar = el("model");
    // "Pick a model" stays while no default is set, so the picker never shows one as chosen.
    const where = (id) => isLocal(id) ? `on ${THIS_DEVICE}` : "cloud";
    speechSelect.replaceChildren(...[...toolbar.options].filter((o) => o.value || !toolbar.value)
      .map((o) => new Option(o.value ? `${o.textContent} · ${where(o.value)}` : o.textContent, o.value, false, o.value === toolbar.value)));
    speechSelect.disabled = building || !speechSelect.options.length;
  }
  speechSelect.addEventListener("change", () => {
    const toolbar = el("model");
    if (!speechSelect.value) return;
    toolbar.value = speechSelect.value;
    toolbar.dispatchEvent(new Event("change"));
  });

  const node = (tag, text = "", cls = "") => Object.assign(document.createElement(tag), { textContent: text, className: cls });
  const button = (text, fn, cls = "btn ghost sm") => {
    const b = node("button", text, cls); b.type = "button"; b.addEventListener("click", fn); return b;
  };
  const words = (text) => text.trim().split(/\s+/).join(" ");
  // "AssemblyAI" from "AssemblyAI / universal-3-5-pro".
  const shortName = (model) => model.label.split(" / ")[0].replace(/\s*\(.*\)$/, "");
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const cap = (text) => text[0].toUpperCase() + text.slice(1);
  // A speech model that runs on this computer: transcribing costs time, never money.
  const isLocal = (id) => Boolean(id && getSettings()?.providers.find((p) => p.id === id.split("/")[0])?.local);
  // A word without a meaning, which Entune can't offer yet, or with one no one confirmed.
  const toCheck = (w) => w.needs_review || !w.meaning.trim();
  // How often the person used a word: the places suggestions found it in their dictations.
  // A word added by hand or by an agent has none.
  const usedText = (w) => w.evidence?.length ? `used ${w.evidence.length}×` : "added by you";
  // The letter a word is listed under: its first letter or digit, without accents.
  const initial = (text) => (text.normalize("NFD").match(/[\p{L}\p{N}]/u)?.[0] ?? "#").toUpperCase();

  // ---- Toasts and confirmation ----
  let toastTimer;
  function toast(text, kind = "") {
    const box = el("dict-toast");
    box.textContent = text;
    box.className = `toast ${kind}`.trim();
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, kind === "err" ? 8000 : 2800);
  }
  function confirmAction(heading, body, ok) {
    const dialog = el("dict-confirm");
    el("confirm-title").textContent = heading;
    el("confirm-body").textContent = body;
    el("confirm-ok").textContent = ok;
    dialog.returnValue = "";
    dialog.showModal();
    return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
  }
  // A side panel closes from its button, Escape, or a click on the page beside it.
  for (const drawer of [wordDrawer, suggestDrawer]) {
    drawer.addEventListener("click", (event) => { if (event.target === drawer) drawer.close(); });
  }

  // ---- Suggestion model ----
  // One dictionary-building model for every learning run; keys stay in Settings.
  function languageName(ref) {
    if (!ref) return null;
    const [provider, ...rest] = ref.split(":");
    const known = getSettings()?.llmProviders.find((p) => p.id === provider)?.models.find((m) => m.id === ref);
    return known?.name ?? rest.join(":");
  }
  function fillModels() {
    const settings = getSettings();
    if (!settings) return;
    const current = settings.dictionaryModel;
    const owner = settings.llmProviders.find((p) => current?.startsWith(`${p.id}:`));
    // The saved model stays shown, with its missing key named, rather than another model.
    const keyed = settings.llmProviders.filter((p) => p.keyHint || p === owner);
    modelSelect.replaceChildren(...keyed.map((provider) => {
      const group = document.createElement("optgroup"); group.label = provider.name;
      const models = [...provider.models];
      if (current?.startsWith(`${provider.id}:`) && !models.some((m) => m.id === current)) models.unshift({ id: current, name: `${current.slice(provider.id.length + 1)} (custom)` });
      group.append(...models.map((m) => new Option(m.name, m.id, false, m.id === current)));
      return group;
    }));
    const note = el("dictionary-model-note");
    missingKey = Boolean(owner && !owner.keyHint);
    note.hidden = keyed.length > 0 && !missingKey;
    note.textContent = !missingKey
      ? "Add a key for Anthropic, OpenAI, Google Gemini, Groq or Mistral, or sign in with ChatGPT, in Settings › Dictionary setup to choose a suggestion model."
      : owner.id === "chatgpt"
        ? `${languageName(current)} needs you signed in with ChatGPT: sign in under Settings › Dictionary setup, or choose another model.`
        : `${languageName(current)} needs an API key for ${owner.name}: add it in Settings › Dictionary setup, or choose another model.`;
    if (!keyed.length) modelSelect.add(new Option("No key yet", ""));
    modelSelect.disabled = building || !keyed.length;
    gate();
    drawEffect();
    onboarding.redraw(); // the audio estimate depends on the suggestion model
    historyReuse.redraw();
  }
  modelSelect.addEventListener("change", async () => {
    try {
      await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ keys: {}, dictionaryModel: modelSelect.value }) });
      await onSettingsChanged();
    } catch (err) { flash(buildStatus, errorText(err), "err"); fillModels(); }
  });
  el("suggest-settings").addEventListener("click", () => { suggestDrawer.close(); openSettings("dictionary"); });
  function gate() {
    const ready = Boolean(getModel() && getSettings()?.dictionaryModel && !missingKey);
    el("build-dictionary").disabled = importing || building || !ready;
    for (const item of document.querySelectorAll("#dict-menu [data-audio]")) item.disabled = building;
  }

  // ---- Loading and saving ----
  let shownModel; // the speech model the audio list was last filtered for
  async function loadDictionary(pollBuild = true) {
    if (getModel()?.id !== shownModel) {
      shownModel = getModel()?.id;
      onboarding.modelChanged();
      historyReuse.load().catch(() => { /* shown again when the panel opens */ });
    }
    dictVersion = null;
    const res = await fetch("/api/dictionary");
    const text = await res.text();
    if (!res.ok) {
      showView("json");
      showJsonError("Could not read dictionary.json.", text);
      return;
    }
    dict = JSON.parse(text);
    dictVersion = res.headers.get("etag");
    renderDictionary(text);
    if (pollBuild) await builds.load();
  }

  // Every edit sends the whole document, named with the version it was made on. The
  // server refuses a save on a stale version (an agent or a hand edit got there first)
  // and the fresh document is shown instead. Explicit JSON repair can replace an
  // unreadable document after confirmation; list edits always need a loaded version.
  // Returns an error message, or "" when saved.
  // `base` is the revision `next` was made from, when that is older than the loaded one.
  async function saveDictionary(next, fromJson = false, base = dictVersion) {
    if (building) return "Apply or discard the open suggestions first.";
    if (!base && !fromJson) {
      await loadDictionary();
      return dictVersion ? "Dictionary reloaded; please redo that change." : "The dictionary could not be loaded.";
    }
    const headers = { "content-type": "application/json" };
    if (base) headers["if-match"] = base;
    else if (!await confirmAction("Replace dictionary.json?", "The dictionary could not be loaded. Saving replaces the file with this JSON.", "Replace")) return "Not saved.";
    const res = await fetch("/api/dictionary", { method: "PUT", headers, body: JSON.stringify(next) });
    const text = await res.text();
    if (res.status === 409) {
      await loadDictionary();
      return `${text} Reloaded; please redo that change.`;
    }
    if (!res.ok) return text;
    dict = JSON.parse(text);
    dictVersion = res.headers.get("etag");
    renderDictionary(text);
    return "";
  }

  // Editing waits while suggestions are open: applying them needs the dictionary they read.
  function lockEditors() {
    el("add-word").disabled = building;
    jsonBox.readOnly = building;
    jsonDirty();
    wordStatus();
    modelSelect.disabled = building || modelSelect.value === "";
  }

  // ---- The list ----
  const selectFilter = segmentedGroup({ all: el("filter-all"), check: el("filter-check") }, (name) => { filter = name; renderRows(); });
  el("dict-search").addEventListener("input", (event) => { query = event.target.value; renderRows(); });

  function renderDictionary(jsonText) {
    // Unsaved text in the JSON view survives a reload (a model change, a word saved in the
    // list); only Save and Revert replace it.
    if (jsonText !== undefined) {
      const dirty = jsonBox.value !== savedText;
      savedText = jsonText;
      if (!dirty) { jsonBox.value = jsonText; draftBase = null; el("json-error").hidden = true; }
    }
    const total = dict.words.length;
    const check = dict.words.filter(toCheck).length;
    el("count-all").textContent = String(total);
    el("count-check").textContent = String(check);
    // The filter shows only while a word needs checking.
    el("dict-filters").hidden = !check;
    if (filter === "check" && !check) { filter = "all"; selectFilter("all"); }
    el("dict-summary").textContent = total
      ? [plural(total, "word"), ...(check ? [`${check} to check`] : []), "the same for every speech model"].join(" · ")
      : "The names and terms you use, for every speech model";
    drawMenu();
    renderRows();
    fillModels();
    if (stage(run) === "setup") fillSpeech();
    lockEditors();
  }

  function renderRows() {
    const q = query.trim().toLowerCase();
    const shown = dict.words
      .filter((w) => (filter !== "check" || toCheck(w)) && (!q || [w.spelling, w.meaning, w.said_as ?? ""].join("\n").toLowerCase().includes(q)))
      .sort((a, b) => initial(a.spelling).localeCompare(initial(b.spelling)) || a.spelling.localeCompare(b.spelling));
    // Words under their first letter, A to Z.
    const groups = [];
    for (const w of shown) {
      const letter = initial(w.spelling);
      if (groups.at(-1)?.letter !== letter) groups.push({ letter, items: node("div", "", "dict-group-items") });
      groups.at(-1).items.append(wordRow(w));
    }
    el("dict-rows").replaceChildren(...groups.map(({ letter, items }) => {
      const group = node("div", "", "dict-group");
      group.append(node("span", letter, "dict-letter"), items);
      return group;
    }));
    el("dict-empty").hidden = shown.length > 0;
    el("empty-title").textContent = q ? "No matches" : "Your dictionary is empty";
    el("empty-body").textContent = q ? "Try a spelling, a meaning, or how a word is said." : "Add the names and terms you use, or get suggestions from your transcripts.";
    el("empty-suggest").hidden = Boolean(q);
  }

  // One line per word: its spelling, meaning, how it is said, its capitals and how often you
  // used it. A click opens it in the editor.
  function wordRow(w) {
    const row = node("button", "", "dict-row");
    row.type = "button";
    row.dataset.word = w.id;
    row.setAttribute("aria-haspopup", "dialog");
    const name = node("span", "", "cell-title");
    name.title = w.spelling;
    name.append(node("span", w.spelling, "title-text"));
    if (toCheck(w)) name.append(node("span", "to check", "check-tag"));
    const meaning = node("span", w.meaning || "No meaning yet", w.meaning ? "cell-desc" : "cell-desc missing");
    meaning.title = w.meaning;
    row.append(name, meaning, node("span", w.said_as ? `said “${w.said_as}”` : "", "cell-said"),
      node("span", w.casing === "fixed" ? "Exact capitals" : "Normal word", "cell-meta"), node("span", usedText(w), "cell-meta"));
    row.addEventListener("click", () => openEditor(w));
    return row;
  }
  // A saved word is shown in the list, whatever the search or filter would have hidden.
  function reveal(id) {
    query = ""; el("dict-search").value = "";
    if (filter !== "all") { filter = "all"; selectFilter("all"); }
    renderRows();
    const row = el("dict-rows").querySelector(`[data-word="${CSS.escape(id)}"]`);
    row?.scrollIntoView({ block: "nearest" });
    row?.focus();
  }

  // ---- The editor: one word, in a side panel ----
  const fields = { spelling: el("word-spelling"), meaning: el("word-meaning"), said_as: el("word-said"), personal_context: el("word-context") };
  const casingButtons = { fixed: el("casing-fixed"), ordinary: el("casing-ordinary") };
  let casing = "fixed";
  const selectCasing = segmentedGroup(casingButtons, (name) => { casing = name; checked(casingButtons); edited(); });
  function openEditor(w = null) {
    draft = { id: w?.id ?? null, seen: w ? JSON.stringify(w) : null, problem: "" };
    el("word-title").textContent = w ? "Edit word" : "New word";
    el("word-save").textContent = w ? "Save" : "Add word";
    el("word-delete").hidden = !w;
    fill(w);
    if (!wordDrawer.open) wordDrawer.showModal();
    fields.spelling.focus();
  }
  // The fields as the word is saved, and what is known about it.
  function fill(w) {
    for (const [key, field] of Object.entries(fields)) field.value = w?.[key] ?? "";
    casing = w?.casing ?? "fixed";
    selectCasing(casing);
    checked(casingButtons);
    const notice = el("word-notice");
    notice.hidden = !w || !toCheck(w);
    notice.textContent = w?.meaning ? "To check: confirm its spelling and meaning, then save." : "To check: give it a meaning. Until it has one, Entune can't offer it.";
    el("word-used").textContent = !w ? "" : w.evidence?.length ? `Used ${w.evidence.length}× in the dictations suggestions read.` : "Added by you or an agent.";
    wordStatus();
  }
  // Why saving waits, or what it would do.
  function wordStatus() {
    if (!draft) return;
    const spelling = words(fields.spelling.value).toLowerCase();
    const same = spelling && dict.words.find((w) => w.id !== draft.id && words(w.spelling).toLowerCase() === spelling);
    const status = el("word-status");
    status.textContent = building ? "Editing waits until the suggestions are applied or discarded."
      : draft.problem || (same ? `${same.spelling} is already a word${same.meaning ? ` (${same.meaning})` : ""}; this adds another sense.` : "");
    status.classList.toggle("err", building || Boolean(draft.problem));
    el("word-save").disabled = building || saving || !spelling;
    el("word-delete").disabled = building || saving;
  }
  function edited() { if (draft) draft.problem = ""; wordStatus(); }
  for (const field of Object.values(fields)) field.addEventListener("input", edited);
  // The meaning is one short line: Enter saves, as in the other fields.
  fields.meaning.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return; // Enter confirms an input-method candidate
    event.preventDefault();
    el("word-form").requestSubmit();
  });
  wordDrawer.addEventListener("close", () => { draft = null; });
  el("word-close").addEventListener("click", () => wordDrawer.close());
  el("word-cancel").addEventListener("click", () => wordDrawer.close());
  el("add-word").addEventListener("click", () => { showView("list"); openEditor(); });
  el("empty-suggest").addEventListener("click", () => openSuggestions("history"));

  // An edit is made on the word as the editor opened it. After a reload it still saves when
  // that word is unchanged; otherwise the editor shows the saved version, so a save never
  // undoes a change made elsewhere.
  function unchanged(d) {
    if (!d.seen) return true;
    const stored = dict.words.find((w) => w.id === d.id);
    if (!stored) { wordDrawer.close(); toast("This word was deleted elsewhere; nothing was saved.", "err"); return false; }
    if (JSON.stringify(stored) === d.seen) return true;
    d.seen = JSON.stringify(stored);
    fill(stored);
    d.problem = "This word changed elsewhere and now shows the saved version. Redo your change.";
    wordStatus();
    return false;
  }
  async function write(d, next, base = dictVersion) {
    saving = true;
    wordStatus();
    const problem = await saveDictionary(next, false, base).finally(() => { saving = false; wordStatus(); });
    // The panel may have been closed while this was saving.
    if (problem && draft !== d) toast(problem, "err");
    return problem;
  }
  el("word-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const d = draft;
    const spelling = words(fields.spelling.value);
    if (!d || !spelling || building || saving || !unchanged(d)) return;
    const meaning = words(fields.meaning.value);
    // Saving is the person's check of the word: only one without a meaning stays to check.
    // Its places of use are kept as they are.
    const word = { ...(d.seen ? JSON.parse(d.seen) : { id: `w_${crypto.randomUUID().replaceAll("-", "")}`, evidence: [] }), spelling, meaning, casing, needs_review: !meaning };
    for (const key of ["said_as", "personal_context"]) {
      const value = words(fields[key].value);
      if (value) word[key] = value; else delete word[key];
    }
    if (JSON.stringify(word) === d.seen) { wordDrawer.close(); return; }
    const next = structuredClone(dict);
    const at = next.words.findIndex((w) => w.id === word.id);
    if (at < 0) next.words.push(word); else next.words[at] = word;
    const version = dictVersion;
    const problem = await write(d, next);
    if (problem) {
      if (draft !== d) return;
      // Refused after a reload: the edit still applies if its word is unchanged.
      if (dictVersion === version) d.problem = problem;
      else if (unchanged(d)) d.problem = "The dictionary changed elsewhere and was reloaded. Save again to apply this.";
      wordStatus();
      return;
    }
    if (draft === d) wordDrawer.close();
    reveal(word.id);
    toast(`${d.seen ? "Saved" : "Added"} ${spelling}`);
  });
  el("word-delete").addEventListener("click", async () => {
    const d = draft;
    if (!d?.seen || building || saving || !unchanged(d)) return;
    const { spelling } = JSON.parse(d.seen);
    // Made on the version shown now: a reload while confirming makes it a conflict, not an overwrite.
    const version = dictVersion;
    const next = { ...dict, words: dict.words.filter((w) => w.id !== d.id) };
    if (!await confirmAction(`Delete “${spelling}”?`, "Entune stops offering it when your speech model is unsure of a word. Suggestions may add it again if you keep using it.", "Delete")) return;
    const problem = await write(d, next, version);
    if (problem) {
      if (draft === d) { d.problem = problem; wordStatus(); }
      return;
    }
    if (draft === d) wordDrawer.close();
    toast(`Deleted ${spelling}`);
  });

  // ---- List and JSON: two views of the same dictionary.json ----
  const selectView = segmentedGroup({ list: el("show-list"), json: el("show-json") }, (name) => showView(name));
  function showView(name) {
    selectView(name);
    el("dict-list").hidden = name !== "list";
    el("json-editor").hidden = name !== "json";
    if (name === "json") { if (!jsonDirty()) jsonBox.value = savedText; jsonDirty(); }
  }
  function showJsonError(heading, text) {
    el("json-error-title").textContent = heading;
    el("json-error-text").textContent = text;
    el("json-error").hidden = false;
  }
  function jsonDirty() {
    const dirty = jsonBox.value !== savedText;
    el("save-dictionary").disabled = building || (!dirty && Boolean(dictVersion));
    el("revert-dictionary").disabled = building || !dirty;
    el("dictionary-status").textContent = building ? "Read-only while suggestions are open."
      : dirty ? "Unsaved changes."
        : "dictionary.json in Entune's data folder: the same words as the list.";
    return dirty;
  }
  // Text started before the dictionary loaded names no revision it could have seen:
  // its first save is refused, and saving again replaces.
  jsonBox.addEventListener("input", () => { draftBase ??= dictVersion ?? "unloaded"; el("json-error").hidden = true; jsonDirty(); });
  el("revert-dictionary").addEventListener("click", () => { jsonBox.value = savedText; draftBase = null; el("json-error").hidden = true; jsonDirty(); });
  el("save-dictionary").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(jsonBox.value); } catch (err) { showJsonError("Not saved.", err.message); return; }
    // Without a readable dictionary there is no revision to name: saving asks to replace it.
    const base = dictVersion ? draftBase ?? dictVersion : null;
    const problem = await saveDictionary(parsed, true, base);
    if (problem) {
      if (!dictVersion) draftBase = null;
      // Changed elsewhere since this text was started: it stays, and saving again replaces.
      if (dictVersion && dictVersion !== base) {
        draftBase = dictVersion;
        showJsonError("Not saved.", "The dictionary changed elsewhere since you started editing. Your text is kept: Revert to see the current version, or Save again to replace it.");
      } else showJsonError("Not saved.", problem);
      jsonDirty();
      return;
    }
    jsonBox.value = savedText;
    draftBase = null;
    el("json-error").hidden = true;
    jsonDirty();
    toast("Saved dictionary.json");
  });

  // ---- Learn from audio: the ⋯ popover ----
  const menu = el("dict-menu");
  const menuButton = el("dict-menu-btn");
  function closeMenu() { menu.hidden = true; menuButton.setAttribute("aria-expanded", "false"); }
  // What learning from audio costs depends on the speech model that transcribes it.
  function drawMenu() {
    const model = getModel();
    const name = model ? shortName(model) : "your speech model";
    const Name = cap(name);
    const local = isLocal(model?.id);
    el("learn-pop-lead").textContent = `Coming from another dictation app, or have recordings of you talking? ${Name} transcribes them, and Entune finds the names and terms you use.`;
    el("learn-step-1").textContent = `${Name} transcribes it again`;
    el("learn-cost").textContent = !model ? "Transcribing takes time, and a cloud speech model charges for it."
      : local ? `Transcribing takes time, and a cloud speech model charges for it. ${name} runs on ${THIS_DEVICE}, so there's no charge.`
        : `Transcribing takes time, and ${name} charges for the audio it transcribes.`;
    el("learn-pop-already").textContent = `Already dictating with ${name}?`;
  }
  menuButton.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    menuButton.setAttribute("aria-expanded", String(!menu.hidden));
    if (menu.hidden) return;
    drawMenu();
    // In a short window the popover scrolls within the space below its button.
    menu.style.maxHeight = `${Math.max(120, window.innerHeight - menu.getBoundingClientRect().top - 12)}px`;
    menu.querySelector(".learn-source:not(:disabled)")?.focus();
  });
  document.addEventListener("click", (event) => { if (!menu.hidden && !event.target.closest(".menu-wrap")) closeMenu(); });
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeMenu(); menuButton.focus(); } });
  for (const item of menu.querySelectorAll("[data-audio]")) {
    item.addEventListener("click", () => openSuggestions(item.dataset.audio));
  }
  el("learn-why-btn").addEventListener("click", () => {
    const why = el("learn-why");
    why.hidden = !why.hidden;
    el("learn-why-btn").setAttribute("aria-expanded", String(!why.hidden));
  });
  el("learn-guide").addEventListener("click", () => showHelp("guide-audio"));
  el("learn-suggest").addEventListener("click", () => openSuggestions("history"));

  // The guide is a modal over the page, so drafts and scroll position stay as they are.
  // Help in the toolbar shows all of it; Help in the suggestions panel shows only the
  // sections about what that panel does, under its own title.
  const HELP_TITLE = el("help-title").textContent;
  const PANEL_HELP = {
    history: ["Getting suggestions", ["guide-suggestions", "guide-reuse", "guide-time", "guide-review"]],
    audio: ["Learning from audio", ["guide-audio", "guide-time", "guide-review"]],
  };
  function showHelp(topic = null, panel = null) {
    const guide = el("help-dialog");
    closeMenu();
    if (guide.open) return;
    const [title, sections] = panel ? PANEL_HELP[panel] : [HELP_TITLE, null];
    el("help-title").textContent = title;
    let section = null;
    for (const part of guide.querySelector(".guide").children) {
      if (part.tagName === "H3") section = part.id;
      part.hidden = Boolean(sections) && !sections.includes(section);
    }
    guide.showModal();
    guide.querySelector(".guide").scrollTop = topic ? el(topic).offsetTop - guide.querySelector(".guide").offsetTop : 0;
  }
  el("dict-help").addEventListener("click", () => showHelp());
  // About the run under way when there is one, else about the source being set up.
  el("suggest-help").addEventListener("click", () => {
    const source = stage(run) === "setup" ? setupSource : run.source;
    showHelp(null, source === "history" ? "history" : "audio");
  });

  // ---- Suggestions: a side panel, and a banner above the list while a run is open ----
  const RUNNING = ["queued", "transcribing", "building", "cancelling", "cleaning"];
  const stage = (state) => RUNNING.includes(state.phase) ? "running" : state.phase === "ready" ? "review"
    : ["failed", "cancelled"].includes(state.phase) ? "halted" : "setup";
  // Before a run the panel starts one from a source: recent transcripts ("history"), or the
  // audio of Entune recordings, another dictation app or a folder, each chosen in the menu.
  let setupSource = "history";
  let reviewing = false; // a finished run shows its steps until Review suggestions opens the proposal
  const SETUP_TITLES = { history: "Get suggestions", entune: "Learn from your other models' recordings",
    provider: "Learn from another dictation app", folder: "Learn from an audio folder" };
  // What the source is, and how it becomes suggestions, in the speech model's name.
  const LEAD = {
    history: (m) => `Reads what ${m} already wrote and suggests the names, products and terms you use, each with how often you used it.`,
    entune: (m) => `Recordings you made in Entune with other speech models. ${m} transcribes them again, then Entune suggests the words you use in them.`,
    provider: (m) => `Recordings another dictation app keeps on ${THIS_DEVICE}. ${m} transcribes them, then Entune suggests the words you use in them.`,
    folder: (m) => `Audio of you talking, such as meetings or voice notes. ${m} transcribes it, then Entune suggests the words you use in it.`,
  };
  const HOW = {
    history: (m) => [`Get suggestions reads up to 300 of ${m}'s newest transcripts that suggestions you applied haven't used yet. A run you discard or stop uses none.`,
      "Each transcript is read once, so you don't pay twice for the same text. To read earlier ones again, open Advanced and choose a span."],
    entune: (m) => [`Get suggestions reads only ${m}'s own transcripts. These recordings were transcribed by other speech models, so ${m} transcribes them first.`,
      `The hours shown are recorded audio, not how long the run takes. ${cap(m)} transcribes first; then the suggestion model reads the new transcripts.`],
    provider: (m) => [`Import copies the audio that app keeps on ${THIS_DEVICE} into Entune's data folder. Its transcripts are never copied, and its own files stay as they are. Importing again adds only what's new.`,
      `Then ${m} transcribes the audio and the suggestion model reads the new transcripts, so moving from that app doesn't mean starting from scratch.`],
    folder: (m) => ["Choose a folder of recordings of you talking: WAV, MP3, M4A, FLAC, OGG or WebM. Each file is copied once into Entune's data folder; other files are skipped.",
      `Then ${m} transcribes the audio and the suggestion model reads the new transcripts. An hour or two of recent audio gives quick results.`],
  };
  // How this works starts open; the panel remembers it closed.
  let howOpen = true;
  try { howOpen = localStorage.getItem("entune.suggest.how") !== "closed"; } catch { /* open */ }
  el("how-toggle").addEventListener("click", () => {
    howOpen = !howOpen;
    try { localStorage.setItem("entune.suggest.how", howOpen ? "open" : "closed"); } catch { /* not remembered */ }
    drawSheet();
  });
  el("adv-toggle").addEventListener("click", () => {
    const open = el("adv-toggle").getAttribute("aria-expanded") !== "true";
    el("adv-toggle").setAttribute("aria-expanded", String(open));
    el("adv-body").hidden = !open;
  });
  el("suggest-cancel").addEventListener("click", () => suggestDrawer.close());
  el("review-proposal").addEventListener("click", () => { reviewing = true; showRun(run); el("proposal-body").scrollTop = 0; });

  function openSuggestions(source) {
    closeMenu();
    if (source) setupSource = source;
    if (source && source !== "history") onboarding.show(source).catch((err) => toast(errorText(err), "err"));
    if (setupSource === "history") historyReuse.load().catch((err) => toast(errorText(err), "err"));
    if (stage(run) === "review") reviewing = true; // opened to review: the proposal, not the steps
    showRun(run);
    if (!suggestDrawer.open) suggestDrawer.showModal();
  }
  el("suggest-btn").addEventListener("click", () => openSuggestions("history"));
  el("banner-action").addEventListener("click", () => openSuggestions());
  el("suggest-close").addEventListener("click", () => suggestDrawer.close());
  async function discardRun() {
    const job = run.id;
    if (await confirmAction("Discard these suggestions?", `Nothing changes in your dictionary, and a later run can read the same ${run.source === "audio" ? "recordings" : "transcripts"} again.`, "Discard")) await builds.discard(job);
  }
  el("discard-proposal").addEventListener("click", discardRun);
  el("abandon-dictionary-build").addEventListener("click", discardRun);

  const speechShort = (id) => id && getModel()?.id === id ? shortName(getModel()) : modelName(id ?? "", []).split(" · ")[0];
  const suggestionName = () => missingKey ? null : languageName(getSettings()?.dictionaryModel);
  // "about 3 parts, about 4 min each, about 12 min in all"; untimed settings say so.
  function partsText(parts, perPart, once = false) {
    return `about ${plural(parts, "part")}${perPart ? `, about ${duration(perPart)} each${once ? " (measured once)" : ""}, about ${duration(parts * perPart)} in all` : ""}`;
  }
  // Numbered steps: a dot, a title (with an Extra step tag), and a line beneath.
  function steps(list, items) {
    list.replaceChildren(...items.map((item, i) => {
      const li = node("li", "", item.cls ?? "");
      const dot = node("span", item.mark ?? String(i + 1), "step-dot");
      const text = node("div", "", "step-text");
      const head = node("p", item.title, "step-title");
      if (item.extra) head.append(node("span", "Extra step", "extra-tag"));
      text.append(head);
      if (item.body) text.append(node("p", item.body, "step-body"));
      if (item.status) text.append(node("p", item.status, "step-status"));
      if (item.bar != null) {
        const bar = node("div", "", "step-bar");
        bar.append(node("span"));
        bar.firstChild.style.width = `${Math.round(item.bar * 100)}%`;
        text.append(bar);
      }
      li.append(dot, text);
      return li;
    }));
  }

  // Advanced: the Read choice is about transcripts before a run; the summary says what is set.
  function drawAdvanced(reads) {
    for (const id of ["read-label", "read-mode", "read-note"]) el(id).hidden = !reads;
    el("adv-summary").textContent = `${suggestionName() ?? "No suggestion model"} · ${effort} effort${!reads ? "" : historyReuse.span ? " · a chosen span" : " · new transcripts"}`;
  }

  // The panel before a run: what the source is, what it costs, and what happens.
  function drawSheet() {
    if (stage(run) !== "setup") return;
    const audio = setupSource !== "history";
    const model = getModel();
    const m = model ? shortName(model) : "your speech model";
    const M = cap(m);
    const local = isLocal(model?.id);
    const sm = suggestionName();
    el("learn-source").textContent = model ? LEAD[setupSource](m) : "Choose a speech model first.";
    const tag = el("cost-tag");
    tag.textContent = audio ? "Transcribes again" : "Nothing to transcribe";
    tag.classList.toggle("warn", audio);
    el("cost-note").textContent = !audio ? "Uses the transcripts you already have."
      : local ? `${M} runs on ${THIS_DEVICE}: no charge, but it takes a while.` : `${M} charges for transcribing the audio you select.`;
    el("speech-field").hidden = !audio;
    el("how-toggle").setAttribute("aria-expanded", String(howOpen));
    el("how-body").hidden = !howOpen;
    el("how-body").replaceChildren(...HOW[setupSource](m).map((text) => node("p", text)));
    el("other-models-label").textContent = `Only recordings ${m} hasn't transcribed`;
    el("suggest-setup").hidden = audio;
    el("suggest-audio").hidden = !audio;
    drawAdvanced(!audio);
    // What happens, from what is chosen and what Entune has measured.
    const review = { title: "You review", body: "Edit any suggestion or leave it out, then apply. Nothing in your dictionary changes until you do." };
    const reader = sm ?? "The suggestion model";
    let what = [];
    let note = "Dictation keeps working.";
    if (audio) {
      const plan = onboarding.plan();
      if (plan.count) {
        const took = plan.transcribe != null ? `about ${duration(plan.transcribe)}` : "not timed yet for this speech model";
        const transcribe = plan.unknown ? "Some recordings have no known length, so there is no time estimate."
          : local ? `${duration(plan.seconds)} of audio on ${THIS_DEVICE}, one recording at a time: ${took}. No charge.`
            : `${M} bills you for ${duration(plan.seconds)} of audio. ${took[0].toUpperCase()}${took.slice(1)}${plan.workers > 1 ? `, ${plan.workers} recordings at a time` : ""}.`;
        const read = plan.unknown ? `${reader} reads the new transcripts in parts, starting while transcription runs.`
          : `${reader} reads the new transcripts in ${partsText(plan.parts, plan.perPart, plan.measuredOnce)}, starting while transcription runs.${plan.perPart ? "" : " Not timed yet with these settings."}`;
        what = [{ title: `${M} transcribes again`, body: transcribe, extra: true, cls: "extra" }, { title: "Find suggestions", body: read }, review];
      }
      note = !model ? "Choose a speech model first." : !sm ? "Choose a suggestion model with a key first."
        : plan.available ? note : setupSource === "provider" ? "Import its recordings first." : setupSource === "folder" ? "Choose a folder first." : "";
      el("build-audio-dictionary").disabled = plan.busy || !plan.count || !model || !sm;
    } else {
      const plan = historyReuse.plan();
      if (plan?.count) {
        const read = `${reader} reads ${plural(plan.count, plan.fresh ? "new transcript" : "transcript")} in ${partsText(plan.parts, plan.perPart)}.${plan.perPart ? "" : " Not timed yet with these settings."}`;
        what = [{ title: "Find suggestions", body: read }, review];
      }
      if (!model) note = "Choose a speech model first.";
      else if (!sm) note = "Choose a suggestion model with a key first.";
    }
    el("what-happens").hidden = !what.length;
    steps(el("what-steps"), what);
    el("suggest-note").textContent = note;
  }

  // A run under way, stopped or finished: the same steps, each with its progress.
  function drawRun(state, now) {
    const audio = state.source === "audio";
    const total = state.total ?? 0, completed = state.completed ?? 0, done = state.completedBatches ?? 0, parts = state.steps ?? 0;
    const running = now === "running";
    const halted = now === "halted" || (now === "review" && ["failed", "stopped"].includes(state.outcome));
    // Every recording processed (skipped ones count); a stop part-way leaves the rest to Continue.
    const transcribed = !audio || completed >= total;
    const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    const elapsed = running && state.stepStartedAt ? ` · ${clock(Math.max(0, Date.now() / 1000 - state.stepStartedAt))}` : "";
    const look = (doneNow, active) => doneNow ? "done" : active ? (running ? "active" : "paused") : "pending";
    const items = [];
    if (audio) {
      const cls = look(transcribed, true);
      items.push({ title: `${speechShort(state.model)} transcribes again`, cls, mark: cls === "done" ? "✓" : undefined,
        body: `${plural(total, "recording")}${isLocal(state.model) ? `, one at a time on ${THIS_DEVICE}` : ""}.`,
        status: transcribed ? `Transcribed ${plural(completed, "recording")}` : `Transcribed ${completed} of ${total} recordings`,
        bar: transcribed ? null : total ? completed / total : 0 });
    }
    const read = now === "review" && !halted;
    const started = !audio || transcribed || state.stepStartedAt || done;
    const findCls = look(read, started);
    items.push({ title: "Find suggestions", cls: findCls, mark: findCls === "done" ? "✓" : undefined,
      body: `${languageName(state.dictionaryModel) ?? "The suggestion model"} at ${state.effort ?? "high"} effort${audio ? ", starting while transcription runs" : ""}.`,
      status: read ? `Read ${plural(done || parts, "part")}`
        : !started ? "Starts once enough audio is transcribed"
          : halted ? `Stopped after ${plural(done, "part")}. Finished parts are kept.`
            : state.phase === "cancelling" ? "Stopping…"
              : state.step ? `Part ${state.step}${parts ? ` of ${parts}` : ""} · ${done} done${elapsed}` : "Getting ready…" });
    const kinds = (kind) => proposalChanges.filter((c) => c.kind === kind).length;
    items.push({ title: "You review", cls: now === "review" ? "active" : "pending",
      body: "Edit any suggestion or leave it out, then apply. Nothing in your dictionary changes until you do.",
      status: now !== "review" ? "" : !proposalChanges.length ? "No changes suggested."
        : `${plural(proposalChanges.length, "suggestion")} ready: ${[...(kinds("add") ? [plural(kinds("add"), "new word")] : []),
          ...(kinds("meaning") ? [plural(kinds("meaning"), "clearer meaning")] : [])].join(", ")}` });
    steps(el("run-steps"), items);
  }

  function showRun(state) {
    const before = stage(run);
    run = state;
    const now = stage(state);
    if (before === "running" && now === "review") reviewing = false; // finished while watching: show the steps first
    const showProposal = now === "review" && reviewing;
    const included = proposalChanges.filter((c) => c.included).length;
    // The toolbar button: start, progress, or the proposal waiting for review.
    el("suggest-spinner").hidden = now !== "running";
    el("suggest-icon").hidden = now === "running";
    el("suggest-label").textContent = now === "running" ? "Suggesting…" : now === "review" ? "Review" : "Get suggestions";
    el("suggest-badge").hidden = now !== "review";
    el("suggest-badge").textContent = String(included);
    el("suggest-btn").classList.toggle("waiting", now === "review");
    // The panel shows the one stage the run is in.
    const titles = { setup: SETUP_TITLES[setupSource], running: "Getting suggestions", review: "Review suggestions", halted: "Suggestions stopped" };
    el("suggest-title").textContent = titles[now];
    const model = getModel();
    el("suggest-sub").textContent = now === "setup" ? ""
      : `${plural(state.total ?? 0, state.source === "audio" ? "recording" : "transcript")} · ${modelName(state.model ?? "", model ? [model] : [])} → ${languageName(state.dictionaryModel) ?? "suggestion model"}`;
    const audio = setupSource !== "history";
    el("sheet-setup").hidden = now !== "setup";
    // The settings stay open after a stop or failure: Continue can use another reasoning
    // effort. The speech model is only chosen before a run.
    const resumable = now === "halted" || (now === "review" && ["failed", "stopped"].includes(state.outcome));
    el("suggest-shared").hidden = now !== "setup" && !resumable;
    if (resumable) drawAdvanced(false);
    if (now === "setup") { fillSpeech(); drawSheet(); }
    // Reviewing hides the steps, never the run's errors, skipped recordings or coverage.
    el("run-steps").hidden = showProposal;
    el("learn-status").hidden = now === "setup" || (showProposal && el("dictionary-build-status").hidden && el("build-error-detail").hidden);
    if (now !== "setup") drawRun(state, now);
    el("run-note").hidden = now !== "running";
    el("proposal").hidden = !showProposal;
    el("build-dictionary").hidden = now !== "setup" || audio;
    el("build-audio-dictionary").hidden = now !== "setup" || !audio;
    el("suggest-cancel").hidden = now !== "setup";
    el("suggest-note").hidden = now !== "setup";
    el("review-proposal").hidden = now !== "review" || showProposal;
    el("accept-proposal").hidden = !showProposal;
    el("discard-proposal").hidden = now !== "review";
    // The banner: what the run is doing, and whether editing waits for it.
    const banner = el("dict-banner");
    banner.hidden = now === "setup";
    banner.classList.toggle("err", state.phase === "failed" || (now === "review" && state.outcome === "failed"));
    el("banner-spinner").hidden = now !== "running";
    el("banner-dot").hidden = now === "running";
    const done = state.completedBatches ? ` · ${plural(state.completedBatches, "part")} of suggestions done` : "";
    const progress = state.phase === "transcribing" ? `Transcribed ${state.completed} of ${state.total} recordings${done}`
      : state.phase === "cancelling" ? "Stopping"
        : state.step ? `Getting suggestions · part ${state.step}${state.steps ? ` of ${state.steps}` : ""}${done}` : "Getting suggestions";
    el("banner-text").textContent = now === "running" ? `${progress}. Dictionary editing waits until the suggestions are applied or discarded.`
      : now === "review" ? `${state.outcome === "stopped" || state.outcome === "failed" ? "The run stopped early. " : ""}${plural(proposalChanges.length, "suggestion")} waiting for review. Dictionary editing waits until you apply or discard.`
        : state.phase === "failed" ? "The suggestion run stopped with an error. Your dictionary is unchanged; continue or discard."
          : `The suggestion run was stopped after ${plural(state.completedBatches ?? 0, "part")}. Continue, or discard.`;
    el("banner-action").textContent = now === "running" ? "Details" : now === "review" ? "Review" : "Open";
    // A finished review closes the panel and says what happened.
    if ((before === "review" || before === "halted") && now === "setup") {
      if (suggestDrawer.open) suggestDrawer.close();
      if (state.phase === "accepted") toast(state.applied ? "Applied. Your dictionary is updated." : "Closed without changes.");
      if (state.phase === "discarded") toast("Discarded. Nothing changed.");
    }
    lockEditors();
  }

  // ---- Review: new words you used, and clearer meanings for words still to check ----
  function renderProposal(p) {
    proposalChanges = p.changes.map((c) => ({ ...structuredClone(c), included: true, editing: false }));
    drawProposal();
  }
  function drawProposal(focus = null) {
    proposalBody.replaceChildren();
    for (const [kind, heading] of [["add", "New words"], ["meaning", "Clearer meanings"]]) {
      const items = proposalChanges.filter((c) => c.kind === kind);
      if (items.length) proposalBody.append(node("h3", `${heading} · ${items.length}`), ...items.map(changeBox));
    }
    if (!proposalChanges.length) proposalBody.append(node("p", "No changes suggested. Finish records where these dictations used words your dictionary has.", "caption"));
    const included = proposalChanges.filter((c) => c.included).length;
    el("accept-proposal").textContent = included ? `Apply ${plural(included, "change")}` : proposalChanges.length ? "Finish without changes" : "Finish";
    el("suggest-badge").textContent = String(included);
    if (focus) proposalBody.querySelector(`[data-toggle="${CSS.escape(focus)}"]`)?.focus();
  }
  // One suggestion, as Apply would write it, with its editor while it is included. A new
  // word keeps the places it was found; only its spelling, capitals and meaning can change.
  function changeBox(change) {
    const { before, after } = change;
    const added = change.kind === "add";
    const box = node("section", "", "proposal-change");
    box.classList.toggle("dismissed", !change.included);
    const name = node("b", after.spelling);
    const facts = node("span", "", "caption");
    const drawFacts = () => { facts.textContent = added ? `${after.casing === "fixed" ? "exact capitals" : "normal word"} · used ${after.evidence?.length ?? 0}×` : ""; };
    drawFacts();
    const toggle = change.included
      ? button("×", () => { change.included = false; drawProposal(change.id); }, "btn-icon remove")
      : button("Include", () => { change.included = true; drawProposal(change.id); }, "btn link");
    toggle.dataset.toggle = change.id;
    toggle.setAttribute("aria-label", `${change.included ? "Leave out" : "Include"} ${added ? "the new word" : "the clearer meaning of"} ${after.spelling}`);
    toggle.title = change.included ? "Leave this suggestion out; your dictionary stays as it is" : "Include this suggestion again";
    const head = node("div", "", "change-head");
    head.append(name, facts, node("span", "", "spacer"), toggle);
    const meaning = node(added ? "p" : "pre", after.meaning || "No meaning yet.", added ? "change-desc" : "after");
    box.append(head);
    if (added) box.append(meaning);
    else {
      const compare = node("div", "", "compare");
      compare.append(node("span", "Now", "caption"), node("pre", before.meaning || "No meaning yet.", "before"), node("span", "Suggested", "suggested"), meaning);
      box.append(compare);
    }
    if (!change.included) return box;
    const editor = node("details", "", "proposal-editor");
    editor.open = change.editing;
    editor.addEventListener("toggle", () => { change.editing = editor.open; });
    const form = node("div", "", "meaning-editor");
    if (added) {
      // A spelling is never empty: clearing it keeps the one before.
      edit(form, "Spelling", "input", after.spelling, (value) => { after.spelling = words(value) || after.spelling; name.textContent = after.spelling; return after.spelling; });
      edit(form, "Capitals", "select", after.casing, (value) => { after.casing = value; drawFacts(); return value; });
    }
    edit(form, "Meaning", "textarea", after.meaning, (value) => {
      after.meaning = words(value);
      after.needs_review ||= !after.meaning; // a word without a meaning stays to check
      meaning.textContent = after.meaning || "No meaning yet.";
      return after.meaning;
    });
    editor.append(node("summary", "Edit this suggestion"), form);
    box.append(editor);
    return box;
  }
  // A field of a suggestion's editor: a change updates what Apply sends, and shows it as kept.
  function edit(parent, label, tag, value, update) {
    const wrap = node("label", label, "group-field");
    const control = document.createElement(tag);
    control.className = tag === "select" ? "select" : "input";
    if (tag === "select") control.append(new Option("Exact capitals: names, products, acronyms", "fixed"), new Option("Normal word: capitalised at a sentence start", "ordinary"));
    if (tag === "textarea") { control.rows = 2; control.maxLength = 120; }
    control.value = value ?? "";
    control.addEventListener("change", () => { control.value = update(control.value); });
    wrap.append(control);
    parent.append(wrap);
  }
  el("build-dictionary").addEventListener("click", () => {
    const selection = historyReuse.selection();
    if (!selection) { flash(buildStatus, "The transcripts are still loading. Try again in a moment.", "err"); return; }
    builds.start("history", { ...selection, ...runSettings() });
  });

  return { load: loadDictionary, refreshAudio: () => onboarding.load() };
}
