import { createRange } from "./range.js";
import { THIS_DEVICE, api, el, errorText } from "./ui.js";

// Learn from audio, in the suggestions panel: one source at a time (chosen from the menu), a
// contiguous span chosen on a timeline over recorded time (oldest to newest, without calendar
// gaps), whole recordings only. It starts at about the most recent 90 minutes of a dictation
// source, and at all of an imported folder.
const PAGE = 60;
const RECENT_SECONDS = 90 * 60;

export function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}
// What the selected audio will take, from measurements only. Transcription uses the
// speech model's measured wait per minute of audio, several recordings at a time for a
// cloud model. Suggestions are counted in parts: about 37,000 characters of transcript per
// hour of speech (452,400 characters in a 12.3-hour dictation sample), divided by
// the part size, at the seconds per part measured for this model and this effort. The
// one reference without a measurement: GPT-6.1 Sol at medium effort took 135 minutes for
// 20 parts (docs/models.md). Both steps run side by side. Times are null where nothing
// was measured.
const CHARS_PER_HOUR = 37_000;
const SOL_SECONDS_PER_PART = 405;
export const PART_CHARS = 24_000;
function workPlan({ durations, speech, local, timing, dictionaryModel, effort }) {
  const seconds = durations.reduce((sum, d) => sum + d, 0);
  const rate = timing?.speech?.[speech];
  // Whole recordings go to the workers: never more at once than recordings, and never
  // shorter than the longest recording alone.
  const workers = Math.max(1, Math.min(local ? 1 : timing?.workers ?? 1, durations.length));
  const wall = Math.max(seconds / workers, ...durations, 0);
  const parts = Math.max(1, Math.ceil((seconds / 3600) * CHARS_PER_HOUR / PART_CHARS));
  const measured = timing?.suggestion?.[`${dictionaryModel}|${effort}`];
  const sol = !measured && /:gpt-6\.1-sol$/.test(dictionaryModel ?? "") && effort === "medium";
  return { seconds, workers, transcribe: rate ? (wall / 60) * rate : null, parts,
    perPart: measured?.secondsPerPart ?? (sol ? SOL_SECONDS_PER_PART : null), measuredOnce: sol };
}
export const day = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "Undated";
const moment = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }) : "Undated";

