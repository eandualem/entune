import { el, flash, segmentedGroup } from "./ui.js";
import { createAudioOnboarding } from "./audio-onboarding.js";
import { createDictionaryBuild } from "./dictionary-build.js";

// The dictionary owns its document, revision and pending proposal. Model selection
// stays in the app; getters read the current selection when an action is made.
export function createDictionary({ getModel, getSettings }) {
  // Confusion groups are the edit unit; stable IDs distinguish meanings with one spelling.
  let dict = { version: 2, pinned: [], learned: {} };
  let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
  let importing = false;
  let building = false;
  let filter = "all";
  const dictionaryBox = el("dictionary");
  const buildBtn = el("build-dictionary");
  const buildStatus = el("build-status");
  const proposalPanel = el("proposal");
  const proposalBody = el("proposal-body");
  const onboarding = createAudioOnboarding({
    getModel, getSettings,
    onBuild() { return builds.start("audio"); },
    onBusy(value) { importing = value; buildBtn.disabled = importing || building; },
  });

  const builds = createDictionaryBuild({
    onBusy(value) { building = value; buildBtn.disabled = importing || building; onboarding.setBuildBusy(value); },
    onProposal(value) { if (value) renderProposal(value); else proposalPanel.hidden = true; },
    onAccepted: () => loadDictionary(false),
  });

  segmentedGroup({ all: el("filter-all"), pinned: el("filter-pinned"), learned: el("filter-learned") }, (name) => { filter = name; renderDictionary(); });

  async function loadDictionary(pollBuild = true) {
    if (pollBuild) await builds.load();
    dictVersion = null;
    const res = await fetch("/api/dictionary");
    const text = await res.text();
    if (!res.ok) { dictVersion = null; flash(el("dictionary-status"), text, "err"); el("json-editor").hidden = false; return; }
    dict = JSON.parse(text);
    dictVersion = res.headers.get("etag");
    renderDictionary(text);
    await onboarding.load();
  }

  // Every edit sends the whole document, named with the version it was made on. The
  // server refuses a save on a stale version (an agent or a hand edit got there first)
  // and the fresh document is shown instead. Explicit JSON repair can replace an
  // unreadable document after confirmation; table edits always need a loaded version.
  async function saveDictionary(next, fromEditor = false) {
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
    if (!res.ok) { flash(el("dictionary-status"), text, "err"); flash(buildStatus, text, "err"); return false; }
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
  function meanings(doc = dict) {
    return new Map([...doc.pinned, ...learnedOf(doc)].flatMap((g) => g.meanings).map((m) => [m.id, m]));
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
  function entryRow(source, group) {
    const box = node("details", "", "confusion-group");
    const title = node("summary", "", "group-title");
    const known = meanings();
    const mids = new Set([...group.meanings.map((m) => m.id), ...group.recognized_forms.flatMap((f) => f.associations.map((a) => a.meaning_id))]);
    title.append(node("strong", [...mids].map((mid) => known.get(mid)?.spelling ?? mid).join(" / ")),
      node("span", `${source === "pinned" ? "Pinned · shared" : "Learned · this model"}${group.needs_review ? " · Imported: review definitions and casing" : ""}`, "caption"));
    box.append(title);
    for (const form of group.recognized_forms) {
      const names = form.associations.map((a) => known.get(a.meaning_id)?.spelling ?? a.meaning_id);
      box.append(node("p", `${form.text} → ${names.join(" / ")}${form.direct ? " · Approved direct mapping" : ""}`, "group-form"));
    }
    for (const meaning of group.meanings) {
      const line = node("div", "", "group-meaning");
      line.append(node("strong", meaning.spelling), node("p", meaning.meaning || "Definition needed before contextual use."),
        node("p", meaning.personal_context || "", "caption"));
      if (source === "learned") line.append(button("Pin this meaning", () => pinMeaning(group, meaning)));
      box.append(line);
    }
    box.append(button("Edit group", () => editGroup(box, source, group)), button("Remove group", async () => {
      const next = clone(), list = source === "pinned" ? next.pinned : learnedOf(next);
      list.splice(list.findIndex((g) => g.id === group.id), 1);
      await saveDictionary(next); // references from other groups must be resolved explicitly
    }));
    return box;
  }
  function editGroup(box, source, original) {
    box.querySelector(".group-editor")?.remove();
    const draft = structuredClone(original);
    const panel = node("div", "", "group-editor");
    box.append(panel);
    function render() {
      panel.replaceChildren();
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
      const known = meanings(); for (const m of draft.meanings) known.set(m.id, m);
      for (const form of draft.recognized_forms) {
        const section = node("fieldset", "", "group-form-editor"); section.append(node("legend", "Recognized form"));
        field(section, "What the speech model writes", form.text, (v) => {
          form.text = v; form.associations = form.associations.map((a) => ({ meaning_id: a.meaning_id, basis: "user", evidence: [] }));
          form.direct = null; form.direct_reason = ""; render();
        });
        section.append(node("p", "Eligible meanings for this form", "caption"));
        for (const [mid, m] of known) {
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
        field(section, "Without context", form.direct, (v) => { form.direct = v || null; if (!v) form.direct_reason = ""; },
          [["", "Require context"], ...form.associations.map((a) => [a.meaning_id, `Always use ${known.get(a.meaning_id)?.spelling}`])]);
        field(section, "Direct-mapping approval reason", form.direct_reason, (v) => { form.direct_reason = v; });
        section.append(node("p", "Approve a direct mapping only when this form always has that meaning. A competing output or overlapping span still requires context.", "caption"));
        section.append(button("Remove form", () => { draft.recognized_forms = draft.recognized_forms.filter((f) => f !== form); render(); }));
        panel.append(section);
      }
      panel.append(button("Add recognized form", () => { draft.recognized_forms.push({ text: "", associations: [], direct: null, direct_reason: "" }); render(); }));
      panel.append(button("Save group", async () => {
        const next = clone(), list = source === "pinned" ? next.pinned : learnedOf(next);
        draft.needs_review = draft.meanings.some((m) => !m.meaning.trim());
        list[list.findIndex((g) => g.id === original.id)] = draft;
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
        await saveDictionary(next);
      }), button("Cancel", () => panel.remove()));
    }
    function clearDirect(mid) {
      for (const f of draft.recognized_forms) if (f.direct === mid) { f.direct = null; f.direct_reason = ""; }
    }
    render();
  }

  function renderDictionary(jsonText) {
    const model = getModel();
    const dictionaryModel = getSettings()?.dictionaryModel;
    const learned = learnedOf(dict);
    const rows = [];
    if (filter !== "learned") rows.push(...dict.pinned.map((e) => entryRow("pinned", e)));
    if (filter !== "pinned") rows.push(...learned.map((e) => entryRow("learned", e)));
    if (rows.length === 0) {
      const none = document.createElement("div");
      none.className = "entry-row none";
      none.textContent = filter === "learned" ? "Nothing learned for this model yet. Build from history proposes a list." : "Nothing here yet.";
      rows.push(none);
    }
    el("dict-rows").replaceChildren(...rows);
    el("filter-learned").textContent = model ? `Learned · ${model.label.replace(" / ", " · ")}` : "Learned";
    el("pin-all").hidden = learned.length === 0;
    buildBtn.textContent = learned.length ? "Refine from history" : "Build from history";
    buildBtn.title = dictionaryModel
      ? `Send this model's recent transcripts to ${dictionaryModel} and review a proposal; nothing is saved before Accept`
      : "Needs an Anthropic or OpenAI key in Settings › Providers";
    if (jsonText !== undefined) dictionaryBox.value = jsonText;
  }

  el("pin-all").addEventListener("click", () => pinMeaning(null, null));

  el("add-entry-btn").addEventListener("click", async () => {
    const spelling = el("add-meant").value.trim(), definition = el("add-description").value.trim();
    const heard = el("add-heard").value.split(",").map((h) => h.trim()).filter(Boolean);
    if (!spelling || !definition || !heard.length) { flash(buildStatus, "Give a recognized form, spelling and definition.", "err"); return; }
    const mid = id("m"), next = clone();
    next.pinned.push({ id: id("g"), needs_review: false,
      meanings: [{ id: mid, spelling, meaning: definition, personal_context: null, casing: "fixed" }],
      recognized_forms: [...new Set([...heard, spelling])].map((text) => ({ text,
        associations: [{ meaning_id: mid, basis: text.toLowerCase() === spelling.toLowerCase() ? "literal" : "user", evidence: [] }], direct: null, direct_reason: "" })),
    });
    if (await saveDictionary(next)) for (const name of ["add-heard", "add-meant", "add-description"]) el(name).value = "";
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

  // Build: the model proposes a learned list for the default speech model, from that
  // model's transcripts; nothing changes until Accept.
  const label = (g) => `${g.meanings.map((m) => m.spelling).join(" / ")} ← ${g.recognized_forms.map((f) => f.text).join(", ")}`;
  function chips(list, cls) {
    const box = document.createElement("div");
    box.className = "chips";
    for (const entry of list) {
      const c = document.createElement("span");
      c.className = `chip ${cls}`;
      c.textContent = label(entry);
      c.title = entry.meanings.map((m) => `${m.spelling}: ${m.meaning}`).join("\n");
      box.append(c);
    }
    return box;
  }
  function renderProposal(p) {
    el("proposal-title").textContent = `Proposed for ${p.model}`;
    proposalBody.replaceChildren();
    const groups = [["Added", p.added, "add"], ["Removed", p.removed, "remove"]];
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
    proposalPanel.hidden = false;
  }
  buildBtn.addEventListener("click", () => builds.start("history"));

  return { load: loadDictionary, showHelp };
}
