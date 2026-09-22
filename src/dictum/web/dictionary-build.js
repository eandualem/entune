import { api, el, errorText, flash } from "./ui.js";

// Both sources use the same server-owned job. Poll small status records; fetch a
// potentially large proposal once per job, and name that job on every action.
export function createDictionaryBuild({ onBusy, onProposal, onAccepted }) {
  const progress = el("dictionary-build-status");
  const cancel = el("cancel-dictionary-build");
  let state = { phase: "idle" };
  let proposalId = null;
  let acceptedId = null;
  let version = 0;
  let timer, reading;
  const running = () => ["queued", "transcribing", "building", "cancelling", "cleaning"].includes(state.phase);
  function clearFeedback() {
    clearTimeout(el("build-status")._timer);
    el("build-status").textContent = "";
    el("build-status").classList.remove("show");
  }

  async function render() {
    onBusy(running() || state.phase === "ready");
    cancel.hidden = !running();
    cancel.disabled = state.phase === "cancelling";
    progress.classList.toggle("err", state.phase === "failed");
    const model = state.model;
    const messages = {
      idle: "", queued: `Preparing ${state.source} build for ${model}…`,
      transcribing: `Transcribing ${state.completed} of ${state.total} with ${model}…`,
      building: `Building for ${model} with ${state.dictionaryModel} · step ${state.step ?? 0} of ${state.steps ?? "…"}`,
      cancelling: "Cancelling… the current speech operation may need to finish. No further clips or chunks will start.",
      cleaning: "Finishing cleanup…", ready: `Proposal ready for ${model}. Review before accepting.`,
      cancelled: "Build cancelled. Original audio is kept; temporary build text was discarded.",
      failed: `${state.error} No dictionary changed; original audio is kept.`,
      accepted: "Proposal accepted.", discarded: "Proposal discarded. Original audio is kept.",
    };
    progress.textContent = messages[state.phase] ?? state.phase;
    if (state.phase === "accepted" && acceptedId !== state.id) {
      acceptedId = state.id;
      await onAccepted();
    }
    if (state.phase === "ready" && proposalId !== state.id) {
      const detail = await api(`/api/dictionary/build/${state.id}`);
      if (detail.phase === "ready" && state.phase === "ready" && detail.id === state.id) {
        proposalId = detail.id;
        onProposal(detail.proposal);
      }
    } else if (state.phase !== "ready") {
      proposalId = null;
      onProposal(null);
    }
  }
  function poll() {
    if (reading) return reading;
    clearTimeout(timer);
    reading = (async () => {
      const started = version;
      try {
        const result = await api("/api/dictionary/build");
        if (version === started) { state = result; await render(); }
      } catch (err) {
        progress.textContent = `Could not read build progress: ${errorText(err)}`;
        progress.classList.add("err");
      } finally {
        reading = null;
        timer = setTimeout(poll, document.hidden ? 10000 : running() ? 1000 : 3000);
      }
    })();
    return reading;
  }
  async function action(name) {
    const id = state.id;
    version++;
    clearFeedback();
    try {
      state = await api(`/api/dictionary/build/${id}${name === "discard" ? "" : `/${name}`}`, {
        method: name === "discard" ? "DELETE" : "POST",
      });
      await render();
    } catch (err) {
      flash(el("build-status"), errorText(err), "err");
    }
    await poll();
  }
  cancel.addEventListener("click", () => action("cancel"));
  el("accept-proposal").addEventListener("click", () => action("accept"));
  el("discard-proposal").addEventListener("click", () => action("discard"));
  return {
    load: poll,
    async start(source) {
      version++;
      clearFeedback();
      onBusy(true);
      try {
        state = await api("/api/dictionary/build", {
          method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ source }),
        });
        await render();
      } catch (err) {
        flash(el("build-status"), errorText(err), "err");
      }
      await poll();
    },
  };
}
