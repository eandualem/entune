// History card rendering keeps each player and retry picker attached to its card.

import { ICON, errorText, fillModels, whenLabel } from "./ui.js";

const attemptLabel = (t) => (t.provider === "none" ? "no model set" : `${t.provider} / ${t.model}`);
const clock = (s) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "–:––");

function player(url, label, seconds) {
  const box = document.createElement("div");
  box.className = "player";
  const audio = document.createElement("audio");
  audio.preload = "none";
  audio.setAttribute("aria-label", label);
  const play = document.createElement("button");
  play.type = "button";
  play.className = "btn-icon";
  play.title = "Play";
  play.innerHTML = ICON.play;
  const now = document.createElement("span");
  now.textContent = "0:00";
  const track = document.createElement("div");
  track.className = "track";
  track.innerHTML = "<span></span>";
  const total = document.createElement("span");
  total.textContent = clock(seconds);
  for (const event of ["loadedmetadata", "durationchange"]) audio.addEventListener(event, () => { total.textContent = clock(audio.duration); });
  audio.addEventListener("timeupdate", () => {
    now.textContent = clock(audio.currentTime);
    track.firstChild.style.width = audio.duration ? `${(audio.currentTime / audio.duration) * 100}%` : "0";
  });
  audio.addEventListener("play", () => { play.innerHTML = ICON.pause; play.title = "Pause"; });
  audio.addEventListener("pause", () => { play.innerHTML = ICON.play; play.title = "Play"; });
  audio.addEventListener("ended", () => { audio.currentTime = 0; });
  play.addEventListener("click", async () => {
    if (!audio.src) audio.src = url;
    try { audio.paused ? await audio.play() : audio.pause(); }
    catch (err) { play.title = `Playback failed: ${errorText(err)}`; }
  });
  track.addEventListener("click", (e) => {
    const rect = track.getBoundingClientRect();
    if (audio.duration) audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
  });
  box.append(audio, play, now, track, total);
  return box;
}

// Stage counts describe processing, not the accuracy of the delivered words.
function processingLines(t) {
  const lines = [];
  for (const [name, stage] of [["Dictionary", t.correction], ["Formatting", t.formatting]]) {
    if (!stage || stage.status === "disabled") continue;
    const line = document.createElement("div");
    line.className = stage.status === "failed" ? "jev err" : "jev";
    line.setAttribute("role", "status");
    const parts = [name, stage.status];
    if (stage.status === "failed") {
      parts.push(name === "Dictionary" ? "original transcription retained" : "prior text retained", stage.error);
    } else if (name === "Dictionary" && stage.status === "succeeded") {
      parts.push(`${stage.replacements} replacement${stage.replacements === 1 ? "" : "s"}`);
      if (stage.method === "contextual") parts.push(`${stage.preserved} preserved`, `${stage.decisions} contextual decision${stage.decisions === 1 ? "" : "s"}`);
      else parts.push(stage.method === "unconditional" ? "historical unconditional" : "approved direct mappings only");
      parts.push(`${stage.direct_replacements ?? 0} direct`, `${stage.abstained} unresolved`);
    }
    if (stage.attempts > 1) parts.push(`${stage.attempts - 1} ${stage.attempts === 2 ? "retry" : "retries"}`);
    if (stage.seconds) parts.push(`+${stage.seconds.toFixed(1)} s`);
    line.textContent = parts.join(" · ");
    lines.push(line);
  }
  if (t.legacy_processing) {
    const line = document.createElement("div");
    line.className = "jev";
    line.textContent = "Legacy processing record · excluded from current metrics";
    lines.push(line);
  }
  return lines;
}

