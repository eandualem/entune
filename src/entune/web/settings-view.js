import { THIS_DEVICE, api, el, errorText, flash, whenLabel } from "./ui.js";

// Settings owns its forms and shortcut capture. Callbacks refresh the model and
// dictionary views after a successful configuration change.
export function createSettings({ onLoaded, onShortcutsChanged, onError }) {
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
  // The dictionary model: a summary line with Change, then provider, key and model. OpenAI
  // is reached with an API key or on a ChatGPT subscription (PLAN, its own provider id).
  const dm = { edit: el("dm-edit"), provider: el("dm-provider"), key: el("dm-key"), model: el("dm-model"), custom: el("dm-model-custom"), access: document.querySelectorAll('input[name="dm-access"]') };
  const PLAN = "chatgpt";
  function chosenProvider() {
    const plan = dm.provider.value === "openai" && document.querySelector('input[name="dm-access"]:checked')?.value === PLAN;
    return settings.llmProviders.find((p) => p.id === (plan ? PLAN : dm.provider.value));
  }
  function renderDictionaryModel() {
    const [providerId, , modelId] = splitRef(settings.dictionaryModel);
    const provider = settings.llmProviders.find((p) => p.id === providerId);
    const anyKey = settings.llmProviders.find((p) => p.keyHint);
    if (provider?.id === PLAN) {
      el("dm-title").textContent = `OpenAI · ${modelId}`;
      el("dm-caption").textContent = provider.keyHint ? `ChatGPT subscription, signed in as ${provider.keyHint}` : "ChatGPT subscription, not signed in yet: sign in to build the dictionary";
    } else if (provider) {
      el("dm-title").textContent = `${provider.name} · ${modelId}`;
      el("dm-caption").textContent = provider.keyHint ? `key saved ${provider.keyHint}` : `no key for ${provider.name} yet: add one to build the dictionary`;
    } else {
      el("dm-title").textContent = "No model";
      el("dm-caption").textContent = anyKey ? "" : "Sign in with ChatGPT, or add a key for OpenAI, Anthropic, Google Gemini, Groq or Mistral, to build the dictionary from your history.";
    }
  }
  function splitRef(ref) {
    const i = (ref ?? "").indexOf(":");
    return i < 0 ? [ref ?? "", "", ""] : [ref.slice(0, i), ":", ref.slice(i + 1)];
  }
  function fillDictionaryModelForm() {
    const [savedId, , modelId] = splitRef(settings.dictionaryModel);
    const shown = (id) => (id === PLAN ? "openai" : id);
    dm.provider.replaceChildren(...settings.llmProviders.filter((p) => p.id !== PLAN).map((p) => new Option(p.name, p.id, false, p.id === shown(savedId))));
    if (!savedId) dm.provider.value = shown(settings.llmProviders.find((p) => p.keyHint)?.id ?? settings.llmProviders[0]?.id ?? "");
    // OpenAI's access: as saved; otherwise the ChatGPT sign-in, unless an API key is saved.
    const key = (id) => settings.llmProviders.find((p) => p.id === id)?.keyHint;
    const plan = savedId === PLAN || (savedId !== "openai" && !key("openai"));
    for (const input of dm.access) input.checked = input.value === (plan ? PLAN : "openai");
    fillDictionaryModelChoices(modelId);
  }
  function fillDictionaryModelChoices(chosen) {
    el("dm-access-row").hidden = dm.provider.value !== "openai";
    const provider = chosenProvider();
    if (!provider) return;
    // A ChatGPT plan is signed in to instead of given a key.
    el("dm-key-row").hidden = provider.id === PLAN;
    el("dm-signin").hidden = provider.id !== PLAN;
    renderSignIn();
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
  // Switching how OpenAI is reached keeps the model picked, where both offer it.
  for (const input of dm.access) input.addEventListener("change", () => fillDictionaryModelChoices(dm.model.value === "__custom__" ? null : dm.model.value));
  dm.model.addEventListener("change", () => {
    dm.custom.hidden = dm.model.value !== "__custom__";
    if (!dm.custom.hidden) dm.custom.focus();
  });
  el("dm-change").addEventListener("click", () => {
    fillDictionaryModelForm();
    el("dm-summary").hidden = true;
    dm.edit.hidden = false;
  });
  el("dm-cancel").addEventListener("click", () => { stopSignIn(); dm.edit.hidden = true; el("dm-summary").hidden = false; });

  // Signing in with ChatGPT: OpenAI gives a code to enter on its page; Entune checks, at
  // the pace OpenAI asks, until the person approves it there.
  let signInTimer = null, signInRun = 0;
  function stopSignIn() { clearTimeout(signInTimer); signInRun += 1; }
  function renderSignIn() {
    stopSignIn();
    const account = settings.llmProviders.find((p) => p.id === PLAN)?.keyHint;
    el("dm-signin-state").textContent = account ? `Signed in as ${account}` : "Not signed in";
    el("dm-signin-start").hidden = Boolean(account);
    el("dm-signout").hidden = !account;
    el("dm-signin-note").hidden = Boolean(account);
  }
  async function signInChanged(message) {
    const custom = dm.model.value === "__custom__" ? dm.custom.value : null;
    const picked = custom === null ? dm.model.value : null;
    await loadSettings();
    // The account's own models arrive with the sign-in: offer them, keeping the pick only
    // if the account offers it (else its suggested model), and a custom model as typed.
    if (!dm.edit.hidden) {
      const provider = chosenProvider();
      const offered = provider?.models.some((m) => m.id === `${provider.id}:${picked}`);
      fillDictionaryModelChoices(offered ? picked : null);
      if (custom !== null) {
        dm.model.value = "__custom__";
        dm.custom.hidden = false;
        dm.custom.value = custom;
      }
    } else renderSignIn();
    flash(el("dm-status"), message, "ok");
  }
  let starting = false; // one start at a time
  el("dm-signin-start").addEventListener("click", async () => {
    if (starting) return;
    starting = true;
    el("dm-signin-start").disabled = true;
    stopSignIn();
    const run = signInRun;
    try {
      // OpenAI's sign-in opens in the browser and comes back to Entune once approved.
      const start = await api("/api/chatgpt/sign-in", { method: "POST" });
      if (run !== signInRun) return; // the form changed while OpenAI answered
      const page = document.createElement("a");
      page.href = start.url;
      page.target = "_blank";
      page.rel = "noopener";
      page.textContent = "OpenAI's sign-in page";
      el("dm-signin-state").replaceChildren(
        start.opened ? "Approve Entune on " : "Open ", page, start.opened ? " in your browser. Waiting…" : " to sign in. Waiting…",
      );
      el("dm-signin-start").hidden = true;
      const check = async () => {
        if (run !== signInRun) return;
        try {
          const result = await api("/api/chatgpt/sign-in");
          if (run !== signInRun) return;
          if (result.state === "waiting") signInTimer = setTimeout(check, 2000);
          else if (result.state === "signed-in") await signInChanged("Signed in");
          else { renderSignIn(); if (result.error) flash(el("dm-status"), result.error, "err"); }
        } catch (err) {
          if (run !== signInRun) return;
          renderSignIn();
          flash(el("dm-status"), errorText(err), "err");
        }
      };
      signInTimer = setTimeout(check, 2000);
    } catch (err) {
      if (run === signInRun) flash(el("dm-status"), errorText(err), "err");
    } finally {
      starting = false;
      el("dm-signin-start").disabled = false;
    }
  });
  el("dm-signout").addEventListener("click", async () => {
    try {
      const result = await api("/api/chatgpt/sign-in", { method: "DELETE" });
      await signInChanged(result.revoked ? "Signed out"
        : "Signed out here. OpenAI did not confirm it: disconnect Entune in ChatGPT Settings › Usage.");
    } catch (err) {
      flash(el("dm-status"), errorText(err), "err");
    }
  });
  dm.edit.addEventListener("submit", async (e) => {
    e.preventDefault();
    const modelId = dm.model.value === "__custom__" ? dm.custom.value.trim() : dm.model.value;
    if (!modelId) { flash(el("dm-status"), "Give the model's id", "err"); return; }
    const provider = chosenProvider().id;
    const keys = provider !== PLAN && dm.key.value.trim() ? { [provider]: dm.key.value.trim() } : {};
    if (await saveSetting({ keys, dictionaryModel: `${provider}:${modelId}` }, el("dm-status"))) {
      dm.edit.hidden = true;
      el("dm-summary").hidden = false;
      await loadSettings();
    }
  });

  // The decision model: the choice, Jev's key or Laya's state on this Mac, independent
  // processing controls, and what the chosen model has done.
  const jev = { dictionary: el("jev-dictionary"), formatting: el("jev-formatting"), cleanup: el("jev-cleanup"), key: el("key-typesafe") };
  const LAYA = {
    unavailable: `Its engine is not installed on ${THIS_DEVICE}.`,
    stopped: "Installed. It starts when a step below is on.",
    starting: `Starting on ${THIS_DEVICE}. The first start downloads the model, about 850 MB.`,
    ready: `Running on ${THIS_DEVICE}.`,
  };
  let layaPoll = null;
  // The option shown is the saved one, unless a switch was refused: then the picked option
  // stays shown with its setup (Jev's key, Laya's install command) until it can be saved.
  let picked = null, refusal = "";
  function renderDecisionModel() {
    const { selected, laya } = settings.decisionModel;
    const shown = picked ?? selected;
    for (const input of document.querySelectorAll('input[name="decision-model"]')) input.checked = input.value === shown;
    el("jev-key-form").hidden = shown !== "jev";
    el("openai-key-form").hidden = shown !== "openai";
    el("perplexity-key-form").hidden = shown !== "perplexity";
    const perplexityKey = settings.decisionModel.perplexityKey;
    el("key-perplexity").value = "";
    el("key-perplexity").placeholder = perplexityKey ? `saved ${perplexityKey} · type to replace` : "Not set";
    el("laya-setup").hidden = shown !== "laya";
    const openaiKey = settings.llmProviders.find((p) => p.id === "openai")?.keyHint;
    el("key-openai-decisions").value = "";
    el("key-openai-decisions").placeholder = openaiKey ? `saved ${openaiKey} · type to replace` : "Not set";
    const names = { jev: "Jev", laya: "Laya", openai: "OpenAI", perplexity: "Perplexity" };
    el("decision-note").textContent = picked ? `Not switched yet: ${refusal}${selected ? ` The steps still use ${names[selected]}.` : ""}` : "";
    el("laya-status").textContent = laya.state === "failed" ? laya.error : LAYA[laya.state];
    el("laya-status").classList.toggle("err", laya.state === "failed");
    el("laya-retry").hidden = !["failed", "unavailable"].includes(laya.state);
    el("laya-retry").textContent = laya.state === "failed" ? "Start again" : "Check again";
    el("laya-install").hidden = laya.state !== "unavailable";
    el("laya-command").textContent = laya.install;
    // Follow Laya while it starts; the first start can take minutes.
    clearTimeout(layaPoll);
    if (shown === "laya" && laya.state === "starting") {
      layaPoll = setTimeout(async () => {
        try { settings.decisionModel = (await api("/api/settings")).decisionModel; renderDecisionModel(); } catch { /* the next load shows it */ }
      }, 2000);
    }
  }
  async function chooseDecisionModel(value) {
    try {
      await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ keys: {}, decisionModel: value }) });
      picked = null;
      flash(el("decision-status"), "Saved", "ok");
      await loadSettings();
    } catch (err) {
      picked = value === settings.decisionModel.selected ? null : value;
      refusal = errorText(err);
      if (!picked) flash(el("decision-status"), refusal, "err");
      renderDecisionModel();
    }
  }
  for (const input of document.querySelectorAll('input[name="decision-model"]')) {
    input.addEventListener("change", () => chooseDecisionModel(input.value));
  }
  // Start again after a failure; after installing, Check again finds the engine and, if
  // Laya was picked but refused for lack of it, saves the choice.
  el("laya-retry").addEventListener("click", () => (settings.decisionModel.laya.state === "failed" || picked === "laya" ? chooseDecisionModel("laya") : loadSettings()));
  function renderJev() {
    const j = settings.jev;
    renderDecisionModel();
    jev.key.value = "";
    jev.key.placeholder = j.key_hint ? `saved ${j.key_hint} · type to replace` : "Not set";
    jev.dictionary.checked = j.dictionary;
    jev.formatting.checked = j.formatting;
    jev.cleanup.checked = j.cleanup;
    for (const name of ["total_seconds", "attempt_seconds", "max_attempts"]) {
      el(`jev-${name}`).value = j.policy[name];
    }
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
    // A key saved while Jev is picked but not yet chosen completes the switch.
    const choice = picked === "jev" ? { decisionModel: "jev" } : {};
    if (await saveSetting({ keys: { typesafe: key }, ...choice }, el("jev-key-status"))) { picked = null; await loadSettings(); }
  });
  el("perplexity-key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = el("key-perplexity").value.trim();
    if (!key) { flash(el("perplexity-key-status"), "Nothing to save", "ok"); return; }
    // A key saved while Perplexity is picked but not yet chosen completes the switch.
    const choice = picked === "perplexity" ? { decisionModel: "perplexity" } : {};
    if (await saveSetting({ keys: { perplexity: key }, ...choice }, el("perplexity-key-status"))) { picked = null; await loadSettings(); }
  });
  el("openai-key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = el("key-openai-decisions").value.trim();
    if (!key) { flash(el("openai-key-status"), "Nothing to save", "ok"); return; }
    // A key saved while OpenAI is picked but not yet chosen completes the switch.
    const choice = picked === "openai" ? { decisionModel: "openai" } : {};
    if (await saveSetting({ keys: { openai: key }, ...choice }, el("openai-key-status"))) { picked = null; await loadSettings(); }
  });
  // One save at a time, as for fast mode: a refused save is undone exactly.
  el("remove-silence").addEventListener("change", async (e) => {
    e.target.disabled = true;
    if (!(await saveSetting({ removeSilence: e.target.checked }, null))) e.target.checked = !e.target.checked;
    e.target.disabled = false;
  });
  for (const name of ["dictionary", "formatting", "cleanup"]) {
    jev[name].addEventListener("change", async () => {
      if (await saveSetting({ jev: { [name]: jev[name].checked } }, el("decision-status"))) await loadSettings();
      else jev[name].checked = !jev[name].checked;
    });
  }

  // Integrations: the local API, and what arrived through it.
  function renderAgents() {
    el("mcp-endpoint").textContent = `${location.origin}/mcp`;
    el("mcp-command").textContent = `claude mcp add --transport http entune ${location.origin}/mcp`;
    const endpoint = `${location.origin}/api/dictionary/corrections`;
    el("agent-endpoint").textContent = endpoint;
    el("agent-curl").textContent = `curl -s -m 2 -X POST ${endpoint} \\\n  -H 'content-type: application/json' \\\n  -d '{"entries": [{"spelling": "Claude Code", "description": "Anthropic'"'"'s coding agent", "heard": ["cloud code"]}], "source": "my-agent"}'`;
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

  // Delete all data: the dialog lists the scope and the folder, offers exports, and
  // enables the button only for the exact phrase, which the server checks again.
  const PHRASE = "delete everything";
  const reset = { dialog: el("reset-dialog"), phrase: el("reset-phrase"), confirm: el("reset-confirm"), status: el("reset-status") };
  el("reset-open").addEventListener("click", async () => {
    reset.phrase.value = "";
    reset.confirm.disabled = true;
    reset.status.textContent = "";
    el("reset-size").textContent = "";
    reset.dialog.showModal();
    try {
      const data = await api("/api/data");
      el("reset-folder").textContent = data.folder;
      const bytes = data.items.reduce((sum, item) => sum + item.bytes, 0);
      el("reset-size").textContent = `About ${bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1048576))} MB`} in total.`
        + (data.other.length ? ` ${data.other.length} other file${data.other.length === 1 ? "" : "s"} in the folder ${data.other.length === 1 ? "is" : "are"} left alone.` : "");
    } catch (err) {
      reset.status.textContent = errorText(err);
    }
  });
  reset.phrase.addEventListener("input", () => { reset.confirm.disabled = reset.phrase.value.trim().toLowerCase() !== PHRASE; });
  reset.confirm.addEventListener("click", async () => {
    if (document.documentElement.hasAttribute("data-importing")) {
      reset.status.textContent = "Audio is still being imported; wait for it to finish first.";
      return;
    }
    reset.confirm.disabled = true;
    reset.status.textContent = "";
    try {
      await api("/api/data/reset", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirm: PHRASE }) });
    } catch (err) {
      reset.status.textContent = errorText(err);
      reset.confirm.disabled = false;
      return;
    }
    // This window's appearance choices go too; then the page starts again, empty.
    try { localStorage.removeItem("theme"); localStorage.removeItem("scale"); sessionStorage.setItem("entune-reset", "1"); } catch (e) {}
    location.hash = "";
    location.reload();
  });

  async function loadSettings() {
    settings = await api("/api/settings");
    fastInput.checked = Boolean(settings.fastMode);
    el("remove-silence").checked = Boolean(settings.removeSilence);
    onShortcutsChanged(settings.shortcuts);
    el("shortcut-hold").textContent = settings.shortcuts.hold ?? "";
    el("shortcut-toggle").textContent = settings.shortcuts.toggle ?? "";
    el("shortcut-cancel").textContent = settings.shortcuts.cancel ?? "";
    renderDictionaryModel();
    renderJev();
    renderAgents();
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

  // Langfuse tracing: keys stay masked; the line under the form says whether it is on.
  const tracingForm = el("tracing-form");
  function showTracing(t) {
    el("tracing-public").placeholder = t.publicKeyHint ? `saved ${t.publicKeyHint} · type to replace` : "Not set";
    el("tracing-secret").placeholder = t.secretKeyHint ? `saved ${t.secretKeyHint} · type to replace` : "Not set";
    el("tracing-host").placeholder = t.host;
    el("tracing-off").hidden = !t.publicKeyHint && !t.secretKeyHint;
    el("tracing-state").textContent = {
      off: "Off.", connecting: "Connecting to Langfuse…",
      on: `On: sending to ${t.detail}.${t.lastError ? ` The last traces did not arrive (${t.lastError}).` : ""}`,
      failed: `Not connected: ${t.detail}`, missing: t.detail,
    }[t.state] ?? t.state;
    // Connecting settles in a moment: look again until it does.
    if (t.state === "connecting") setTimeout(() => loadTracing().catch(() => {}), 1500);
  }
  async function loadTracing() { showTracing(await api("/api/tracing")); }
  tracingForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { publicKey: el("tracing-public").value, secretKey: el("tracing-secret").value, host: el("tracing-host").value };
    try {
      showTracing(await api("/api/tracing", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }));
      for (const id of ["tracing-public", "tracing-secret", "tracing-host"]) el(id).value = "";
    } catch (err) { el("tracing-state").textContent = errorText(err); }
  });
  el("tracing-off").addEventListener("click", async () => {
    try { showTracing(await api("/api/tracing", { method: "DELETE" })); }
    catch (err) { el("tracing-state").textContent = errorText(err); }
  });

  return {
    load: loadSettings, save: saveSetting,
    refreshCorrections: () => Promise.all([loadCorrections(), loadTracing()]).catch((err) => onError(errorText(err))),
  };
}
