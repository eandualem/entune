import { ICON, api, el, errorText, flash, whenLabel } from "./ui.js";

// Settings owns its forms, local-model polling and shortcut capture. Callbacks
// refresh the model and dictionary views after a successful configuration change.
export function createSettings({ onLoaded, onModelsChanged, onShortcutsChanged, onError }) {
  let settings = null;
  const fastInput = el("fast-mode");

  async function saveSetting(body, statusTarget) {
    try {
      await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ keys: {}, ...body }) });
      if (statusTarget) flash(statusTarget, "Saved", "ok");
      return true;
    } catch (err) {
      if (statusTarget) flash(statusTarget, errorText(err), "err");
      else onError(errorText(err));
      return false;
    }
  }

  // ---- Settings ----
  function keyRow(provider) {
    const row = document.createElement("div");
    row.className = "srow";
    const label = document.createElement("label");
    label.className = "name";
    label.htmlFor = `key-${provider.id}`;
    label.textContent = provider.name;
    const input = document.createElement("input");
    input.className = "input field";
    input.id = `key-${provider.id}`;
    input.type = "password";
    input.name = provider.id;
    input.autocomplete = "off";
    input.placeholder = provider.keyHint ? `saved ${provider.keyHint} · type to replace` : "Not set";
    row.append(label, input);
    return row;
  }

  const keysForm = el("keys-form");
  keysForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const keys = {};
    for (const input of keysForm.querySelectorAll("input[type=password]")) {
      if (input.value.trim()) keys[input.name] = input.value.trim();
    }
    if (Object.keys(keys).length === 0) { flash(el("keys-status"), "Nothing to save", "ok"); return; }
    if (await saveSetting({ keys }, el("keys-status"))) await loadSettings();
  });

  // The dictionary model: a summary line with Change, then provider, key and model.
  const dm = { edit: el("dm-edit"), provider: el("dm-provider"), key: el("dm-key"), model: el("dm-model"), custom: el("dm-model-custom") };
  function renderDictionaryModel() {
    const [providerId, , modelId] = splitRef(settings.dictionaryModel);
    const provider = settings.llmProviders.find((p) => p.id === providerId);
    const anyKey = settings.llmProviders.find((p) => p.keyHint);
    if (provider) {
      el("dm-title").textContent = `${provider.name} · ${modelId}`;
      el("dm-caption").textContent = provider.keyHint ? `key saved ${provider.keyHint}` : `no key for ${provider.name} yet: add one to build the dictionary`;
    } else {
      el("dm-title").textContent = "No model";
      el("dm-caption").textContent = anyKey ? "" : "Add an Anthropic or OpenAI key to build the dictionary from your history.";
    }
  }
  function splitRef(ref) {
    const i = (ref ?? "").indexOf(":");
    return i < 0 ? [ref ?? "", "", ""] : [ref.slice(0, i), ":", ref.slice(i + 1)];
  }
  function fillDictionaryModelForm() {
    const [providerId, , modelId] = splitRef(settings.dictionaryModel);
    dm.provider.replaceChildren(...settings.llmProviders.map((p) => new Option(p.name, p.id, false, p.id === providerId)));
    if (!providerId) dm.provider.value = settings.llmProviders.find((p) => p.keyHint)?.id ?? settings.llmProviders[0]?.id ?? "";
    fillDictionaryModelChoices(modelId);
  }
  function fillDictionaryModelChoices(chosen) {
    const provider = settings.llmProviders.find((p) => p.id === dm.provider.value);
    if (!provider) return;
    dm.key.value = "";
    dm.key.placeholder = provider.keyHint ? `saved ${provider.keyHint} · type to replace` : "Not set";
    const known = provider.models.map((m) => m.id);
    const listed = chosen && !known.includes(`${provider.id}:${chosen}`) ? [{ id: `${provider.id}:${chosen}`, name: chosen }] : [];
    dm.model.replaceChildren(
      ...[...listed, ...provider.models].map((m) => new Option(`${m.name}${m.id === provider.defaultModel ? " (suggested)" : ""}`, m.id.slice(provider.id.length + 1))),
      new Option("Custom…", "__custom__"),
    );
    dm.model.value = chosen && dm.model.querySelector(`option[value="${CSS.escape(chosen)}"]`) ? chosen : provider.defaultModel.slice(provider.id.length + 1);
    dm.custom.hidden = true;
    dm.custom.value = "";
  }
  dm.provider.addEventListener("change", () => fillDictionaryModelChoices(null));
  dm.model.addEventListener("change", () => {
    dm.custom.hidden = dm.model.value !== "__custom__";
    if (!dm.custom.hidden) dm.custom.focus();
  });
  el("dm-change").addEventListener("click", () => {
    fillDictionaryModelForm();
    el("dm-summary").hidden = true;
    dm.edit.hidden = false;
  });
  el("dm-cancel").addEventListener("click", () => { dm.edit.hidden = true; el("dm-summary").hidden = false; });
  dm.edit.addEventListener("submit", async (e) => {
    e.preventDefault();
    const modelId = dm.model.value === "__custom__" ? dm.custom.value.trim() : dm.model.value;
    if (!modelId) { flash(el("dm-status"), "Give the model's id", "err"); return; }
    const keys = dm.key.value.trim() ? { [dm.provider.value]: dm.key.value.trim() } : {};
    if (await saveSetting({ keys, dictionaryModel: `${dm.provider.value}:${modelId}` }, el("dm-status"))) {
      dm.edit.hidden = true;
      el("dm-summary").hidden = false;
      await loadSettings();
    }
  });

  // Jev: its key, the two things it can do to every transcript, and what it has done.
  const jev = { dictionary: el("jev-dictionary"), formatting: el("jev-formatting"), key: el("key-typesafe") };
  function renderJev() {
    const j = settings.jev;
    jev.key.value = "";
    jev.key.placeholder = j.key_hint ? `saved ${j.key_hint} · type to replace` : "Not set";
    jev.dictionary.checked = j.dictionary;
    jev.formatting.checked = j.formatting;
    for (const name of ["total_seconds", "attempt_seconds", "max_attempts"]) {
      el(`jev-${name}`).value = j.policy[name];
    }
    const s = j.summary;
    const lines = [];
    if (s.transcriptions) {
      lines.push(`${s.transcriptions} processed transcripts · median +${s.median_seconds?.toFixed(1) ?? "–"} s`);
      for (const [method, label] of [["contextual", "Contextual dictionary"], ["deterministic", "Approved direct mappings"], ["unconditional", "Historical unconditional mappings"], ["formatting", "Formatting"]]) {
        const stage = s.stages[method];
        if (!stage.succeeded && !stage.failed && !stage.replacements && !stage.decisions) continue;
        lines.push(`${label}: ${stage.succeeded} succeeded, ${stage.failed} failed, ${stage.skipped} skipped; retries: ${stage.retries}; decisions: ${stage.decisions}; replacements: ${stage.replacements} (${stage.direct_replacements ?? 0} direct); preserved: ${stage.preserved}; unresolved: ${stage.abstained}`);
      }
      lines.push("Counts describe processing, not accuracy. Legacy counters are excluded.");
    }
    el("jev-summary").textContent = lines.join("\n") || (j.key_hint ? "" : "Add a TypeSafe key to turn either on.");
  }
  el("jev-policy-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const policy = Object.fromEntries(["total_seconds", "attempt_seconds", "max_attempts"].map((name) => [name, Number(el(`jev-${name}`).value)]));
    if (await saveSetting({ jev: { policy } }, el("jev-policy-status"))) await loadSettings();
  });
  el("jev-key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = jev.key.value.trim();
    if (!key) { flash(el("jev-key-status"), "Nothing to save", "ok"); return; }
    if (await saveSetting({ keys: { typesafe: key } }, el("jev-key-status"))) await loadSettings();
  });
  for (const name of ["dictionary", "formatting"]) {
    jev[name].addEventListener("change", async () => {
      if (!(await saveSetting({ jev: { [name]: jev[name].checked } }, el("jev-key-status")))) jev[name].checked = !jev[name].checked;
    });
  }

  // Local models: a card per local provider, rows with size, state and one button.
  let localPoll = null;
  let localList = [];
  const localSearch = el("local-search");
  localSearch.addEventListener("input", () => renderLocalModels());

  async function loadLocalModels() {
    const providers = (settings?.providers ?? []).filter((p) => p.local);
    if (providers.length === 0) return;
    localList = await api("/api/local/models");
    renderLocalModels();
    const busy = localList.some((m) => m.state === "downloading");
    if (busy && !localPoll) localPoll = setInterval(() => loadLocalModels().catch(() => {}), 1500);
    if (!busy && localPoll) {
      clearInterval(localPoll);
      localPoll = null;
      await onModelsChanged(); // a model that just finished downloading is now offered
    }
  }

  const gb = (bytes) => (bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(1)} GB` : `${(bytes / 1048576).toFixed(0)} MB`);

  function renderLocalModels() {
    const query = localSearch.value.trim().toLowerCase();
    const ready = localList.filter((m) => m.state === "ready");
    const onDisk = ready.reduce((n, m) => n + m.size_bytes, 0);
    el("local-summary").textContent = localList.length ? `${ready.length} of ${localList.length} downloaded · ${gb(onDisk)} on disk` : "";
    const cards = [];
    for (const provider of settings.providers.filter((p) => p.local)) {
      const mine = localList.filter((m) => m.provider === provider.id && (!query || `${m.label} ${m.note}`.toLowerCase().includes(query)));
      if (query && mine.length === 0) continue;
      const card = document.createElement("div");
      card.className = "scard";
      const head = document.createElement("div");
      head.className = "scard-head";
      const isParakeet = provider.id === "parakeet";
      head.innerHTML = isParakeet
        ? `<div class="name strong">Parakeet <span class="caption">· NVIDIA on Apple MLX</span></div><div class="caption hint">The most accurate offline model. Its engine is installed outside Dictum, once; Dictum then finds it.</div>`
        : `<div class="name strong">Whisper <span class="caption">· whisper.cpp</span></div><div class="caption hint">Downloaded inside Dictum with one click. Runs on this machine; nothing leaves it.</div>`;
      card.append(head, ...mine.map((m) => localRow(m, isParakeet)));
      const missing = mine.find((m) => m.state === "unavailable");
      if (isParakeet && missing) card.append(engineNote(missing));
      else if (isParakeet && mine.some((m) => m.state !== "unavailable")) {
        const found = document.createElement("div");
        found.className = "engine";
        found.innerHTML = `<span class="dot ok"></span>Engine found.`;
        card.append(found);
      }
      cards.push(card);
    }
    el("local-cards").replaceChildren(...cards);
  }

  function localRow(m, isParakeet) {
    const row = document.createElement("div");
    row.className = "lrow";
    const name = document.createElement("div");
    name.innerHTML = `<div class="name"></div><div class="note"></div>`;
    name.querySelector(".name").textContent = m.label;
    name.querySelector(".note").textContent = m.note;
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = gb(m.size_bytes);
    const state = document.createElement("div");
    state.className = "state";
    const dot = document.createElement("span");
    dot.className = "dot";
    const text = document.createElement("span");
    if (m.state === "ready") { dot.classList.add("ok"); text.textContent = "ready"; }
    else if (m.state === "downloading") { dot.classList.add("busy"); text.textContent = `${Math.round(m.progress * 100)}%`; }
    else if (m.state === "error") { dot.classList.add("err"); text.textContent = "failed"; }
    else if (m.state === "unavailable") { dot.classList.add("busy"); text.textContent = "setup required"; state.classList.add("setup"); }
    else text.textContent = "not downloaded";
    state.append(dot, text);
    if (m.state === "downloading") {
      const bar = document.createElement("span");
      bar.className = "bar";
      bar.innerHTML = `<span style="width:${Math.round(m.progress * 100)}%"></span>`;
      state.append(bar);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn sm";
    button.dataset.model = m.name;
    if (m.state === "ready") { button.textContent = "Remove"; button.dataset.action = "remove"; }
    else if (m.state === "downloading") { button.textContent = "Downloading…"; button.disabled = true; }
    else if (m.state === "unavailable") { button.textContent = "Check installation"; button.classList.add("setup"); button.dataset.action = "check"; }
    else { button.textContent = m.state === "error" ? "Retry" : "Download"; button.dataset.action = "download"; }
    row.append(name, size, state, button);
    if (m.state === "error" && m.error) {
      const wrap = document.createElement("div");
      wrap.append(row);
      const why = document.createElement("div");
      why.className = "engine";
      why.innerHTML = `<span class="dot err"></span>`;
      why.append(m.error);
      wrap.append(why);
      return wrap;
    }
    return row;
  }

  let engineStepsOpen = false;
  function engineNote(m) {
    const wrap = document.createElement("div");
    const line = document.createElement("div");
    line.className = "engine";
    line.innerHTML = `<span>Engine not found. Install it in Terminal, then check again.</span><span class="spacer"></span><button type="button" class="btn link steps-toggle">${ICON.chevron}Installation steps</button>`;
    const steps = document.createElement("div");
    steps.className = "engine-steps";
    steps.hidden = !engineStepsOpen;
    steps.innerHTML = `<span class="n">1</span><div>Install the engine, once:<pre>uv tool install parakeet-mlx</pre></div>
      <span class="n">2</span><div>Come back and press <b>Check installation</b>. Dictum looks for <code>parakeet-mlx</code> where uv installs tools.</div>
      <span class="n">3</span><div>Then press <b>Download</b> here to fetch the model (about ${gb(m.size_bytes)}).</div>`;
    line.querySelector(".steps-toggle").addEventListener("click", () => { engineStepsOpen = !engineStepsOpen; steps.hidden = !engineStepsOpen; });
    wrap.append(line, steps);
    return wrap;
  }

  el("local-cards").addEventListener("click", async (e) => {
    const button = e.target.closest("button[data-model]");
    if (!button) return;
    const { model, action } = button.dataset;
    try {
      if (action === "download") await api(`/api/local/models/${model}/download`, { method: "POST" });
      else if (action === "remove") await api(`/api/local/models/${model}`, { method: "DELETE" });
      await loadLocalModels();
      if (action === "remove") await onModelsChanged();
    } catch (err) {
      el("local-summary").textContent = errorText(err);
    }
  });

  // Agents: the local API, and what arrived through it.
  function renderAgents() {
    const endpoint = `${location.origin}/api/dictionary/corrections`;
    el("agent-endpoint").textContent = endpoint;
    el("agent-curl").textContent = `curl -s -m 2 -X POST ${endpoint} \\\n  -H 'content-type: application/json' \\\n  -d '{"entries": [{"spelling": "Claude Code", "description": "Anthropic\'s coding agent", "heard": ["cloud code"]}], "source": "my-agent"}'`;
  }
  for (const button of document.querySelectorAll(".copy-btn")) {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(el(button.dataset.copy).textContent);
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = "Copy"; }, 1400);
      } catch (err) {
        button.textContent = "Copy failed";
      }
    });
  }
  async function loadCorrections() {
    const rows = await api("/api/dictionary/corrections");
    const list = el("corrections-rows");
    if (rows.length === 0) {
      list.innerHTML = `<div class="crow none">Nothing received yet.</div>`;
      return;
    }
    list.replaceChildren(
      ...rows.map((c) => {
        const row = document.createElement("div");
        row.className = "crow";
        const ts = document.createElement("span");
        ts.className = "ts";
        ts.textContent = whenLabel(c.created_at);
        const what = document.createElement("span");
        what.className = "what";
        if (c.meant) {
          what.innerHTML = `<span class="heard"></span><span class="arrow">→</span><span class="meant"></span>`;
          what.querySelector(".heard").textContent = c.heard;
          what.querySelector(".meant").textContent = c.meant;
        } else what.textContent = c.heard;
        const from = document.createElement("span");
        from.className = "from";
        from.textContent = c.source ?? "";
        row.append(ts, what, from);
        return row;
      }),
    );
  }

  async function loadSettings() {
    settings = await api("/api/settings");
    el("keys").replaceChildren(...settings.providers.filter((p) => !p.local).map(keyRow));
    fastInput.checked = Boolean(settings.fastMode);
    onShortcutsChanged(settings.shortcuts);
    el("shortcut-hold").textContent = settings.shortcuts.hold ?? "";
    el("shortcut-toggle").textContent = settings.shortcuts.toggle ?? "";
    el("shortcut-cancel").textContent = settings.shortcuts.cancel ?? "";
    renderDictionaryModel();
    renderJev();
    renderAgents();
    loadLocalModels().catch(() => {});
    loadCorrections().catch(() => {});
    await onLoaded(settings);
  }

  // ---- Shortcuts: recorded by pressing them ----
  const shortcutStatus = el("shortcut-status");
  function showShortcutStatus(text) {
    shortcutStatus.textContent = text;
    el("shortcut-status-row").hidden = !text;
  }
  async function saveShortcuts() {
    const hold = el("shortcut-hold").textContent.trim();
    const toggle = el("shortcut-toggle").textContent.trim();
    const cancel = el("shortcut-cancel").textContent.trim();
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ keys: {}, shortcuts: { hold, toggle, cancel } }) });
    showShortcutStatus("");
    onShortcutsChanged({ hold: hold || null, toggle: toggle || null, cancel: cancel || null });
  }

  async function captureShortcut(display, button) {
    const previous = display.textContent;
    display.textContent = "";
    display.dataset.empty = "Press keys…";
    button.disabled = true;
    showShortcutStatus("");
    try {
      const res = await fetch("/api/capture", { method: "POST" });
      if (res.status === 409) {
        display.textContent = await captureInPage();
      } else if (!res.ok) {
        throw new Error(await res.text());
      } else {
        const deadline = Date.now() + 15000;
        let keys = null;
        while (Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, 120));
          const state = await api("/api/capture");
          if (state.state === "done") { keys = state.keys; break; }
          if (state.state === "idle") break;
        }
        if (keys === null) {
          await fetch("/api/capture", { method: "DELETE" });
          throw new Error("Nothing pressed.");
        }
        display.textContent = keys;
      }
      await saveShortcuts();
    } catch (err) {
      display.textContent = previous;
      showShortcutStatus(errorText(err));
    } finally {
      display.dataset.empty = "Not set";
      button.disabled = false;
    }
  }

  function captureInPage() {
    return new Promise((resolve, reject) => {
      const names = { Meta: "cmd", Control: "ctrl", Alt: "alt", Shift: "shift", " ": "space", Enter: "enter", Escape: "esc", Tab: "tab", Backspace: "backspace", ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
      const keys = [];
      const down = new Set();
      const nameOf = (e) => names[e.key] ?? e.key.toLowerCase();
      const onDown = (e) => {
        e.preventDefault();
        const name = nameOf(e);
        if (!keys.includes(name)) keys.push(name);
        down.add(name);
      };
      const onUp = (e) => {
        down.delete(nameOf(e));
        if (keys.length && down.size === 0) { cleanup(); resolve(keys.join("+")); }
      };
      const cancel = () => { cleanup(); reject(new Error("Shortcut capture cancelled. Try again.")); };
      const cleanup = () => {
        clearTimeout(timeout);
        window.removeEventListener("keydown", onDown, true);
        window.removeEventListener("keyup", onUp, true);
        window.removeEventListener("blur", cancel);
      };
      window.addEventListener("keydown", onDown, true);
      window.addEventListener("keyup", onUp, true);
      window.addEventListener("blur", cancel);
      const timeout = setTimeout(() => {
        cleanup();
        reject(new Error(keys.length ? "Shortcut was not released. Try again." : "Nothing pressed."));
      }, 15000);
    });
  }

  for (const button of document.querySelectorAll("button.capture")) {
    button.addEventListener("click", () => captureShortcut(el(button.dataset.target), button));
  }
  for (const button of document.querySelectorAll("button.clear")) {
    button.addEventListener("click", async () => {
      const display = el(button.dataset.target);
      const previous = display.textContent;
      display.textContent = "";
      try { await saveShortcuts(); }
      catch (err) { display.textContent = previous; showShortcutStatus(errorText(err)); }
    });
  }

  return { load: loadSettings, save: saveSetting, refreshCorrections: () => loadCorrections().catch((err) => onError(errorText(err))) };
}
