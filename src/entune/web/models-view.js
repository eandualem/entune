import { CHECKED, FACTS } from "./model-facts.js";
import { api, el, errorText } from "./ui.js";

// The Models page, from the design handoff of 2026-10-09: cloud providers and local models
// as one kind of table, one chart where every model competes, and what the dictations add up
// to. Published numbers come from model-facts.js, the person's own from their history. A
// model is never shown by what failed: the page counts what completed.

const clamp = (v) => Math.max(0.04, Math.min(1, v));
const share = (v) => `${Math.round(v * 100)}%`;
const speedWord = (fill) => (fill >= 0.8 ? "Very fast" : fill >= 0.6 ? "Fast" : fill >= 0.36 ? "Moderate" : "Slower");
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const X0 = 6, XS = 84, Y0 = 88, YS = 74; // the plot area, in % of the chart
const FEW = 20; // a model with fewer dictations is drawn faded
const JSON_HEADERS = { "content-type": "application/json" };

const accuracyFill = (r) => (r.wer === undefined ? null : clamp((10 - r.wer) / 8));
const speedFill = (r) => (r.speed === undefined ? null : clamp(Math.log(r.speed) / Math.log(r.local ? 40 : 200)));
const accuracyText = (r) => (r.wer === undefined ? "–" : `${r.estimated ? "≈" : ""}${(100 - r.wer).toFixed(1)}%`);
const speedText = (r) => (r.speed === undefined ? "–" : speedWord(speedFill(r)));
const gb = (bytes) => (bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(1)} GB` : `${(bytes / 1048576).toFixed(0)} MB`);
const checkedOn = new Date(`${CHECKED}T12:00:00`).toLocaleDateString("en", { month: "long", year: "numeric" });

function audioLength(seconds) {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function meter(text, fill, thick = false) {
  const cell = node("span", "meter");
  const track = node("span", thick ? "track thick" : "track");
  const bar = node("span");
  bar.style.width = fill === null ? "0" : share(fill);
  track.append(bar);
  cell.append(node("span", "figure", text), track);
  return cell;
}

export function createModels({ getDefault, reloadSettings, onModelsChanged, onError, openSection }) {
  let providers = []; // from /api/settings: id, name, keyHint, local
  let localList = []; // from /api/local/models
  let metrics = []; // from /api/metrics
  let usageData = null; // from /api/usage
  let sort = "acc"; // acc, spd, or extra: size for local models, price for cloud
  let openKey = null; // the provider whose key field is open
  let mode = "you";
  let selected = null; // the chart's selected dot; null: the model in use
  let localPoll = null;

  // ---- Rows: a model's facts with its state on this computer ----
  function rows() {
    const inUse = getDefault();
    const local = localList.map((m) => {
      const id = `${m.provider}/${m.name}`;
      const facts = FACTS[id] ?? {};
      const state = m.state === "ready" ? (id === inUse ? "inuse" : "ready") : ["downloading", "error"].includes(m.state) ? m.state : "absent";
      return { ...facts, id, local: true, model: m.name, name: facts.name ?? m.label, sub: facts.sub ?? m.note, note: m.note, size: m.size_bytes, state, progress: m.progress, error: m.error };
    });
    const cloud = providers.filter((p) => !p.local).flatMap((p) =>
      Object.keys(FACTS).filter((id) => id.startsWith(`${p.id}/`)).map((id) => ({
        ...FACTS[id], id, local: false, provider: p, state: !p.keyHint ? "key" : id === inUse ? "inuse" : "ready",
      })));
    return { local, cloud, all: [...local, ...cloud] };
  }

  function sorted(list, local) {
    const key = sort === "acc" ? (r) => -(accuracyFill(r) ?? -1)
      : sort === "spd" ? (r) => -(speedFill(r) ?? -1)
      : local ? (r) => r.size : (r) => r.price ?? Infinity;
    return [...list].sort((a, b) => key(a) - key(b));
  }

  // The one action a model offers: Use, Download, Add key, or a state when there is none.
  function actionButton(r, onPanel = false) {
    const button = node("button", "maction");
    button.type = "button";
    const [text, kind] = r.state === "inuse" ? ["In use", "quiet"]
      : r.state === "ready" ? ["Use", "use"]
      : r.state === "downloading" ? ["Downloading…", "quiet"]
      : r.state === "key" ? ["Add key", openKey === r.provider.id && !onPanel ? "quiet" : "line"]
      : ["Download", "line"];
    button.textContent = text;
    button.classList.add(kind);
    button.disabled = r.state === "inuse" || r.state === "downloading";
    button.addEventListener("click", () => run(r, onPanel));
    return button;
  }

  async function run(r, onPanel) {
    try {
      if (r.state === "ready") {
        await api("/api/settings", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ defaultModel: r.id }) });
        await onModelsChanged();
      } else if (r.state === "absent" || r.state === "error") {
        await api(`/api/local/models/${r.model}/download`, { method: "POST" });
        await loadLocal();
      } else if (r.state === "key") {
        openKey = openKey === r.provider.id && !onPanel ? null : r.provider.id;
        if (onPanel) openSection("cloud");
        renderTables();
        el("cloud-table").querySelector(".keyrow input")?.focus();
      }
    } catch (err) {
      onError(errorText(err));
    }
  }

  async function remove(r) {
    try {
      await api(`/api/local/models/${r.model}`, { method: "DELETE" });
      await loadLocal();
      await onModelsChanged();
    } catch (err) {
      onError(errorText(err));
    }
  }

  // ---- Cloud providers and Local models: one table each ----
  function nameCell(r) {
    const cell = node("span", "mname");
    cell.append(node("span", "title", r.name));
    const line = node("span", "line2");
    if (r.verdict) line.append(node("span", "verdict", r.verdict));
    const sub = r.state === "downloading" ? `${Math.round(r.progress * 100)}% · ${r.note}`
      : r.state === "error" && r.error ? r.error : r.sub;
    const text = node("span", "sub", sub);
    if (r.state === "error") text.title = r.error;
    line.append(text);
    // Quiet ways to undo: a downloaded model can go, a download can stop, a key can change.
    const link = (label, act, always = false) => {
      const button = node("button", always ? "mlink always" : "mlink", label);
      button.type = "button";
      button.addEventListener("click", act);
      line.append(button);
    };
    if (r.local && (r.state === "ready" || r.state === "inuse")) link("Remove", () => remove(r));
    if (r.local && r.state === "downloading") link("Cancel", () => remove(r), true);
    if (!r.local && r.state !== "key") link("Replace key", () => { openKey = r.provider.id; renderTables(); el("cloud-table").querySelector(".keyrow input")?.focus(); });
    cell.append(line);
    return cell;
  }

  function keyRow(r) {
    const form = node("form", "keyrow");
    const input = node("input", "input");
    Object.assign(input, { type: "password", autocomplete: "off", name: r.provider.id });
    input.placeholder = r.provider.keyHint ? `saved ${r.provider.keyHint} · paste a new key to replace` : `Paste your ${r.tag ?? r.provider.name} API key`;
    input.setAttribute("aria-label", `${r.provider.name} API key`);
    const link = node("a", "key-link");
    Object.assign(link, { href: r.link, target: "_blank", rel: "noopener", title: `Open ${r.provider.name}'s API key page in your browser` });
    link.innerHTML = 'Get a key<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 2h4.5v4.5M8 2L2.5 7.5"/></svg>';
    const save = node("button", "maction use", "Save");
    save.type = "submit";
    const cancel = node("button", "btn ghost", "Cancel");
    cancel.type = "button";
    const status = node("span", "caption keyrow-status");
    status.setAttribute("role", "status");
    const close = () => { openKey = null; renderTables(); };
    cancel.addEventListener("click", close);
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = input.value.trim();
      if (!key) { input.focus(); return; }
      save.disabled = true;
      try {
        await api("/api/settings", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ keys: { [r.provider.id]: key } }) });
        openKey = null;
        await reloadSettings();
      } catch (err) {
        status.textContent = errorText(err);
        save.disabled = false;
      }
    });
    form.append(input, link, save, cancel, status);
    return form;
  }

  function renderTable(kind, list) {
    const local = kind === "local";
    const head = node("div", "mrow mhead");
    for (const label of local ? ["Model", "Accuracy", "Speed", "Size", ""] : ["Model", "Accuracy", "Speed", "Price", "Free use", ""]) head.append(node("span", "", label));
    const items = sorted(list, local).map((r) => {
      const item = node("div", "mitem");
      const row = node("div", "mrow");
      row.append(nameCell(r), meter(accuracyText(r), accuracyFill(r)), meter(speedText(r), speedFill(r)));
      row.append(node("span", "mcol", local ? gb(r.size) : r.price === undefined ? "–" : `$${r.price.toFixed(2)}/h`));
      if (!local) {
        const free = node("span", "free");
        free.append(node("span", r.free ? "amount" : "amount none", r.free ?? "None"), node("span", "terms", r.freeSub ?? ""));
        row.append(free);
      }
      row.append(actionButton(r));
      item.append(row);
      if (!local && openKey === r.provider.id) item.append(keyRow(r));
      return item;
    });
    el(`${kind}-table`).replaceChildren(head, ...items);
  }

  function renderTables() {
    // A key being typed survives a redraw, in its own provider's field only.
    const field = el("cloud-table").querySelector(".keyrow input");
    const typed = field?.name === openKey ? field.value : "";
    const focused = field !== null && field === document.activeElement;
    const { local, cloud } = rows();
    renderTable("local", local);
    renderTable("cloud", cloud);
    const again = el("cloud-table").querySelector(".keyrow input");
    if (again) {
      again.value = typed;
      if (focused) again.focus();
    }
    renderPerformance();
  }

  for (const button of document.querySelectorAll("[data-sort]")) {
    button.addEventListener("click", () => {
      sort = button.dataset.sort;
      for (const other of document.querySelectorAll("[data-sort]")) other.setAttribute("aria-selected", String(other.dataset.sort === sort));
      renderTables();
    });
  }

  // ---- Performance: one chart, the person's own numbers or the published ones ----
  function usedModels() {
    const local = new Set(providers.filter((p) => p.local).map((p) => p.id));
    return metrics.map((m) => {
      const base = `${m.provider}/${m.model}`;
      return {
        id: m.fast ? `${base}#fast` : base, base, fast: m.fast, m, local: local.has(m.provider),
        label: `${FACTS[base]?.dot ?? m.model}${m.fast ? " · Fast" : ""}`,
        kept: m.words ? 100 * (1 - m.replaced_words / m.words) : null,
      };
    });
  }

  function chart() {
    const { all } = rows();
    const used = usedModels();
    const inUse = getDefault();
    if (mode === "you") {
      const plotted = used.filter((u) => u.kept !== null && u.m.seconds_per_minute !== null);
      // The design's scales, widened when this history goes past them; the slowest model
      // keeps clear of the axis labels on the left.
      const slowest = Math.max(6, ...plotted.map((u) => u.m.seconds_per_minute * 1.6));
      const lowest = Math.min(99.5, ...plotted.map((u) => Math.floor(u.kept * 2) / 2));
      const x = (wait) => Math.log(slowest / wait) / Math.log(slowest / 0.08);
      const y = (kept) => (kept - lowest) / ((100 - lowest) * 1.1);
      const still = used.some((u) => u.id === selected) ? selected : null; // gone after new data
      // The model in use: its plain runs, or its fast ones when it has only those.
      const current = used.find((u) => u.base === inUse && !u.fast) ?? used.find((u) => u.base === inUse);
      const pick = still ?? (current ?? used[0])?.id ?? inUse ?? all[0]?.id;
      const both = (u) => used.some((v) => v.base === u.base && v.fast !== u.fast && plotted.includes(v));
      return {
        pick, used, plotted,
        dots: plotted.map((u) => ({
          id: u.id, name: u.label, x: x(u.m.seconds_per_minute), y: y(u.kept), cloud: !u.local, few: u.m.runs < FEW,
          inUse: u === current, place: both(u) ? (u.fast ? "above" : "below") : "",
        })),
        xTicks: [...(slowest > 12 ? [[10, "10 s"]] : []), [5, "5 s"], [1, "1 s"], [0.1, "0.1 s"]].map(([v, label]) => [x(v), label]),
        yTicks: [lowest, (lowest + 100) / 2, 100].map((v) => [y(v), `${+v.toFixed(2)}%`]),
        xTitle: "Shorter wait after you stop →", yTitle: "↑ Fewer words the dictionary had to fix",
      };
    }
    const x = (speed) => Math.log(speed / 3) / Math.log(220 / 3);
    const y = (wer) => (100 - wer - 92) / 6.4;
    return {
      pick: (all.some((r) => r.id === selected) ? selected : null) ?? inUse ?? all[0]?.id, used, plotted: [],
      dots: all.filter((r) => r.wer !== undefined && r.speed !== undefined).map((r) => ({
        id: r.id, name: r.dot ?? r.name, x: x(r.speed), y: y(r.wer), cloud: !r.local, inUse: r.id === inUse, place: r.place ?? "",
      })),
      xTicks: [[10, "10× realtime"], [30, "30×"], [100, "100×"]].map(([v, label]) => [x(v), label]),
      yTicks: [93, 95, 97].map((v) => [y(100 - v), `${v}%`]),
      xTitle: "Faster →", yTitle: "↑ More accurate",
    };
  }

  const dotElements = new Map(); // kept across redraws, so a dot moves when the view changes
  function renderPerformance() {
    const view = chart();
    const at = (fraction, origin, span, sign) => `${origin + sign * Math.max(0, Math.min(1, fraction)) * span}%`;
    const axes = el("perf-axes");
    const parts = [];
    for (const [fraction, label] of view.yTicks) {
      const top = at(fraction, Y0, YS, -1);
      const line = node("span", "grid-y");
      line.style.top = top;
      const tick = node("span", "tick-y", label);
      tick.style.top = top;
      parts.push(line, tick);
    }
    for (const [fraction, label] of view.xTicks) {
      const left = at(fraction, X0, XS, 1);
      const line = node("span", "grid-x");
      line.style.left = left;
      const tick = node("span", "tick-x", label);
      tick.style.left = left;
      parts.push(line, tick);
    }
    parts.push(node("span", "title-y", view.yTitle), node("span", "title-x", view.xTitle));
    axes.replaceChildren(...parts);

    const seen = new Set();
    for (const d of view.dots) {
      seen.add(d.id);
      const picked = d.id === view.pick;
      const size = d.inUse ? 14 : picked ? 13 : 10;
      let dot = dotElements.get(d.id);
      const created = !dot;
      if (created) {
        dot = node("button", "pdot");
        dot.type = "button";
        dot.append(node("span", "mark"), node("span", "label"));
        dot.addEventListener("click", () => { selected = d.id; renderPerformance(); });
        dotElements.set(d.id, dot);
      }
      dot.style.left = at(d.x, X0, XS, 1);
      dot.style.top = at(d.y, Y0, YS, -1);
      dot.style.setProperty("--size", `${size}px`);
      dot.className = `pdot${d.cloud ? " cloud" : ""}${picked ? " picked" : ""}${d.few ? " few" : ""}${d.x > 0.73 ? " left" : ""}${d.place ? ` ${d.place}` : ""}`;
      dot.querySelector(".label").textContent = d.name;
      dot.setAttribute("aria-label", d.name);
      dot.setAttribute("aria-pressed", String(picked));
      if (created) el("perf-chart").append(dot);
    }
    for (const [id, dot] of dotElements) {
      if (!seen.has(id)) { dot.remove(); dotElements.delete(id); }
    }

    renderPanel(view);
    el("perf-sub").textContent = mode === "you" ? "How each model has done on your dictations." : "Every model, local and cloud, on public benchmarks.";
    const note = el("perf-note");
    if (mode === "you") {
      // A model without a word count has no dot; its name in the note shows it in the panel.
      const unplotted = view.used.filter((u) => !view.plotted.includes(u));
      const parts = [`Accuracy for you: the share of words your dictionary didn't need to correct. Faded dots have fewer than ${FEW} dictations`];
      unplotted.forEach((u, i) => {
        parts.push(i === 0 ? "; " : i === unplotted.length - 1 ? " and " : ", ");
        const name = node("button", "btn link", u.label);
        name.type = "button";
        name.addEventListener("click", () => { selected = u.id; renderPerformance(); });
        parts.push(name);
      });
      parts.push(unplotted.length ? ` ${unplotted.length === 1 ? "has" : "have"} too few to plot.` : ".");
      note.replaceChildren(...parts);
    } else {
      note.textContent = "Local: FLEURS English (Handy models), reference laptop. Cloud: Artificial Analysis; speed excludes upload. The two benchmarks differ, so treat close calls as ties.";
    }
  }

  function renderPanel(view) {
    const { all } = rows();
    const pick = view.pick;
    const u = mode === "you" ? view.used.find((v) => v.id === pick)
      : view.used.find((v) => v.base === pick && !v.fast) ?? view.used.find((v) => v.base === pick);
    const row = all.find((r) => r.id === (u ? u.base : pick));
    const panel = el("perf-panel");
    if (!row && !u) { panel.replaceChildren(); return; }
    const head = node("div", "phead");
    const title = node("span", "pname");
    title.append(node("span", "", row?.name ?? u.m.provider_name));
    if (u?.fast && mode === "you") title.append(node("span", "verdict", "Fast"));
    head.append(title, node("span", "psub", row?.sub ?? u.m.model));
    const parts = [head, node("span", "plabel", "For you")];
    if (u) {
      const m = u.m;
      const list = node("div", "pfacts");
      const wait = m.seconds_per_minute === null ? "–" : `${m.seconds_per_minute < 10 ? m.seconds_per_minute.toFixed(1) : Math.round(m.seconds_per_minute)} s`;
      for (const [label, value] of [
        ["Wait per audio min", wait],
        ["Words kept", u.kept === null ? "Not enough data" : `${u.kept.toFixed(1)}%`],
        ["Dictations fixed", m.checked ? `${m.corrected} of ${m.checked}` : "–"],
        ["Completed", m.ok === m.runs ? `${m.ok} of ${m.runs}` : `${((100 * m.ok) / m.runs).toFixed(1)}% · ${m.ok} of ${m.runs}`],
        ["Audio", m.audio_seconds ? audioLength(m.audio_seconds) : "–"],
      ]) {
        const line = node("span", "pfact");
        line.append(node("span", "", label), node("span", "value", value));
        list.append(line);
      }
      parts.push(list);
    } else {
      parts.push(node("span", "pnone", "Not used yet. Your own numbers appear here after a few dictations."));
    }
    parts.push(node("span", "plabel reported", "Reported"));
    const accuracy = meter(accuracyText(row ?? {}), accuracyFill(row ?? {}), true);
    accuracy.prepend(node("span", "", "Accuracy"));
    const speed = meter(speedText(row ?? {}), speedFill(row ?? {}), true);
    speed.prepend(node("span", "", "Speed"));
    parts.push(accuracy, speed, node("span", "spacer"));
    if (row) parts.push(actionButton(row, true));
    panel.replaceChildren(...parts);
  }

  for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => {
      mode = button.dataset.mode;
      selected = null;
      for (const other of document.querySelectorAll("[data-mode]")) other.setAttribute("aria-selected", String(other.dataset.mode === mode));
      renderPerformance();
    });
  }

  // ---- Usage: what the dictations add up to ----
  function renderUsage() {
    const u = usageData;
    if (!u) return;
    const percent = u.dictations ? `${((100 * u.transcribed) / u.dictations).toFixed(1)}%` : "–";
    const hours = u.audio_seconds / 3600;
    el("usage-transcribed").textContent = percent;
    el("usage-transcribed-of").textContent = `of ${plural(u.dictations, "dictation")} transcribed`;
    el("usage-audio").textContent = hours >= 1 ? `${hours.toFixed(1)} h` : `${Math.round(u.audio_seconds / 60)} min`;
    const changes = u.dictionary.count + u.fillers.count + u.layout.count;
    el("usage-changes").textContent = changes.toLocaleString();
    el("usage-changes-what").textContent = `${changes === 1 ? "change" : "changes"} after transcription`;
    el("usage-words").textContent = plural(u.words, "word");
    const pages = (words) => plural(Math.max(1, Math.round(words / 250)), "page");
    const novels = Math.round(u.words / 90000); // a novel is about 90,000 words
    el("usage-novel").textContent = u.words >= 90000 ? `About the length of ${novels === 1 ? "a novel" : `${novels} novels`}` : u.words ? `About ${pages(u.words)}` : "";
    // Minutes, typing at 40 words a minute, from the dictations whose length is known.
    const saved = u.timed_words / 40 - u.audio_seconds / 60;
    el("usage-saved-block").hidden = saved < 1;
    el("usage-saved").textContent = saved >= 60 ? `${Math.round(saved / 60)} h saved` : `${Math.round(saved)} min saved`;

    const most = Math.max(1, ...u.weeks.map((w) => w.words));
    el("usage-weeks").replaceChildren(...u.weeks.map((w, i) => {
      const week = node("div", i === u.weeks.length - 1 ? "week now" : "week");
      const bar = node("span", "wbar");
      bar.style.height = `${(w.words / most) * 6}rem`;
      const [year, month, day] = w.start.split("-").map(Number);
      week.append(bar, node("span", "wlabel", new Date(year, month - 1, day).toLocaleDateString("en", { month: "short", day: "numeric" })));
      return week;
    }));
    const now = u.weeks.at(-1).words;
    const before = u.weeks.slice(0, -1).map((w) => w.words);
    const best = now > 0 && before.some((n) => n > 0) && before.every((n) => n < now);
    el("usage-week").textContent = `${plural(now, "word")} this week`;
    el("usage-week-pages").textContent = now ? ` · about ${pages(now)}${best ? ", your best week yet" : ""}` : "";

    const typical = (step) => (step.median_seconds === null ? "not run yet" : `+${step.median_seconds.toFixed(1)} s typical`);
    const steps = [
      ["Transcribed", u.transcribed, u.transcribed === 1 ? "dictation" : "dictations", `of ${u.dictations.toLocaleString()} · ${percent}`],
      ["Dictionary", u.dictionary.count, u.dictionary.count === 1 ? "word corrected" : "words corrected", typical(u.dictionary)],
      ["Fillers", u.fillers.count, u.fillers.count === 1 ? "filler removed" : "fillers removed", typical(u.fillers)],
      ["Layout", u.layout.count, u.layout.count === 1 ? "layout change" : "layout changes", typical(u.layout)],
    ];
    el("usage-steps").replaceChildren(...steps.map(([name, n, verb, meta]) => {
      const step = node("div", "pipe-step");
      const mark = node("span", "pipe-mark");
      mark.append(node("span", "pipe-dot"), node("span", "pipe-line"));
      step.append(mark, node("span", "pipe-name", name), node("span", "pipe-n", n.toLocaleString()), node("span", "pipe-verb", verb), node("span", "pipe-meta", meta));
      return step;
    }));
  }

  // ---- Loading ----
  async function loadLocal() {
    localList = await api("/api/local/models");
    renderTables();
    const busy = localList.some((m) => m.state === "downloading");
    if (busy && !localPoll) localPoll = setInterval(() => loadLocal().catch(() => {}), 1500);
    if (!busy && localPoll) {
      clearInterval(localPoll);
      localPoll = null;
      await onModelsChanged(); // a model that just finished downloading is now offered
    }
  }

  el("cloud-source").textContent = `Accuracy and speed: Artificial Analysis speech-to-text index; speed excludes upload. Free use and prices are the providers' current terms and may change; checked ${checkedOn}.`;

  return {
    // Settings arrived: which providers there are and which have keys.
    setProviders(next) {
      providers = next;
      renderTables();
      loadLocal().catch((err) => onError(errorText(err)));
    },
    render: renderTables,
    // The page is opened: the person's own numbers may have changed since.
    async refresh() {
      [metrics, usageData] = await Promise.all([api("/api/metrics"), api("/api/usage")]);
      renderPerformance();
      renderUsage();
    },
  };
}
