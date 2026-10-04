import { THIS_DEVICE, api, el, errorText, flash, modelName, segmentedGroup } from "./ui.js";
import { createAudioOnboarding } from "./audio-onboarding.js";
import { createDictionaryBuild } from "./dictionary-build.js";

// The dictionary owns its document, revision and pending proposal. Model selection
// stays in the app; getters read the current selection when an action is made.
export function createDictionary({ getModel, getSettings, onSettingsChanged, openSettings }) {
  // Confusion groups are the edit unit; stable IDs distinguish meanings with one spelling.
  let dict = { version: 2, pinned: [], learned: {} };
  let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
  let savedText = ""; // the JSON view's last loaded or saved text
  let importing = false;
  let building = false;
  let run = { phase: "idle" };
  let filter = "all";
  let query = "";
  let sortDir = 1;
  let openId = null; // the one entry shown expanded, as scope:group (pinning can split a group's ID across both)
  let addPinned = false;
  let draft = null; // the entry open in the editor panel
  let editorError = ""; // a refused save, shown until the draft changes
  let saving = false; // one editor save at a time
  let proposalChanges = [];
  let proposalModel = null;
  let missingKey = false; // the saved dictionary model's provider has no key
  const modelSelect = el("dictionary-model");
  const buildStatus = el("build-status");
  const proposalBody = el("proposal-body");
  const entryDrawer = el("entry-drawer");
  const suggestDrawer = el("suggest-drawer");
  const jsonBox = el("dictionary");
  // How a run works: the suggestion model's reasoning and the part size, remembered in
  // this browser. Both shape how long each reply takes; see drawEffect.
  const effortSelect = el("suggest-effort"), partSelect = el("suggest-part");
  try {
    effortSelect.value = localStorage.getItem("entune.suggest.effort") ?? "medium";
    partSelect.value = localStorage.getItem("entune.suggest.part") ?? "standard";
  } catch { /* storage unavailable: the defaults stand */ }
  const runSettings = () => ({ effort: effortSelect.value || "medium", part: partSelect.value || "standard" });
  const onboarding = createAudioOnboarding({
    getModel, getSettings, getRunSettings: runSettings,
    getDictionaryModelName: () => missingKey ? null : languageName(getSettings()?.dictionaryModel),
    onBuild(selection) { return builds.start("audio", { mode: mode(), ...selection, ...runSettings() }); },
    onBusy(value) { importing = value; gate(); },
  });

  const builds = createDictionaryBuild({
    onBusy(value) { building = value; gate(); onboarding.setBuildBusy(value); lockEditors(); },
    onState(value) { showRun(value); },
    onProposal(value) { if (value) renderProposal(value); else { proposalChanges = []; el("proposal").hidden = true; } },
    onAccepted: () => loadDictionary(false),
    getSelected: () => proposalChanges.filter(c => c.included).map(c => ({id: c.id, after: c.after})),
    getRunSettings: runSettings,
  });
  for (const [select, key] of [[effortSelect, "effort"], [partSelect, "part"]]) {
    select.addEventListener("change", () => {
      try { localStorage.setItem(`entune.suggest.${key}`, select.value); } catch { /* not remembered */ }
      drawEffect();
      onboarding.redraw();
    });
  }
  // What the two choices do, in one line; on a ChatGPT plan, also its limit per reply.
  function drawEffect() {
    const model = getSettings()?.dictionaryModel ?? "";
    const { effort, part } = runSettings();
    const parts = part === "small" ? "Smaller parts: about 8,000 characters per request, so more requests that each finish sooner."
      : "Standard parts: about 24,000 characters per request.";
    const replies = effort === "low" ? "Faster replies reason less." : "Thorough replies reason more and take longer.";
    const plan = model.startsWith("chatgpt:")
      ? ` On a ChatGPT plan each reply must finish within about 15 minutes${model === "chatgpt:gpt-6-astra" && effort === "medium" ? "; GPT-6 Astra often needs Faster to stay within it" : ""}.`
      : "";
    el("run-effect").textContent = `${replies} ${parts}${plan}`;
  }
  // The speech model the dictionary is for: the same choice as the toolbar's default model.
  const speechSelect = el("suggest-speech");
  function fillSpeech() {
    const toolbar = el("model");
    speechSelect.replaceChildren(...[...toolbar.options].filter((o) => o.value).map((o) => new Option(o.textContent, o.value, false, o.value === toolbar.value)));
    speechSelect.disabled = building || !speechSelect.options.length;
  }
  speechSelect.addEventListener("change", () => {
    const toolbar = el("model");
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

  // A heard form is kept as written only when it is the meaning's own spelling: exactly
  // for fixed capitals, ignoring case for a normal word. "anthropic" → "Anthropic" is a fix.
  function isLiteral(meaning, text) {
    const a = words(meaning?.spelling ?? ""), b = words(text ?? "");
    if (!a || !b) return false;
    return meaning.casing === "ordinary" ? a.toLowerCase() === b.toLowerCase() : a === b;
  }
  function meanings(doc = dict, modelId = getModel()?.id) {
    const learned = modelId ? doc.learned[modelId] ?? [] : [];
    return new Map([...doc.pinned, ...learned].flatMap((g) => g.meanings).map((m) => [m.id, m]));
  }
  const pinnedIds = () => new Set(dict.pinned.flatMap((g) => g.meanings.map((m) => m.id)));
  // An extension group has no meanings of its own; it is named by the meanings it links to.
  function title(g, known = meanings()) {
    if (g.meanings.length) return g.meanings.map((m) => m.spelling).join(" · ");
    return [...new Set(g.recognized_forms.flatMap((f) => f.associations.map((a) => a.meaning_id)))].map((mid) => known.get(mid)?.spelling ?? mid).join(" · ");
  }
  const attention = (g) => g.needs_review || g.meanings.some((m) => !m.meaning.trim());
  // The entries that apply to the selected speech model: pinned, then its learned ones.
  function visible() {
    const model = getModel();
    return [...dict.pinned.map((g) => ({ g, scope: "pinned" })), ...(model ? dict.learned[model.id] ?? [] : []).map((g) => ({ g, scope: "learned" }))];
  }

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
  async function loadDictionary(pollBuild = true) {
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
    el("pin-all").disabled = building || !(dict.learned[getModel()?.id] ?? []).length;
    modelSelect.disabled = building || modelSelect.value === "";
  }

  // One action: with no entries that apply to the selected speech model (pinned or its
  // learned), suggestions start a dictionary; once any apply, they refine it, which can
  // also add missing entries. The server's effective dictionary is the same union.
  function mode() {
    const learned = dict.learned[getModel()?.id] ?? [];
    return dict.pinned.length || learned.length ? "refine" : "generate";
  }

  // ---- The table ----
  const selectFilter = segmentedGroup({ all: el("filter-all"), pinned: el("filter-pinned"), learned: el("filter-learned"), attention: el("filter-attention") }, (name) => { filter = name; openId = null; renderRows(); });
  el("dict-search").addEventListener("input", (event) => { query = event.target.value; renderRows(); });
  el("sort-spelling").addEventListener("click", () => { sortDir = -sortDir; renderRows(); });

  function renderDictionary(jsonText) {
    if (jsonText !== undefined) { savedText = jsonText; jsonBox.value = jsonText; el("json-error").hidden = true; }
    const model = getModel();
    const groups = visible();
    const learned = groups.filter((v) => v.scope === "learned").length;
    if (filter === "learned" && !model) { filter = "all"; selectFilter("all"); }
    const counts = { all: groups.length, pinned: dict.pinned.length, learned, attention: groups.filter((v) => attention(v.g)).length };
    for (const [name, count] of Object.entries(counts)) el(`count-${name}`).textContent = count ? String(count) : "";
    el("attention-dot").hidden = !counts.attention;
    el("filter-learned-name").textContent = shortName(model);
    el("filter-learned").title = model ? `Learned only for ${speechName(model)}` : "Choose a speech model in the toolbar first";
    el("filter-learned").disabled = !model;
    el("legend-model").textContent = shortName(model);
    el("pin-all-label").textContent = model ? `Pin all learned for ${shortName(model)}` : "Pin all learned";
    el("pin-all-count").textContent = learned ? plural(learned, "entry", "entries") : "";
    el("learn-source").textContent = !model ? "Choose a speech model first."
      : `Reads your newest transcripts that no earlier run has read, and suggests ${mode() === "generate" ? "entries for the words your speech model gets wrong" : "fixes and missing entries"}.`;
    if (proposalModel) proposalTitle();
    renderRows();
    fillModels();
    lockEditors();
  }

  function renderRows() {
    const model = getModel();
    const known = meanings();
    const groups = visible();
    const q = query.trim().toLowerCase();
    const shown = groups
      .filter((v) => filter === "all" || (filter === "attention" ? attention(v.g) : v.scope === filter))
      .filter((v) => !q || [title(v.g, known), ...v.g.recognized_forms.map((f) => f.text), ...v.g.meanings.map((m) => m.meaning)].join(" ").toLowerCase().includes(q))
      .sort((a, b) => title(a.g, known).localeCompare(title(b.g, known)) * sortDir);
    el("sort-mark").textContent = sortDir > 0 ? "↑" : "↓";
    el("dict-count").textContent = shown.length === groups.length ? plural(groups.length, "entry", "entries") : `${shown.length} of ${groups.length}`;
    const pinned = pinnedIds();
    el("dict-rows").replaceChildren(...shown.map((v) => entryRow(v, known, pinned)));
    const empty = !shown.length;
    el("dict-empty").hidden = !empty;
    if (empty) {
      const short = shortName(model);
      const [heading, body] = q ? ["No matches", "Try a spelling, a heard form or a word from a description."]
        : filter === "learned" ? [`Nothing learned for ${short} yet`, "Each speech model makes its own mistakes. Suggestions read its recent transcripts and propose first entries."]
          : filter === "attention" ? ["Nothing needs attention", "Every meaning has a description."]
            : filter === "pinned" ? ["No pinned entries", "Pin a learned entry to use it with every speech model, or add one yourself."]
              : ["Your dictionary is empty", "Add a word, or get suggestions from your transcripts."];
      el("empty-title").textContent = heading;
      el("empty-body").textContent = body;
      el("empty-suggest").hidden = Boolean(q) || filter === "attention";
    }
    lockEditors();
  }

  const PIN = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M6 1.5h4l-.6 4.5 2.8 2.5H3.8L6.6 6 6 1.5zM8 8.5V14.5"/></svg>';
  // One line per entry; a click shows its heard forms and meanings underneath.
  function entryRow({ g, scope }, known, pinned) {
    const model = getModel();
    const item = node("div", "", "dict-entry");
    const line = node("div", "", "dict-row");
    const rowKey = `${scope}:${g.id}`;
    const open = openId === rowKey;
    line.tabIndex = 0;
    line.setAttribute("role", "button");
    line.setAttribute("aria-expanded", String(open));
    const name = node("span", "", "cell-title");
    name.append(node("span", title(g, known), "title-text"));
    if (attention(g)) {
      const dot = node("span", "", "attention-dot");
      dot.title = g.meanings.some((m) => !m.meaning.trim()) ? "Needs a description before Entune can choose it" : "Imported: check the descriptions and capitals";
      name.append(dot);
    }
    const description = g.meanings.find((m) => m.meaning.trim())?.meaning;
    const linksPinned = !g.meanings.length && g.recognized_forms.every((f) => f.associations.every((a) => pinned.has(a.meaning_id)));
    const what = node("span", description ?? (g.meanings.length ? "No description yet" : `Another way ${shortName(model)} hears ${linksPinned ? "a pinned word" : "a word from another entry"}`), description ? "cell-desc" : "cell-desc faint");
    const where = node("span", "", "cell-scope");
    if (scope === "pinned") where.innerHTML = PIN;
    where.append(scope === "pinned" ? "Pinned" : shortName(model));
    line.append(name, node("span", g.recognized_forms.map((f) => f.text).join(" · "), "cell-heard"), what, where);
    const toggle = () => { openId = open ? null : rowKey; renderRows(); document.querySelector(`[data-entry="${CSS.escape(rowKey)}"] .dict-row`)?.focus(); };
    line.addEventListener("click", toggle);
    line.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
    item.dataset.entry = rowKey;
    item.classList.toggle("open", open);
    item.append(line);
    if (open) item.append(entryDetail(g, scope, known, pinned));
    return item;
  }

  function entryDetail(g, scope, known, pinned) {
    const model = getModel();
    const detail = node("div", "", "dict-detail");
    const forms = node("div", "", "detail-forms");
    forms.append(node("p", "Heard → written", "detail-label"));
    for (const form of g.recognized_forms) {
      const line = node("div", "", "heard-line");
      line.append(node("code", form.text, "heard"), node("span", "→", "arrow"));
      form.associations.forEach((link, i) => {
        if (i) line.append(node("span", "or", "or"));
        const meaning = known.get(link.meaning_id);
        const own = g.meanings.some((m) => m.id === link.meaning_id);
        const note = link.basis === "literal" ? " (as written)" : own ? "" : pinned.has(link.meaning_id) ? " (pinned)" : " (other entry)";
        const target = node("span", `${meaning?.spelling ?? link.meaning_id}${note}`, link.basis === "literal" ? "target literal" : "target");
        if (link.basis === "literal") target.title = "Left as written when this meaning fits the sentence";
        line.append(target);
      });
      if (form.direct) {
        const chip = node("span", "Always", "always-chip");
        chip.title = `Always used, without reading the sentence: ${form.direct_reason}`;
        line.append(chip);
      }
      forms.append(line);
    }
    if (!g.recognized_forms.length) forms.append(node("p", "No heard forms yet.", "caption"));
    const list = node("div", "", "detail-meanings");
    list.append(node("p", "Meanings · the decision model picks from the sentence", "detail-label"));
    for (const meaning of g.meanings) {
      const row = node("div", "", "meaning-line");
      const head = node("span", "", "meaning-name");
      head.append(node("b", meaning.spelling), node("span", meaning.casing === "fixed" ? "exact capitals" : "normal word", "casing"));
      const text = node("span", "", "meaning-text");
      text.append(node("span", meaning.meaning || "No description: the decision model can't choose it yet.", meaning.meaning ? "definition" : "definition missing"));
      if (meaning.personal_context) text.append(node("span", meaning.personal_context, "personal"));
      // Pinning one meaning of several leaves its competitors learned for this model.
      if (scope === "learned" && g.meanings.length > 1) {
        const pin = button("Pin", () => pinMeanings(g, [meaning]), "btn link pin-meaning");
        pin.title = `Use ${meaning.spelling} with every speech model`;
        text.append(pin);
      }
      row.append(head, text);
      list.append(row);
    }
    if (!g.meanings.length) list.append(node("p", `No meanings of its own: teaches ${shortName(model)} another way it hears a word from another entry.`, "caption"));
    if (g.needs_review) list.append(node("p", "Imported: check the descriptions and capitals.", "caption warn"));
    const actions = node("div", "", "detail-actions");
    actions.append(button("Edit", () => openEditor(scope, g), "btn fill sm"));
    if (scope === "learned") {
      const pin = button("Pin", () => pinMeanings(g, g.meanings), "btn sm");
      pin.title = "Use with every speech model and protect from suggestions";
      pin.hidden = !g.meanings.length;
      actions.append(pin);
    }
    actions.append(button("Remove", () => removeEntry(scope, g), "btn ghost sm remove"), node("span", "", "spacer"),
      node("span", scope === "pinned" ? "Pinned · every speech model" : `Learned · ${speechName(model)}`, "caption"));
    list.append(actions);
    detail.append(forms, list);
    return detail;
  }

  async function pin(body, model = getModel()?.id) {
    const res = await fetch("/api/dictionary/pin", {
      method: "POST", headers: { "content-type": "application/json", "if-match": dictVersion },
      body: JSON.stringify({ model, ...body }),
    });
    if (!res.ok) { toast(await res.text(), "err"); await loadDictionary(); return false; }
    await loadDictionary();
    return true;
  }
  // The server pins one meaning at a time; the learned group keeps its ID for the rest, under
  // the model it was learned for even if the toolbar selection changes meanwhile.
  async function pinMeanings(g, list) {
    const model = getModel()?.id;
    for (const meaning of list) if (!await pin({ group: g.id, meaning: meaning.id }, model)) return;
    toast(`Pinned ${list.map((m) => m.spelling).join(" · ")} for every speech model`);
  }
  el("pin-all").addEventListener("click", async () => {
    const count = (dict.learned[getModel()?.id] ?? []).length;
    closeMenu();
    if (await pin({})) toast(`Pinned ${plural(count, "entry", "entries")}`);
  });

  // Removing an entry removes the links other entries made to its meanings, where no other
  // entry still defines them: pinned ones for every section, learned ones for their model.
  // A heard form left with no meaning, and an entry left empty, go with them.
  function withoutMeanings(doc, removed) {
    const defined = (groups) => groups.flatMap((g) => g.meanings.map((m) => m.id));
    const pinned = defined(doc.pinned);
    let touched = 0;
    for (const section of [doc.pinned, ...Object.values(doc.learned)]) {
      const available = new Set(section === doc.pinned ? pinned : [...pinned, ...defined(section)]);
      const gone = new Set([...removed].filter((mid) => !available.has(mid)));
      if (!gone.size) continue;
      for (let i = section.length - 1; i >= 0; i--) {
        const g = section[i];
        let changed = false;
        g.recognized_forms = g.recognized_forms.filter((f) => {
          const kept = f.associations.filter((a) => !gone.has(a.meaning_id));
          if (kept.length !== f.associations.length) {
            changed = true;
            f.associations = kept;
            if (gone.has(f.direct)) { f.direct = null; f.direct_reason = ""; }
          }
          return kept.length > 0;
        });
        if (changed) touched++;
        if (!g.meanings.length && !g.recognized_forms.length) section.splice(i, 1);
      }
    }
    return touched;
  }
  async function removeEntry(scope, g) {
    const version = dictVersion; // a reload while confirming makes this a conflict, not an overwrite
    const next = clone();
    const list = scope === "pinned" ? next.pinned : next.learned[getModel()?.id] ?? [];
    const at = list.findIndex((x) => x.id === g.id);
    if (at >= 0) list.splice(at, 1);
    const others = withoutMeanings(next, new Set(g.meanings.map((m) => m.id)));
    const name = title(g);
    const body = "Its meanings and heard forms are deleted." + (others ? ` ${plural(others, "other entry", "other entries")} linked to its meanings; those links are removed too.` : "");
    if (!await confirmAction(`Remove “${name}”?`, body, "Remove")) return;
    const problem = await saveDictionary(next, false, version);
    if (problem) { toast(problem, "err"); return; }
    openId = null;
    renderRows();
    toast(`Removed ${name}`);
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
    scope.textContent = addPinned ? "Pinned" : `${shortName(model)} only`;
    scope.title = addPinned ? (model ? "Used with every speech model. Click to use it only with this one." : "Used with every speech model") : `Only ${speechName(model)}. Click to pin it for every speech model.`;
    scope.setAttribute("aria-pressed", String(addPinned));
    scope.disabled = building || !model;
    el("add-save").disabled = building || !words(addFields.spelling.value) || !heardList().length;
    const hint = el("add-hint");
    hint.classList.toggle("err", Boolean(addProblem));
    hint.textContent = addProblem || (heardList().length > 1 ? `${heardList().length} heard forms, separated by commas.`
      : words(addFields.desc.value) ? "Enter to add." : "A description is optional to save, but the decision model can't choose the word without it.");
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
    const meaning = { id: id("m"), spelling, meaning: description, personal_context: null, casing: "fixed" };
    const group = { id: id("g"), needs_review: !description, meanings: [meaning],
      recognized_forms: heard.map((text) => ({ text, associations: [{ meaning_id: meaning.id, basis: isLiteral(meaning, text) ? "literal" : "user", evidence: [] }], direct: null, direct_reason: "" })) };
    const scope = addPinned || !model ? "pinned" : "learned";
    const next = clone();
    (scope === "pinned" ? next.pinned : (next.learned[model.id] ??= [])).push(group);
    addProblem = await saveDictionary(next);
    drawAdd();
    if (addProblem) return;
    clearAdd();
    showAdd(false);
    reveal(scope, group.id);
    toast(`Added ${spelling}`);
  }
  // A saved entry opens expanded, whatever filter or search would have hidden it.
  function reveal(scope, groupId) {
    openId = `${scope}:${groupId}`;
    query = ""; el("dict-search").value = "";
    if (filter !== "all") { filter = "all"; selectFilter("all"); }
    renderRows();
    document.querySelector(`[data-entry="${CSS.escape(openId)}"]`)?.scrollIntoView({ block: "nearest" });
  }
  el("add-advanced").addEventListener("click", () => {
    const model = getModel();
    const meaning = { id: id("m"), spelling: words(addFields.spelling.value), meaning: words(addFields.desc.value), personal_context: null, casing: "fixed" };
    const heard = heardList();
    openEditor(addPinned || !model ? "pinned" : "learned", { id: id("g"), needs_review: false, meanings: [meaning],
      recognized_forms: (heard.length ? heard : [""]).map((text) => ({ text, associations: [{ meaning_id: meaning.id, basis: "user", evidence: [] }], direct: null, direct_reason: "" })) }, true);
  });
  el("empty-suggest").addEventListener("click", () => openSuggestions("history"));

  // ---- The full editor, in a side panel ----
  // A learned draft belongs to the speech model it was started for, even if the toolbar
  // selection changes before it is saved.
  function openEditor(scope, group, created = false, model = getModel()) {
    editorError = "";
    draft = {
      id: group.id, created, scope, notices: [], model,
      version: dictVersion, // the revision this draft was made from
      original: structuredClone(group),
      meanings: group.meanings.map((m) => ({ ...m, meaning: m.meaning ?? "", personal_context: m.personal_context ?? "" })),
      forms: group.recognized_forms.map((f) => ({
        key: id("f"), text: f.text, origText: created ? null : f.text, links: f.associations.map((a) => a.meaning_id),
        basis: Object.fromEntries(f.associations.map((a) => [a.meaning_id, { basis: a.basis, evidence: a.evidence ?? [] }])),
        direct: f.direct, direct_reason: f.direct_reason ?? "",
      })),
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
  const spellingOf = (mid) => draft.meanings.find((m) => m.id === mid)?.spelling || meanings(dict, draft.model?.id).get(mid)?.spelling || "(no spelling)";
  const canKeep = (form) => Boolean(words(form.text)) && !draft.meanings.some((m) => literalLink(form, m.id, m));
  function pillLabel(pill, form, meaning) {
    const on = form.links.includes(meaning.id);
    pill.replaceChildren(`${on ? "✓ " : ""}${meaning.spelling || "(no spelling)"}`);
    if (on && literalLink(form, meaning.id, meaning)) pill.append(node("span", " · as written", "pill-note"));
  }
  function refresh() {
    const d = draft;
    const problems = editorProblems();
    for (const meaning of d.meanings) {
      showError(part("error", `m:${meaning.id}`), problems.meanings.get(meaning.id));
      part("remove-meaning", meaning.id)?.setAttribute("aria-label", `Remove ${meaning.spelling || "this meaning"} and its links`);
    }
    for (const form of d.forms) {
      showError(part("error", `f:${form.key}`), problems.forms.get(form.key));
      showError(part("error", `r:${form.key}`), problems.direct.has(form.key) ? "Write a reason. Use this only when the phrase can never mean anything else." : "");
      for (const meaning of d.meanings) { const pill = part("pill", `${form.key}|${meaning.id}`); if (pill) pillLabel(pill, form, meaning); }
      const keep = part("keep", form.key);
      if (keep) { keep.hidden = !canKeep(form); keep.textContent = `+ keep “${words(form.text)}” as written`; }
      const reason = el("entry-body").querySelector(`[data-key="${CSS.escape(`f:${form.key}:reason`)}"]`);
      if (reason) reason.placeholder = `Why can “${words(form.text)}” never mean anything else?`;
      for (const option of part("target", form.key)?.options ?? []) option.textContent = spellingOf(option.value);
      part("remove-form", form.key)?.setAttribute("aria-label", `Remove ${form.text || "this heard form"}`);
    }
    showError(part("error", "none"), problems.none ? "Add a meaning, or link a heard form to another entry's meaning." : "");
    editorStatus();
  }
  // An always-replace approval was given for one heard form and one output; changing
  // either clears it, and the panel says so.
  function clearAlways(form, why) {
    const notice = `“Always” on “${words(form.text)}” was cleared because you changed ${why}. Turn it on again if it still applies.`;
    form.direct = null; form.direct_reason = "";
    part("always-box", form.key)?.remove();
    const always = part("always", form.key);
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

  // A stored "as written" link stays one while its heard form and meaning are unchanged;
  // otherwise the spelling rule above decides.
  function literalLink(form, mid, meaning) {
    const old = words(form.text) === form.origText ? form.basis[mid] : null;
    const before = draft.original.meanings.find((m) => m.id === mid);
    const same = !before || (words(before.spelling) === words(meaning?.spelling ?? "") && before.casing === meaning?.casing);
    return (old?.basis === "literal" && same) || isLiteral(meaning, form.text);
  }

  function editorProblems() {
    const d = draft;
    const problems = { meanings: new Map(), forms: new Map(), direct: new Set(), none: false, count: 0 };
    for (const m of d.meanings) if (!words(m.spelling)) problems.meanings.set(m.id, "Write how it should be spelled.");
    const seen = new Set();
    for (const f of d.forms) {
      const text = words(f.text).toLowerCase();
      const problem = !text ? "Write what the speech model writes." : seen.has(text) ? "Already listed above." : !f.links.length ? "Link at least one meaning it can stand for." : "";
      seen.add(text);
      if (problem) problems.forms.set(f.key, problem);
      if (f.direct && !words(f.direct_reason)) problems.direct.add(f.key);
    }
    problems.none = !d.meanings.length && !d.forms.some((f) => f.links.length);
    problems.count = problems.meanings.size + problems.forms.size + problems.direct.size + (problems.none ? 1 : 0)
      + (d.scope === "learned" && !d.model ? 1 : 0);
    return problems;
  }
  function editorStatus() {
    if (!draft) return;
    const problems = editorProblems();
    const missing = draft.meanings.filter((m) => !words(m.meaning)).length;
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
    const known = meanings(dict, model?.id);
    const pinned = pinnedIds();
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

    // Written as: the meanings, one card each.
    const written = node("section", "", "editor-section");
    const writtenHead = node("div", "", "editor-head");
    writtenHead.append(node("h3", "Written as"), node("span", "the meanings · the decision model picks one from the sentence", "caption"));
    written.append(writtenHead);
    const own = new Set(d.meanings.map((m) => m.id));
    for (const meaning of d.meanings) {
      const card = node("div", "", "editor-card");
      const top = node("div", "", "editor-line");
      // Changing a spelling or capitals clears approvals that name this meaning.
      const spelling = input(meaning.spelling, `m:${meaning.id}:spelling`, (value) => {
        meaning.spelling = value;
        for (const form of d.forms) if (form.direct === meaning.id) clearAlways(form, `${meaning.spelling || "the meaning"}'s spelling`);
      }, { placeholder: "Spelling", cls: "input strong" });
      top.append(spelling, segmented([["fixed", "Exact capitals", "Always exactly like this: names, acronyms"], ["ordinary", "Normal word", "Capitalised only at a sentence start"]],
        meaning.casing, (value) => {
          if (value === meaning.casing) return;
          meaning.casing = value;
          for (const form of d.forms) if (form.direct === meaning.id) clearAlways(form, `${meaning.spelling || "the meaning"}'s capitals`);
          change(() => {});
        }, "Capitals"));
      if (d.meanings.length > 1 || d.forms.some((f) => f.links.some((mid) => !own.has(mid)))) {
        const remove = button("×", () => change((dd) => {
          dd.meanings = dd.meanings.filter((m) => m.id !== meaning.id);
          for (const f of dd.forms) { f.links = f.links.filter((l) => l !== meaning.id); if (f.direct === meaning.id) { f.direct = null; f.direct_reason = ""; } }
        }), "btn-icon remove");
        remove.dataset.part = `remove-meaning:${meaning.id}`;
        remove.setAttribute("aria-label", `Remove ${meaning.spelling || "this meaning"} and its links`);
        remove.title = "Remove this meaning and its links";
        top.append(remove);
      }
      card.append(top, errorLine(`m:${meaning.id}`, problems.meanings.get(meaning.id)));
      const description = input(meaning.meaning, `m:${meaning.id}:meaning`, (value) => { meaning.meaning = value; description.classList.toggle("missing", !words(value)); warn.hidden = Boolean(words(value)); },
        { tag: "textarea", placeholder: "What it is and what it goes with. The decision model reads this.", label: "What it is" });
      description.classList.toggle("missing", !words(meaning.meaning));
      const warn = node("p", "Without a description the decision model can't choose this meaning. You can save; the entry is flagged.", "field-error");
      warn.hidden = Boolean(words(meaning.meaning));
      card.append(description, warn,
        input(meaning.personal_context, `m:${meaning.id}:context`, (value) => { meaning.personal_context = value; }, { placeholder: "How you use it (optional)", cls: "input small" }));
      written.append(card);
    }
    written.append(button("+ Another meaning", () => change((dd) => { dd.meanings.push({ id: id("m"), spelling: "", meaning: "", personal_context: "", casing: "fixed" }); }), "btn link add-more"));
    parts.push(written);

    // Heard as: the forms, each linked to the meanings it can stand for.
    const heard = node("section", "", "editor-section");
    const heardHead = node("div", "", "editor-head");
    heardHead.append(node("h3", "Heard as"), node("span", `what ${model ? shortName(model) : "the speech model"} writes · link the meanings each can stand for`, "caption"));
    heard.append(heardHead);
    for (const form of d.forms) {
      const card = node("div", "", "editor-card");
      const top = node("div", "", "editor-line wrap");
      top.append(input(form.text, `f:${form.key}:text`, (value) => {
        if (form.direct) clearAlways(form, "the heard form");
        form.text = value;
      }, { placeholder: "heard form", cls: "input mono heard-input", label: "Heard as" }), node("span", "→", "arrow"));
      for (const meaning of d.meanings) {
        const on = form.links.includes(meaning.id);
        const pill = button("", () => change(() => {
          form.links = on ? form.links.filter((l) => l !== meaning.id) : [...form.links, meaning.id];
          if (on && form.direct === meaning.id) { form.direct = null; form.direct_reason = ""; }
        }), on ? "pill on" : "pill");
        pill.dataset.part = `pill:${form.key}|${meaning.id}`;
        pill.setAttribute("aria-pressed", String(on));
        pill.title = on ? "Click to unlink" : "Click to link";
        pillLabel(pill, form, meaning);
        top.append(pill);
      }
      for (const mid of form.links.filter((l) => !own.has(l))) {
        const chip = node("span", `✓ ${spellingOf(mid)}`, "pill on external");
        chip.title = known.get(mid)?.meaning || "No description yet";
        chip.append(node("span", pinned.has(mid) ? " · pinned" : " · other entry", "pill-note"));
        const remove = button("×", () => change(() => { form.links = form.links.filter((l) => l !== mid); if (form.direct === mid) { form.direct = null; form.direct_reason = ""; } }), "pill-remove");
        remove.setAttribute("aria-label", `Unlink ${spellingOf(mid)}`);
        chip.append(remove);
        top.append(chip);
      }
      const keep = button(`+ keep “${words(form.text)}” as written`, () => change((dd) => {
        const mid = id("m");
        dd.meanings.push({ id: mid, spelling: words(form.text), meaning: "", personal_context: "", casing: "ordinary" });
        form.links.push(mid);
      }), "pill dashed");
      keep.dataset.part = `keep:${form.key}`;
      keep.hidden = !canKeep(form);
      keep.title = "Adds a meaning spelled exactly like this, so it's left alone when that sense fits";
      // The form and its meanings on one line; ways to link more, Always and × beneath.
      const tools = node("div", "", "editor-line");
      const links = node("div", "", "editor-line wrap grow");
      links.append(keep);
      tools.append(links);
      const others = [...known].filter(([mid]) => !own.has(mid) && !form.links.includes(mid) && !d.original.meanings.some((m) => m.id === mid));
      if (others.length) {
        const pick = document.createElement("select");
        pick.className = "pill dashed pick";
        pick.setAttribute("aria-label", "Link a meaning from another entry");
        pick.title = "Link a meaning from another entry";
        pick.add(new Option("+ other entry…", ""));
        for (const [mid, m] of others) pick.add(new Option(`${m.spelling} (${pinned.has(mid) ? "pinned" : "learned"}) — ${m.meaning || "no description yet"}`, mid));
        pick.addEventListener("change", () => { if (pick.value) change(() => { form.links.push(pick.value); }); });
        links.append(pick);
      }
      const always = button("Always", () => change(() => {
        form.direct = form.direct ? null : form.links[0];
        if (!form.direct) form.direct_reason = "";
      }), form.direct ? "pill always on" : "pill always");
      always.dataset.part = `always:${form.key}`;
      always.disabled = !form.links.length;
      always.setAttribute("aria-pressed", String(Boolean(form.direct)));
      always.title = "Replace without reading the sentence. Rare.";
      tools.append(always);
      if (d.forms.length > 1 || d.meanings.length) {
        const remove = button("×", () => change((dd) => { dd.forms = dd.forms.filter((f) => f !== form); }), "btn-icon remove");
        remove.dataset.part = `remove-form:${form.key}`;
        remove.setAttribute("aria-label", `Remove ${form.text || "this heard form"}`);
        tools.append(remove);
      }
      card.append(top, tools, errorLine(`f:${form.key}`, problems.forms.get(form.key)));
      if (form.direct) {
        const box = node("div", "", "always-box");
        box.dataset.part = `always-box:${form.key}`;
        const line = node("div", "", "editor-line wrap");
        const target = document.createElement("select");
        target.className = "select";
        target.dataset.part = `target:${form.key}`;
        target.setAttribute("aria-label", "Always write");
        for (const mid of form.links) target.add(new Option(spellingOf(mid), mid, false, mid === form.direct));
        target.addEventListener("change", () => change(() => { form.direct = target.value; }));
        line.append("Always write ", target, " without reading the sentence.");
        box.append(line, input(form.direct_reason, `f:${form.key}:reason`, (value) => { form.direct_reason = value; },
          { placeholder: `Why can “${words(form.text)}” never mean anything else?`, cls: "input small", label: "Why this is always right" }),
        errorLine(`r:${form.key}`, problems.direct.has(form.key) ? "Write a reason. Use this only when the phrase can never mean anything else." : ""));
        card.append(box);
      }
      heard.append(card);
    }
    heard.append(button("+ Another heard form", () => change((dd) => {
      dd.forms.push({ key: id("f"), text: "", origText: null, links: dd.meanings.length === 1 ? [dd.meanings[0].id] : [], basis: {}, direct: null, direct_reason: "" });
    }), "btn link add-more"));
    heard.append(errorLine("none", problems.none ? "Add a meaning, or link a heard form to another entry's meaning." : ""));
    parts.push(heard);
    return parts;
  }

  // A draft is based on its entry as it was when opened. After a reload it can still be
  // saved if that entry is unchanged; otherwise it reopens on the saved version, so a
  // save never undoes a change made elsewhere.
  function rebase(d) {
    if (d.created || d.version === dictVersion) return true;
    const fresh = (d.scope === "pinned" ? dict.pinned : dict.learned[d.model.id] ?? []).find((g) => g.id === d.id);
    if (fresh && JSON.stringify(fresh) === JSON.stringify(d.original)) { d.version = dictVersion; return true; }
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
    const known = meanings(dict, d.model?.id);
    const meaningOf = (mid) => d.meanings.find((m) => m.id === mid) ?? known.get(mid);
    const group = {
      id: d.id,
      meanings: d.meanings.map((m) => ({ id: m.id, spelling: words(m.spelling), meaning: m.meaning.trim(), personal_context: m.personal_context.trim() || null, casing: m.casing })),
      recognized_forms: d.forms.map((f) => {
        const text = words(f.text);
        return {
          text,
          associations: f.links.map((mid) => {
            // A changed form loses the evidence that located the old text in transcripts.
            const old = text === f.origText ? f.basis[mid] : null;
            const basis = literalLink(f, mid, meaningOf(mid)) ? "literal" : old && old.basis !== "literal" ? old.basis : "user";
            return { meaning_id: mid, basis, evidence: old && old.basis === basis ? old.evidence : [] };
          }),
          direct: f.direct || null,
          direct_reason: f.direct ? f.direct_reason.trim() : "",
        };
      }),
    };
    group.needs_review = group.meanings.some((m) => !m.meaning);
    const next = clone();
    const list = d.scope === "pinned" ? next.pinned : (next.learned[d.model.id] ??= []);
    const at = list.findIndex((g) => g.id === d.id);
    if (d.created || at < 0) list.push(group); else list[at] = group;
    const changedOutputs = new Set(group.meanings.filter((m) => {
      const old = d.original.meanings.find((before) => before.id === m.id);
      return old && (old.spelling !== m.spelling || old.casing !== m.casing);
    }).map((m) => m.id));
    // Shared identity remains one definition, even when another section holds a copy.
    for (const g of [...next.pinned, ...Object.values(next.learned).flat()]) {
      if (g === group) continue;
      g.meanings = g.meanings.map((m) => group.meanings.find((edited) => edited.id === m.id) ?? m);
      for (const f of g.recognized_forms) {
        if (changedOutputs.has(f.direct)) { f.direct = null; f.direct_reason = ""; }
        // The old canonical form becomes a user-maintained association when
        // its output spelling changes; it no longer asserts literal identity.
        f.associations = f.associations.map((a) => a.basis === "literal" && changedOutputs.has(a.meaning_id)
          ? { meaning_id: a.meaning_id, basis: "user", evidence: [] } : a);
      }
    }
    const removed = new Set(d.original.meanings.map((m) => m.id).filter((mid) => !group.meanings.some((m) => m.id === mid)));
    if (removed.size) withoutMeanings(next, removed);
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
    reveal(d.scope, group.id);
    toast(`${d.created ? "Added" : "Saved"} ${title(group) || "entry"}`);
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
        : "dictionary.json in Entune's data folder, the same content as the list. Forms match whole words regardless of case.";
    return dirty;
  }
  jsonBox.addEventListener("input", () => { el("json-error").hidden = true; jsonDirty(); });
  el("revert-dictionary").addEventListener("click", () => { jsonBox.value = savedText; el("json-error").hidden = true; jsonDirty(); });
  el("save-dictionary").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(jsonBox.value); } catch (err) { showJsonError("Not saved.", err.message); return; }
    const problem = await saveDictionary(parsed, true);
    if (problem) { showJsonError("Not saved.", problem); return; }
    toast("Saved dictionary.json");
  });

  // ---- The ⋯ menu ----
  const menu = el("dict-menu");
  const menuButton = el("dict-menu-btn");
  function closeMenu() { menu.hidden = true; menuButton.setAttribute("aria-expanded", "false"); }
  menuButton.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    menuButton.setAttribute("aria-expanded", String(!menu.hidden));
    if (menu.hidden) return;
    // In a short window the menu scrolls within the space below its button.
    menu.style.maxHeight = `${Math.max(120, window.innerHeight - menu.getBoundingClientRect().top - 12)}px`;
    menu.querySelector("button:not(:disabled)")?.focus();
  });
  document.addEventListener("click", (event) => { if (!menu.hidden && !event.target.closest(".menu-wrap")) closeMenu(); });
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeMenu(); menuButton.focus(); } });
  for (const item of menu.querySelectorAll("[data-audio]")) {
    item.addEventListener("click", () => openSuggestions(item.dataset.audio));
  }
  el("menu-agents").addEventListener("click", () => { closeMenu(); openSettings("integrations"); });
  const reuse = el("learning-reuse");
  function drawReuse() {
    el("menu-reuse").setAttribute("aria-checked", String(reuse.checked));
    el("menu-reuse-state").textContent = reuse.checked ? "On" : "Off";
  }
  reuse.addEventListener("change", drawReuse);
  el("menu-reuse").addEventListener("click", () => { reuse.checked = !reuse.checked; drawReuse(); });

  // The guide is a modal over the page, so drafts and scroll position stay as they are.
  function showHelp(topic = null) {
    const guide = el("help-dialog");
    closeMenu();
    if (guide.open) return;
    guide.showModal();
    guide.querySelector(".guide").scrollTop = topic ? el(topic).offsetTop - guide.querySelector(".guide").offsetTop : 0;
  }
  el("help-toggle").addEventListener("click", () => showHelp());
  el("legend-help").addEventListener("click", () => showHelp());
  el("suggest-help").addEventListener("click", () => showHelp("guide-suggestions"));

  // ---- Suggestions: a side panel, and a banner above the list while a run is open ----
  const RUNNING = ["queued", "transcribing", "building", "cancelling", "cleaning"];
  const stage = (state) => RUNNING.includes(state.phase) ? "running" : state.phase === "ready" ? "review"
    : ["failed", "cancelled"].includes(state.phase) ? "halted" : "setup";
  // Before a run the panel starts one from a source: recent transcripts ("history"), or the
  // audio of Entune recordings, another dictation app or a folder, each chosen in the menu.
  let setupSource = "history";
  const SETUP_TITLES = { history: "Get suggestions", entune: "Learn from your other models' recordings",
    provider: "Learn from another dictation app", folder: "Learn from an audio folder" };
  // What the source is; how it becomes suggestions is the same for all three.
  function audioIntro(source) {
    return {
      entune: "Your Entune recordings made with other speech models. Entune transcribes them again with this one, then suggests entries.",
      provider: `Recordings another dictation app keeps on ${THIS_DEVICE}. Entune transcribes them, then suggests entries.`,
      folder: "Audio of you talking, such as meetings or voice notes. Entune transcribes it, then suggests entries.",
    }[source];
  }
  function openSuggestions(source) {
    closeMenu();
    if (source) setupSource = source;
    if (source && source !== "history") onboarding.show(source).catch((err) => toast(errorText(err), "err"));
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

  function showRun(state) {
    const before = stage(run);
    run = state;
    const now = stage(state);
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
    el("suggest-setup").hidden = now !== "setup" || audio;
    el("suggest-audio").hidden = now !== "setup" || !audio;
    if (audio) el("audio-intro").textContent = audioIntro(setupSource);
    // The settings stay open after a stop or failure: Continue can use smaller parts or
    // faster replies. The speech model is only chosen before a run.
    const resumable = now === "halted" || (now === "review" && ["failed", "stopped"].includes(state.outcome));
    el("suggest-shared").hidden = now !== "setup" && !resumable;
    el("speech-field").hidden = now !== "setup";
    el("audio-generation-estimate").hidden = now !== "setup" || !audio;
    if (now === "setup") fillSpeech();
    if (now === "setup") el("learn-status").hidden = true;
    el("run-note").hidden = now !== "running";
    el("proposal").hidden = now !== "review";
    el("build-dictionary").hidden = now !== "setup" || audio;
    el("build-audio-dictionary").hidden = now !== "setup" || !audio;
    el("audio-models").hidden = now !== "setup" || !audio;
    el("accept-proposal").hidden = now !== "review";
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

  const label = (g) => g ? `${g.meanings.map(m => m.spelling).join(" · ")}` : "";
  const describe = g => g ? [...g.meanings.map(m => `${m.spelling}: ${m.meaning}${m.personal_context ? ` (${m.personal_context})` : ""}`), ...g.recognized_forms.map(f => `Heard as: ${f.text}`)].join("\n") : "";
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
    proposalChanges = p.changes.map(c => ({...structuredClone(c), included: true, editing: false}));
    drawProposal();
  }
  // Each suggestion's summary shows what Apply submits, so it follows every edit, including
  // edits to a meaning shared with other suggestions.
  function refreshSummaries() {
    const known = proposalMeanings();
    for (const change of proposalChanges) {
      if (!change.after) continue;
      const set = (part, text) => { const e = proposalBody.querySelector(`[data-summary="${CSS.escape(`${part}:${change.id}`)}"]`); if (e) e.textContent = text; };
      set("heard", change.after.recognized_forms.map(f => f.text).join(", "));
      set("to", label(change.after) || title(change.after, known));
      set("after", describe(change.after));
      set("desc", change.after.meanings.map(m => m.meaning).filter(Boolean).join(" · ") || "No description yet.");
    }
  }
  function field(parent, text, value, update) {
    const wrap = node("label", text, "group-field");
    const control = document.createElement("input");
    control.className = "input";
    control.value = value ?? ""; control.addEventListener("change", () => { update(control.value); refreshSummaries(); });
    wrap.append(control); parent.append(wrap); return control;
  }
  // Meanings a suggestion may name: the saved ones for its model, and those other suggestions add.
  const proposalMeanings = () => new Map([...dict.pinned, ...(dict.learned[proposalModel] ?? []), ...proposalChanges.flatMap(c => c.after ? [c.after] : [])].flatMap(g => g.meanings).map(m => [m.id, m]));
  function drawProposal() {
    proposalBody.replaceChildren();
    const known = proposalMeanings();
    const pinned = pinnedIds();
    for (const [kind, heading] of [["add", "New entries"], ["update", "Changes"], ["remove", "Remove"]]) {
      const items = proposalChanges.filter(c => c.kind === kind);
      if (!items.length) continue;
      proposalBody.append(node("h3", `${heading} · ${items.length}`));
      for (const change of items) {
        const g = change.after || change.before;
        const box = node("section", "", "proposal-change");
        box.classList.toggle("dismissed", !change.included);
        const head = node("div", "", "change-head");
        const struck = kind === "remove" && change.included ? " struck" : "";
        const summary = (element, part) => { element.dataset.summary = `${part}:${change.id}`; return element; };
        head.append(summary(node("code", g.recognized_forms.map(f => f.text).join(", "), `heard${struck}`), "heard"), node("span", "→", "arrow"),
          summary(node("b", label(g) || title(g, known), struck.trim()), "to"), node("span", "", "spacer"));
        const toggle = change.included
          ? button("×", () => { change.included = false; drawProposal(); }, "btn-icon remove")
          : button("Include", () => { change.included = true; drawProposal(); }, "btn link");
        toggle.setAttribute("aria-label", `${change.included ? "Leave out" : "Include"} ${kind} ${label(g)}`);
        toggle.title = change.included ? "Leave this suggestion out; your current dictionary is kept as it is" : "Include this suggestion again";
        head.append(toggle);
        box.append(head);
        if (kind === "update" && change.before) {
          const compare = node("div", "", "compare");
          compare.append(node("span", "Now", "caption"), node("pre", describe(change.before), "before"),
            node("span", "Suggested", "suggested"), summary(node("pre", describe(change.after), "after"), "after"));
          box.append(compare);
        } else if (kind === "add") {
          box.append(summary(node("p", g.meanings.map(m => m.meaning).filter(Boolean).join(" · ") || "No description yet.", "change-desc"), "desc"));
        } else {
          box.append(node("p", "Removed only if you keep this suggestion when applying.", "caption"));
        }
        if (change.after && change.included) {
          const editor = node("details", "", "proposal-editor");
          editor.append(node("summary", "Edit this suggestion"));
          editor.open = change.editing;
          editor.addEventListener("toggle", () => { change.editing = editor.open; });
          // A shared meaning can be proposed in several groups; its copies stay one definition.
          // Update every copy and its visible input in place, so each editor shows what
          // Apply submits without replacing the control the user moves to next.
          const shared = (mid, part, value) => {
            for (const copy of proposalChanges.flatMap(c => c.after?.meanings ?? [])) if (copy.id === mid) copy[part] = value;
            for (const control of proposalBody.querySelectorAll("input[data-meaning]")) {
              if (control.dataset.meaning === mid && control.dataset.part === part) control.value = value ?? "";
            }
          };
          const tag = (control, mid, part) => { control.dataset.meaning = mid; control.dataset.part = part; return control; };
          for (const meaning of change.after.meanings) {
            const row = node("div", "", "meaning-editor");
            const spelling = field(row, "Write it as", meaning.spelling, value => {
              for (const form of proposalChanges.flatMap(c => c.after?.recognized_forms ?? [])) {
                if (form.direct === meaning.id) { form.direct = null; form.direct_reason = ""; }
                form.associations = form.associations.map(a => a.basis === "literal" && a.meaning_id === meaning.id
                  ? {meaning_id: a.meaning_id, basis: "user", evidence: []} : a);
              }
              shared(meaning.id, "spelling", value);
            });
            spelling.readOnly = pinned.has(meaning.id);
            tag(spelling, meaning.id, "spelling");
            tag(field(row, "What it is", meaning.meaning, value => shared(meaning.id, "meaning", value)), meaning.id, "meaning");
            tag(field(row, "How you use it (optional)", meaning.personal_context, value => shared(meaning.id, "personal_context", value || null)), meaning.id, "personal_context");
            editor.append(row);
          }
          for (const form of change.after.recognized_forms) {
            const row = node("div", "", "meaning-editor");
            const protectedForm = change.before?.recognized_forms.some(old => old.text.toLowerCase() === form.text.toLowerCase() && old.associations.some(a => pinned.has(a.meaning_id)));
            const text = field(row, "Heard as", form.text, value => { form.text = value; form.direct = null; form.direct_reason = ""; form.associations = form.associations.map(a => ({meaning_id: a.meaning_id, basis: "user", evidence: []})); });
            text.readOnly = Boolean(protectedForm);
            const choices = node("div", "", "proposal-associations");
            for (const [mid, meaning] of known) {
              if (!change.after.meanings.some(m => m.id === mid) && !form.associations.some(a => a.meaning_id === mid)) continue;
              const wrap = node("label", "", "caption check");
              const check = document.createElement("input"); check.type = "checkbox";
              check.checked = form.associations.some(a => a.meaning_id === mid);
              check.disabled = Boolean(protectedForm && pinned.has(mid) && check.checked);
              check.addEventListener("change", () => {
                if (check.checked) form.associations.push({meaning_id: mid, basis: "user", evidence: []});
                else form.associations = form.associations.filter(a => a.meaning_id !== mid);
                form.direct = null; form.direct_reason = "";
                refreshSummaries();
              });
              wrap.append(check, `${meaning.spelling} — ${meaning.meaning}`); choices.append(wrap);
            }
            const remove = button("Remove this heard form", () => { change.after.recognized_forms = change.after.recognized_forms.filter(f => f !== form); drawProposal(); });
            remove.disabled = Boolean(protectedForm);
            row.append(choices, remove); editor.append(row);
          }
          editor.append(button("Add another heard form", () => {
            const first = change.after.meanings[0]?.id || change.after.recognized_forms[0]?.associations[0]?.meaning_id;
            change.after.recognized_forms.push({text: "", associations: first ? [{meaning_id: first, basis: "user", evidence: []}] : []}); drawProposal();
          }));
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
  const scope = () => reuse.checked ? "all" : "new";
  el("build-dictionary").addEventListener("click", () => builds.start("history", {mode: mode(), scope: scope(), ...runSettings()}));

  return { load: loadDictionary, refreshAudio: () => onboarding.load(), refreshModels: fillModels };
}
