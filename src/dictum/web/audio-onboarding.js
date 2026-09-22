import { api, el, errorText } from "./ui.js";

export function createAudioOnboarding({ getModel, getSettings, onProposal, onBusy }) {
  const folder = el("audio-folder");
  const choose = el("choose-audio-folder");
  const wispr = el("import-wispr");
  const build = el("build-audio-dictionary");
  const status = el("audio-import-status");
  const progress = el("audio-build-status");
  let count = 0;
  let importing = false;
  let running = false;
  let historyBusy = false;
  let timer;

  function render() {
    el("audio-count").textContent = `${count} audio files saved for reuse`;
    const speech = getModel();
    const language = getSettings()?.dictionaryModel;
    el("audio-models").textContent = speech && language
      ? `Transcribe with ${speech.label}; build the dictionary with ${language}. Provider charges apply. Review before accepting.`
      : "Choose a speech model in the toolbar and a dictionary model in Settings → Providers.";
    choose.disabled = wispr.disabled = importing || running || historyBusy;
    build.disabled = importing || running || historyBusy || !count || !speech || !language;
    onBusy(importing || running || historyBusy);
  }

  async function refreshCount() {
    count = (await api("/api/dictionary/audio")).count;
    render();
  }

  async function poll() {
    clearTimeout(timer);
    try {
      const state = await api("/api/dictionary/audio/build");
      running = ["transcribing", "building"].includes(state.phase);
      progress.classList.toggle("err", state.phase === "error");
      if (state.phase === "transcribing") progress.textContent = `Transcribing ${state.completed} of ${state.total} with ${state.model}…`;
      else if (state.phase === "building") progress.textContent = `Building for ${state.model} with ${state.dictionaryModel}… Large collections go in steps and can take several minutes.`;
      else if (state.phase === "error") progress.textContent = `${state.error} Audio is kept; no dictionary was changed. You can start again after fixing the error.`;
      else if (state.phase === "done") {
        progress.textContent = `Proposal ready for ${state.model}. Temporary transcripts have been discarded.`;
        onProposal(state.proposal);
      } else progress.textContent = "";
      render();
      if (running) timer = setTimeout(poll, 1000);
    } catch (err) {
      progress.textContent = `Could not read build progress: ${errorText(err)}`;
      progress.classList.add("err");
      if (running) timer = setTimeout(poll, 3000);
    }
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
  build.addEventListener("click", async () => {
    running = true;
    render();
    try {
      await api("/api/dictionary/audio/build", { method: "POST" });
      await poll();
    } catch (err) {
      running = false;
      progress.textContent = errorText(err);
      progress.classList.add("err");
      render();
    }
  });

  return {
    async load() { await refreshCount(); await poll(); },
    setHistoryBusy(value) { historyBusy = value; render(); },
    async dismiss() {
      await api("/api/dictionary/audio/build", { method: "DELETE" });
      progress.textContent = "";
    },
  };
}
