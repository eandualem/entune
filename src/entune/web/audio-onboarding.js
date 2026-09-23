import { api, el, errorText } from "./ui.js";

// Learn from audio: one source at a time, a contiguous span chosen on a range over
// recorded time (oldest to newest, without calendar gaps), whole recordings only.
const PAGE = 60;
const NOTES = {
  entune: "Recordings you made in Entune.",
  folder: "WAV, MP3, M4A, FLAC, OGG or WebM files, up to 199 MB each, dated by when they were modified.",
};
// Other providers: transcription apps whose recordings Entune can import. Each entry is
// an importer that exists; the stored source is the provider's id.
const PROVIDERS = {
  wispr: "Wispr Flow keeps its recordings, including backups, on this Mac. Entune copies the audio only, never Wispr's transcripts.",
};

export function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}
const day = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "Undated";
const moment = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }) : "Undated";

export function createAudioOnboarding({ getModel, getSettings, getDictionaryModelName, onBuild, onBusy }) {
  const dialog = el("audio-dialog");
  const folder = el("audio-folder");
  const start = el("audio-range-start"), end = el("audio-range-end");
  const player = new Audio();
  let items = [];
  let source = "entune";
  let list = [];          // this source's recordings, oldest first
  let edges = [0];        // cumulative position of each boundary, 0..1000
  let from = 0, to = 0;   // included recordings: list[from .. to - 1]
  let shown = PAGE;
  let importing = false, buildBusy = false, loaded = false;
  let playing = null;

  function positions() {
    // A recording of unknown duration takes the average known length, so every
    // recording keeps its own selectable stretch of the range.
    const measured = list.filter((item) => item.seconds != null);
    const typical = measured.length ? measured.reduce((sum, item) => sum + item.seconds, 0) / measured.length : 1;
    const widths = list.map((item) => item.seconds ?? typical);
    const total = widths.reduce((sum, width) => sum + width, 0) || 1;
    edges = [0];
    for (const width of widths) edges.push(edges.at(-1) + (width / total) * 1000);
  }
  const nearest = (value) => edges.reduce((best, edge, i) => Math.abs(edge - value) < Math.abs(edges[best] - value) ? i : best, 0);

  // The stored source of the recordings shown: the tab, or the provider picked under it.
  const shownSource = () => (source === "provider" ? el("audio-provider").value : source);

  function choose() {
    const skip = source === "entune" && el("audio-other-models").checked ? getModel()?.id : null;
    list = items
      .filter((item) => item.source === shownSource() && !(skip && item.models.includes(skip)))
      .map((item, order) => ({ ...item, order }))
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.order - b.order);
    positions();
    from = 0; to = list.length; shown = PAGE;
    stop();
    draw();
  }

  function draw() {
    for (const tab of dialog.querySelectorAll("[data-source]")) tab.setAttribute("aria-selected", String(tab.dataset.source === source));
    el("audio-provider-pick").hidden = source !== "provider";
    el("audio-source-note").textContent = source === "provider" ? PROVIDERS[shownSource()] : NOTES[source];
    el("import-wispr").hidden = shownSource() !== "wispr";
    el("choose-audio-folder").hidden = source !== "folder";
    const some = items.some((item) => item.source === shownSource());
    el("import-wispr").textContent = some ? "Import new Wispr audio" : "Import from Wispr Flow";
    el("choose-audio-folder").textContent = some ? "Add another folder…" : "Choose folder…";
    el("audio-other-models").closest("label").hidden = source !== "entune";
    el("audio-pick").hidden = !list.length;
    el("audio-empty").hidden = !loaded || list.length > 0;
    el("audio-empty").textContent = source === "entune"
      ? (items.some((item) => item.source === "entune") ? "Every recording here was already transcribed by this speech model." : "No Entune recordings yet.")
      : source === "provider" ? `Nothing imported from ${el("audio-provider").selectedOptions[0]?.textContent ?? "this provider"} yet.`
        : "No audio files imported yet.";
    const chosen = list.slice(from, to);
    const seconds = (values) => values.reduce((sum, item) => sum + (item.seconds ?? 0), 0);
    const unknown = chosen.filter((item) => item.seconds == null).length;
    el("audio-selected").textContent = list.length ? `${duration(seconds(chosen))} selected` : "";
    el("audio-available").textContent = list.length
      ? `${chosen.length} of ${list.length} recordings · ${duration(seconds(list))} available${unknown ? ` · ${unknown} without a known duration` : ""}`
      : "";
    start.value = String(Math.round(edges[from] ?? 0));
    end.value = String(Math.round(edges[to] ?? 1000));
    el("audio-range-fill").style.left = `${(edges[from] ?? 0) / 10}%`;
    el("audio-range-fill").style.right = `${100 - (edges[to] ?? 1000) / 10}%`;
    el("audio-start-label").textContent = chosen.length ? day(chosen[0].created_at) : "";
    el("audio-end-label").textContent = chosen.length ? day(chosen.at(-1).created_at) : "";
    el("audio-detail-summary").textContent = `Show the ${chosen.length} included recording${chosen.length === 1 ? "" : "s"}`;
    if (el("audio-detail").open) drawList(chosen);
    const speech = getModel();
    const language = getDictionaryModelName();
    el("audio-models").textContent = !speech
      ? "Choose a speech model in the toolbar first."
      : !language
        ? (el("dictionary-model-note").hidden ? "Add an Anthropic or OpenAI key in Settings › Dictionary setup to choose a suggestion model." : el("dictionary-model-note").textContent)
        : `Transcribe ${duration(seconds(chosen))} (${chosen.length} recording${chosen.length === 1 ? "" : "s"}) with ${speech.label}, then suggest with ${language}.`;
    const blocked = importing || buildBusy || !chosen.length || !speech || !language;
    el("build-audio-dictionary").disabled = blocked;
    for (const control of [start, end, el("import-wispr"), el("choose-audio-folder")]) control.disabled = importing || buildBusy;
    onBusy(importing);
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
        drawList(list.slice(from, to));
      });
      playing = item.id;
    }
    drawList(list.slice(from, to));
  }
  player.addEventListener("ended", () => { playing = null; drawList(list.slice(from, to)); });

  // The two handles cannot cross; at least one whole recording stays included.
  start.addEventListener("input", () => { from = Math.min(nearest(+start.value), to - 1); draw(); });
  end.addEventListener("input", () => { to = Math.max(nearest(+end.value), from + 1); draw(); });
  // Arrow, Page and Home/End keys move a handle by whole recordings.
  for (const [handle, isStart] of [[start, true], [end, false]]) {
    handle.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -10, PageUp: 10, Home: -Infinity, End: Infinity }[event.key];
      if (step === undefined) return;
      event.preventDefault();
      if (isStart) from = Math.max(0, Math.min(to - 1, from + (Number.isFinite(step) ? step : step > 0 ? list.length : -list.length)));
      else to = Math.min(list.length, Math.max(from + 1, to + (Number.isFinite(step) ? step : step > 0 ? list.length : -list.length)));
      draw();
    });
  }
  el("audio-other-models").addEventListener("change", choose);
  el("audio-detail").addEventListener("toggle", () => draw());
  el("audio-more").addEventListener("click", () => { shown += PAGE; draw(); });
  el("audio-provider").addEventListener("change", () => { el("audio-import-status").textContent = ""; choose(); });
  for (const tab of dialog.querySelectorAll("[data-source]")) {
    tab.addEventListener("click", () => { source = tab.dataset.source; el("audio-import-status").textContent = ""; choose(); });
  }
  dialog.addEventListener("close", stop);

  async function refresh() {
    items = (await api("/api/dictionary/audio")).items;
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
  el("import-wispr").addEventListener("click", async () => {
    importing = true;
    draw();
    const status = el("audio-import-status");
    status.classList.remove("err");
    status.textContent = "Copying the audio Wispr Flow kept on this Mac…";
    try {
      const result = await api("/api/dictionary/audio/wispr", { method: "POST" });
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
    const ids = list.slice(from, to).map((item) => item.id);
    dialog.close();
    await onBuild({ audio_ids: ids });
  });

  return {
    load: refresh,
    async open() { dialog.showModal(); await refresh(); },
    // Status polls repeat the same value; redraw only on a change, keeping focus in the list.
    setBuildBusy(value) { if (value !== buildBusy) { buildBusy = value; draw(); } },
    redraw: draw,
  };
}
