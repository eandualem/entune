const timeLabel = seconds => seconds < 60 ? `${Math.round(seconds)} s` : `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
import { api, el, errorText } from "./ui.js";

export function createAudioOnboarding({ getModel, getSettings, onBuild, onBusy }) {
  const folder = el("audio-folder");
  const choose = el("choose-audio-folder");
  const wispr = el("import-wispr");
  const build = el("build-audio-dictionary");
  const status = el("audio-import-status");
  let items = [];
  const selected = new Set();
  let importing = false;
  let buildBusy = false;

  function render() {
    const duration = values => {
      const seconds = values.reduce((sum, item) => sum + (item.seconds ?? 0), 0);
      const unknown = values.filter(item => item.seconds == null).length;
      return `${timeLabel(seconds)} known${unknown ? ` + ${unknown} unknown duration${unknown === 1 ? "" : "s"}` : ""}`;
    };
    const chosen = items.filter(item => selected.has(item.id));
    el("audio-count").textContent = `Available: ${items.length} recordings · ${duration(items)}. Selected: ${chosen.length} · ${duration(chosen)}.`;
    const speech = getModel();
    const language = getSettings()?.dictionaryModel;
    el("audio-models").textContent = speech && language
      ? `Transcribe with ${speech.label}; build the dictionary with ${language}. Cloud provider charges may apply; no cost estimate is available. Review before accepting.`
      : "Choose a speech model in the toolbar and a dictionary model in Settings → Providers.";
    choose.disabled = wispr.disabled = importing || buildBusy;
    build.disabled = importing || buildBusy || !selected.size || !speech || !language;
    for (const field of el("learning-audio-list").querySelectorAll("input")) field.disabled = importing || buildBusy;
    onBusy(importing);
  }

  async function refreshCount() {
    items = (await api("/api/dictionary/audio")).items;
    for (const id of selected) if (!items.some(item => item.id === id)) selected.delete(id);
    drawSelection();
  }

  function visibleItems() {
    const from = el("audio-from").value, through = el("audio-through").value;
    return items.filter(item => {
      const date = item.created_at?.slice(0, 10);
      return (!from && !through) || date && (!from || date >= from) && (!through || date <= through);
    });
  }
  function drawSelection() {
    const list = el("learning-audio-list"); list.replaceChildren();
    for (const item of visibleItems()) {
      const row = document.createElement("label"); row.className = "learning-audio-item";
      const check = document.createElement("input"); check.type = "checkbox";
      check.checked = selected.has(item.id);
      check.addEventListener("change", () => { check.checked ? selected.add(item.id) : selected.delete(item.id); render(); });
      const text = document.createElement("span");
      text.textContent = `${item.name} · ${item.created_at ? new Date(item.created_at).toLocaleString() : "imported audio"} · ${item.seconds == null ? "duration unknown" : timeLabel(item.seconds)}`;
      row.append(check, text); list.append(row);
    }
    render();
  }
  for (const id of ["audio-from", "audio-through"]) el(id).addEventListener("change", drawSelection);
  el("audio-select-visible").addEventListener("click", () => { if (!buildBusy) { for (const item of visibleItems()) selected.add(item.id); drawSelection(); } });
  el("audio-clear").addEventListener("click", () => { if (!buildBusy) { selected.clear(); drawSelection(); } });

  choose.addEventListener("click", () => folder.click());
  folder.addEventListener("change", async () => {
    const selected = [...folder.files];
    const files = selected.filter((f) => /\.(wav|mp3|m4a|flac|ogg|webm)$/i.test(f.name));
    if (!selected.length) return;
    importing = true;
    render();
    status.classList.remove("err");
    let added = 0;
    try {
      for (const [index, file] of files.entries()) {
        status.textContent = `Importing ${index + 1} of ${files.length}: ${file.name}`;
        if (file.size > 199 * 1024 * 1024) throw new Error(`${file.name}: exceeds the 199 MB file limit.`);
        const form = new FormData();
        form.append("audio", file);
        const result = await api("/api/dictionary/audio", { method: "POST", body: form });
        if (result.added) added++;
      }
      status.textContent = `Imported ${added}; ${files.length - added} already saved; ${selected.length - files.length} other files skipped.`;
    } catch (err) {
      status.textContent = `${errorText(err)} Import stopped; ${added} new audio files kept.`;
      status.classList.add("err");
    } finally {
      importing = false;
      folder.value = "";
      await refreshCount();
    }
  });
  wispr.addEventListener("click", async () => {
    importing = true;
    render();
    status.classList.remove("err");
    status.textContent = "Copying retained Wispr audio from this Mac…";
    try {
      const result = await api("/api/dictionary/audio/wispr", { method: "POST" });
      status.textContent = `Imported ${result.added}; ${result.duplicates} already saved; ${result.empty} empty recordings skipped.`;
    } catch (err) {
      status.textContent = errorText(err);
      status.classList.add("err");
    } finally {
      importing = false;
      await refreshCount();
    }
  });
  build.addEventListener("click", () => onBuild({audio_ids: [...selected]}));

  return {
    load: refreshCount,
    setBuildBusy(value) { buildBusy = value; render(); },
  };
}
