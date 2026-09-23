import { api, el, errorText, flash, segmentedGroup } from "./ui.js";
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
  const refineBtn = el("refine-dictionary");
  const buildStatus = el("build-status");
  const proposalPanel = el("proposal");
  const proposalBody = el("proposal-body");
  const modelSelect = el("dictionary-model");
  let missingKey = false; // the saved dictionary model's provider has no key
  const onboarding = createAudioOnboarding({
    getModel, getSettings, getDictionaryModelName: () => missingKey ? null : languageName(getSettings()?.dictionaryModel),
    onBuild(selection) { return builds.start("audio", selection); },
    onBusy(value) { importing = value; gate(); },
  });

  const builds = createDictionaryBuild({
    names: { speech: (id) => { const model = getModel(); return model && model.id === id ? model.label : id; }, language: (ref) => languageName(ref) ?? ref },
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
    buildBtn.disabled = refineBtn.disabled = importing || building || !ready;
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
    if (building) { flash(buildStatus, "Finish learning by applying or discarding first.", "err"); return false; }
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
    const model = getModel()?.label ?? "this speech model";
    head.append(node("span", source === "pinned" ? "Pinned · every speech model" : `Learned · ${model}`, `scope ${source}`));
    if (group.needs_review) head.append(node("span", "Imported: review definitions and casing", "badge warn"));
    head.append(node("span", "", "spacer"), button("Edit", () => editGroup(card, source, group)), button("Remove", async () => {
      const next = clone(), list = source === "pinned" ? next.pinned : learnedOf(next);
      list.splice(list.findIndex((g) => g.id === group.id), 1);
      await saveDictionary(next); // references from other groups must be resolved explicitly
    }));
    const forms = node("div", "", "group-forms");
    forms.append(node("h3", "Recognized as"));
    for (const form of group.recognized_forms) {
      const row = node("div", "", "form-row");
      const chips = node("span", "", "chips");
      for (const link of form.associations) {
        const meaning = known.get(link.meaning_id);
        const chip = node("span", meaning?.spelling ?? link.meaning_id, "chip");
        if (link.basis === "literal") { chip.classList.add("literal"); chip.title = "Kept as written when this meaning fits"; chip.append(node("span", " · as written", "chip-note")); }
        if (form.direct === link.meaning_id) { chip.classList.add("direct"); chip.title = `Always used without context: ${form.direct_reason}`; }
        chips.append(chip);
      }
      row.append(node("code", form.text, "form-text"), node("span", "→", "arrow"), chips);
      forms.append(row);
    }
    if (!group.recognized_forms.length) forms.append(node("p", "No recognized forms.", "caption"));
    const list = node("div", "", "group-meanings");
    list.append(node("h3", "Meanings"));
    for (const meaning of group.meanings) {
      const item = node("div", "", "meaning");
      const line = node("div", "", "meaning-line");
      line.append(node("strong", meaning.spelling, "spelling"), node("span", meaning.casing === "fixed" ? "exact casing" : "sentence casing", "casing"));
      if (source === "learned") line.append(node("span", "", "spacer"), button("Pin", () => pinMeaning(group, meaning)));
      item.append(line, node("p", meaning.meaning || "Definition needed before it can be chosen.", meaning.meaning ? "definition" : "definition missing"));
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
    const panel = node("div", "", "group-editor");
    const status = node("p", "", "caption save-status");
    let scope = source;
    // A learned draft belongs to the speech model it was started for, even if the
    // toolbar selection changes before it is saved.
    const model = getModel();
    host.append(panel);
    function render() {
      panel.replaceChildren();
      if (created) {
        panel.append(node("h3", "New confusion group"));
        field(panel, "Scope", scope, (v) => { scope = v; }, [["pinned", "Pinned · every speech model"], ...(model ? [["learned", `Learned · ${model.label}`]] : [])]);
      }
      for (const meaning of draft.meanings) {
        const section = node("fieldset", "", "group-meaning"); section.append(node("legend", "Meaning"));
        field(section, "Output spelling (including plural)", meaning.spelling, (v) => { meaning.spelling = v; clearDirect(meaning.id); });
        field(section, "General definition", meaning.meaning, (v) => { meaning.meaning = v; });
        field(section, "Personal usage (optional)", meaning.personal_context, (v) => { meaning.personal_context = v || null; });
        field(section, "Casing", meaning.casing, (v) => { meaning.casing = v; clearDirect(meaning.id); }, [["fixed", "Fixed name or acronym"], ["ordinary", "Ordinary sentence casing"]]);
        section.append(button("Remove meaning", () => {
          draft.meanings = draft.meanings.filter((m) => m.id !== meaning.id);
          for (const f of draft.recognized_forms) f.associations = f.associations.filter((a) => a.meaning_id !== meaning.id);
          clearDirect(meaning.id); render();
        })); panel.append(section);
      }
      panel.append(button("Add meaning", () => { draft.meanings.push({ id: id("m"), spelling: "", meaning: "", personal_context: null, casing: "fixed" }); render(); }));
      const known = meanings(dict, model?.id); for (const m of draft.meanings) known.set(m.id, m);
      for (const form of draft.recognized_forms) {
        const section = node("fieldset", "", "group-form-editor"); section.append(node("legend", "Recognized form"));
        field(section, "What the speech model writes", form.text, (v) => {
          form.text = v; form.associations = form.associations.map((a) => ({ meaning_id: a.meaning_id, basis: "user", evidence: [] }));
          form.direct = null; form.direct_reason = ""; render();
        });
        section.append(node("p", "Eligible meanings for this form", "caption"));
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
          label.append(check, ` ${m.spelling || "New meaning"} — ${m.meaning || "definition needed"}`); section.append(label);
        }
        const others = [...known].filter(([mid]) => !own.has(mid) && !linked.has(mid));
        if (others.length) {
          field(section, "Also eligible: a meaning from another group", "", (v) => {
            if (v) { form.associations.push({ meaning_id: v, basis: "user", evidence: [] }); form.direct = null; form.direct_reason = ""; render(); }
          }, [["", "Choose a meaning…"], ...others.map(([mid, m]) => [mid, `${m.spelling} — ${m.meaning || "definition needed"}`])]);
        }
        field(section, "Without context", form.direct, (v) => { form.direct = v || null; if (!v) form.direct_reason = ""; },
          [["", "Require context"], ...form.associations.map((a) => [a.meaning_id, `Always use ${known.get(a.meaning_id)?.spelling}`])]);
        field(section, "Direct-mapping approval reason", form.direct_reason, (v) => { form.direct_reason = v; });
        section.append(node("p", "Approve a direct mapping only when this form always has that meaning. A competing output or overlapping span still requires context.", "caption"));
        section.append(button("Remove form", () => { draft.recognized_forms = draft.recognized_forms.filter((f) => f !== form); render(); }));
        panel.append(section);
      }
      panel.append(button("Add recognized form", () => { draft.recognized_forms.push({ text: "", associations: [], direct: null, direct_reason: "" }); render(); }));
      panel.append(button("Save group", async () => {
        const problem = draft.meanings.some((m) => !m.spelling.trim()) ? "Give every meaning an output spelling."
          : draft.recognized_forms.some((f) => !f.text.trim()) ? "Give every recognized form its text, or remove it."
          : draft.recognized_forms.some((f) => !f.associations.length) ? "Link every recognized form to at least one meaning."
          : !draft.meanings.length && !draft.recognized_forms.length ? "An empty group has no knowledge." : "";
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
        if (await saveDictionary(next, false, status) && created) panel.remove();
      }), button("Cancel", () => panel.remove()), status);
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
      none.textContent = filter === "learned" ? "Nothing learned for this speech model yet. Generate proposes groups from its transcripts." : "No groups yet. Generate from history, learn from audio, or create a group.";
      rows.push(none);
    }
    el("dict-rows").replaceChildren(...rows);
    el("filter-learned").textContent = model ? `Learned · ${model.label.replace(" / ", " · ")}` : "Learned";
    el("pin-all").hidden = learned.length === 0;
    el("groups-count").textContent = `${dict.pinned.length} pinned · ${learned.length} learned for this speech model`;
    el("learn-source").textContent = model
      ? `Uses the saved transcripts of ${model.label}, the speech model selected above.`
      : "Choose a speech model in the toolbar to learn from its transcripts.";
    fillModels();
    if (jsonText !== undefined) dictionaryBox.value = jsonText;
    lockEditors();
  }

  el("pin-all").addEventListener("click", () => pinMeaning(null, null));

  el("add-entry-btn").addEventListener("click", () => {
    const meaning = { id: id("m"), spelling: "", meaning: "", personal_context: null, casing: "fixed" };
    editGroup(el("new-group"), "pinned", { id: id("g"), needs_review: false, meanings: [meaning],
      recognized_forms: [{ text: "", associations: [{ meaning_id: meaning.id, basis: "user", evidence: [] }], direct: null, direct_reason: "" }] }, true);
    el("new-group").scrollIntoView({ block: "nearest" });
    el("new-group").querySelector(".group-meaning input")?.focus();
  });

  // JSON editor and help panel
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

  const label = (g) => g ? `${g.meanings.map(m => m.spelling).join(" / ")} ← ${g.recognized_forms.map(f => f.text).join(", ")}` : "";
  const describe = g => g ? [...g.meanings.map(m => `${m.spelling}: ${m.meaning}${m.personal_context ? ` (${m.personal_context})` : ""}`), ...g.recognized_forms.map(f => `Recognized: ${f.text}`)].join("\n") : "";
  function renderProposal(p) {
    proposalModel = p.model;
    proposalChanges = p.changes.map(c => ({...structuredClone(c), included: true}));
    const model = getModel();
    el("proposal-title").textContent = `Proposed for ${model && model.id === p.model ? model.label : p.model}`;
    drawProposal();
    proposalPanel.hidden = false;
  }
  function drawProposal() {
    proposalBody.replaceChildren();
    const known = new Map([...dict.pinned, ...(dict.learned[proposalModel] ?? []), ...proposalChanges.flatMap(c => c.after ? [c.after] : [])].flatMap(g => g.meanings).map(m => [m.id, m]));
    const pinnedIds = new Set(dict.pinned.flatMap(g => g.meanings.map(m => m.id)));
    for (const [kind, title] of [["add", "Additions"], ["update", "Updates"], ["remove", "Proposed removals"]]) {
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
        dismiss.title = "Dismiss this proposal; the current dictionary entry is kept";
        heading.append(dismiss); box.append(heading);
        if (change.before) {
          box.append(node("b", kind === "remove" ? "Will be removed only if included when applying" : "Before"), node("pre", describe(change.before), "proposal-before"));
        }
        if (change.after && change.included) {
          const editor = node("details", "", "proposal-editor");
          editor.append(node("summary", "After · inspect and edit"));
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
            const spelling = field(row, "Output spelling", meaning.spelling, value => {
              for (const form of proposalChanges.flatMap(c => c.after?.recognized_forms ?? [])) {
                if (form.direct === meaning.id) { form.direct = null; form.direct_reason = ""; }
                form.associations = form.associations.map(a => a.basis === "literal" && a.meaning_id === meaning.id
                  ? {meaning_id: a.meaning_id, basis: "user", evidence: []} : a);
              }
              shared(meaning.id, "spelling", value);
            });
            spelling.readOnly = pinnedIds.has(meaning.id);
            tag(spelling, meaning.id, "spelling");
            tag(field(row, "Definition", meaning.meaning, value => shared(meaning.id, "meaning", value)), meaning.id, "meaning");
            tag(field(row, "Personal usage", meaning.personal_context, value => shared(meaning.id, "personal_context", value || null)), meaning.id, "personal_context");
            editor.append(row);
          }
          for (const form of change.after.recognized_forms) {
            const row = node("div", "", "meaning-editor");
            const protectedForm = change.before?.recognized_forms.some(old => old.text.toLowerCase() === form.text.toLowerCase() && old.associations.some(a => pinnedIds.has(a.meaning_id)));
            const input = field(row, "Recognized form", form.text, value => { form.text = value; form.direct = null; form.direct_reason = ""; form.associations = form.associations.map(a => ({meaning_id: a.meaning_id, basis: "user", evidence: []})); });
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
            const remove = button("Remove proposed form", () => { change.after.recognized_forms = change.after.recognized_forms.filter(f => f !== form); drawProposal(); });
            remove.disabled = Boolean(protectedForm);
            row.append(choices, remove); editor.append(row);
          }
          editor.append(button("Add recognized form", () => {
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
    if (!proposalChanges.length) proposalBody.append(node("p", "No changes proposed. Finishing leaves these inputs eligible for another learning run."));
  }
  buildBtn.addEventListener("click", () => builds.start("history", {mode: "generate", scope: el("learning-scope").value}));
  refineBtn.addEventListener("click", () => builds.start("history", {mode: "refine", scope: el("learning-scope").value}));

  return { load: loadDictionary, showHelp, refreshAudio: () => onboarding.load(), refreshModels: fillModels };
}
