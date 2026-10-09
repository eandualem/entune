import { THIS_DEVICE, api, el, errorText, flash, modelName, segmentedGroup } from "./ui.js";
import { createAudioOnboarding, duration } from "./audio-onboarding.js";
import { createHistoryReuse } from "./history-reuse.js";
import { createDictionaryBuild } from "./dictionary-build.js";

// The dictionary owns its document, revision and pending proposal. Model selection
// stays in the app; getters read the current selection when an action is made.
export function createDictionary({ getModel, getSettings, onSettingsChanged, openSettings }) {
  // Words are defined once; a heard entry is the edit unit, keyed by its text in its scope.
  let dict = { version: 3, words: [], pinned: [], learned: {} };
  let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
  let savedText = ""; // the JSON view's last loaded or saved text
  let draftBase = null; // the revision the JSON view's unsaved text was started from
  let importing = false;
  let building = false;
  let run = { phase: "idle" };
  let filter = "all";
  let query = "";
  let openId = null; // the one entry shown expanded, as scope:heard text
  let addPinned = false;
  let draft = null; // the entry open in the editor panel
  let editorError = ""; // a refused save, shown until the draft changes
  let saving = false; // one editor save at a time
  let proposalChanges = [];
  let proposalWords = new Map(); // the words a proposal names, new ones as the review edits them
  let proposalModel = null;
  let missingKey = false; // the saved dictionary model's provider has no key
  const modelSelect = el("dictionary-model");
  const buildStatus = el("build-status");
  const proposalBody = el("proposal-body");
  const entryDrawer = el("entry-drawer");
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
    onProposal(value) { if (value) renderProposal(value); else { proposalChanges = []; el("proposal").hidden = true; } },
    onAccepted: () => loadDictionary(false),
    getSelected: () => {
      // Included changes, and each new word the included entries name, as the review left it.
      const chosen = proposalChanges.filter(c => c.included);
      const named = new Set(chosen.filter(c => c.kind !== "word").flatMap(c => (c.after?.candidates ?? []).map(x => x.word)));
      return [...chosen.map(c => ({id: c.id, after: c.after})),
        ...[...named].filter(isNewWord).map(wid => ({id: `word:${wid}`, after: proposalWords.get(wid)})).filter(x => x.after)];
    },
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
  // What the choice does, in one line; on a ChatGPT plan, also its limit per reply.
  function drawEffect() {
    const model = getSettings()?.dictionaryModel ?? "";
    // What effort does is in Help; only a ChatGPT plan's limit per reply is said here.
    el("run-effect").textContent = model.startsWith("chatgpt:")
      ? "On a ChatGPT plan each reply must finish within about 15 minutes."
      : "";
    el("run-effect").hidden = !el("run-effect").textContent;
  }
  // The speech model the dictionary is for: the same choice as the toolbar's default model.
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
  const clone = () => JSON.parse(JSON.stringify(dict));
  const id = (prefix) => `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
  const words = (text) => text.trim().split(/\s+/).join(" ");
  // "AssemblyAI" from "AssemblyAI / universal-3-5-pro": the filter and Scope column name.
  const shortName = (model) => model ? model.label.split(" / ")[0].replace(/\s*\(.*\)$/, "") : "Learned";
  const speechName = (model) => model ? modelName(model.id, [model]) : "the selected speech model";
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  // A speech model that runs on this computer: transcribing costs time, never money.
  const isLocal = (id) => Boolean(id && getSettings()?.providers.find((p) => p.id === id.split("/")[0])?.local);

  // A heard text is kept as written only when it is the word's own spelling: exactly
  // for fixed capitals, ignoring case for a normal word. "anthropic" → "Anthropic" is a fix.
  function isLiteral(word, text) {
    const a = words(word?.spelling ?? ""), b = words(text ?? "");
    if (!a || !b) return false;
    return word.casing === "ordinary" ? a.toLowerCase() === b.toLowerCase() : a === b;
  }
  // Heard texts match ignoring case and spacing; one entry per text in each scope.
  const textKey = (text) => words(text ?? "").toLowerCase();
  const wordMap = (doc = dict) => new Map(doc.words.map((w) => [w.id, w]));
  // The words an entry can stand for, in its order.
  const named = (h, known = wordMap()) => h.candidates.map((c) => known.get(c.word) ?? { id: c.word, spelling: c.word, meaning: "", personal_context: null, casing: "fixed", needs_review: false });
  const title = (h, known = wordMap()) => named(h, known).map((w) => w.spelling).join(" · ");
  const attention = (h, known = wordMap()) => named(h, known).some((w) => w.needs_review || !w.meaning.trim());
  // The letter a title is listed under: its first letter or digit, without accents.
  const initial = (text) => (text.normalize("NFD").match(/[\p{L}\p{N}]/u)?.[0] ?? "#").toUpperCase();
  // The entries that apply to the selected speech model: pinned, then its learned ones. A
  // learned entry is not used while a pinned one has its text.
  function visible() {
    const model = getModel();
    const pinnedTexts = new Set(dict.pinned.map((h) => textKey(h.text)));
    return [...dict.pinned.map((h) => ({ h, scope: "pinned" })),
      ...(model ? dict.learned[model.id] ?? [] : []).map((h) => ({ h, scope: "learned", superseded: pinnedTexts.has(textKey(h.text)) }))];
  }
  // Every entry that names a word, in every section: an edit to the word reaches them all.
  function usesOf(wid, doc = dict) {
    return [...doc.pinned.map((h) => ({ h, scope: "pinned", model: null })),
      ...Object.entries(doc.learned).flatMap(([m, list]) => list.map((h) => ({ h, scope: "learned", model: m })))]
      .filter((u) => u.h.candidates.some((c) => c.word === wid));
  }
  const whereUsed = (u) => `“${u.h.text}”${u.scope === "pinned" ? " (pinned)" : ` (${speechShort(u.model)})`}`;

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
  for (const drawer of [entryDrawer, suggestDrawer]) {
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
  async function saveDictionary(next, fromEditor = false, base = dictVersion) {
    if (building) return "Apply or discard the open suggestions first.";
    if (!base && !fromEditor) {
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

  // Editing pauses while suggestions are open; the banner says why.
  function lockEditors() {
    for (const field of document.querySelectorAll("#add-entry-btn, #add-row input, #add-advanced, #dict-rows .detail-actions button, #dict-rows .pin-meaning")) field.disabled = building;
    jsonBox.readOnly = building;
    jsonDirty();
    drawAdd();
    editorStatus();
    modelSelect.disabled = building || modelSelect.value === "";
  }

  // ---- The table ----
  const selectFilter = segmentedGroup({ all: el("filter-all"), pinned: el("filter-pinned"), learned: el("filter-learned"), attention: el("filter-attention") }, (name) => { filter = name; openId = null; renderRows(); });
  el("dict-search").addEventListener("input", (event) => { query = event.target.value; renderRows(); });

  function renderDictionary(jsonText) {
    // Unsaved text in the JSON view survives a reload (a model change, a pin); only Save
    // and Revert replace it.
    if (jsonText !== undefined) {
      const draft = jsonBox.value !== savedText;
      savedText = jsonText;
      if (!draft) { jsonBox.value = jsonText; draftBase = null; el("json-error").hidden = true; }
    }
    const model = getModel();
    const known = wordMap();
    const entries = visible();
    const learned = entries.filter((v) => v.scope === "learned").length;
    if (filter === "learned" && !model) { filter = "all"; selectFilter("all"); }
    const counts = { all: entries.length, pinned: dict.pinned.length, learned, attention: entries.filter((v) => attention(v.h, known)).length };
    for (const [name, count] of Object.entries(counts)) el(`count-${name}`).textContent = count ? String(count) : "";
    el("attention-dot").hidden = !counts.attention;
    // "To check" shows only while something needs checking.
    el("filter-attention").hidden = !counts.attention;
    if (filter === "attention" && !counts.attention) { filter = "all"; selectFilter("all"); }
    el("filter-learned").title = model ? `Learned only for ${speechName(model)}` : "Choose a speech model in the toolbar first";
    el("filter-learned").disabled = !model;
    el("dict-summary").textContent = [plural(entries.length, "entry", "entries"), `${dict.pinned.length} pinned for every speech model`,
      ...(model ? [`${learned} learned for ${shortName(model)}`] : [])].join(" · ");
    drawMenu();
    el("learn-source").textContent = !model ? "Choose a speech model first."
      : "Reads your newest transcripts not yet used by suggestions you applied, finds the words your speech model gets wrong, and improves the entries those transcripts show.";
    if (proposalModel) proposalTitle();
    renderRows();
    fillModels();
    if (stage(run) === "setup") fillSpeech();
    lockEditors();
  }

  function renderRows() {
    const model = getModel();
    const known = wordMap();
    const q = query.trim().toLowerCase();
    const shown = visible()
      .filter((v) => filter === "all" || (filter === "attention" ? attention(v.h, known) : v.scope === filter))
      .filter((v) => !q || [v.h.text, ...named(v.h, known).flatMap((w) => [w.spelling, w.meaning])].join(" ").toLowerCase().includes(q))
      .map((v) => ({ ...v, name: title(v.h, known) || v.h.text }))
      .sort((a, b) => initial(a.name).localeCompare(initial(b.name)) || a.name.localeCompare(b.name) || a.h.text.localeCompare(b.h.text));
    // Entries under the first letter of their title, A to Z.
    const letters = [];
    for (const v of shown) {
      const letter = initial(v.name);
      if (letters.at(-1)?.letter !== letter) letters.push({ letter, items: node("div", "", "dict-group-items") });
      letters.at(-1).items.append(entryRow(v, known));
    }
    el("dict-rows").replaceChildren(...letters.map(({ letter, items }) => {
      const group = node("div", "", "dict-group");
      group.append(node("span", letter, "dict-letter"), items);
      return group;
    }));
    const empty = !shown.length;
    el("dict-empty").hidden = !empty;
    if (empty) {
      const short = shortName(model);
      const [heading, body] = q ? ["No matches", "Try a spelling, a heard text or a word from a description."]
        : filter === "learned" ? [`Nothing learned for ${short} yet`, "Each speech model makes its own mistakes. Suggestions read its recent transcripts and propose first entries."]
          : filter === "attention" ? ["Nothing needs attention", "Every word has a description."]
            : filter === "pinned" ? ["No pinned entries", "Pin a learned entry to use it with every speech model, or add one yourself."]
              : ["Your dictionary is empty", "Add a word, or get suggestions from your transcripts."];
      el("empty-title").textContent = heading;
      el("empty-body").textContent = body;
      el("empty-suggest").hidden = Boolean(q) || filter === "attention";
    }
    lockEditors();
  }

  const PIN = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M6 1.5h4l-.6 4.5 2.8 2.5H3.8L6.6 6 6 1.5zM8 8.5V14.5"/></svg>';
  const CHEVRON = '<svg class="i12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 2.5L8 6l-3.5 3.5"/></svg>';
  const ARROW = '<svg class="i14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 7h9M8 4l3 3-3 3"/></svg>';
  const icon = (svg, cls) => { const span = node("span", "", cls); span.innerHTML = svg; return span; };
  // One line per heard entry: the words it can mean, what was heard, a description and
  // where it applies. A click shows the words in full underneath.
  function entryRow({ h, scope, superseded, name: heading }, known) {
    const model = getModel();
    const item = node("div", "", "dict-entry");
    const line = node("div", "", "dict-row");
    const rowKey = `${scope}:${textKey(h.text)}`;
    const open = openId === rowKey;
    line.tabIndex = 0;
    line.setAttribute("role", "button");
    line.setAttribute("aria-expanded", String(open));
    const name = node("span", "", "cell-title");
    name.append(node("span", heading, "title-text"));
    name.title = heading;
    if (attention(h, known)) {
      const dot = node("span", "", "attention-dot");
      dot.title = named(h, known).some((w) => !w.meaning.trim()) ? "A word here needs a description before Entune can choose it" : "Imported: check the descriptions and capitals";
      name.append(dot);
    }
    const heard = node("span", "", "cell-heard");
    heard.title = h.text;
    heard.append(node("code", h.text, "chip"));
    // The description of what it was heard for first, then of the word kept as written.
    const order = [...h.candidates.filter((c) => c.basis !== "literal"), ...h.candidates.filter((c) => c.basis === "literal")];
    const description = order.map((c) => known.get(c.word)?.meaning.trim()).find(Boolean);
    const what = node("span", superseded ? "Not used: pinned for every model" : description ?? "No description yet",
      superseded ? "cell-desc faint" : description ? "cell-desc" : "cell-desc missing");
    const where = node("span", "", "cell-scope");
    if (scope === "pinned") where.innerHTML = PIN;
    where.append(scope === "pinned" ? "Pinned" : shortName(model));
    line.append(name, heard, what, where, icon(CHEVRON, "cell-chevron"));
    const toggle = () => { openId = open ? null : rowKey; renderRows(); document.querySelector(`[data-entry="${CSS.escape(rowKey)}"] .dict-row`)?.focus(); };
    line.addEventListener("click", toggle);
    line.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
    item.dataset.entry = rowKey;
    item.classList.toggle("open", open);
    item.append(line);
    if (open) item.append(entryDetail(h, scope, superseded, known));
    return item;
  }

  // Heard → written on the left, beside the words the decision model chooses from.
  function entryDetail(h, scope, superseded, known) {
    const model = getModel();
    const detail = node("div", "", "dict-detail");
    const forms = node("div", "", "detail-forms");
    forms.append(node("p", model ? `When ${shortName(model)} hears` : "When the speech model hears", "detail-label"));
    const line = node("div", "", "heard-line");
    line.append(node("code", h.text, "heard"), icon(ARROW, "arrow"));
    h.candidates.forEach((candidate, i) => {
      const target = node("span", "", "target");
      if (i) target.append(node("span", "or", "or"));
      const literal = candidate.basis === "literal";
      target.append(node("span", known.get(candidate.word)?.spelling ?? candidate.word, literal ? "word literal" : "word"));
      if (literal) {
        target.append(node("span", "as written", "note"));
        target.title = "Left as written when this word fits the sentence";
      }
      line.append(target);
    });
    if (h.direct) {
      const chip = node("span", "Always", "always-chip");
      chip.title = `Always used, without reading the sentence: ${h.direct_reason}`;
      line.append(chip);
    }
    forms.append(line);
    if (superseded) forms.append(node("p", "Not used: a pinned entry hears the same words. Pin this one to add its words to that entry, or remove it.", "caption warn"));
    const list = node("div", "", "detail-meanings");
    list.append(node("p", h.candidates.length > 1 ? "Words · Entune picks one from the sentence" : "Word", "detail-label"));
    named(h, known).forEach((word, i) => {
      const card = node("div", "", "meaning-card");
      const body = node("div", "", "meaning-body");
      const head = node("div", "", "meaning-head");
      head.append(node("b", word.spelling), node("span", word.casing === "fixed" ? "exact capitals" : "normal word", "casing"));
      if (known.has(word.id)) {
        const remove = button("Delete word", () => deleteWord(word), "btn link pin-meaning");
        remove.title = `Delete ${word.spelling} from your dictionary and from every entry that uses it`;
        head.append(remove);
      }
      body.append(head, node("p", word.meaning || "No description yet. Entune can't choose this word until it has one.", word.meaning ? "definition" : "definition missing"));
      if (word.personal_context) {
        const personal = node("p", "", "personal");
        personal.append(node("span", "Personal · ", "faint"), word.personal_context);
        body.append(personal);
      }
      // Where else the word is used, so the reach of an edit is clear.
      const others = usesOf(word.id).filter((u) => u.h !== h);
      if (others.length) body.append(node("p", `Also for ${others.slice(0, 3).map(whereUsed).join(", ")}${others.length > 3 ? ` and ${others.length - 3} more` : ""}`, "personal"));
      card.append(node("span", String(i + 1), "meaning-n"), body);
      list.append(card);
    });
    if (named(h, known).some((w) => w.needs_review)) list.append(node("p", "Imported: check the descriptions and capitals.", "caption warn"));
    const actions = node("div", "", "detail-actions");
    actions.append(button("Edit", () => openEditor(scope, h), "btn fill sm"));
    if (scope === "learned") {
      const pin = button("Pin for every model", () => pinEntry(h), "btn sm");
      pin.insertAdjacentHTML("afterbegin", PIN);
      pin.title = "Use with every speech model and protect from suggestions";
      actions.append(pin);
    }
    actions.append(button("Remove", () => removeEntry(scope, h), "btn ghost sm remove"), node("span", "", "spacer"),
      node("span", scope === "pinned" ? "Pinned · used with every speech model" : `Learned · ${speechName(model)} only`, "caption"));
    detail.append(forms, list, actions);
    return detail;
  }

  async function pin(body, model = getModel()?.id) {
    const res = await fetch("/api/dictionary/pin", {
      method: "POST", headers: { "content-type": "application/json", "if-match": dictVersion },
      body: JSON.stringify({ model, ...body }),
    });
    const text = await res.text();
    if (!res.ok) { toast(text, "err"); await loadDictionary(); return false; }
    dict = JSON.parse(text);
    dictVersion = res.headers.get("etag");
    renderDictionary(text);
    return true;
  }
  // The server moves the learned entry, under the model it was learned for even if the
  // toolbar selection changes meanwhile; a pinned one with its text gains its words.
  async function pinEntry(h) {
    if (!await pin({ text: h.text }, getModel()?.id)) return;
    openId = `pinned:${textKey(h.text)}`;
    renderRows();
    toast(`Pinned “${h.text}” for every speech model`);
  }

  // Removing a heard entry never deletes a word: other entries may use it.
  async function removeEntry(scope, h) {
    const version = dictVersion; // a reload while confirming makes this a conflict, not an overwrite
    const next = clone();
    const list = scope === "pinned" ? next.pinned : next.learned[getModel()?.id] ?? [];
    const at = list.findIndex((x) => textKey(x.text) === textKey(h.text));
    if (at >= 0) list.splice(at, 1);
    if (!await confirmAction(`Remove “${h.text}”?`, `Entune stops changing “${h.text}”. Its words stay in your dictionary.`, "Remove")) return;
    const problem = await saveDictionary(next, false, version);
    if (problem) { toast(problem, "err"); return; }
    openId = null;
    renderRows();
    toast(`Removed “${h.text}”`);
  }
  // Deleting a word removes it from every entry that names it; an entry left with no word goes.
  async function deleteWord(word) {
    const version = dictVersion;
    const next = clone();
    const uses = usesOf(word.id, next);
    next.words = next.words.filter((w) => w.id !== word.id);
    let gone = 0;
    for (const list of [next.pinned, ...Object.values(next.learned)]) {
      for (let i = list.length - 1; i >= 0; i--) {
        const h = list[i];
        h.candidates = h.candidates.filter((c) => c.word !== word.id);
        if (h.direct === word.id) { h.direct = null; h.direct_reason = ""; }
        if (!h.candidates.length) { list.splice(i, 1); gone++; }
      }
    }
    const body = uses.length ? `It is removed from ${plural(uses.length, "heard entry", "heard entries")}: ${uses.map(whereUsed).join(", ")}.${gone ? ` ${plural(gone, "entry", "entries")} left with no word ${gone === 1 ? "goes" : "go"} too.` : ""}` : "No heard entry uses it.";
    if (!await confirmAction(`Delete the word “${word.spelling}”?`, body, "Delete")) return;
    const problem = await saveDictionary(next, false, version);
    if (problem) { toast(problem, "err"); return; }
    renderRows();
    toast(`Deleted ${word.spelling}`);
  }

  // ---- Add: one line for the simple case ----
  const addRow = el("add-row");
  const addFields = { spelling: el("add-spelling"), heard: el("add-heard"), desc: el("add-desc") };
  const heardList = () => {
    const seen = new Set();
    return addFields.heard.value.split(",").map(words).filter((t) => t && !seen.has(t.toLowerCase()) && seen.add(t.toLowerCase()));
  };
  let addProblem = ""; // a refused save, shown until the line is edited
  function drawAdd() {
    const model = getModel();
    if (!model) addPinned = true;
    const scope = el("add-scope");
    scope.innerHTML = PIN;
    scope.append(addPinned ? "Pinned · every model" : `Learned · ${shortName(model)}`);
    scope.title = addPinned ? (model ? "Used with every speech model. Click to use it only with this one." : "Used with every speech model") : `Only ${speechName(model)}. Click to pin it for every speech model.`;
    scope.setAttribute("aria-pressed", String(addPinned));
    scope.disabled = building || !model;
    el("add-save").disabled = building || !words(addFields.spelling.value) || !heardList().length;
    const hint = el("add-hint");
    hint.classList.toggle("err", Boolean(addProblem));
    hint.textContent = addProblem || "Separate heard texts with commas.";
  }
  function showAdd(open) {
    addRow.hidden = !open;
    el("add-entry-btn").setAttribute("aria-expanded", String(open));
    if (open) { drawAdd(); addFields.spelling.focus(); }
  }
  function clearAdd() { for (const input of Object.values(addFields)) input.value = ""; addProblem = ""; }
  el("add-entry-btn").addEventListener("click", () => { showView("list"); showAdd(addRow.hidden); });
  el("add-cancel").addEventListener("click", () => showAdd(false));
  el("add-scope").addEventListener("click", () => { addPinned = !addPinned; drawAdd(); });
  for (const input of Object.values(addFields)) {
    input.addEventListener("input", () => { addProblem = ""; drawAdd(); });
    input.addEventListener("keydown", (event) => {
      if (event.isComposing || event.keyCode === 229) return; // Enter confirms an input-method candidate
      if (event.key === "Enter") { event.preventDefault(); quickAdd(); }
      if (event.key === "Escape") showAdd(false);
    });
  }
  el("add-save").addEventListener("click", quickAdd);
  async function quickAdd() {
    const spelling = words(addFields.spelling.value), heard = heardList(), description = words(addFields.desc.value);
    if (!spelling || !heard.length || building) return;
    const model = getModel();
    const scope = addPinned || !model ? "pinned" : "learned";
    const next = clone();
    const pinnedTexts = new Set(next.pinned.map((h) => textKey(h.text)));
    const clash = scope === "learned" && heard.find((text) => pinnedTexts.has(textKey(text)));
    if (clash) { addProblem = `“${clash}” is pinned for every model; edit that entry instead.`; drawAdd(); return; }
    const list = scope === "pinned" ? next.pinned : (next.learned[model.id] ??= []);
    // A name is one word: the one already spelled exactly so, unless it is described otherwise.
    let word = next.words.find((w) => w.spelling === spelling && (!description || !w.meaning || w.meaning === description));
    if (word && description && !word.meaning) Object.assign(word, { meaning: description, needs_review: false });
    if (!word) {
      word = { id: id("w"), spelling, meaning: description, personal_context: null, casing: "fixed", needs_review: !description };
      next.words.push(word);
    }
    // Each heard text is its own entry; one already in the list gains the word.
    for (const text of heard) {
      const candidate = { word: word.id, basis: isLiteral(word, text) ? "literal" : "user", evidence: [] };
      const entry = list.find((h) => textKey(h.text) === textKey(text));
      if (!entry) list.push({ text, candidates: [candidate], direct: null, direct_reason: "" });
      else if (!entry.candidates.some((c) => c.word === word.id)) entry.candidates.push(candidate);
    }
    addProblem = await saveDictionary(next);
    drawAdd();
    if (addProblem) return;
    clearAdd();
    showAdd(false);
    reveal(scope, heard[0]);
    toast(`Added ${spelling}`);
  }
  // A saved entry opens expanded, whatever filter or search would have hidden it.
  function reveal(scope, text) {
    openId = `${scope}:${textKey(text)}`;
    query = ""; el("dict-search").value = "";
    if (filter !== "all") { filter = "all"; selectFilter("all"); }
    renderRows();
    document.querySelector(`[data-entry="${CSS.escape(openId)}"]`)?.scrollIntoView({ block: "nearest" });
  }
  el("add-advanced").addEventListener("click", () => {
    const model = getModel();
    const spelling = words(addFields.spelling.value);
    const heard = heardList();
    const word = (spelling && dict.words.find((w) => w.spelling === spelling)) || { id: id("w"), spelling, meaning: words(addFields.desc.value), personal_context: null, casing: "fixed", needs_review: false };
    openEditor(addPinned || !model ? "pinned" : "learned", { text: heard[0] ?? "", candidates: [{ word: word.id, basis: "user", evidence: [] }], direct: null, direct_reason: "" },
      true, model, [word], heard.slice(1));
  });
  el("empty-suggest").addEventListener("click", () => openSuggestions("history"));

  // ---- The full editor, in a side panel ----
  // One heard entry: its text, and the words it can stand for. A word is shared by every
  // entry that names it, so its edits reach them all. A learned draft belongs to the speech
  // model it was started for, even if the toolbar selection changes before it is saved.
  const blankWord = () => ({ id: id("w"), spelling: "", meaning: "", personal_context: null, casing: "fixed", needs_review: false });
  // What a draft was made from: the entry and the words it names, as stored.
  const snapshot = (h, known = wordMap()) => JSON.stringify({ h, words: h.candidates.map((c) => known.get(c.word) ?? null) });
  function openEditor(scope, entry, created = false, model = getModel(), fresh = [], more = []) {
    editorError = "";
    const known = wordMap();
    for (const w of fresh) if (!known.has(w.id)) known.set(w.id, w);
    draft = {
      created, scope, model,
      notices: more.length ? [`Only “${entry.text}” is in this entry; add ${more.map((t) => `“${t}”`).join(", ")} as entries of their own.`] : [],
      version: dictVersion, // the revision this draft was made from
      original: structuredClone(entry),
      base: created ? null : snapshot(entry),
      text: entry.text, origText: created ? null : entry.text,
      items: entry.candidates.map((c) => {
        const w = known.get(c.word) ?? blankWord();
        return { key: id("c"), word: { ...w, meaning: w.meaning ?? "", personal_context: w.personal_context ?? "" }, stored: dict.words.some((x) => x.id === w.id),
          basis: c.basis, evidence: c.evidence ?? [] };
      }),
      direct: entry.direct, direct_reason: entry.direct_reason ?? "",
    };
    el("entry-title").textContent = created ? "New entry" : "Edit entry";
    el("entry-save").textContent = created ? "Add entry" : "Save";
    drawEditor();
    if (!entryDrawer.open) entryDrawer.showModal();
    el("entry-body").scrollTop = 0;
    el("entry-body").querySelector("input")?.focus();
  }
  function closeEditor() { if (entryDrawer.open) entryDrawer.close(); }
  entryDrawer.addEventListener("close", () => { draft = null; });
  el("entry-close").addEventListener("click", closeEditor);
  el("entry-cancel").addEventListener("click", closeEditor);

  // Clicks and structural changes redraw the draft; typing only updates it and refreshes
  // what depends on the text, so the field being typed in is never replaced (native undo
  // and input methods keep working).
  function drawEditor() {
    const body = el("entry-body");
    const active = document.activeElement?.dataset?.key;
    body.replaceChildren(...editorParts());
    if (active) body.querySelector(`[data-key="${CSS.escape(active)}"]`)?.focus();
    editorStatus();
  }
  const change = (fn) => { editorError = ""; fn(draft); drawEditor(); };
  const part = (name, value) => el("entry-body").querySelector(`[data-part="${CSS.escape(`${name}:${value}`)}"]`);
  const showError = (p, text) => { if (p) { p.textContent = text ?? ""; p.hidden = !text; } };
  const spellingOf = (item) => item?.word.spelling || "(no spelling)";
  // The heard text's own word, kept as written, is offered while no candidate is spelled so.
  const canKeep = (d) => Boolean(words(d.text)) && !d.items.some((item) => textKey(item.word.spelling) === textKey(d.text));
  function refresh() {
    const d = draft;
    const problems = editorProblems();
    showError(part("error", "text"), problems.text);
    for (const item of d.items) {
      showError(part("error", `w:${item.key}`), problems.items.get(item.key));
      part("literal", item.key)?.toggleAttribute("hidden", !literalItem(item));
    }
    const keep = part("keep", "text");
    if (keep) { keep.hidden = !canKeep(d); keep.textContent = `+ keep “${words(d.text)}” as written`; }
    showError(part("error", "reason"), problems.direct ? "Write a reason. Use this only when the words can never mean anything else." : "");
    const reason = el("entry-body").querySelector('[data-key="reason"]');
    if (reason) reason.placeholder = `Why can “${words(d.text)}” never mean anything else?`;
    for (const option of part("target", "always")?.options ?? []) option.textContent = spellingOf(d.items.find((item) => item.word.id === option.value));
    showError(part("error", "none"), problems.none ? "Add a word it can stand for." : "");
    editorStatus();
  }
  // An always-replace approval was given for one heard text and one output; changing
  // either clears it, and the panel says so.
  function clearAlways(why) {
    const notice = `“Always” on “${words(draft.text)}” was cleared because you changed ${why}. Turn it on again if it still applies.`;
    draft.direct = null; draft.direct_reason = "";
    part("always-box", "always")?.remove();
    const always = part("always", "button");
    always?.classList.remove("on");
    always?.setAttribute("aria-pressed", "false");
    if (draft.notices.includes(notice)) return;
    draft.notices.push(notice);
    part("notices", "all")?.append(node("p", notice, "editor-notice"));
  }
  function segmented(items, current, onPick, label) {
    const group = node("div", "", "segmented small");
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", label);
    for (const [value, text, hint] of items) {
      const b = button(text, () => onPick(value), "");
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(value === current));
      b.setAttribute("aria-selected", String(value === current));
      if (hint) b.title = hint;
      group.append(b);
    }
    return group;
  }
  function input(value, key, onInput, { placeholder = "", cls = "input", tag = "input", label = placeholder } = {}) {
    const field = document.createElement(tag);
    field.className = cls;
    field.value = value ?? "";
    field.placeholder = placeholder;
    field.dataset.key = key;
    field.setAttribute("aria-label", label);
    if (tag === "textarea") field.rows = 2;
    field.addEventListener("input", () => { editorError = ""; onInput(field.value); refresh(); });
    return field;
  }
  const errorLine = (name, text = "") => {
    const p = node("p", text, "field-error");
    p.dataset.part = `error:${name}`;
    p.hidden = !text;
    return p;
  };

  // A stored "as written" candidate stays one while its heard text and word are unchanged;
  // otherwise the spelling rule above decides.
  function literalItem(item) {
    const d = draft;
    const old = words(d.text) === d.origText && item.basis === "literal" ? dict.words.find((w) => w.id === item.word.id) : null;
    const same = old && words(old.spelling) === words(item.word.spelling) && old.casing === item.word.casing;
    return Boolean(same) || isLiteral(item.word, d.text);
  }

  function editorProblems() {
    const d = draft;
    const problems = { text: "", items: new Map(), direct: false, none: false, count: 0 };
    const text = textKey(d.text);
    const list = d.scope === "pinned" ? dict.pinned : dict.learned[d.model?.id] ?? [];
    const taken = list.some((h) => textKey(h.text) === text && (d.created || text !== textKey(d.origText)));
    problems.text = !text ? "Write what the speech model writes."
      : taken ? "Already an entry in this list; edit that one."
        : d.scope === "learned" && (d.created || text !== textKey(d.origText)) && dict.pinned.some((h) => textKey(h.text) === text)
          ? "Pinned for every model already; edit that entry." : "";
    for (const item of d.items) if (!words(item.word.spelling)) problems.items.set(item.key, "Write how it should be spelled.");
    problems.none = !d.items.length;
    problems.direct = Boolean(d.direct) && !words(d.direct_reason);
    problems.count = (problems.text ? 1 : 0) + problems.items.size + (problems.direct ? 1 : 0) + (problems.none ? 1 : 0)
      + (d.scope === "learned" && !d.model ? 1 : 0);
    return problems;
  }
  function editorStatus() {
    if (!draft) return;
    const problems = editorProblems();
    const missing = draft.items.filter((item) => !words(item.word.meaning)).length;
    const status = el("entry-status");
    status.textContent = editorError || (problems.count ? plural(problems.count, "thing") + " to fix"
      : missing ? `${missing} without a description` : "Ready to save");
    status.classList.toggle("err", Boolean(editorError) || problems.count > 0);
    el("entry-save").disabled = building || saving || problems.count > 0;
  }

  function editorParts() {
    const d = draft;
    const model = d.model;
    const problems = editorProblems();
    const parts = [];
    if (d.created) {
      parts.push(segmented([["pinned", "Every model · pinned", "Shared by every speech model, protected from suggestions"],
        ...(model ? [["learned", `Only ${shortName(model)}`, `Learned for ${speechName(model)}`]] : [])], d.scope, (value) => change((dd) => { dd.scope = value; }), "Use with"));
    } else {
      parts.push(node("p", d.scope === "pinned" ? "Pinned · used with every speech model." : `Learned · used only with ${speechName(model)}. Pin it from the list to share it.`, "caption editor-scope"));
    }
    if (d.scope === "learned" && !model) parts.push(node("p", "Choose a speech model in the toolbar first.", "field-error"));
    const notices = node("div", "", "editor-notices");
    notices.dataset.part = "notices:all";
    notices.append(...d.notices.map((notice) => node("p", notice, "editor-notice")));
    parts.push(notices);

    // Heard as: the one text this entry is for.
    const heard = node("section", "", "editor-section");
    const heardHead = node("div", "", "editor-head");
    heardHead.append(node("h3", "Heard as"), node("span", `what ${model ? shortName(model) : "the speech model"} writes`, "caption"));
    const card = node("div", "", "editor-card");
    card.append(input(d.text, "text", (value) => {
      if (d.direct) clearAlways("the heard text");
      d.text = value;
    }, { placeholder: "heard text", cls: "input mono heard-input", label: "Heard as" }), errorLine("text", problems.text));
    heard.append(heardHead, card);
    parts.push(heard);

    // Written as: the words it can stand for, one card each.
    const written = node("section", "", "editor-section");
    const writtenHead = node("div", "", "editor-head");
    writtenHead.append(node("h3", "Written as"), node("span", "the words it can stand for · the decision model picks one from the sentence", "caption"));
    written.append(writtenHead);
    for (const item of d.items) {
      const wordCard = node("div", "", "editor-card");
      const top = node("div", "", "editor-line");
      // Changing a spelling or capitals clears an approval that names this word.
      top.append(input(item.word.spelling, `w:${item.key}:spelling`, (value) => {
        item.word.spelling = value;
        if (d.direct === item.word.id) clearAlways(`${item.word.spelling || "the word"}'s spelling`);
      }, { placeholder: "Spelling", cls: "input strong" }), segmented([["fixed", "Exact capitals", "Always exactly like this: names, acronyms"], ["ordinary", "Normal word", "Capitalised only at a sentence start"]],
        item.word.casing, (value) => {
          if (value === item.word.casing) return;
          item.word.casing = value;
          if (d.direct === item.word.id) clearAlways(`${item.word.spelling || "the word"}'s capitals`);
          change(() => {});
        }, "Capitals"));
      const literal = node("span", "as written", "pill-note");
      literal.dataset.part = `literal:${item.key}`;
      literal.hidden = !literalItem(item);
      literal.title = "Spelled like the heard text: left as written when this word fits the sentence";
      top.append(literal);
      const remove = button("×", () => change((dd) => {
        dd.items = dd.items.filter((x) => x !== item);
        if (dd.direct === item.word.id) { dd.direct = null; dd.direct_reason = ""; }
      }), "btn-icon remove");
      remove.setAttribute("aria-label", `Remove ${item.word.spelling || "this word"} from this entry`);
      remove.title = "Remove this word from this entry; the word stays in your dictionary";
      top.append(remove);
      wordCard.append(top, errorLine(`w:${item.key}`, problems.items.get(item.key)));
      const warn = node("p", "Without a description the decision model can't choose this word. You can save; the word is flagged.", "field-error");
      warn.hidden = Boolean(words(item.word.meaning));
      const description = input(item.word.meaning, `w:${item.key}:meaning`, (value) => { item.word.meaning = value; description.classList.toggle("missing", !words(value)); warn.hidden = Boolean(words(value)); },
        { tag: "textarea", placeholder: "What it is and what it goes with. The decision model reads this.", label: "What it is" });
      description.classList.toggle("missing", !words(item.word.meaning));
      wordCard.append(description, warn,
        input(item.word.personal_context, `w:${item.key}:context`, (value) => { item.word.personal_context = value; }, { placeholder: "How you use it (optional)", cls: "input small" }));
      const self = (u) => !d.created && u.scope === d.scope && textKey(u.h.text) === textKey(d.origText) && (u.scope === "pinned" || u.model === d.model?.id);
      const others = usesOf(item.word.id).filter((u) => !self(u));
      if (others.length) wordCard.append(node("p", `Shared with ${others.slice(0, 3).map(whereUsed).join(", ")}${others.length > 3 ? ` and ${others.length - 3} more` : ""}: edits to this word apply there too.`, "caption"));
      written.append(wordCard);
    }
    // More words: a new one, the heard text kept as written, or one already in the dictionary.
    const more = node("div", "", "editor-line wrap");
    more.append(button("+ New word", () => change((dd) => { dd.items.push({ key: id("c"), word: { ...blankWord(), personal_context: "" }, stored: false, basis: "user", evidence: [] }); }), "btn link add-more"));
    const keep = button(`+ keep “${words(d.text)}” as written`, () => change((dd) => {
      const same = dict.words.find((w) => w.casing === "ordinary" && textKey(w.spelling) === textKey(dd.text));
      const word = same ? { ...same, personal_context: same.personal_context ?? "" } : { ...blankWord(), spelling: words(dd.text), casing: "ordinary", personal_context: "" };
      dd.items.push({ key: id("c"), word, stored: Boolean(same), basis: "literal", evidence: [] });
    }), "pill dashed");
    keep.dataset.part = "keep:text";
    keep.hidden = !canKeep(d);
    keep.title = "Adds the word spelled exactly like this, so it's left alone when that sense fits";
    more.append(keep);
    const others = dict.words.filter((w) => !d.items.some((item) => item.word.id === w.id));
    if (others.length) {
      const pick = document.createElement("select");
      pick.className = "pill dashed pick";
      pick.setAttribute("aria-label", "Add a word from your dictionary");
      pick.title = "Add a word already in your dictionary";
      pick.add(new Option("+ existing word…", ""));
      for (const w of [...others].sort((a, b) => a.spelling.localeCompare(b.spelling))) pick.add(new Option(`${w.spelling} — ${w.meaning || "no description yet"}`, w.id));
      pick.addEventListener("change", () => {
        const w = dict.words.find((x) => x.id === pick.value);
        if (w) change((dd) => { dd.items.push({ key: id("c"), word: { ...w, personal_context: w.personal_context ?? "" }, stored: true, basis: "user", evidence: [] }); });
      });
      more.append(pick);
    }
    written.append(more, errorLine("none", problems.none ? "Add a word it can stand for." : ""));
    parts.push(written);

    // Always: replace without reading the sentence. Rare.
    const always = node("section", "", "editor-section");
    const toggle = button("Always", () => change((dd) => {
      dd.direct = dd.direct ? null : dd.items[0]?.word.id ?? null;
      if (!dd.direct) dd.direct_reason = "";
    }), d.direct ? "pill always on" : "pill always");
    toggle.dataset.part = "always:button";
    toggle.disabled = !d.items.length;
    toggle.setAttribute("aria-pressed", String(Boolean(d.direct)));
    toggle.title = "Replace without reading the sentence. Rare.";
    const line = node("div", "", "editor-line");
    line.append(toggle);
    always.append(line);
    if (d.direct) {
      const box = node("div", "", "always-box");
      box.dataset.part = "always-box:always";
      const line = node("div", "", "editor-line wrap");
      const target = document.createElement("select");
      target.className = "select";
      target.dataset.part = "target:always";
      target.setAttribute("aria-label", "Always write");
      for (const item of d.items) target.add(new Option(spellingOf(item), item.word.id, false, item.word.id === d.direct));
      target.addEventListener("change", () => change((dd) => { dd.direct = target.value; }));
      line.append("Always write ", target, " without reading the sentence.");
      box.append(line, input(d.direct_reason, "reason", (value) => { d.direct_reason = value; },
        { placeholder: `Why can “${words(d.text)}” never mean anything else?`, cls: "input small", label: "Why this is always right" }),
      errorLine("reason", problems.direct ? "Write a reason. Use this only when the words can never mean anything else." : ""));
      always.append(box);
    }
    parts.push(always);
    return parts;
  }

  // A draft is based on its entry and words as they were when opened. After a reload it can
  // still be saved if they are unchanged; otherwise it reopens on the saved version, so a
  // save never undoes a change made elsewhere.
  function rebase(d) {
    if (d.created || d.version === dictVersion) return true;
    const list = d.scope === "pinned" ? dict.pinned : dict.learned[d.model.id] ?? [];
    const fresh = list.find((h) => textKey(h.text) === textKey(d.origText));
    if (fresh && snapshot(fresh) === d.base) { d.version = dictVersion; return true; }
    if (!fresh) { closeEditor(); toast("This entry was removed elsewhere; nothing was saved.", "err"); return false; }
    openEditor(d.scope, fresh, false, d.model);
    editorError = "This entry changed while you were editing and now shows the saved version. Redo your change.";
    editorStatus();
    return false;
  }

  el("entry-save").addEventListener("click", saveEditor);
  async function saveEditor() {
    const d = draft;
    if (!d || editorProblems().count || building || saving || !rebase(d)) return;
    const text = words(d.text);
    const sameText = !d.created && text === d.origText;
    const next = clone();
    // Words: an edited one replaces its stored copy everywhere; a new one is added.
    const changedOutputs = new Set();
    for (const item of d.items) {
      const meaning = item.word.meaning.trim();
      const word = { id: item.word.id, spelling: words(item.word.spelling), meaning, personal_context: item.word.personal_context.trim() || null,
        casing: item.word.casing, needs_review: !meaning };
      const at = next.words.findIndex((w) => w.id === word.id);
      if (at < 0) { next.words.push(word); continue; }
      const old = next.words[at];
      if (old.spelling !== word.spelling || old.casing !== word.casing) changedOutputs.add(word.id);
      next.words[at] = word;
    }
    const spelling = (wid) => next.words.find((w) => w.id === wid)?.spelling ?? "";
    // Other entries naming a word whose output changed: an approval naming it is cleared, and a
    // candidate no longer spelled like its heard text is the person's own, not as written.
    for (const list of [next.pinned, ...Object.values(next.learned)]) {
      for (const h of list) {
        if (changedOutputs.has(h.direct)) { h.direct = null; h.direct_reason = ""; }
        h.candidates = h.candidates.map((c) => c.basis === "literal" && changedOutputs.has(c.word) && textKey(spelling(c.word)) !== textKey(h.text)
          ? { word: c.word, basis: "user", evidence: [] } : c);
      }
    }
    const entry = {
      text,
      candidates: d.items.map((item) => {
        // A changed heard text loses the evidence that located the old one in transcripts.
        const old = sameText ? d.original.candidates.find((c) => c.word === item.word.id) : null;
        const literal = textKey(spelling(item.word.id)) === textKey(text) && literalItem(item);
        const basis = literal ? "literal" : old && old.basis !== "literal" ? old.basis : "user";
        return { word: item.word.id, basis, evidence: old && old.basis === basis ? old.evidence : [] };
      }),
      direct: d.direct || null,
      direct_reason: d.direct ? d.direct_reason.trim() : "",
    };
    const list = d.scope === "pinned" ? next.pinned : (next.learned[d.model.id] ??= []);
    const at = d.created ? -1 : list.findIndex((h) => textKey(h.text) === textKey(d.origText));
    if (at < 0) list.push(entry); else list[at] = entry;
    const version = dictVersion;
    saving = true;
    editorStatus();
    const problem = await saveDictionary(next).finally(() => { saving = false; editorStatus(); });
    // The panel may have been closed, or another entry opened, while this was saving.
    const current = draft === d;
    if (problem) {
      if (!current) { toast(problem, "err"); return; }
      // A refused save that reloaded the dictionary: keep the draft only if its entry is unchanged.
      if (dictVersion !== version) {
        if (!rebase(d)) return;
        editorError = "The dictionary changed elsewhere and was reloaded. Save again to apply this.";
      } else editorError = problem;
      editorStatus();
      return;
    }
    if (current) closeEditor();
    if (current && d.created) { clearAdd(); showAdd(false); }
    reveal(d.scope, text);
    toast(`${d.created ? "Added" : "Saved"} “${text}”`);
  }

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
        : "dictionary.json in Entune's data folder, the same content as the list. Heard texts match whole words regardless of case.";
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
  // What learning from audio costs depends on the speech model it is for.
  function drawMenu() {
    const model = getModel();
    const name = model ? shortName(model) : "your speech model";
    const local = isLocal(model?.id);
    el("learn-pop-lead").textContent = `New speech model, or coming from another dictation app? Let ${name} listen to recordings you already have. It learns your names, your terms and the way you say them.`;
    el("learn-step-1").textContent = `${model ? name : "Your speech model"} transcribes it again`;
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
    history: (m) => `Reads what ${m} already wrote and suggests entries for the words it got wrong.`,
    entune: (m) => `Recordings you made in Entune with other speech models. ${m} transcribes them again, then Entune suggests entries for the words it gets wrong.`,
    provider: (m) => `Recordings another dictation app keeps on ${THIS_DEVICE}. ${m} transcribes them, then Entune suggests entries for the words it gets wrong.`,
    folder: (m) => `Audio of you talking, such as meetings or voice notes. ${m} transcribes it, then Entune suggests entries for the words it gets wrong.`,
  };
  const HOW = {
    history: (m) => [`Get suggestions reads up to 300 of ${m}'s newest transcripts that suggestions you applied haven't used yet. A run you discard or stop uses none.`,
      "Each transcript is read once, so you don't pay twice for the same text. To read earlier ones again, open Advanced and choose a span."],
    entune: (m) => [`Each speech model mishears different words, so what your other models learned doesn't carry over to ${m}. These recordings let ${m} catch up.`,
      `The hours shown are recorded audio, not how long the run takes. ${m} transcribes first; then the suggestion model reads the new transcripts.`],
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
    const local = isLocal(model?.id);
    const sm = suggestionName();
    el("learn-source").textContent = model ? LEAD[setupSource](m) : "Choose a speech model first.";
    const tag = el("cost-tag");
    tag.textContent = audio ? "Transcribes again" : "Nothing to transcribe";
    tag.classList.toggle("warn", audio);
    el("cost-note").textContent = !audio ? "Uses the transcripts you already have."
      : local ? `${m} runs on ${THIS_DEVICE}: no charge, but it takes a while.` : `${m} charges for transcribing the audio you select.`;
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
            : `${m} bills you for ${duration(plan.seconds)} of audio. ${took[0].toUpperCase()}${took.slice(1)}${plan.workers > 1 ? `, ${plan.workers} recordings at a time` : ""}.`;
        const read = plan.unknown ? `${reader} reads the new transcripts in parts, starting while transcription runs.`
          : `${reader} reads the new transcripts in ${partsText(plan.parts, plan.perPart, plan.measuredOnce)}, starting while transcription runs.${plan.perPart ? "" : " Not timed yet with these settings."}`;
        what = [{ title: `${m} transcribes again`, body: transcribe, extra: true, cls: "extra" }, { title: "Find suggestions", body: read }, review];
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
        : `${plural(proposalChanges.length, "suggestion")} ready: ${[plural(kinds("add"), "new entry", "new entries"), plural(kinds("update"), "change"),
          ...(kinds("remove") ? [plural(kinds("remove"), "removal")] : []), ...(kinds("word") ? [plural(kinds("word"), "clearer meaning")] : [])].join(", ")}` });
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
    if (now === "review") proposalTitle();
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

  // A suggestion names words by ID: new ones from the proposal, as the review edits them.
  const isNewWord = (wid) => !dict.words.some((w) => w.id === wid);
  const proposalWord = (wid) => proposalWords.get(wid) ?? wordMap().get(wid);
  const label = (h) => h ? h.candidates.map((c) => proposalWord(c.word)?.spelling ?? c.word).join(" · ") : "";
  const describe = (h) => h ? [`Heard as: ${h.text}`, ...h.candidates.map((c) => {
    const w = proposalWord(c.word);
    return `${w?.spelling ?? c.word}${c.basis === "literal" ? " (as written)" : ""}: ${w?.meaning || "no description yet"}`;
  })].join("\n") : "";
  const descriptions = (h) => h.candidates.map((c) => {
    const w = proposalWord(c.word);
    return `${w?.spelling ?? c.word}${isNewWord(c.word) ? " (new)" : ""}: ${w?.meaning || "no description yet"}`;
  }).join(" · ");
  // Suggestions belong to the speech model they were made for; say so when the toolbar
  // shows another one, since applying still updates that model's dictionary.
  let proposalLabel = "";
  function proposalTitle() {
    if (!proposalModel) return;
    const model = getModel();
    if (model?.id === proposalModel) proposalLabel = modelName(model.id, [model]);
    el("suggest-title").textContent = model?.id === proposalModel ? "Review suggestions" : `Review suggestions for ${proposalLabel}`;
  }
  function renderProposal(p) {
    proposalLabel = modelName(p.model, []);
    proposalModel = p.model;
    proposalWords = new Map(p.words.map((w) => [w.id, structuredClone(w)]));
    // An entry's words as suggested, so one left out while editing can be taken back.
    proposalChanges = p.changes.map(c => ({...structuredClone(c), included: true, editing: false,
      choices: c.kind !== "word" && c.after ? structuredClone(c.after.candidates) : []}));
    drawProposal();
  }
  // Each suggestion's summary shows what Apply submits, so it follows every edit, including
  // edits to a new word that other suggestions name too.
  function refreshSummaries() {
    for (const change of proposalChanges) {
      if (!change.after || change.kind === "word") continue;
      const set = (part, text) => { const e = proposalBody.querySelector(`[data-summary="${CSS.escape(`${part}:${change.id}`)}"]`); if (e) e.textContent = text; };
      set("to", label(change.after));
      set("after", describe(change.after));
      set("desc", descriptions(change.after));
    }
  }
  function field(parent, text, value, update, tag = "input") {
    const wrap = node("label", text, "group-field");
    const control = document.createElement(tag);
    control.className = "input";
    if (tag === "textarea") control.rows = 2;
    control.value = value ?? ""; control.addEventListener("change", () => { update(control.value); refreshSummaries(); });
    wrap.append(control); parent.append(wrap); return control;
  }
  function drawProposal() {
    proposalBody.replaceChildren();
    for (const [kind, heading] of [["add", "New entries"], ["update", "Changes"], ["remove", "Remove"], ["word", "Clearer meanings"]]) {
      const items = proposalChanges.filter(c => c.kind === kind);
      if (!items.length) continue;
      proposalBody.append(node("h3", `${heading} · ${items.length}`));
      for (const change of items) {
        const box = node("section", "", "proposal-change");
        box.classList.toggle("dismissed", !change.included);
        const head = node("div", "", "change-head");
        const struck = kind === "remove" && change.included ? " struck" : "";
        const summary = (element, part) => { element.dataset.summary = `${part}:${change.id}`; return element; };
        const name = kind === "word" ? change.after.spelling : (change.after || change.before).text;
        if (kind === "word") head.append(node("b", name), node("span", "", "spacer"));
        else {
          const h = change.after || change.before;
          head.append(node("code", h.text, `heard${struck}`), node("span", "→", "arrow"),
            summary(node("b", label(h), struck.trim()), "to"), node("span", "", "spacer"));
        }
        const toggle = change.included
          ? button("×", () => { change.included = false; drawProposal(); }, "btn-icon remove")
          : button("Include", () => { change.included = true; drawProposal(); }, "btn link");
        toggle.setAttribute("aria-label", `${change.included ? "Leave out" : "Include"} ${kind === "word" ? "the clearer meaning of" : kind} ${name}`);
        toggle.title = change.included ? "Leave this suggestion out; your current dictionary is kept as it is" : "Include this suggestion again";
        head.append(toggle);
        box.append(head);
        if (kind === "word" || kind === "update") {
          const compare = node("div", "", "compare");
          const shown = (value) => kind === "word" ? value.meaning || "No description yet." : describe(value);
          compare.append(node("span", "Now", "caption"), node("pre", shown(change.before), "before"),
            node("span", "Suggested", "suggested"), summary(node("pre", shown(change.after), "after"), "after"));
          box.append(compare);
        } else if (kind === "add") {
          box.append(summary(node("p", descriptions(change.after), "change-desc"), "desc"));
        } else {
          box.append(node("p", "Removed only if you keep this suggestion when applying. Its words stay in your dictionary.", "caption"));
        }
        if (change.after && change.included) {
          const editor = node("details", "", "proposal-editor");
          editor.append(node("summary", "Edit this suggestion"));
          editor.open = change.editing;
          editor.addEventListener("toggle", () => { change.editing = editor.open; });
          if (kind === "word") {
            const row = node("div", "", "meaning-editor");
            field(row, "What it is", change.after.meaning, value => { change.after.meaning = value.trim(); }, "textarea");
            editor.append(row);
          } else {
            // The words it can stand for: one left out stays offered here; at least one stays.
            const choices = node("div", "", "proposal-associations");
            for (const choice of change.choices) {
              const w = proposalWord(choice.word);
              const wrap = node("label", "", "caption check");
              const check = document.createElement("input"); check.type = "checkbox";
              check.checked = change.after.candidates.some(c => c.word === choice.word);
              check.disabled = check.checked && change.after.candidates.length === 1;
              check.addEventListener("change", () => {
                change.after.candidates = change.choices.filter(c => c.word === choice.word ? check.checked : change.after.candidates.some(x => x.word === c.word));
                if (!change.after.candidates.some(c => c.word === change.after.direct)) { change.after.direct = null; change.after.direct_reason = ""; }
                drawProposal();
              });
              wrap.append(check, `${w?.spelling ?? choice.word}${choice.basis === "literal" ? " (as written)" : ""} — ${w?.meaning || "no description yet"}`);
              choices.append(wrap);
            }
            editor.append(choices);
            // A new word is defined here, once, for every suggestion that names it.
            for (const choice of change.choices.filter(c => isNewWord(c.word) && proposalWords.has(c.word))) {
              const word = proposalWords.get(choice.word);
              const row = node("div", "", "meaning-editor");
              field(row, "Write it as", word.spelling, value => {
                word.spelling = value.trim();
                // An "as written" candidate stays one only while it is spelled like its heard text.
                for (const c of proposalChanges) {
                  if (c.kind === "word" || !c.after) continue;
                  c.after.candidates = c.after.candidates.map(x => x.word === word.id && x.basis === "literal" && textKey(word.spelling) !== textKey(c.after.text)
                    ? {word: x.word, basis: "user", evidence: []} : x);
                }
                for (const control of proposalBody.querySelectorAll(`input[data-word="${CSS.escape(word.id)}"]`)) control.value = word.spelling;
              }).dataset.word = word.id;
              field(row, "What it is", word.meaning, value => { word.meaning = value.trim(); word.needs_review = !word.meaning; });
              field(row, "How you use it (optional)", word.personal_context, value => { word.personal_context = value.trim() || null; });
              editor.append(row);
            }
          }
          box.append(editor);
        }
        proposalBody.append(box);
      }
    }
    const included = proposalChanges.filter(c => c.included).length;
    el("accept-proposal").textContent = included ? `Apply ${plural(included, "change")}` : "Finish without changes";
    el("suggest-badge").textContent = String(included);
    if (!proposalChanges.length) proposalBody.append(node("p", "No changes suggested. Finish to close this review; the same dictations can be used again later.", "caption"));
  }
  el("build-dictionary").addEventListener("click", () => {
    const selection = historyReuse.selection();
    if (!selection) { flash(buildStatus, "The transcripts are still loading. Try again in a moment.", "err"); return; }
    builds.start("history", {...selection, ...runSettings()});
  });

  return { load: loadDictionary, refreshAudio: () => onboarding.load() };
}
