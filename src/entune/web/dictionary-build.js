import { api, el, errorText, flash } from "./ui.js";

// Both sources use the same server-owned job. Poll small status records; fetch a
// potentially large proposal once per job, and name that job on every action.
export function createDictionaryBuild({ onBusy, onState, onProposal, onAccepted, getSelected, getRunSettings }) {
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
    const kept = (n = 0) => `${n} temporary transcript${n === 1 ? "" : "s"}`;
    const from = state.source === "audio" ? "audio" : "history";
    // The steps above say which part runs and what is done; this line adds what they
    // can't: a retry, a rule the model is asked to fix, skipped recordings, an error.
    // A reply that broke a rule goes back to the model for a fix; say so, never silently.
    const suggesting = ["building", "transcribing"].includes(state.phase); // parts run alongside transcription
    const fixing = suggesting && state.attempt > 1 && state.brokenRule;
    // A part that failed in a way that may pass starts over; say why and which attempt.
    const again = suggesting && state.partAttempt > 1 && state.retryReason;
    // A recording that would not transcribe, even on a second try, is skipped; say how many.
    const skippedNote = state.skipped ? `${state.skipped} could not be transcribed and ${state.skipped === 1 ? "was" : "were"} skipped${state.skippedReason ? ` (${state.skippedReason}${state.skipped > 1 ? "; every reason is in the terminal or entune.log" : ""})` : ""}.` : "";
    const notes = [
      again ? `Retrying this part (${state.retryReason}), attempt ${state.partAttempt} of ${state.partAttempts}.` : "",
      fixing ? `The model's reply broke a dictionary rule, so it is asked to fix it: attempt ${state.attempt} of ${state.attempts}. Stop if you'd rather not wait.` : "",
      skippedNote,
    ].filter(Boolean).join(" ");
    const messages = {
      idle: "", queued: "", transcribing: notes, building: notes,
      cancelling: "A transcription already under way may need to finish.",
      cleaning: "", ready: "",
      cancelled: [from === "audio" ? `${kept(state.cachedTranscripts)} kept for Continue.` : "", skippedNote].filter(Boolean).join(" "),
      failed: `${state.error} Your dictionary is unchanged.${from === "audio" ? ` ${kept(state.cachedTranscripts)} kept; your audio is kept.` : ""}${skippedNote ? ` ${skippedNote}` : ""}`,
      accepted: state.applied ? "Applied. Your dictionary is updated." : "Closed without changes.",
      discarded: from === "audio" ? "Proposal discarded. Original audio is kept." : "Proposal discarded.",
    };
    let message = messages[state.phase] ?? state.phase;
    if (state.phase === "ready") {
      const coverage = `${state.coveredInputs} of ${state.total} ${from === "audio" ? "recordings" : "dictations"} read`;
      message = state.outcome === "stopped" ? `Stopped with ${coverage}. Review what's ready, continue with the rest, or discard.`
        : state.outcome === "failed" ? `${state.error} ${coverage}. Review what's ready, continue with the rest, or discard.`
        : `${coverage[0].toUpperCase()}${coverage.slice(1)}.${skippedNote ? ` ${skippedNote}` : ""}`;
    }
    progress.textContent = message;
    progress.hidden = !message;
    const failed = state.phase === "failed" || (state.phase === "ready" && state.outcome === "failed");
    const detail = failed ? state.errorDetail : fixing ? `Rule broken: ${state.brokenRule}` : "";
    el("build-error-text").textContent = detail || "";
    el("build-error-detail").hidden = !detail;
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
    onState(state);
  }
  function poll() {
    if (reading) return reading;
    clearTimeout(timer);
    reading = (async () => {
      const started = version;
      try {
        const result = await api("/api/dictionary/build");
        if (version === started) {
          // The same answer again needs no redraw, except while running: its clock moves.
          const same = JSON.stringify(result) === JSON.stringify(state);
          state = result;
          if (!same || running()) await render();
        }
      } catch (err) {
        progress.textContent = `Could not read build progress: ${errorText(err)}`;
        progress.classList.add("err");
        progress.hidden = false;
        el("learn-status").hidden = false;
      } finally {
        reading = null;
        timer = setTimeout(poll, document.hidden ? 10000 : running() ? 1000 : 3000);
      }
    })();
    return reading;
  }
  async function action(name, id = state.id) {
    version++;
    clearFeedback();
    try {
      const body = name === "accept" ? { selected: getSelected() } : name === "retry" ? getRunSettings() : null;
      state = await api(`/api/dictionary/build/${id}${name === "discard" ? "" : `/${name}`}`, {
        method: name === "discard" ? "DELETE" : "POST",
        ...(body ? { headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {}),
      });
      await render();
    } catch (err) {
      flash(el("build-status"), errorText(err), "err");
    }
    await poll();
  }
  el("retry-dictionary-build").addEventListener("click", () => action("retry"));
  cancel.addEventListener("click", () => action("cancel"));
  el("accept-proposal").addEventListener("click", () => action("accept"));
  return {
    load: poll,
    // Discarding asks first; the view names the job it confirmed, so a run that replaced
    // it meanwhile is refused rather than discarded.
    discard: (id) => action("discard", id),
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
