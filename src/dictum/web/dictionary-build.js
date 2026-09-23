import { api, el, errorText, flash } from "./ui.js";

// Both sources use the same server-owned job. Poll small status records; fetch a
// potentially large proposal once per job, and name that job on every action.
export function createDictionaryBuild({ names, onBusy, onProposal, onAccepted, getSelected }) {
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
    el("retry-dictionary-build").hidden = running() || !["failed", "stopped"].includes(state.outcome) || ["accepted", "discarded"].includes(state.phase);
    el("abandon-dictionary-build").hidden = running() || !["failed", "cancelled"].includes(state.phase);
    cancel.disabled = state.phase === "cancelling";
    progress.classList.toggle("err", state.phase === "failed");
    const model = names.speech(state.model);
    const kept = (n = 0) => `${n} temporary transcript${n === 1 ? "" : "s"}`;
    const task = state.mode === "refine" ? "Refining" : "Generating";
    const from = state.source === "audio" ? "audio" : "history";
    const messages = {
      idle: "", queued: `Preparing to learn from ${from}…`,
      transcribing: `Transcribing audio ${state.completed} of ${state.total} with ${model}…`,
      building: `${task} with ${names.language(state.dictionaryModel)} · step ${state.step ?? 0} of ${state.steps ?? "…"}`,
      cancelling: "Cancelling… the current speech operation may need to finish. No further clips or chunks will start.",
      cleaning: "Finishing cleanup…", ready: `Proposal ready for ${model}. Review before accepting.`,
      cancelled: `You stopped. ${state.completedBatches ?? 0} of ${state.steps ?? 0} batches completed.${from === "audio" ? ` ${kept(state.cachedTranscripts)} kept for Retry.` : ""}`,
      failed: `${state.error} No dictionary changed.${from === "audio" ? ` Original audio is kept; ${kept(state.cachedTranscripts)} kept for Retry.` : ""}`,
      accepted: state.applied ? "Changes applied; fully covered inputs marked learned." : "No changes applied. Inputs remain eligible.",
      discarded: from === "audio" ? "Proposal discarded. Original audio is kept." : "Proposal discarded.",
    };
    let message = messages[state.phase] ?? state.phase;
    if (state.phase === "ready") {
      const coverage = `${state.completedBatches} of ${state.steps} batches; ${state.coveredInputs} of ${state.total} inputs fully covered`;
      message = state.outcome === "stopped" ? `You stopped after ${coverage}. Review the completed portion, retry, or discard.`
        : state.outcome === "failed" ? `${state.error} Completed ${coverage}. Review the completed portion, retry, or discard.`
        : `Ready for review: ${coverage}. Edit or dismiss changes, then apply the remainder once.`;
      message += " Finish learning before dictating or editing the active dictionary.";
    }
    progress.textContent = message;
    const actions = ["cancel-dictionary-build", "retry-dictionary-build", "abandon-dictionary-build"];
    el("learn-status").hidden = !message && actions.every((id) => el(id).hidden);
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
        el("learn-status").hidden = false;
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
        ...(name === "accept" ? {headers: {"content-type": "application/json"}, body: JSON.stringify({selected: getSelected()})} : {}),
      });
      await render();
    } catch (err) {
      flash(el("build-status"), errorText(err), "err");
    }
    await poll();
  }
  el("retry-dictionary-build").addEventListener("click", () => action("retry"));
  el("abandon-dictionary-build").addEventListener("click", () => action("discard"));
  cancel.addEventListener("click", () => action("cancel"));
  el("accept-proposal").addEventListener("click", () => action("accept"));
  el("discard-proposal").addEventListener("click", () => action("discard"));
  return {
    load: poll,
    async start(source, selection = {}) {
      version++;
      clearFeedback();
      onBusy(true);
      try {
        state = await api("/api/dictionary/build", {
          method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ source, ...selection }),
        });
        await render();
      } catch (err) {
        flash(el("build-status"), errorText(err), "err");
      }
      await poll();
    },
  };
}
