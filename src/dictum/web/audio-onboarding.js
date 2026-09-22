import { api, el, errorText } from "./ui.js";

export function createAudioOnboarding({ getModel, getSettings, onBuild, onBusy }) {
  const folder = el("audio-folder");
  const choose = el("choose-audio-folder");
  const wispr = el("import-wispr");
  const build = el("build-audio-dictionary");
  const status = el("audio-import-status");
  let count = 0;
  let importing = false;
  let buildBusy = false;

  function render() {
    el("audio-count").textContent = `${count} audio files saved for reuse`;
    const speech = getModel();
    const language = getSettings()?.dictionaryModel;
    el("audio-models").textContent = speech && language
      ? `Transcribe with ${speech.label}; build the dictionary with ${language}. Provider charges apply. Review before accepting.`
      : "Choose a speech model in the toolbar and a dictionary model in Settings → Providers.";
    choose.disabled = wispr.disabled = importing || buildBusy;
    build.disabled = importing || buildBusy || !count || !speech || !language;
    onBusy(importing);
  }

  async function refreshCount() {
    count = (await api("/api/dictionary/audio")).count;
    render();
  }

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
  build.addEventListener("click", onBuild);

  return {
    load: refreshCount,
    setBuildBusy(value) { buildBusy = value; render(); },
  };
}
