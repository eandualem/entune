import { ICON, el, errorText, flash, segmentedGroup } from "./ui.js";
import { createAudioOnboarding } from "./audio-onboarding.js";

// The dictionary owns its document, revision and pending proposal. Model selection
// stays in the app; getters read the current selection when an action is made.
export function createDictionary({ getModel, getSettings }) {
  // ---- Dictionary ----
  // `dict` mirrors dictionary.json: pinned (the user's, including what their agents sent;
  // never changed by the model) and learned (the last accepted proposal, keyed by the
  // speech model it was learned for). An entry is a spelling, a description and the
  // phrases heard instead. The table shows pinned and the default model's learned list,
  // filtered; every change is saved whole.
  let dict = { pinned: [], learned: {} };
  let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
  let proposal = null;
  let filter = "all";
  const dictionaryBox = el("dictionary");
  const buildBtn = el("build-dictionary");
  const buildStatus = el("build-status");
  const proposalPanel = el("proposal");
  const proposalBody = el("proposal-body");
  const onboarding = createAudioOnboarding({
    getModel, getSettings,
    onProposal(value) { proposal = value; renderProposal(value); },
    onBusy(value) { buildBtn.disabled = value; },
  });

  segmentedGroup({ all: el("filter-all"), pinned: el("filter-pinned"), learned: el("filter-learned") }, (name) => { filter = name; renderDictionary(); });

  async function loadDictionary() {
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
  const same = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();
  const drop = (list, spelling) => {
    const at = list.findIndex((e) => same(e.spelling, spelling));
    if (at >= 0) list.splice(at, 1);
  };

  function entryRow(source, entry) {
    const row = document.createElement("div");
    row.className = "entry-row";
    const what = document.createElement("span");
    what.className = "what";
    what.innerHTML = `<span class="meant"></span><span class="arrow">←</span><span class="heard"></span><span class="desc"></span>`;
    what.querySelector(".meant").textContent = entry.spelling;
    what.querySelector(".heard").textContent = entry.heard.length ? entry.heard.join(", ") : "(only ever spelled right)";
    what.querySelector(".desc").textContent = entry.description;
    what.querySelector(".desc").hidden = !entry.description;
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
      pin.addEventListener("click", () => moveToPinned(entry));
      actions.append(pin);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn-icon";
    remove.title = "Remove";
    remove.setAttribute("aria-label", "Remove");
    remove.innerHTML = ICON.remove;
    remove.addEventListener("click", () => removeEntry(source, entry));
    actions.append(remove);
    row.append(what, src, actions);
    return row;
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

  // Pinning merges by spelling: new heard phrases join an existing pinned entry.
  function pinInto(list, entry) {
    const existing = list.find((e) => same(e.spelling, entry.spelling));
    if (!existing) { list.push(entry); return; }
    for (const h of entry.heard) if (!existing.heard.some((x) => same(x, h))) existing.heard.push(h);
    if (!existing.description) existing.description = entry.description;
  }
  async function addToPinned(entry) {
    const next = clone();
    pinInto(next.pinned, entry);
    return saveDictionary(next);
  }
  async function removeEntry(source, entry) {
    const next = clone();
    drop(source === "pinned" ? next.pinned : learnedOf(next), entry.spelling);
    await saveDictionary(next);
  }
  async function moveToPinned(entry) {
    const next = clone();
    drop(learnedOf(next), entry.spelling);
    pinInto(next.pinned, entry);
    await saveDictionary(next);
  }
  el("pin-all").addEventListener("click", async () => {
    const model = getModel();
    if (!model) return;
    const next = clone();
    for (const entry of learnedOf(next)) pinInto(next.pinned, entry);
    delete next.learned[model.id];
    await saveDictionary(next);
  });

  // Add: what was heard → what you meant, with an optional description; pinned.
  el("add-entry-btn").addEventListener("click", async () => {
    const heard = el("add-heard").value.split(",").map((h) => h.trim()).filter(Boolean);
    const spelling = el("add-meant").value.trim();
    if (!spelling) return;
    const entry = { spelling, description: el("add-description").value.trim(), heard };
    if (await addToPinned(entry)) for (const id of ["add-heard", "add-meant", "add-description"]) el(id).value = "";
  });
  for (const id of ["add-heard", "add-meant", "add-description"]) {
    el(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el("add-entry-btn").click(); } });
  }

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
  const label = (e) => (e.heard.length ? `${e.spelling} ← ${e.heard.join(", ")}` : e.spelling);
  function chips(list, cls) {
    const box = document.createElement("div");
    box.className = "chips";
    for (const entry of list) {
      const c = document.createElement("span");
      c.className = `chip ${cls}`;
      c.textContent = label(entry);
      if (entry.description) c.title = entry.description;
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
  buildBtn.addEventListener("click", async () => {
    onboarding.setHistoryBusy(true);
    buildBtn.disabled = true;
    buildStatus.className = "caption save-status show";
    buildStatus.textContent = "Asking the model… a long history goes in steps and can take several minutes.";
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
      onboarding.setHistoryBusy(false);
    }
  });
  el("accept-proposal").addEventListener("click", async () => {
    if (!proposal) return;
    if (proposal.version && proposal.version !== dictVersion) {
      flash(buildStatus, "The dictionary changed during this build. Discard this proposal and build again.", "err");
      return;
    }
    const next = clone();
    next.learned[proposal.model] = proposal.learned;
    if (await saveDictionary(next)) {
      if (proposal.version) await onboarding.dismiss();
      proposal = null;
      proposalPanel.hidden = true;
      flash(buildStatus, "Accepted", "ok");
    }
  });
  el("discard-proposal").addEventListener("click", async () => {
    if (proposal?.version) await onboarding.dismiss();
    proposal = null;
    proposalPanel.hidden = true;
  });

  return { load: loadDictionary, showHelp };
}