export function createAudioOnboarding({ getModel, getSettings, getRunSettings, onBuild, onBusy, onChange }) {
  const folder = el("audio-folder");
  const player = new Audio();
  let items = [];
  let timing = null;      // measured speeds, for the estimate
  let apps = [];          // dictation apps with an importer, from the server; the stored source is the id
  let source = "entune";
  let list = [];          // this source's recordings, oldest first
  const range = createRange({ track: el("audio-range"), onChange: () => draw(), label: "recording" });
  let shown = PAGE;
  let importing = false, buildBusy = false, loaded = false;
  let playing = null;

  function positions() {
    // A recording of unknown duration takes the average known length, so every
    // recording keeps its own selectable stretch of the range.
    const measured = list.filter((item) => item.seconds != null);
    const typical = measured.length ? measured.reduce((sum, item) => sum + item.seconds, 0) / measured.length : 1;
    const weights = list.map((item) => item.seconds ?? typical);
    let from = list.length;
    if (source === "folder") from = 0;
    else for (let heard = 0; from > 0 && heard < RECENT_SECONDS; heard += weights[from]) from--;
    range.setWeights(weights, [Math.min(from, Math.max(0, list.length - 1)), list.length]);
  }

  // The stored source of the recordings shown: Entune, a folder, or the app picked in the list.
  const pickedApp = () => apps.find((app) => app.id === document.querySelector('input[name="audio-provider"]:checked')?.value) ?? apps[0];
  const shownSource = () => (source === "provider" ? pickedApp()?.id : source);
  const span = (cls, text) => Object.assign(document.createElement("span"), { className: cls, textContent: text });
  function drawApps() {
    const box = el("audio-provider-pick");
    if (box.childElementCount === apps.length) return;
    box.replaceChildren(...apps.map((app, index) => {
      const option = document.createElement("label");
      option.className = "app-option";
      const input = Object.assign(document.createElement("input"), { type: "radio", name: "audio-provider", value: app.id, checked: index === 0 });
      input.addEventListener("change", () => { el("audio-import-status").textContent = ""; choose(); });
      const text = span("app-text", "");
      text.append(span("app-name", app.name), span("app-note", app.note));
      option.append(input, text, span("app-fact", ""));
      option.dataset.app = app.id;
      return option;
    }));
  }
  // Each app's imported audio, or that nothing is imported yet.
  function drawFacts() {
    for (const option of el("audio-provider-pick").children) {
      const saved = items.filter((item) => item.source === option.dataset.app);
      option.querySelector(".app-fact").textContent = saved.length
        ? `${saved.length} · ${duration(saved.reduce((sum, item) => sum + (item.seconds ?? 0), 0))}` : "Not imported";
    }
  }

  function choose() {
    const skip = source === "entune" && el("audio-other-models").checked ? getModel()?.id : null;
    list = items
      .filter((item) => item.source === shownSource() && !(skip && item.models.includes(skip)))
      .map((item, order) => ({ ...item, order }))
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.order - b.order);
    positions();
    shown = PAGE;
    stop();
    draw();
  }

  function draw() {
    el("app-pick").hidden = source !== "provider";
    drawFacts();
    const app = source === "provider" ? pickedApp() : null;
    el("import-app").hidden = !app;
    el("choose-audio-folder").hidden = source !== "folder";
    const some = items.some((item) => item.source === shownSource());
    el("import-app").textContent = app ? (some ? `Import new ${app.name} audio` : `Import from ${app.name}`) : "";
    el("choose-audio-folder").textContent = some ? "Add another folder…" : "Choose folder…";
    el("other-models").hidden = source !== "entune";
    el("audio-pick").hidden = !list.length;
    el("audio-empty").hidden = !loaded || list.length > 0;
    el("audio-empty").textContent = source === "entune"
      ? (items.some((item) => item.source === "entune") ? "Every recording here was already transcribed by this speech model." : "No Entune recordings yet.")
      : source === "provider" ? `Nothing imported from ${app?.name ?? "this app"} yet. Press Import to copy its recordings.`
        : "No audio files imported yet.";
    const chosen = list.slice(range.from, range.to);
    const seconds = (values) => values.reduce((sum, item) => sum + (item.seconds ?? 0), 0);
    const unknown = chosen.filter((item) => item.seconds == null).length;
    el("audio-selected").textContent = list.length ? `${duration(seconds(chosen))} selected` : "";
    el("audio-available").textContent = list.length
      ? `${chosen.length} of ${list.length} recordings · ${duration(seconds(list))} available${unknown ? ` · ${unknown} without a known duration` : ""}`
      : "";
    range.draw({ describe: (i) => moment(list[i]?.created_at) });
    el("audio-start-label").textContent = chosen.length ? day(chosen[0].created_at) : "";
    el("audio-end-label").textContent = chosen.length ? day(chosen.at(-1).created_at) : "";
    el("audio-detail-summary").textContent = `Show the ${chosen.length} included recording${chosen.length === 1 ? "" : "s"}`;
    if (el("audio-detail").open) drawList(chosen);
    for (const control of [el("import-app"), el("choose-audio-folder")]) control.disabled = importing || buildBusy;
    el("audio-range").classList.toggle("disabled", importing || buildBusy);
    onBusy(importing);
    onChange();
    // A folder import is one request per file; deleting all data waits for the last one.
    document.documentElement.toggleAttribute("data-importing", importing);
  }

  function drawList(chosen) {
    const box = el("learning-audio-list");
    box.replaceChildren();
    for (const item of chosen.slice(0, shown)) {
      const row = document.createElement("div"); row.className = "audio-row";
      const play = document.createElement("button");
      play.type = "button"; play.className = "btn ghost sm play";
      play.textContent = playing === item.id ? "Stop" : "Play";
      play.setAttribute("aria-label", `${playing === item.id ? "Stop" : "Play"} recording from ${moment(item.created_at)}`);
      play.addEventListener("click", () => toggle(item));
      const when = document.createElement("span"); when.className = "when"; when.textContent = moment(item.created_at);
      const length = document.createElement("span"); length.className = "length"; length.textContent = item.seconds == null ? "—" : duration(item.seconds);
      const about = document.createElement("span"); about.className = "about caption";
      about.textContent = item.source === "entune" ? (item.models.join(", ") || "Not transcribed") : item.name;
      row.append(play, when, length, about);
      box.append(row);
    }
    el("audio-more").hidden = chosen.length <= shown;
  }

  function stop() { player.pause(); playing = null; }
  function toggle(item) {
    if (playing === item.id) { stop(); }
    else {
      player.src = item.source === "entune" ? `/api/recordings/${item.id.split(":")[1]}/audio` : `/api/dictionary/audio/${item.id}/file`;
      player.play().catch((err) => {
        playing = null;
        el("audio-import-status").textContent = `Could not play that recording: ${errorText(err)}`;
        drawList(list.slice(range.from, range.to));
      });
      playing = item.id;
    }
    drawList(list.slice(range.from, range.to));
  }
  player.addEventListener("ended", () => { playing = null; drawList(list.slice(range.from, range.to)); });

  el("audio-other-models").addEventListener("change", choose);
  el("audio-detail").addEventListener("toggle", () => draw());
  el("audio-more").addEventListener("click", () => { shown += PAGE; draw(); });
  el("suggest-drawer").addEventListener("close", stop);

  async function refresh() {
    [{ items, apps }, timing] = await Promise.all([api("/api/dictionary/audio"), api("/api/dictionary/timing").catch(() => null)]);
    drawApps();
    loaded = true;
    choose();
  }

  el("choose-audio-folder").addEventListener("click", () => folder.click());
  folder.addEventListener("change", async () => {
    const picked = [...folder.files];
    const files = picked.filter((f) => /\.(wav|mp3|m4a|flac|ogg|webm)$/i.test(f.name));
    if (!picked.length) return;
    importing = true;
    draw();
    const status = el("audio-import-status");
    status.classList.remove("err");
    let added = 0;
    try {
      for (const [index, file] of files.entries()) {
        status.textContent = `Importing ${index + 1} of ${files.length}: ${file.name}`;
        if (file.size > 199 * 1024 * 1024) throw new Error(`${file.name}: exceeds the 199 MB file limit.`);
        const form = new FormData();
        form.append("audio", file);
        form.append("modified", String(file.lastModified));
        if ((await api("/api/dictionary/audio", { method: "POST", body: form })).added) added++;
      }
      status.textContent = `Imported ${added}; ${files.length - added} already saved; ${picked.length - files.length} other files skipped.`;
    } catch (err) {
      status.textContent = `${errorText(err)} Import stopped; ${added} new audio files kept.`;
      status.classList.add("err");
    } finally {
      importing = false;
      folder.value = "";
      await refresh();
    }
  });
  el("import-app").addEventListener("click", async () => {
    const app = pickedApp();
    importing = true;
    draw();
    const status = el("audio-import-status");
    status.classList.remove("err");
    status.textContent = `Copying the audio ${app.name} keeps on ${THIS_DEVICE}…`;
    try {
      const result = await api(`/api/dictionary/audio/apps/${encodeURIComponent(app.id)}`, { method: "POST" });
      status.textContent = `Imported ${result.added}; ${result.duplicates} already saved; ${result.empty} empty recordings skipped.`;
    } catch (err) {
      status.textContent = errorText(err);
      status.classList.add("err");
    } finally {
      importing = false;
      await refresh();
    }
  });
  el("build-audio-dictionary").addEventListener("click", async () => {
    const ids = list.slice(range.from, range.to).map((item) => item.id);
    stop();
    await onBuild({ audio_ids: ids });
  });

  return {
    load: refresh,
    // What Transcribe and suggest would do now: the chosen recordings and the measured
    // times; `available` is how many recordings the source has.
    plan() {
      const chosen = list.slice(range.from, range.to);
      const speech = getModel();
      return {
        available: list.length, loaded, busy: importing || buildBusy, count: chosen.length,
        unknown: chosen.filter((item) => item.seconds == null).length,
        local: (timing?.local ?? []).includes(speech?.id.split("/")[0]),
        ...workPlan({
          durations: chosen.map((item) => item.seconds ?? 0), speech: speech?.id, local: (timing?.local ?? []).includes(speech?.id.split("/")[0]),
          timing, dictionaryModel: getSettings()?.dictionaryModel, effort: getRunSettings().effort,
        }),
      };
    },
    // Opens on the source the menu named: Entune recordings, another app, or a folder.
    // The selection switches at once from the recordings already loaded, so Transcribe and
    // suggest never sends the previous source's audio while the list refreshes.
    async show(pick) { if (pick !== source) { source = pick; el("audio-import-status").textContent = ""; choose(); } await refresh(); },
    // Status polls repeat the same value; redraw only on a change, keeping focus in the list.
    setBuildBusy(value) { if (value !== buildBusy) { buildBusy = value; draw(); } },
    redraw: draw,
    // Another speech model hears different recordings as new: filter the list again.
    modelChanged() { if (loaded) choose(); },
  };
}
