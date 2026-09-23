import { api, el, errorText } from "./ui.js";

// Learn from audio: one source at a time, a contiguous span chosen on a range over
// recorded time (oldest to newest, without calendar gaps), whole recordings only.
const PAGE = 60;
const NOTES = {
  dictum: "Recordings you made in Dictum.",
  wispr: "Audio that Wispr Flow kept on this Mac, including its local backups. Only audio is copied; Wispr's transcripts are never read.",
  folder: "Audio files from a folder: WAV, MP3, M4A, FLAC, OGG or WebM, up to 199 MB each. A file's modification time dates it.",
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
  let source = "dictum";
  let list = [];          // this source's recordings, oldest first
  let edges = [0];        // cumulative position of each boundary, 0..1000
  let from = 0, to = 0;   // included recordings: list[from .. to - 1]
  let shown = PAGE;
  let importing = false, buildBusy = false, loaded = false;
  let playing = null;

  function positions() {
    const known = list.reduce((sum, item) => sum + (item.seconds ?? 0), 0);
    // Without any known duration, each recording takes an equal share of the range.
    const width = (item) => known ? (item.seconds ?? 0) / known : 1 / list.length;
    edges = [0];
    for (const item of list) edges.push(edges.at(-1) + width(item) * 1000);
  }
  const nearest = (value) => edges.reduce((best, edge, i) => Math.abs(edge - value) < Math.abs(edges[best] - value) ? i : best, 0);

  function choose() {
    const skip = source === "dictum" && el("audio-other-models").checked ? getModel()?.id : null;
    list = items
      .filter((item) => item.source === source && !(skip && item.models.includes(skip)))
      .map((item, order) => ({ ...item, order }))
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.order - b.order);
    positions();
    from = 0; to = list.length; shown = PAGE;
    stop();
    draw();
  }

  function draw() {
    for (const tab of dialog.querySelectorAll("[data-source]")) tab.setAttribute("aria-selected", String(tab.dataset.source === source));
    el("audio-source-note").textContent = NOTES[source];
    el("import-wispr").hidden = source !== "wispr";
    el("choose-audio-folder").hidden = source !== "folder";
    const some = items.some((item) => item.source === source);
    el("import-wispr").textContent = some ? "Import new Wispr audio" : "Import from Wispr Flow";
    el("choose-audio-folder").textContent = some ? "Add another folder…" : "Choose folder…";
    el("audio-other-models").closest("label").hidden = source !== "dictum";
    el("audio-pick").hidden = !list.length;
    el("audio-empty").hidden = !loaded || list.length > 0;
    el("audio-empty").textContent = source === "dictum"
      ? (items.some((item) => item.source === "dictum") ? "Every recording here was already transcribed by this speech model." : "No Dictum recordings yet.")
      : "Nothing imported from this source yet.";
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
    el("audio-speech-model").textContent = speech?.label ?? "the selected speech model";
    el("audio-models").textContent = !speech
      ? "Choose a speech model in the toolbar first."
      : !language
        ? "Add an Anthropic or OpenAI key in Settings › Providers to choose a dictionary model."
        : `Transcribe ${duration(seconds(chosen))} with ${speech.label}, then build with ${language}.`;
    const blocked = importing || buildBusy || !chosen.length || !speech || !language;
    el("build-audio-dictionary").disabled = el("refine-audio-dictionary").disabled = blocked;
    for (const control of [start, end, el("import-wispr"), el("choose-audio-folder")]) control.disabled = importing || buildBusy;
    onBusy(importing);
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
      about.textContent = item.source === "dictum" ? (item.models.join(", ") || "Not transcribed") : item.name;
      row.append(play, when, length, about);
      box.append(row);
    }
    el("audio-more").hidden = chosen.length <= shown;
  }

  function stop() { player.pause(); playing = null; }
  function toggle(item) {
    if (playing === item.id) { stop(); }
    else {
      player.src = item.source === "dictum" ? `/api/recordings/${item.id.split(":")[1]}/audio` : `/api/dictionary/audio/${item.id}/file`;
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
  el("audio-other-models").addEventListener("change", choose);
  el("audio-detail").addEventListener("toggle", () => draw());
  el("audio-more").addEventListener("click", () => { shown += PAGE; draw(); });
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
  for (const [id, mode] of [["build-audio-dictionary", "generate"], ["refine-audio-dictionary", "refine"]]) {
    el(id).addEventListener("click", async () => {
      const ids = list.slice(from, to).map((item) => item.id);
      dialog.close();
      await onBuild({ mode, audio_ids: ids });
    });
  }

  return {
    load: refresh,
    async open() { dialog.showModal(); await refresh(); },
    setBuildBusy(value) { buildBusy = value; draw(); },
    redraw: draw,
  };
}
