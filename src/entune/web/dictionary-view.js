import { api, el, errorText, flash, modelName, segmentedGroup } from "./ui.js";
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
  let openId = null; // the one entry shown expanded
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
  const onboarding = createAudioOnboarding({
    getModel, getSettings, getDictionaryModelName: () => missingKey ? null : languageName(getSettings()?.dictionaryModel),
    onBuild(selection) { openSuggestions(); return builds.start("audio", { mode: mode(), ...selection }); },
    onBusy(value) { importing = value; gate(); },
  });

  const builds = createDictionaryBuild({
    onBusy(value) { building = value; gate(); onboarding.setBuildBusy(value); lockEditors(); },
    onState(value) { showRun(value); },
    onProposal(value) { if (value) renderProposal(value); else { proposalChanges = []; el("proposal").hidden = true; } },
    onAccepted: () => loadDictionary(false),
    getSelected: () => proposalChanges.filter(c => c.included).map(c => ({id: c.id, after: c.after})),
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
    note.textContent = missingKey
      ? `${languageName(current)} needs an API key for ${owner.name}: add it in Settings › Dictionary setup, or choose another model.`
      : "Add a key for Anthropic, OpenAI, Google Gemini, Groq or Mistral in Settings › Dictionary setup to choose a suggestion model.";
    if (!keyed.length) modelSelect.add(new Option("No key yet", ""));
    modelSelect.disabled = building || !keyed.length;
    gate();
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
  async function saveDictionary(next, fromEditor = false) {
    if (building) return "Apply or discard the open suggestions first.";
    if (!dictVersion && !fromEditor) {
      await loadDictionary();
      return dictVersion ? "Dictionary reloaded; please redo that change." : "The dictionary could not be loaded.";
    }
    const headers = { "content-type": "application/json" };
    if (dictVersion) headers["if-match"] = dictVersion;
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
    el("learn-source").textContent = !model ? "Choose a speech model in the toolbar first."
      : `Reads up to 300 of ${speechName(model)}'s newest transcripts that no earlier run has read, plus your dictionary, and `
        + (mode() === "generate" ? "suggests first entries for the words it gets wrong." : "suggests fixes and missing entries.")
        + " Nothing changes until you review them.";
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
    const open = openId === g.id;
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
    const toggle = () => { openId = open ? null : g.id; renderRows(); document.querySelector(`[data-entry="${CSS.escape(g.id)}"] .dict-row`)?.focus(); };
    line.addEventListener("click", toggle);
    line.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
    item.dataset.entry = g.id;
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
    list.append(node("p", "Meanings · Jev picks from the sentence", "detail-label"));
    for (const meaning of g.meanings) {
      const row = node("div", "", "meaning-line");
      const head = node("span", "", "meaning-name");
      head.append(node("b", meaning.spelling), node("span", meaning.casing === "fixed" ? "exact capitals" : "normal word", "casing"));
      const text = node("span", "", "meaning-text");
      text.append(node("span", meaning.meaning || "No description: Jev can't choose it yet.", meaning.meaning ? "definition" : "definition missing"));
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

  async function pin(body) {
    const res = await fetch("/api/dictionary/pin", {
      method: "POST", headers: { "content-type": "application/json", "if-match": dictVersion },
      body: JSON.stringify({ model: getModel()?.id, ...body }),
    });
    if (!res.ok) { toast(await res.text(), "err"); await loadDictionary(); return false; }
    await loadDictionary();
    return true;
  }
  // The server pins one meaning at a time; the learned group keeps its ID for the rest.
  async function pinMeanings(g, list) {
    for (const meaning of list) if (!await pin({ group: g.id, meaning: meaning.id })) return;
    toast(`Pinned ${list.map((m) => m.spelling).join(" · ")} for every speech model`);
  }
  el("pin-all").addEventListener("click", async () => {
    const count = (dict.learned[getModel()?.id] ?? []).length;
    closeMenu();
    if (await pin({})) toast(`Pinned ${plural(count, "entry", "entries")}`);
  });

  // Removing an entry removes the links other entries made to its meanings; a heard form
  // left with no meaning, and an entry left empty, go with them.
  function withoutMeanings(doc, gone) {
    let touched = 0;
    for (const section of [doc.pinned, ...Object.values(doc.learned)]) {
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
    const next = clone();
    const list = scope === "pinned" ? next.pinned : next.learned[getModel()?.id] ?? [];
    const at = list.findIndex((x) => x.id === g.id);
    if (at >= 0) list.splice(at, 1);
    const others = withoutMeanings(next, new Set(g.meanings.map((m) => m.id)));
    const name = title(g);
    const body = "Its meanings and heard forms are deleted." + (others ? ` ${plural(others, "other entry", "other entries")} linked to its meanings; those links are removed too.` : "");
    if (!await confirmAction(`Remove “${name}”?`, body, "Remove")) return;
    const problem = await saveDictionary(next);
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
      : words(addFields.desc.value) ? "Enter to add." : "A description is optional to save, but Jev can't choose the word without it.");
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
    const next = clone();
    (addPinned || !model ? next.pinned : (next.learned[model.id] ??= [])).push(group);
    addProblem = await saveDictionary(next);
    drawAdd();
    if (addProblem) return;
    clearAdd();
    showAdd(false);
    reveal(group.id);
    toast(`Added ${spelling}`);
  }
  // A saved entry opens expanded, whatever filter or search would have hidden it.
  function reveal(groupId) {
    openId = groupId;
    query = ""; el("dict-search").value = "";
    if (filter !== "all") { filter = "all"; selectFilter("all"); }
    renderRows();
    document.querySelector(`[data-entry="${CSS.escape(groupId)}"]`)?.scrollIntoView({ block: "nearest" });
  }
  el("add-advanced").addEventListener("click", () => {
    const model = getModel();
    const meaning = { id: id("m"), spelling: words(addFields.spelling.value), meaning: words(addFields.desc.value), personal_context: null, casing: "fixed" };
    const heard = heardList();
    openEditor(addPinned || !model ? "pinned" : "learned", { id: id("g"), needs_review: false, meanings: [meaning],
      recognized_forms: (heard.length ? heard : [""]).map((text) => ({ text, associations: [{ meaning_id: meaning.id, basis: "user", evidence: [] }], direct: null, direct_reason: "" })) }, true);
  });
  el("empty-suggest").addEventListener("click", () => openSuggestions());

  // ---- The full editor, in a side panel ----
  function openEditor(scope, group, created = false) {
    const model = getModel();
    editorError = "";
    draft = {
      id: group.id, created, scope, notices: [],
      // A learned draft belongs to the speech model it was started for, even if the
      // toolbar selection changes before it is saved.
      model,
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

  // The draft is redrawn after every change; focus and the caret return to the same field.
  function drawEditor() {
    const body = el("entry-body");
    const active = document.activeElement?.dataset?.key;
    const caret = active && "selectionStart" in document.activeElement ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
    body.replaceChildren(...editorParts());
    if (active) {
      const field = body.querySelector(`[data-key="${CSS.escape(active)}"]`);
      field?.focus();
      if (caret && field?.setSelectionRange) try { field.setSelectionRange(...caret); } catch (e) {}
    }
    editorStatus();
  }
  const change = (fn) => { editorError = ""; fn(draft); drawEditor(); };
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
    field.addEventListener("input", () => { editorError = ""; onInput(field.value); });
    return field;
  }
  // Changing a meaning's spelling or capitals clears an always-replace approval that
  // names it: the approval was given for the old output.
  function updateMeaning(meaning, patch) {
    change((d) => {
      const before = { spelling: meaning.spelling, casing: meaning.casing };
      Object.assign(meaning, patch);
      if (before.spelling === meaning.spelling && before.casing === meaning.casing) return;
      for (const form of d.forms) {
        if (form.direct !== meaning.id) continue;
        form.direct = null; form.direct_reason = "";
        const notice = `“Always” on “${form.text}” was cleared because you changed ${meaning.spelling || "the meaning"}'s ${before.spelling !== meaning.spelling ? "spelling" : "capitals"}. Turn it on again if it still applies.`;
        if (!d.notices.includes(notice)) d.notices.push(notice);
      }
    });
  }

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
    for (const notice of d.notices) parts.push(node("p", notice, "editor-notice"));

    // Written as: the meanings, one card each.
    const written = node("section", "", "editor-section");
    const writtenHead = node("div", "", "editor-head");
    writtenHead.append(node("h3", "Written as"), node("span", "the meanings · Jev picks one from the sentence", "caption"));
    written.append(writtenHead);
    const own = new Set(d.meanings.map((m) => m.id));
    for (const meaning of d.meanings) {
      const card = node("div", "", "editor-card");
      const top = node("div", "", "editor-line");
      const spelling = input(meaning.spelling, `m:${meaning.id}:spelling`, (value) => updateMeaning(meaning, { spelling: value }), { placeholder: "Spelling", cls: "input strong" });
      top.append(spelling, segmented([["fixed", "Exact capitals", "Always exactly like this: names, acronyms"], ["ordinary", "Normal word", "Capitalised only at a sentence start"]],
        meaning.casing, (value) => updateMeaning(meaning, { casing: value }), "Capitals"));
      if (d.meanings.length > 1 || d.forms.some((f) => f.links.some((mid) => !own.has(mid)))) {
        const remove = button("×", () => change((dd) => {
          dd.meanings = dd.meanings.filter((m) => m.id !== meaning.id);
          for (const f of dd.forms) { f.links = f.links.filter((l) => l !== meaning.id); if (f.direct === meaning.id) { f.direct = null; f.direct_reason = ""; } }
        }), "btn-icon remove");
        remove.setAttribute("aria-label", `Remove ${meaning.spelling || "this meaning"} and its links`);
        remove.title = "Remove this meaning and its links";
        top.append(remove);
      }
      card.append(top);
      if (problems.meanings.has(meaning.id)) card.append(node("p", problems.meanings.get(meaning.id), "field-error"));
      const description = input(meaning.meaning, `m:${meaning.id}:meaning`, (value) => { meaning.meaning = value; description.classList.toggle("missing", !words(value)); warn.hidden = Boolean(words(value)); editorStatus(); },
        { tag: "textarea", placeholder: "What it is and what it goes with. Jev reads this.", label: "What it is" });
      description.classList.toggle("missing", !words(meaning.meaning));
      const warn = node("p", "Without a description Jev can't choose this meaning. You can save; the entry is flagged.", "field-error");
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
    const spellingOf = (mid) => d.meanings.find((m) => m.id === mid)?.spelling || known.get(mid)?.spelling || "(no spelling)";
    for (const form of d.forms) {
      const card = node("div", "", "editor-card");
      const top = node("div", "", "editor-line wrap");
      top.append(input(form.text, `f:${form.key}:text`, (value) => change((dd) => {
        // An always-replace approval was given for the old text; the new one needs its own.
        if (form.direct) {
          const notice = `“Always” on “${words(form.text)}” was cleared because you changed the heard form. Turn it on again if it still applies.`;
          if (!dd.notices.includes(notice)) dd.notices.push(notice);
          form.direct = null; form.direct_reason = "";
        }
        form.text = value;
      }), { placeholder: "heard form", cls: "input mono heard-input", label: "Heard as" }), node("span", "→", "arrow"));
      for (const meaning of d.meanings) {
        const on = form.links.includes(meaning.id);
        const pill = button(on ? `✓ ${meaning.spelling || "(no spelling)"}` : meaning.spelling || "(no spelling)", () => change(() => {
          form.links = on ? form.links.filter((l) => l !== meaning.id) : [...form.links, meaning.id];
          if (on && form.direct === meaning.id) { form.direct = null; form.direct_reason = ""; }
        }), on ? "pill on" : "pill");
        pill.setAttribute("aria-pressed", String(on));
        pill.title = on ? "Click to unlink" : "Click to link";
        if (on && literalLink(form, meaning.id, meaning)) pill.append(node("span", " · as written", "pill-note"));
        top.append(pill);
      }
      for (const mid of form.links.filter((l) => !own.has(l))) {
        const chip = node("span", `✓ ${spellingOf(mid)}`, "pill on external");
        chip.append(node("span", pinned.has(mid) ? " · pinned" : " · other entry", "pill-note"));
        const remove = button("×", () => change(() => { form.links = form.links.filter((l) => l !== mid); if (form.direct === mid) { form.direct = null; form.direct_reason = ""; } }), "pill-remove");
        remove.setAttribute("aria-label", `Unlink ${spellingOf(mid)}`);
        chip.append(remove);
        top.append(chip);
      }
      if (words(form.text) && !d.meanings.some((m) => literalLink(form, m.id, m))) {
        const literal = button(`+ keep “${words(form.text)}” as written`, () => change((dd) => {
          const mid = id("m");
          dd.meanings.push({ id: mid, spelling: words(form.text), meaning: "", personal_context: "", casing: "ordinary" });
          form.links.push(mid);
        }), "pill dashed");
        literal.title = "Adds a meaning spelled exactly like this, so it's left alone when that sense fits";
        top.append(literal);
      }
      const others = [...known].filter(([mid]) => !own.has(mid) && !form.links.includes(mid) && !d.original.meanings.some((m) => m.id === mid));
      if (others.length) {
        const pick = document.createElement("select");
        pick.className = "pill dashed pick";
        pick.setAttribute("aria-label", "Link a meaning from another entry");
        pick.title = "Link a meaning from another entry";
        pick.add(new Option("+ other entry…", ""));
        for (const [mid, m] of others) pick.add(new Option(`${m.spelling} (${pinned.has(mid) ? "pinned" : "learned"})`, mid));
        pick.addEventListener("change", () => { if (pick.value) change(() => { form.links.push(pick.value); }); });
        top.append(pick);
      }
      top.append(node("span", "", "spacer"));
      const always = button("Always", () => change(() => {
        form.direct = form.direct ? null : form.links[0];
        if (!form.direct) form.direct_reason = "";
      }), form.direct ? "pill always on" : "pill always");
      always.disabled = !form.links.length;
      always.setAttribute("aria-pressed", String(Boolean(form.direct)));
      always.title = "Replace without reading the sentence. Rare.";
      top.append(always);
      if (d.forms.length > 1) {
        const remove = button("×", () => change((dd) => { dd.forms = dd.forms.filter((f) => f !== form); }), "btn-icon remove");
        remove.setAttribute("aria-label", `Remove ${form.text || "this heard form"}`);
        top.append(remove);
      }
      card.append(top);
      if (problems.forms.has(form.key)) card.append(node("p", problems.forms.get(form.key), "field-error"));
      if (form.direct) {
        const box = node("div", "", "always-box");
        const line = node("div", "", "editor-line wrap");
        const target = document.createElement("select");
        target.className = "select";
        target.setAttribute("aria-label", "Always write");
        for (const mid of form.links) target.add(new Option(spellingOf(mid), mid, false, mid === form.direct));
        target.addEventListener("change", () => change(() => { form.direct = target.value; }));
        line.append("Always write ", target, " without reading the sentence.");
        box.append(line, input(form.direct_reason, `f:${form.key}:reason`, (value) => { form.direct_reason = value; reasonError.hidden = Boolean(words(value)); editorStatus(); },
          { placeholder: `Why can “${words(form.text)}” never mean anything else?`, cls: "input small", label: "Why this is always right" }));
        const reasonError = node("p", "Write a reason. Use this only when the phrase can never mean anything else.", "field-error");
        reasonError.hidden = !problems.direct.has(form.key);
        box.append(reasonError);
        card.append(box);
      }
      heard.append(card);
    }
    heard.append(button("+ Another heard form", () => change((dd) => {
      dd.forms.push({ key: id("f"), text: "", origText: null, links: dd.meanings.length === 1 ? [dd.meanings[0].id] : [], basis: {}, direct: null, direct_reason: "" });
    }), "btn link add-more"));
    if (problems.none) heard.append(node("p", "Add a meaning, or link a heard form to another entry's meaning.", "field-error"));
    parts.push(heard);
    return parts;
  }

  el("entry-save").addEventListener("click", saveEditor);
  async function saveEditor() {
    const d = draft;
    if (!d || editorProblems().count || building || saving) return;
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
      // After a reload the draft is stale: saving it would undo whatever changed the entry.
      if (dictVersion !== version && !d.created) {
        const fresh = (d.scope === "pinned" ? dict.pinned : dict.learned[d.model.id] ?? []).find((g) => g.id === d.id);
        if (!fresh) { closeEditor(); toast(`${problem} The entry is no longer there.`, "err"); return; }
        openEditor(d.scope, fresh);
        draft.model = d.model;
      }
      editorError = problem;
      editorStatus();
      return;
    }
    if (current) closeEditor();
    if (current && d.created) { clearAdd(); showAdd(false); }
    reveal(group.id);
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
    if (!menu.hidden) menu.querySelector("button:not(:disabled)")?.focus();
  });
  document.addEventListener("click", (event) => { if (!menu.hidden && !event.target.closest(".menu-wrap")) closeMenu(); });
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeMenu(); menuButton.focus(); } });
  for (const item of menu.querySelectorAll("[data-audio]")) {
    item.addEventListener("click", () => { closeMenu(); onboarding.open(item.dataset.audio).catch((err) => toast(errorText(err), "err")); });
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
  el("audio-help").addEventListener("click", () => showHelp("guide-audio"));

  // ---- Suggestions: a side panel, and a banner above the list while a run is open ----
  const RUNNING = ["queued", "transcribing", "building", "cancelling", "cleaning"];
  const stage = (state) => RUNNING.includes(state.phase) ? "running" : state.phase === "ready" ? "review"
    : ["failed", "cancelled"].includes(state.phase) ? "halted" : "setup";
  function openSuggestions() {
    closeMenu();
    showRun(run);
    if (!suggestDrawer.open) suggestDrawer.showModal();
  }
  el("suggest-btn").addEventListener("click", openSuggestions);
  el("banner-action").addEventListener("click", openSuggestions);
  el("suggest-close").addEventListener("click", () => suggestDrawer.close());
  async function discardRun() {
    if (await confirmAction("Discard these suggestions?", "Nothing has changed in your dictionary. The transcripts read in this run count as used.", "Discard")) await builds.discard();
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
    const titles = { setup: "Get suggestions", running: "Getting suggestions", review: "Review suggestions", halted: "Suggestions stopped" };
    el("suggest-title").textContent = titles[now];
    if (now === "review") proposalTitle();
    const model = getModel();
    el("suggest-sub").textContent = now === "setup" ? `For ${speechName(model)}. Suggestions land here for review; nothing is saved before you apply.`
      : `${plural(state.total ?? 0, state.source === "audio" ? "recording" : "transcript")} · ${modelName(state.model ?? "", model ? [model] : [])} → ${languageName(state.dictionaryModel) ?? "suggestion model"}`;
    el("suggest-setup").hidden = now !== "setup";
    if (now === "setup") el("learn-status").hidden = true;
    el("run-note").hidden = now !== "running";
    el("proposal").hidden = now !== "review";
    el("build-dictionary").hidden = now !== "setup";
    el("accept-proposal").hidden = now !== "review";
    el("discard-proposal").hidden = now !== "review";
    // The banner: what the run is doing, and whether editing waits for it.
    const banner = el("dict-banner");
    banner.hidden = now === "setup";
    banner.classList.toggle("err", state.phase === "failed" || (now === "review" && state.outcome === "failed"));
    el("banner-spinner").hidden = now !== "running";
    el("banner-dot").hidden = now === "running";
    const progress = state.phase === "transcribing" ? `Transcribing recording ${state.completed} of ${state.total}`
      : state.phase === "cancelling" ? "Stopping"
        : state.steps > 1 ? `Getting suggestions · part ${state.step} of ${state.steps} · ${state.completedBatches ?? 0} done` : "Getting suggestions";
    el("banner-text").textContent = now === "running" ? `${progress}. Editing and dictation are paused until the suggestions are applied or discarded.`
      : now === "review" ? `${state.outcome === "stopped" || state.outcome === "failed" ? "The run stopped early. " : ""}${plural(proposalChanges.length, "suggestion")} waiting for review. Editing and dictation are paused until you apply or discard.`
        : state.phase === "failed" ? "The suggestion run stopped with an error. Your dictionary is unchanged; retry or discard."
          : `The suggestion run was stopped after ${state.completedBatches ?? 0} of ${state.steps ?? 0} parts. Retry the rest, or discard.`;
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
  function field(parent, text, value, update) {
    const wrap = node("label", text, "group-field");
    const control = document.createElement("input");
    control.className = "input";
    control.value = value ?? ""; control.addEventListener("change", () => update(control.value));
    wrap.append(control); parent.append(wrap); return control;
  }
  function drawProposal() {
    proposalBody.replaceChildren();
    const known = new Map([...dict.pinned, ...(dict.learned[proposalModel] ?? []), ...proposalChanges.flatMap(c => c.after ? [c.after] : [])].flatMap(g => g.meanings).map(m => [m.id, m]));
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
        head.append(node("code", g.recognized_forms.map(f => f.text).join(", "), `heard${struck}`), node("span", "→", "arrow"), node("b", label(g) || title(g, known), struck.trim()), node("span", "", "spacer"));
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
            node("span", "Suggested", "suggested"), node("pre", describe(change.after), "after"));
          box.append(compare);
        } else if (kind === "add") {
          box.append(node("p", g.meanings.map(m => m.meaning).filter(Boolean).join(" · ") || "No description yet.", "change-desc"));
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
  el("build-dictionary").addEventListener("click", () => builds.start("history", {mode: mode(), scope: scope()}));

  return { load: loadDictionary, refreshAudio: () => onboarding.load(), refreshModels: fillModels };
}
