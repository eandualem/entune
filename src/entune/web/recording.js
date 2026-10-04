import { api, el, errorText } from "./ui.js";

// The page and the desktop recorder both send mono, 16-bit WAV so every provider
// can read a recording without another decoder.
export function initRecording({ getModelLabel, onStatus, onUploaded }) {
  const button = el("record");
  const timer = el("rec-timer");
  let recorder = null;
  let tick = null;
  let starting = false;
  let uploading = false;
  let operationId = null;
  let active = null;
  let dropping = false; // dropped audio files are being transcribed, one after another
  let shortcut = false; // a recording the shortcut started: this button stops it too
  const cancel = document.createElement("button");
  cancel.type = "button"; cancel.className = "btn ghost";
  cancel.textContent = "Cancel dictation"; cancel.hidden = true;
  button.after(cancel);
  const labels = {recording: "Recording…", saving: "Saving audio…", transcribing: "Transcribing…", correction: "Checking the dictionary…", cleanup: "Reducing fillers…", formatting: "Formatting…", delivering: "Delivering…", cancelling: "Canceling — keeping audio…", learning: "Preparing dictionary suggestions…", review: "Review the dictionary suggestions to dictate again."};
  let previous = null;
  async function poll() {
    try {
      active = await api("/api/operations");
      const key = active ? `${active.id}:${active.stage}` : null;
      if (key !== previous) {
        // Dropped files keep their own progress line ("Transcribing 2 of 5: name").
        if (active && !dropping) onStatus(labels[active.stage] ?? active.stage);
        else if (previous && !uploading) onStatus("Ready");
        previous = key;
      }
      const wasShortcut = shortcut;
      shortcut = !recorder && active?.kind === "dictation" && active.stage === "recording" && active.source !== "web";
      if (shortcut !== wasShortcut) {
        button.setAttribute("aria-pressed", String(shortcut));
        button.querySelector(".label").textContent = shortcut ? "Stop" : "Record";
      }
      button.disabled = !shortcut && (starting || uploading || Boolean(active && active.id !== operationId));
      cancel.hidden = active?.kind !== "dictation";
      cancel.disabled = active?.stage === "cancelling";
      if (recorder && active?.id === operationId && active.stage === "cancelling") recorder.stop();
    } catch (err) {
      onStatus(errorText(err));
      previous = "error"; // the next answer replaces the error, even an unchanged one
    }
    finally { setTimeout(poll, document.hidden ? 1500 : 400); }
  }
  // Closing or reloading the page loses a recording still in its memory. The capture
  // it opened is closed too, or Entune would refuse every later dictation as busy.
  addEventListener("pagehide", () => {
    if (operationId && !uploading) fetch(`/api/operations/${operationId}`, { method: "DELETE", keepalive: true });
  });
  cancel.addEventListener("click", async () => {
    if (!active) return;
    try { await api(`/api/operations/${active.id}/cancel`, {method: "POST"}); }
    catch (err) { onStatus(errorText(err)); }
  });
  poll();

  // Audio dropped anywhere in the window is transcribed with the default model, one file
  // after another, and lands in History like a dictation.
  const overlay = el("drop-overlay");
  const isAudio = (file) => file.type.startsWith("audio/") || /\.(wav|mp3|m4a|mp4|aac|flac|ogg|oga|opus|webm)$/i.test(file.name);
  const hasFiles = (event) => [...(event.dataTransfer?.types ?? [])].includes("Files");
  let depth = 0;
  addEventListener("dragenter", (event) => { if (hasFiles(event)) { event.preventDefault(); depth++; overlay.hidden = false; } });
  addEventListener("dragover", (event) => { if (hasFiles(event)) event.preventDefault(); });
  addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; overlay.hidden = true; } });
  // Drops queue up: files dropped while others are transcribing wait their turn.
  const dropped = [];
  addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault(); // never open the file in place of the app
    depth = 0; overlay.hidden = true;
    const files = [...event.dataTransfer.files];
    const audio = files.filter(isAudio);
    if (!audio.length) { onStatus("Only audio files can be transcribed."); return; }
    if (recorder || starting || (uploading && !dropping)) { onStatus("Finish the current recording first, then drop the files again."); return; }
    dropped.push(...audio);
    if (files.length > audio.length) onStatus(`${files.length - audio.length} file${files.length - audio.length === 1 ? " is" : "s are"} not audio and will be skipped.`);
    if (!dropping) transcribeDropped();
  });
  async function transcribeDropped() {
    dropping = uploading = true; // the status line stays on the files until they are done
    const tally = { ok: 0, failed: 0, cancelled: 0, other: 0 };
    let number = 0;
    while (dropped.length) {
      const file = dropped.shift();
      number++;
      onStatus(`Transcribing ${number} of ${number + dropped.length}: ${file.name}…`);
      const form = new FormData();
      form.append("audio", file, file.name);
      try {
        const recording = await api("/api/recordings", { method: "POST", body: form });
        // A saved recording can still carry a failed transcription: count what happened.
        const attempt = recording.transcriptions?.[0];
        tally[attempt?.processing_state === "cancelled" ? "cancelled" : attempt?.status === "ok" ? "ok" : attempt?.status === "error" ? "failed" : "other"]++;
        await onUploaded();
      } catch (err) {
        dropped.length = 0;
        dropping = uploading = false;
        onStatus(`${file.name}: ${errorText(err)}`);
        return;
      }
    }
    dropping = uploading = false;
    previous = null; // the summary below stays; the poll has nothing newer to say
    const parts = [`Transcribed ${tally.ok} of ${number} file${number === 1 ? "" : "s"}`];
    if (tally.failed) parts.push(`${tally.failed} failed, see History`);
    if (tally.cancelled) parts.push(`${tally.cancelled} cancelled`);
    if (tally.other) parts.push(`${tally.other} saved without text`);
    onStatus(`${parts.join("; ")}.`);
  }

  function setRecording(on) {
    button.setAttribute("aria-pressed", String(on));
    button.querySelector(".label").textContent = on ? "Stop" : "Record";
    timer.hidden = !on;
    clearInterval(tick);
    if (on) {
      const start = Date.now();
      timer.textContent = "0:00";
      tick = setInterval(() => {
        const seconds = Math.floor((Date.now() - start) / 1000);
        timer.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
      }, 250);
    }
  }

  async function upload(audio) {
    const form = new FormData();
    uploading = true; button.disabled = true;
    form.append("audio", audio, "clip");
    form.append("operation", operationId);
    onStatus(`Transcribing with ${getModelLabel()}…`);
    try {
      const result = await api("/api/recordings", { method: "POST", body: form });
      onStatus(result.notice ?? "Saved to history.");
    } catch (err) {
      onStatus(errorText(err));
      // A request refused before the server claimed the audio leaves the capture open.
      await api(`/api/operations/${operationId}`, {method: "DELETE"}).catch(() => {});
    }
    uploading = false; operationId = null;
    button.disabled = false;
    await onUploaded();
  }

  button.addEventListener("click", async () => {
    if (recorder) { recorder.stop(); return; }
    if (shortcut) {
      button.disabled = true; // until the next poll shows the recording has ended
      try { await api(`/api/operations/${active.id}/stop`, {method: "POST"}); }
      catch (err) { onStatus(errorText(err)); }
      return;
    }
    if (starting || uploading) return;
    starting = true;
    let stream;
    let context;
    let source;
    let tap;
    const release = () => {
      // A failed setup can leave only some of these resources acquired.
      if (tap) { tap.onaudioprocess = null; tap.disconnect(); }
      source?.disconnect();
      stream?.getTracks().forEach((track) => track.stop());
      context?.close().catch(() => {});
    };
    try {
      operationId = (await api("/api/operations", {method: "POST"})).id;
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      context = new AudioContext();
      source = context.createMediaStreamSource(stream);
      tap = context.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      tap.onaudioprocess = (event) => chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      source.connect(tap);
      tap.connect(context.destination);
      const rate = context.sampleRate;
      recorder = {
        stop() {
          release();
          recorder = null;
          setRecording(false);
          upload(wavBlob(chunks, rate)).catch((err) => onStatus(errorText(err)));
        },
      };
      setRecording(true);
      onStatus("Recording…");
    } catch (err) {
      release();
      if (operationId) {
        await api(`/api/operations/${operationId}`, {method: "DELETE"}).catch(() => {});
        operationId = null;
      }
      onStatus(errorText(err));
    } finally {
      starting = false;
    }
  });
}

function wavBlob(chunks, rate) {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => [...text].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  ascii(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (const sample of chunk) {
      const value = Math.max(-1, Math.min(1, sample));
      view.setInt16(offset, value < 0 ? value * 32768 : value * 32767, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}
