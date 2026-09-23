import { api, el, errorText, flash, modelName, segmentedGroup } from "./ui.js";
import { createAudioOnboarding } from "./audio-onboarding.js";
import { createDictionaryBuild } from "./dictionary-build.js";

// The dictionary owns its document, revision and pending proposal. Model selection
// stays in the app; getters read the current selection when an action is made.
export function createDictionary({ getModel, getSettings, onSettingsChanged }) {
  // Confusion groups are the edit unit; stable IDs distinguish meanings with one spelling.
  let dict = { version: 2, pinned: [], learned: {} };
  let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
  let importing = false;
  let building = false;
  let filter = "all";
  let proposalChanges = [];
  let proposalModel = null;
  const dictionaryBox = el("dictionary");
  const buildBtn = el("build-dictionary");
  const buildStatus = el("build-status");
  const proposalPanel = el("proposal");
  const proposalBody = el("proposal-body");
  const modelSelect = el("dictionary-model");
  let missingKey = false; // the saved dictionary model's provider has no key
  const onboarding = createAudioOnboarding({
    getModel, getSettings, getDictionaryModelName: () => missingKey ? null : languageName(getSettings()?.dictionaryModel),
    onBuild(selection) { return builds.start("audio", { mode: mode(), ...selection }); },
    onBusy(value) { importing = value; gate(); },
  });

  const builds = createDictionaryBuild({
    onBusy(value) { building = value; gate(); onboarding.setBuildBusy(value); lockEditors(); },
    onProposal(value) { if (value) renderProposal(value); else proposalPanel.hidden = true; },
    onAccepted: () => loadDictionary(false),
    getSelected: () => proposalChanges.filter(c => c.included).map(c => ({id: c.id, after: c.after})),
  });

  segmentedGroup({ all: el("filter-all"), pinned: el("filter-pinned"), learned: el("filter-learned") }, (name) => { filter = name; renderDictionary(); });

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
      ? `${languageName(current)} needs an API key for ${owner.name}: add it in Settings › Providers, or choose another model.`
      : "Add an Anthropic or OpenAI key in Settings › Providers to choose a dictionary model.";
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
  function gate() {
    const ready = Boolean(getModel() && getSettings()?.dictionaryModel && !missingKey);
    buildBtn.disabled = importing || building || !ready;
    el("open-audio-dialog").disabled = building;
  }
  el("open-audio-dialog").addEventListener("click", () => onboarding.open().catch((err) => flash(buildStatus, errorText(err), "err")));

  async function loadDictionary(pollBuild = true) {
    dictVersion = null;
    const res = await fetch("/api/dictionary");
    const text = await res.text();
    if (!res.ok) { dictVersion = null; flash(el("dictionary-status"), text, "err"); el("json-editor").hidden = false; return; }
    dict = JSON.parse(text);
    dictVersion = res.headers.get("etag");
    renderDictionary(text);
    lockEditors();
    if (pollBuild) await builds.load();
    await onboarding.load();
  }

  // Every edit sends the whole document, named with the version it was made on. The
  // server refuses a save on a stale version (an agent or a hand edit got there first)
  // and the fresh document is shown instead. Explicit JSON repair can replace an
  // unreadable document after confirmation; table edits always need a loaded version.
  function lockEditors() {
    for (const field of document.querySelectorAll("#dict-rows input, #dict-rows select, #dict-rows button, #new-group input, #new-group select, #new-group button, #add-entry-btn, #dictionary, #save-dictionary, #pin-all, #dictionary-model")) field.disabled = building;
    el("dictionary-edit-lock").hidden = !building;
  }

  async function saveDictionary(next, fromEditor = false, target = buildStatus) {
    if (building) { flash(buildStatus, "Apply or discard the open suggestions first.", "err"); return false; }
    if (!dictVersion && !fromEditor) {
      await loadDictionary();
      if (dictVersion) flash(el("dictionary-status"), "Dictionary reloaded; please redo that change.", "err");
      return false;
    }
    const headers = { "content-type": "application/json" };
    if (dictVersion) headers["if-match"] = dictVersion;
    else if (!confirm("The dictionary could not be loaded. Replace it with this JSON?")) return false;
    const res = await fetch("/api/dictionary", { method: "PUT", headers, body: JSON.stringify(next) });
    const text = await res.text();
    if (res.status === 409) {
      await loadDictionary();
      flash(el("dictionary-status"), `${text} Reloaded; please redo that change.`, "err");
      return false;
    }
    if (!res.ok) { flash(el("dictionary-status"), text, "err"); flash(target, text, "err"); return false; }
    dict = JSON.parse(text);
    dictVersion = res.headers.get("etag");
    renderDictionary(text);
    return true;
  }

  // One action: with no entries that apply to the selected speech model (pinned or its
  // learned), suggestions start a dictionary; once any apply, they refine it, which can
  // also add missing entries. The server's effective dictionary is the same union.
  function mode() {
    const learned = dict.learned[getModel()?.id] ?? [];
    return dict.pinned.length || learned.length ? "refine" : "generate";
  }

  // The default model's learned list, made if absent. Pinned is shared by every model.
  function learnedOf(doc) {
    const model = getModel();
    if (!model) return [];
    return (doc.learned[model.id] ??= []);
  }
  const clone = () => JSON.parse(JSON.stringify(dict));
  const id = (prefix) => `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
  const button = (text, fn) => {
    const b = document.createElement("button"); b.type = "button"; b.className = "btn ghost sm";
    b.textContent = text; b.addEventListener("click", fn); return b;
  };
  const node = (tag, text, cls = "") => Object.assign(document.createElement(tag), { textContent: text, className: cls });
  function meanings(doc = dict, modelId = getModel()?.id) {
    const learned = modelId ? doc.learned[modelId] ?? [] : [];
    return new Map([...doc.pinned, ...learned].flatMap((g) => g.meanings).map((m) => [m.id, m]));
  }
  function field(parent, label, value, update, options = null) {
    const wrap = node("label", label, "group-field");
    const input = document.createElement(options ? "select" : "input");
    input.className = "input";
    if (options) for (const [value, text] of options) input.add(new Option(text, value));
    input.value = value ?? ""; input.addEventListener("change", () => update(input.value));
    wrap.append(input); parent.append(wrap); return input;
  }
  async function pinMeaning(group, meaning) {
    const res = await fetch("/api/dictionary/pin", {
      method: "POST", headers: { "content-type": "application/json", "if-match": dictVersion },
      body: JSON.stringify({ model: getModel()?.id, ...(group && meaning ? { group: group.id, meaning: meaning.id } : {}) }),
    });
    if (!res.ok) { flash(buildStatus, await res.text(), "err"); if (res.status === 409) await loadDictionary(); return; }
    await loadDictionary();
  }
  // A group shows its recognized forms, each with the meanings it may stand for, then
  // each meaning's output spelling and definition. Scope is stated on every group.
  function entryRow(source, group) {
    const known = meanings();
    const card = node("article", "", "group-card");
    const head = node("header", "", "group-head");
    const current = getModel();
    const model = current ? modelName(current.id, [current]) : "this speech model";
    const scopeLabel = node("span", source === "pinned" ? "Pinned" : "Learned", `scope ${source}`);
    scopeLabel.title = source === "pinned" ? "Used with every speech model; suggestions can't remove it" : `Used only with ${model}`;
    head.append(scopeLabel);
    if (group.needs_review) head.append(node("span", "Imported: check the descriptions and capitals", "badge warn"));
    head.append(node("span", "", "spacer"), button("Edit", () => editGroup(card, source, group)), button("Remove", async () => {
      const next = clone(), list = source === "pinned" ? next.pinned : learnedOf(next);
      list.splice(list.findIndex((g) => g.id === group.id), 1);
      await saveDictionary(next); // references from other groups must be resolved explicitly
    }));
    const forms = node("div", "", "group-forms");
    forms.append(node("h3", "When the speech model writes"));
    for (const form of group.recognized_forms) {
      const row = node("div", "", "form-row");
      const chips = node("span", "", "chips");
      for (const link of form.associations) {
        const meaning = known.get(link.meaning_id);
        const chip = node("span", meaning?.spelling ?? link.meaning_id, "chip");
        if (link.basis === "literal") { chip.classList.add("literal"); chip.title = "Left as written when this meaning fits the sentence"; chip.append(node("span", " · as written", "chip-note")); }
        if (form.direct === link.meaning_id) { chip.classList.add("direct"); chip.title = `Always used, without reading the sentence: ${form.direct_reason}`; chip.append(node("span", " · always", "chip-note")); }
        chips.append(chip);
      }
      row.append(node("code", form.text, "form-text"), node("span", "→", "arrow"), chips);
      forms.append(row);
    }
    if (!group.recognized_forms.length) forms.append(node("p", "No spellings yet.", "caption"));
    const list = node("div", "", "group-meanings");
    list.append(node("h3", "You might have meant"));
    for (const meaning of group.meanings) {
      const item = node("div", "", "meaning");
      const line = node("div", "", "meaning-line");
      line.append(node("strong", meaning.spelling, "spelling"), node("span", meaning.casing === "fixed" ? "always written like this" : "capitalized like a normal word", "casing"));
      if (source === "learned") {
        const pin = button("Pin", () => pinMeaning(group, meaning));
        pin.title = "Use this meaning with every speech model";
        line.append(node("span", "", "spacer"), pin);
      }
      item.append(line, node("p", meaning.meaning || "Needs a description before Entune can choose it.", meaning.meaning ? "definition" : "definition missing"));
      if (meaning.personal_context) item.append(node("p", meaning.personal_context, "personal"));
      list.append(item);
    }
    const body = node("div", "", "group-body");
    body.append(forms, list);
    card.append(head, body);
    return card;
  }
  function editGroup(host, source, original, created = false) {
    host.querySelector(".group-editor")?.remove();
    const draft = structuredClone(original);
    const panel = node("div", "", created ? "group-editor new" : "group-editor");
    const status = node("p", "", "caption save-status");
    let scope = source;
    // A learned draft belongs to the speech model it was started for, even if the
    // toolbar selection changes before it is saved.
    const model = getModel();
    host.append(panel);
    function render() {
      panel.replaceChildren();
      if (created) {
        // Collapse and Discard stay at the top, so the form can be closed without scrolling.
        const head = node("div", "", "editor-head");
        head.append(node("h3", "New entry"), node("span", "", "spacer"),
          button("Collapse", () => showNewGroup(false)), button("Discard", discardNewGroup));
        panel.append(head);
        panel.append(node("p", "Write what you meant, then each spelling the speech model writes for it.", "caption hint editor-intro"));
        field(panel, "Use with", scope, (v) => { scope = v; }, [["pinned", "All speech models (pinned)"], ...(model ? [["learned", `Only ${modelName(model.id, [model])} (learned)`]] : [])]);
      }
      for (const meaning of draft.meanings) {
        const section = node("fieldset", "", "group-meaning"); section.append(node("legend", "You meant"));
        field(section, "Write it as", meaning.spelling, (v) => { meaning.spelling = v; clearDirect(meaning.id); });
        field(section, "What it is", meaning.meaning, (v) => { meaning.meaning = v; });
        section.append(node("p", "A short description Entune uses to recognize it in a sentence, e.g. “an AI assistant you ask to write or code”.", "caption field-hint"));
        field(section, "How you use it (optional)", meaning.personal_context, (v) => { meaning.personal_context = v || null; });
        field(section, "Capitals", meaning.casing, (v) => { meaning.casing = v; clearDirect(meaning.id); }, [["fixed", "Always exactly as written (names, acronyms)"], ["ordinary", "Like a normal word (capital at sentence start)"]]);
        section.append(button("Remove this meaning", () => {
          draft.meanings = draft.meanings.filter((m) => m.id !== meaning.id);
          for (const f of draft.recognized_forms) f.associations = f.associations.filter((a) => a.meaning_id !== meaning.id);
          clearDirect(meaning.id); render();
        })); panel.append(section);
      }
      panel.append(button("Add another meaning", () => { draft.meanings.push({ id: id("m"), spelling: "", meaning: "", personal_context: null, casing: "fixed" }); render(); }));
      const known = meanings(dict, model?.id); for (const m of draft.meanings) known.set(m.id, m);
      for (const form of draft.recognized_forms) {
        const section = node("fieldset", "", "group-form-editor"); section.append(node("legend", "The speech model writes"));
        field(section, "Written as", form.text, (v) => {
          form.text = v; form.associations = form.associations.map((a) => ({ meaning_id: a.meaning_id, basis: "user", evidence: [] }));
          form.direct = null; form.direct_reason = ""; render();
        });
        section.append(node("p", "It can stand for", "caption"));
        const own = new Set(draft.meanings.map((m) => m.id));
        const linked = new Set(form.associations.map((a) => a.meaning_id));
        for (const [mid, m] of [...known].filter(([mid]) => own.has(mid) || linked.has(mid))) {
          const label = node("label", "", "group-candidate");
          const check = document.createElement("input"); check.type = "checkbox";
          check.checked = form.associations.some((a) => a.meaning_id === mid);
          check.addEventListener("change", () => {
            form.associations = form.associations.filter((a) => a.meaning_id !== mid);
            if (check.checked) form.associations.push({ meaning_id: mid, basis: "user", evidence: [] });
            form.direct = null; form.direct_reason = ""; render();
          });
          label.append(check, ` ${m.spelling || "(the meaning above)"} — ${m.meaning || "no description yet"}`); section.append(label);
        }
        const others = [...known].filter(([mid]) => !own.has(mid) && !linked.has(mid));
        if (others.length) {
          field(section, "It can also stand for a meaning from another entry", "", (v) => {
            if (v) { form.associations.push({ meaning_id: v, basis: "user", evidence: [] }); form.direct = null; form.direct_reason = ""; render(); }
          }, [["", "Choose a meaning…"], ...others.map(([mid, m]) => [mid, `${m.spelling} — ${m.meaning || "no description yet"}`])]);
        }
        const advanced = node("details", "", "editor-advanced");
        advanced.open = Boolean(form.direct);
        advanced.append(node("summary", "Advanced: replace without reading the sentence"));
        field(advanced, "When it's written", form.direct, (v) => { form.direct = v || null; if (!v) form.direct_reason = ""; },
          [["", "Read the sentence and choose (recommended)"], ...form.associations.map((a) => [a.meaning_id, `Always write ${known.get(a.meaning_id)?.spelling}`])]);
        field(advanced, "Why this is always right", form.direct_reason, (v) => { form.direct_reason = v; });
        advanced.append(node("p", "Only for spellings that can never mean anything else. If another entry could also match here, Entune still reads the sentence.", "caption"));
        section.append(advanced);
        section.append(button("Remove this spelling", () => { draft.recognized_forms = draft.recognized_forms.filter((f) => f !== form); render(); }));
        panel.append(section);
      }
      panel.append(button("Add another spelling", () => { draft.recognized_forms.push({ text: "", associations: [], direct: null, direct_reason: "" }); render(); }));
      const foot = node("div", "", "editor-foot");
      panel.append(foot);
      foot.append(button(created ? "Save entry" : "Save changes", async () => {
        const problem = draft.meanings.some((m) => !m.spelling.trim()) ? "Fill in “Write it as” for every meaning."
          : draft.recognized_forms.some((f) => !f.text.trim()) ? "Fill in “Written as” for every spelling, or remove it."
          : draft.recognized_forms.some((f) => !f.associations.length) ? "Tick at least one meaning for every spelling."
          : !draft.meanings.length && !draft.recognized_forms.length ? "Add a meaning or a spelling first." : "";
        if (problem) { flash(status, problem, "err"); return; }
        if (scope === "learned" && !model) { flash(status, "Choose a speech model first.", "err"); return; }
        const next = clone(), list = scope === "pinned" ? next.pinned : (next.learned[model.id] ??= []);
        draft.needs_review = draft.meanings.some((m) => !m.meaning.trim());
        if (created) list.push(draft);
        else list[list.findIndex((g) => g.id === original.id)] = draft;
        const changedOutputs = new Set(draft.meanings.filter((m) => {
          const old = original.meanings.find((before) => before.id === m.id);
          return old && (old.spelling !== m.spelling || old.casing !== m.casing);
        }).map((m) => m.id));
        // Shared identity remains one definition, even when another section holds a copy.
        for (const g of [...next.pinned, ...Object.values(next.learned).flat()]) {
          g.meanings = g.meanings.map((m) => draft.meanings.find((changed) => changed.id === m.id) ?? m);
          for (const f of g.recognized_forms) {
            if (changedOutputs.has(f.direct)) { f.direct = null; f.direct_reason = ""; }
            // The old canonical form becomes a user-maintained association when
            // its output spelling changes; it no longer asserts literal identity.
            f.associations = f.associations.map((a) => a.basis === "literal" && changedOutputs.has(a.meaning_id)
              ? { meaning_id: a.meaning_id, basis: "user", evidence: [] } : a);
          }
        }
        // Remove this saved panel only; a newer draft started meanwhile stays as it is.
        if (await saveDictionary(next, false, status) && created) {
          panel.remove();
          showNewGroup(!(newGroupEditor()?.hidden ?? true));
        }
      }), button(created ? "Discard" : "Cancel", () => created ? discardNewGroup() : panel.remove()), status);
    }
    function clearDirect(mid) {
      for (const f of draft.recognized_forms) if (f.direct === mid) { f.direct = null; f.direct_reason = ""; }
    }
    render();
  }

  function renderDictionary(jsonText) {
    const model = getModel();
    const learned = learnedOf(dict);
    const rows = [];
    if (filter !== "learned") rows.push(...dict.pinned.map((e) => entryRow("pinned", e)));
    if (filter !== "pinned") rows.push(...learned.map((e) => entryRow("learned", e)));
    if (rows.length === 0) {
      const none = document.createElement("div");
      none.className = "entry-row none";
      none.textContent = filter === "learned"
        ? "Nothing learned for this speech model yet. “Get suggestions” above finds its common mistakes."
        : filter === "pinned"
          ? "No pinned entries. Pin a learned entry to use it with every speech model, or add one yourself."
          : "Your dictionary is empty. Start with “Get suggestions” above, or add an entry yourself.";
      rows.push(none);
    }
    el("dict-rows").replaceChildren(...rows);
    if (!proposalPanel.hidden) proposalTitle();
    el("pin-all").hidden = learned.length === 0 || filter === "pinned";
    // Counts on the filters; the scope line says what the selected filter means, naming
    // the speech model once.
    const total = dict.pinned.length + learned.length;
    for (const [id, name, count] of [["filter-all", "All", total], ["filter-pinned", "Pinned", dict.pinned.length], ["filter-learned", "Learned", learned.length]]) {
      el(id).textContent = count ? `${name} ${count}` : name;
    }
    const speech = model ? modelName(model.id, [model]) : "the selected speech model";
    el("groups-count").textContent = { all: total ? `Pinned entries apply to every speech model; learned ones only to ${speech}.` : "",
      pinned: "Used with every speech model.", learned: `Used only with ${speech}.` }[filter];
    el("learn-source").textContent = !model ? "Choose a speech model in the toolbar first."
      : mode() === "generate" ? "Finds words your speech model gets wrong in recent transcripts and suggests first entries. Nothing changes until you review them."
        : "Checks how your entries did in recent transcripts and suggests fixes and missing entries. Nothing changes until you review them.";
    fillModels();
    if (jsonText !== undefined) dictionaryBox.value = jsonText;
    lockEditors();
  }

  el("pin-all").addEventListener("click", () => pinMeaning(null, null));

  // New group toggles one draft: collapsing keeps what was typed; only Discard or a
  // successful save removes it.
  const newGroupEditor = () => el("new-group").querySelector(".group-editor");
  function showNewGroup(open) {
    const editor = newGroupEditor();
    if (editor) editor.hidden = !open;
    const add = el("add-entry-btn");
    add.textContent = !editor ? "Add an entry" : open ? "Hide new entry" : "Show new entry draft";
    add.setAttribute("aria-expanded", String(Boolean(editor && open)));
  }
  function discardNewGroup() {
    newGroupEditor()?.remove();
    showNewGroup(false);
  }
  el("add-entry-btn").addEventListener("click", () => {
    const editor = newGroupEditor();
    if (editor) { showNewGroup(editor.hidden); if (!editor.hidden) editor.querySelector("input, select")?.focus(); return; }
    const meaning = { id: id("m"), spelling: "", meaning: "", personal_context: null, casing: "fixed" };
    editGroup(el("new-group"), "pinned", { id: id("g"), needs_review: false, meanings: [meaning],
      recognized_forms: [{ text: "", associations: [{ meaning_id: meaning.id, basis: "user", evidence: [] }], direct: null, direct_reason: "" }] }, true);
    showNewGroup(true);
    el("new-group").scrollIntoView({ block: "start" });
    el("new-group").querySelector(".group-meaning input")?.focus();
  });

  // JSON editor and help panel
  el("learn-options-toggle").addEventListener("click", () => {
    const panel = el("learn-options");
    panel.hidden = !panel.hidden;
    el("learn-options-toggle").setAttribute("aria-expanded", String(!panel.hidden));
  });
  // A non-default scope stays visible on the toggle while the options are closed.
  el("learning-reuse").addEventListener("change", () => {
    el("learn-options-toggle").textContent = el("learning-reuse").checked ? "Options · including used transcripts" : "Options";
  });
  el("json-toggle").addEventListener("click", () => {
    const editor = el("json-editor");
    editor.hidden = !editor.hidden;
    el("json-toggle").setAttribute("aria-expanded", String(!editor.hidden));
  });
  el("save-dictionary").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(dictionaryBox.value); } catch (err) { flash(el("dictionary-status"), `Not valid JSON: ${err.message}`, "err"); return; }
    if (await saveDictionary(parsed, true)) flash(el("dictionary-status"), "Saved", "ok");
  });
  // The guide is a modal over the page, so drafts and scroll position stay as they are.
  function showHelp(open, topic = null) {
    const guide = el("help-dialog");
    if (open && !guide.open) {
      guide.showModal();
      guide.querySelector(".guide").scrollTop = topic ? el(topic).offsetTop - guide.querySelector(".guide").offsetTop : 0;
    }
    else if (!open && guide.open) guide.close();
  }
  el("help-toggle").addEventListener("click", () => showHelp(true));
  el("audio-help").addEventListener("click", () => showHelp(true, "guide-audio"));

  const label = (g) => g ? `${g.meanings.map(m => m.spelling).join(" / ")} ← ${g.recognized_forms.map(f => f.text).join(", ")}` : "";
  const describe = g => g ? [...g.meanings.map(m => `${m.spelling}: ${m.meaning}${m.personal_context ? ` (${m.personal_context})` : ""}`), ...g.recognized_forms.map(f => `Recognized: ${f.text}`)].join("\n") : "";
  // Suggestions belong to the speech model they were made for; say so when the toolbar
  // shows another one, since applying still updates that model's dictionary.
  function proposalTitle() {
    if (!proposalModel) return;
    const model = getModel();
    if (model?.id === proposalModel) proposalLabel = modelName(model.id, [model]);
    el("proposal-title").textContent = model?.id === proposalModel ? "Review suggestions" : `Review suggestions for ${proposalLabel}`;
  }
  let proposalLabel = "";
  function renderProposal(p) {
    proposalLabel = modelName(p.model, []);
    proposalModel = p.model;
    proposalChanges = p.changes.map(c => ({...structuredClone(c), included: true}));
    proposalTitle();
    drawProposal();
    proposalPanel.hidden = false;
  }
  function drawProposal() {
    proposalBody.replaceChildren();
    const known = new Map([...dict.pinned, ...(dict.learned[proposalModel] ?? []), ...proposalChanges.flatMap(c => c.after ? [c.after] : [])].flatMap(g => g.meanings).map(m => [m.id, m]));
    const pinnedIds = new Set(dict.pinned.flatMap(g => g.meanings.map(m => m.id)));
    for (const [kind, title] of [["add", "New entries"], ["update", "Changes to entries"], ["remove", "Entries to remove"]]) {
      const items = proposalChanges.filter(c => c.kind === kind);
      if (!items.length) continue;
      proposalBody.append(node("h3", `${title} (${items.length})`));
      for (const change of items) {
        const box = node("section", "", "proposal-change");
        box.classList.toggle("dismissed", !change.included);
        const heading = node("div", "", "row-actions");
        heading.append(node("strong", label(change.after || change.before)));
        const dismiss = button(change.included ? "×" : "Restore", () => { change.included = !change.included; drawProposal(); });
        dismiss.setAttribute("aria-label", `${change.included ? "Dismiss" : "Restore"} ${kind} ${label(change.after || change.before)}`);
        dismiss.title = "Leave this suggestion out; your current dictionary is kept as it is";
        heading.append(dismiss); box.append(heading);
        if (change.before) {
          box.append(node("b", kind === "remove" ? "Removed only if you keep this suggestion when applying" : "Now"), node("pre", describe(change.before), "proposal-before"));
        }
        if (change.after && change.included) {
          const editor = node("details", "", "proposal-editor");
          editor.append(node("summary", kind === "add" ? "Suggested entry · edit if needed" : "Suggested version · edit if needed"));
          editor.open = true;
          // A shared meaning can be proposed in several groups; its copies stay one definition.
          // Update every copy and its visible input in place, so each editor shows what
          // Apply submits without replacing the control the user moves to next.
          const shared = (id, part, value) => {
            for (const copy of proposalChanges.flatMap(c => c.after?.meanings ?? [])) if (copy.id === id) copy[part] = value;
            for (const input of proposalBody.querySelectorAll("input[data-meaning]")) {
              if (input.dataset.meaning === id && input.dataset.part === part) input.value = value ?? "";
            }
          };
          const tag = (input, id, part) => { input.dataset.meaning = id; input.dataset.part = part; return input; };
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
            spelling.readOnly = pinnedIds.has(meaning.id);
            tag(spelling, meaning.id, "spelling");
            tag(field(row, "What it is", meaning.meaning, value => shared(meaning.id, "meaning", value)), meaning.id, "meaning");
            tag(field(row, "How you use it (optional)", meaning.personal_context, value => shared(meaning.id, "personal_context", value || null)), meaning.id, "personal_context");
            editor.append(row);
          }
          for (const form of change.after.recognized_forms) {
            const row = node("div", "", "meaning-editor");
            const protectedForm = change.before?.recognized_forms.some(old => old.text.toLowerCase() === form.text.toLowerCase() && old.associations.some(a => pinnedIds.has(a.meaning_id)));
            const input = field(row, "The speech model writes", form.text, value => { form.text = value; form.direct = null; form.direct_reason = ""; form.associations = form.associations.map(a => ({meaning_id: a.meaning_id, basis: "user", evidence: []})); });
            input.readOnly = Boolean(protectedForm);
            const choices = node("div", "", "proposal-associations");
            for (const [id, meaning] of known) {
              if (!change.after.meanings.some(m => m.id === id) && !form.associations.some(a => a.meaning_id === id)) continue;
              const wrap = node("label", "", "caption");
              const check = document.createElement("input"); check.type = "checkbox";
              check.checked = form.associations.some(a => a.meaning_id === id);
              check.disabled = Boolean(protectedForm && pinnedIds.has(id) && check.checked);
              check.addEventListener("change", () => {
                if (check.checked) form.associations.push({meaning_id: id, basis: "user", evidence: []});
                else form.associations = form.associations.filter(a => a.meaning_id !== id);
                form.direct = null; form.direct_reason = "";
              });
              wrap.append(check, `${meaning.spelling} — ${meaning.meaning}`); choices.append(wrap);
            }
            const remove = button("Remove this spelling", () => { change.after.recognized_forms = change.after.recognized_forms.filter(f => f !== form); drawProposal(); });
            remove.disabled = Boolean(protectedForm);
            row.append(choices, remove); editor.append(row);
          }
          editor.append(button("Add another spelling", () => {
            const id = change.after.meanings[0]?.id || change.after.recognized_forms[0]?.associations[0]?.meaning_id;
            change.after.recognized_forms.push({text: "", associations: id ? [{meaning_id: id, basis: "user", evidence: []}] : []}); drawProposal();
          }));
          box.append(editor);
        }
        proposalBody.append(box);
      }
    }
    const included = proposalChanges.filter(c => c.included).length;
    el("accept-proposal").textContent = included ? `Apply ${included} change${included === 1 ? "" : "s"}` : "Finish without changes";
    if (!proposalChanges.length) proposalBody.append(node("p", "No changes suggested. Finish to close this review; the same dictations can be used again later."));
  }
  const scope = () => el("learning-reuse").checked ? "all" : "new";
  buildBtn.addEventListener("click", () => builds.start("history", {mode: mode(), scope: scope()}));

  return { load: loadDictionary, showHelp, refreshAudio: () => onboarding.load(), refreshModels: fillModels };
}
