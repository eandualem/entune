import { ICON, el, errorText, flash, segmentedGroup } from "./ui.js";

// The dictionary owns its document, revision and pending proposal. Model selection
// stays in the app; getters read the current selection when an action is made.
export function createDictionary({ getModel, getSettings }) {
  // ---- Dictionary ----
  // `dict` mirrors dictionary.json: pinned (the user's, including what their agents sent;
  // never changed by the model) and learned (the last accepted proposal, keyed by the
  // speech model it was learned for). The table shows pinned and the default model's
  // learned list, filtered; every change is saved whole.
  let dict = { pinned: { terms: [], replacements: {} }, learned: {} };
  let dictVersion = null; // the server's ETag for the document we edit; null after a failed load
  let proposal = null;
  let filter = "all";
  const dictionaryBox = el("dictionary");
  const buildBtn = el("build-dictionary");
  const buildStatus = el("build-status");
  const proposalPanel = el("proposal");
  const proposalBody = el("proposal-body");
  const empty = () => ({ terms: [], replacements: {} });

  segmentedGroup({ all: el("filter-all"), pinned: el("filter-pinned"), learned: el("filter-learned") }, (name) => { filter = name; renderDictionary(); });

  async function loadDictionary() {
    dictVersion = null;
    const res = await fetch("/api/dictionary");
    const text = await res.text();
    if (!res.ok) { dictVersion = null; flash(el("dictionary-status"), text, "err"); el("json-editor").hidden = false; return; }
    dict = JSON.parse(text);
    dictVersion = res.headers.get("etag");
    renderDictionary(text);
  }

  // Every edit sends the whole document, named with the version it was made on. The
  // server refuses a save on a stale version (an agent or a hand edit got there first)
  // and the fresh document is shown instead.
  async function saveDictionary(next) {
    if (!dictVersion) {
      await loadDictionary();
      if (dictVersion) flash(el("dictionary-status"), "Dictionary reloaded; please redo that change.", "err");
      return false;
    }
    const headers = { "content-type": "application/json" };
    headers["if-match"] = dictVersion;
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
    if (!model) return empty();
    return (doc.learned[model.id] ??= empty());
  }
  const clone = () => JSON.parse(JSON.stringify(dict));

  function entryRow(source, kind, heard, meant) {
    const row = document.createElement("div");
    row.className = "entry-row";
    const kindEl = document.createElement("span");
    kindEl.className = "kind";
    kindEl.textContent = kind === "term" ? "TERM" : "REPLACE";
    const what = document.createElement("span");
    what.className = "what";
    if (kind === "term") what.textContent = heard;
    else {
      what.innerHTML = `<span class="heard"></span><span class="arrow">→</span><span class="meant"></span>`;
      what.querySelector(".heard").textContent = heard;
      what.querySelector(".meant").textContent = meant;
    }
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
      pin.addEventListener("click", () => moveToPinned(kind, heard, meant));
      actions.append(pin);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn-icon";
    remove.title = "Remove";
    remove.setAttribute("aria-label", "Remove");
    remove.innerHTML = ICON.remove;
    remove.addEventListener("click", () => removeEntry(source, kind, heard));
    actions.append(remove);
    row.append(kindEl, what, src, actions);
    return row;
  }

  function renderDictionary(jsonText) {
    const model = getModel();
    const dictionaryModel = getSettings()?.dictionaryModel;
    const learned = learnedOf(dict);
    const rows = [];
    if (filter !== "learned") {
      rows.push(...dict.pinned.terms.map((t) => entryRow("pinned", "term", t)));
      rows.push(...Object.entries(dict.pinned.replacements).map(([h, m]) => entryRow("pinned", "replace", h, m)));
    }
    if (filter !== "pinned") {
      rows.push(...learned.terms.map((t) => entryRow("learned", "term", t)));
      rows.push(...Object.entries(learned.replacements).map(([h, m]) => entryRow("learned", "replace", h, m)));
    }
    if (rows.length === 0) {
      const none = document.createElement("div");
      none.className = "entry-row none";
      none.textContent = filter === "learned" ? "Nothing learned for this model yet. Build from history proposes a list." : "Nothing here yet.";
      rows.push(none);
    }
    el("dict-rows").replaceChildren(...rows);
    const learnedCount = learned.terms.length + Object.keys(learned.replacements).length;
    el("filter-learned").textContent = model ? `Learned · ${model.label.replace(" / ", " · ")}` : "Learned";
    el("pin-all").hidden = learnedCount === 0;
    buildBtn.textContent = learnedCount ? "Refine from history" : "Build from history";
    buildBtn.title = dictionaryModel
      ? `Send this model's recent transcripts to ${dictionaryModel} and review a proposal; nothing is saved before Accept`
      : "Needs an Anthropic or OpenAI key in Settings › Providers";
    const budget = termBudgetText(learned);
    el("term-budget").textContent = budget;
    el("term-budget-row").hidden = !budget;
    if (jsonText !== undefined) dictionaryBox.value = jsonText;
  }

  function termBudgetText(learned) {
    const model = getModel();
    if (!model) return "";
    const limit = model.term_limit;
    const pinned = dict.pinned.terms.length;
    const inUse = pinned + learned.terms.length;
    if (limit === null) return `${model.label.split(" / ")[0]} takes no terms · replacements only · ${inUse} pinned words unused here`;
    const line = `${inUse} of ${limit} terms in use · ${pinned} pinned`;
    if (pinned >= limit) return `${line} · full: remove pinned terms to make room`;
    if (inUse >= limit) return `${line} · full`;
    return line;
  }

  async function addToPinned(kind, heard, meant) {
    const next = clone();
    if (kind === "term") { if (!next.pinned.terms.includes(heard)) next.pinned.terms.push(heard); }
    else next.pinned.replacements[heard] = meant;
    return saveDictionary(next);
  }
  async function removeEntry(source, kind, heard) {
    const next = clone();
    const from = source === "pinned" ? next.pinned : learnedOf(next);
    if (kind === "term") from.terms = from.terms.filter((t) => t !== heard);
    else delete from.replacements[heard];
    await saveDictionary(next);
  }
  async function moveToPinned(kind, heard, meant) {
    const next = clone();
    const from = learnedOf(next);
    if (kind === "term") {
      from.terms = from.terms.filter((t) => t !== heard);
      if (!next.pinned.terms.includes(heard)) next.pinned.terms.push(heard);
    } else {
      delete from.replacements[heard];
      next.pinned.replacements[heard] = meant;
    }
    await saveDictionary(next);
  }
  el("pin-all").addEventListener("click", async () => {
    const model = getModel();
    if (!model) return;
    const next = clone();
    const learned = learnedOf(next);
    for (const t of learned.terms) if (!next.pinned.terms.includes(t)) next.pinned.terms.push(t);
    Object.assign(next.pinned.replacements, learned.replacements);
    delete next.learned[model.id];
    await saveDictionary(next);
  });

  // Add: a word, or a heard → meant fix; both are pinned.
  segmentedGroup({ word: el("add-word-mode"), fix: el("add-fix-mode") }, (name) => {
    el("add-word-row").hidden = name !== "word";
    el("add-fix-row").hidden = name !== "fix";
  });
  el("add-term-btn").addEventListener("click", async () => {
    const term = el("add-term").value.trim();
    if (!term) return;
    if (await addToPinned("term", term)) el("add-term").value = "";
  });
  el("add-term").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el("add-term-btn").click(); } });
  el("add-replacement-btn").addEventListener("click", async () => {
    const heard = el("add-heard").value.trim();
    const meant = el("add-meant").value.trim();
    if (!heard || !meant) return;
    if (await addToPinned("replace", heard, meant)) { el("add-heard").value = ""; el("add-meant").value = ""; }
  });
  el("add-meant").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el("add-replacement-btn").click(); } });

  // JSON editor and help panel
  el("json-toggle").addEventListener("click", () => {
    const editor = el("json-editor");
    editor.hidden = !editor.hidden;
    el("json-toggle").setAttribute("aria-expanded", String(!editor.hidden));
  });
  el("save-dictionary").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(dictionaryBox.value); } catch (err) { flash(el("dictionary-status"), `Not valid JSON: ${err.message}`, "err"); return; }
    if (await saveDictionary(parsed)) flash(el("dictionary-status"), "Saved", "ok");
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
  function chips(list, cls) {
    const box = document.createElement("div");
    box.className = "chips";
    for (const text of list) {
      const c = document.createElement("span");
      c.className = `chip ${cls}`;
      c.textContent = text;
      box.append(c);
    }
    return box;
  }
  function renderProposal(p) {
    proposalBody.replaceChildren();
    const groups = [
      ["Added terms", p.added.terms, "add"],
      ["Added replacements", Object.entries(p.added.replacements).map(([h, m]) => `${h} → ${m}`), "add"],
      ["Removed terms", p.removed.terms, "remove"],
      ["Removed replacements", Object.entries(p.removed.replacements).map(([h, m]) => `${h} → ${m}`), "remove"],
    ];
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
    if (p.dropped_terms > 0) {
      const note = document.createElement("p");
      note.className = "nothing";
      note.textContent = `${p.dropped_terms} more proposed terms did not fit: this model takes ${p.budget.limit} and ${p.budget.pinned} are pinned. Remove some to make room.`;
      proposalBody.append(note);
    }
    proposalPanel.hidden = false;
  }
  buildBtn.addEventListener("click", async () => {
    buildBtn.disabled = true;
    buildStatus.className = "caption save-status show";
    buildStatus.textContent = "Asking the model… this can take a minute.";
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
      buildBtn.disabled = false;
    }
  });
  el("accept-proposal").addEventListener("click", async () => {
    if (!proposal) return;
    const next = clone();
    next.learned[proposal.model] = proposal.learned;
    if (await saveDictionary(next)) {
      proposal = null;
      proposalPanel.hidden = true;
      flash(buildStatus, "Accepted", "ok");
    }
  });
  el("discard-proposal").addEventListener("click", () => { proposal = null; proposalPanel.hidden = true; });

  return { load: loadDictionary, showHelp };
}