export function renderCard(r, models) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.id = r.id;
  const [latest, ...earlier] = r.transcriptions;

  const head = document.createElement("div");
  head.className = "card-head";
  const when = document.createElement("time");
  when.className = "when";
  when.dateTime = r.created_at;
  when.textContent = whenLabel(r.created_at);
  when.title = new Date(r.created_at).toLocaleString();
  const model = document.createElement("span");
  model.className = "model";
  model.textContent = latest ? attemptLabel(latest) : "";
  const spacer = document.createElement("span");
  spacer.className = "spacer";
  const copied = document.createElement("span");
  copied.className = "copied-tag";
  copied.textContent = "Copied";
  head.append(when, model, spacer, copied);
  card.append(head);

  if (latest && latest.status !== "ok") {
    const failed = document.createElement("div");
    failed.className = "failed";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = "FAILED";
    const pre = document.createElement("pre");
    pre.textContent = latest.error ?? "";
    failed.append(label, pre);
    card.append(failed);
    card.dataset.copy = latest.error ?? "";
  } else if (latest) {
    const block = document.createElement("div");
    block.className = latest.text ? "transcript" : "transcript empty";
    block.textContent = latest.text || "(no speech detected)";
    block.title = latest.text ? "Click to copy" : "";
    card.append(block);
    card.dataset.copy = latest.text ?? "";
    card.append(...processingLines(latest));
  }

  const row = document.createElement("div");
  row.className = "card-row";
  row.append(player(`/api/recordings/${r.id}/audio`, `Recording ${when.title}`, latest?.audio_seconds));
  const download = document.createElement("a");
  download.className = "btn-icon";
  download.href = `/api/recordings/${r.id}/audio`;
  download.download = "";
  download.title = "Download audio";
  download.setAttribute("aria-label", "Download audio");
  download.innerHTML = ICON.download;
  const rowStatus = document.createElement("span");
  rowStatus.className = "status";
  const rowSpacer = document.createElement("span");
  rowSpacer.className = "spacer";
  row.append(download, rowStatus, rowSpacer);
  if (latest?.raw_text !== null && latest?.raw_text !== undefined) {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "btn ghost copy-raw";
    copy.textContent = "Copy original";
    copy.title = "Copy the untouched speech-provider result";
    card.dataset.raw = latest.raw_text;
    row.append(copy);
    if (latest.correction?.status === "failed") {
      const recover = document.createElement("button"); recover.type = "button";
      recover.className = "btn ghost safe-copy"; recover.textContent = "Apply safe mappings and copy";
      recover.dataset.attempt = latest.id;
      recover.title = "Use only explicitly approved direct mappings. Ambiguous words stay original; history and already-pasted text are unchanged.";
      row.append(recover);
    }

  }
  let attempts = null;
  if (earlier.length > 0) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn ghost attempts-toggle";
    toggle.title = earlier.length === 1 ? `1 earlier attempt · ${attemptLabel(earlier[0])}` : `${earlier.length} earlier attempts`;
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = `${ICON.attempts}${earlier.length}`;
    attempts = document.createElement("div");
    attempts.hidden = true;
    for (const t of earlier) {
      const attempt = document.createElement("div");
      attempt.className = "attempt";
      const meta = document.createElement("div");
      meta.className = "meta";
      const time = document.createElement("time");
      time.dateTime = t.created_at;
      time.textContent = whenLabel(t.created_at);
      meta.append(`${attemptLabel(t)} · `, time);
      const text = document.createElement("div");
      text.className = t.status === "ok" ? "text" : "text err";
      text.textContent = t.status === "ok" ? t.text || "(no speech detected)" : t.error ?? "";
      attempt.append(meta, text, ...processingLines(t));
      attempts.append(attempt);
    }
    toggle.addEventListener("click", () => {
      attempts.hidden = !attempts.hidden;
      toggle.setAttribute("aria-expanded", String(!attempts.hidden));
    });
    row.append(toggle);
  }
  const select = document.createElement("select");
  select.className = "select quiet retry-model";
  select.title = "Transcribe again with…";
  select.setAttribute("aria-label", "Model for re-transcription");
  const current = latest ? `${latest.provider}/${latest.model}` : null;
  fillModels(models, select, models.find((m) => m.id !== current)?.id ?? models[0]?.id ?? null, "No models");
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "btn-icon retry";
  retry.title = latest?.status === "error" ? "Try again" : "Transcribe again";
  retry.setAttribute("aria-label", retry.title);
  retry.innerHTML = ICON.retry;
  retry.disabled = select.disabled;
  row.append(select, retry);
  card.append(row);
  if (attempts) card.append(attempts);
  return card;
}
