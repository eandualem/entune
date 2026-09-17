type Model = { id: string; label: string; default: boolean };
type Transcription = { id: number; provider: string; model: string; status: "ok" | "error"; text: string | null; error: string | null; created_at: string };
type Recording = { id: number; created_at: string; transcriptions: Transcription[] };
type Settings = { providers: { id: string; name: string; keyHint: string | null }[]; defaultModel: string | null };

const el = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const recordBtn = el<HTMLButtonElement>("record");
const modelSelect = el<HTMLSelectElement>("model");
const status = el("status");
const history = el("history");
const settingsForm = el<HTMLFormElement>("settings-form");
const keysDiv = el("keys");
const defaultSelect = el<HTMLSelectElement>("default-model");
const settingsStatus = el("settings-status");

let models: Model[] = [];

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fillModels(select: HTMLSelectElement, selected: string | null, emptyLabel: string) {
  select.replaceChildren();
  if (models.length === 0) {
    select.append(new Option(emptyLabel, "", true, true));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const m of models) select.append(new Option(m.label, m.id, false, m.id === selected));
}

async function loadModels() {
  models = await api<Model[]>("/api/models");
  const def = models.find((m) => m.default)?.id ?? null;
  fillModels(modelSelect, def, "No models: add an API key in Settings");
  fillModels(defaultSelect, def, "Add an API key first");
  if (def === null && models.length > 0) {
    defaultSelect.prepend(new Option("Not set", "", true, true));
    modelSelect.prepend(new Option("Default (not set)", "", true, true));
  }
}

async function loadSettings() {
  const s = await api<Settings>("/api/settings");
  keysDiv.replaceChildren(
    ...s.providers.map((p) => {
      const label = document.createElement("label");
      label.textContent = `${p.name} API key `;
      const input = document.createElement("input");
      input.type = "password";
      input.name = `key:${p.id}`;
      input.autocomplete = "off";
      input.placeholder = p.keyHint ? `saved (${p.keyHint}); type to replace` : "not set";
      label.append(input);
      return label;
    }),
  );
  await loadModels();
}

settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const keys: Record<string, string> = {};
  for (const input of settingsForm.querySelectorAll<HTMLInputElement>("input[type=password]")) {
    if (input.value.trim()) keys[input.name.slice("key:".length)] = input.value.trim();
  }
  const body: { keys: Record<string, string>; defaultModel?: string | null } = { keys };
  if (!defaultSelect.disabled) body.defaultModel = defaultSelect.value || null;
  settingsStatus.textContent = "Saving…";
  try {
    await api("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    settingsStatus.textContent = "Saved.";
    await loadSettings();
  } catch (err) {
    settingsStatus.textContent = String(err instanceof Error ? err.message : err);
  }
});

// Recording: click to start, click again to stop, then upload and transcribe.
let recorder: MediaRecorder | null = null;

recordBtn.addEventListener("click", async () => {
  if (recorder) {
    recorder.stop();
    return;
  }
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    status.textContent = `Microphone unavailable: ${err instanceof Error ? err.message : err}`;
    return;
  }
  const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
  const chunks: Blob[] = [];
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  recorder.addEventListener("dataavailable", (e) => chunks.push(e.data));
  recorder.addEventListener("stop", () => {
    const type = recorder!.mimeType;
    stream.getTracks().forEach((t) => t.stop());
    recorder = null;
    recordBtn.textContent = "Record";
    void upload(new Blob(chunks, { type }));
  });
  recorder.start();
  recordBtn.textContent = "Stop";
  status.textContent = "Recording…";
});

async function upload(audio: Blob) {
  const form = new FormData();
  form.append("audio", audio, "clip");
  if (modelSelect.value) form.append("model", modelSelect.value);
  const label = modelSelect.selectedOptions[0]?.textContent ?? "default model";
  status.textContent = `Transcribing with ${label}…`;
  try {
    await api("/api/recordings", { method: "POST", body: form });
    status.textContent = "";
  } catch (err) {
    status.textContent = err instanceof Error ? err.message : String(err);
  }
  await loadHistory();
}

async function retry(recording: Recording, modelId: string, card: HTMLElement) {
  card.querySelector(".retry-status")!.textContent = "Transcribing…";
  try {
    await api(`/api/recordings/${recording.id}/transcriptions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: modelId }),
    });
  } catch (err) {
    card.querySelector(".retry-status")!.textContent = err instanceof Error ? err.message : String(err);
  }
  await loadHistory();
}

function attemptLabel(t: Transcription) {
  return `${t.provider} / ${t.model}`;
}

function renderAttempt(t: Transcription): HTMLElement {
  const box = document.createElement("div");
  box.className = `attempt ${t.status}`;
  if (t.status === "ok") {
    const p = document.createElement("p");
    p.className = "text";
    p.textContent = t.text ?? "";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "Copy";
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(t.text ?? "");
      copy.textContent = "Copied";
      setTimeout(() => (copy.textContent = "Copy"), 1500);
    });
    box.append(p, copy);
  } else {
    const pre = document.createElement("pre");
    pre.className = "error";
    pre.textContent = `${attemptLabel(t)} failed:\n${t.error ?? ""}`;
    box.append(pre);
  }
  return box;
}

function renderRecording(r: Recording): HTMLElement {
  const card = document.createElement("article");
  card.className = "recording";
  const head = document.createElement("div");
  head.className = "row";
  const time = document.createElement("time");
  time.dateTime = r.created_at;
  time.textContent = new Date(r.created_at).toLocaleString();
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.preload = "none";
  audio.src = `/api/recordings/${r.id}/audio`;
  head.append(time, audio);
  card.append(head);

  const [latest, ...earlier] = r.transcriptions;
  if (latest) {
    const meta = document.createElement("small");
    meta.textContent = attemptLabel(latest);
    card.append(meta, renderAttempt(latest));
    if (latest.status === "error") {
      const row = document.createElement("div");
      row.className = "row";
      const select = document.createElement("select");
      const other = models.find((m) => m.id !== `${latest.provider}/${latest.model}`)?.id ?? models[0]?.id ?? null;
      fillModels(select, other, "No models: add an API key in Settings");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Retry with a different model";
      btn.disabled = select.disabled;
      btn.addEventListener("click", () => retry(r, select.value, card));
      const rs = document.createElement("span");
      rs.className = "status retry-status";
      row.append(select, btn, rs);
      card.append(row);
    }
  }
  if (earlier.length > 0) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${earlier.length} earlier attempt${earlier.length === 1 ? "" : "s"}`;
    details.append(summary);
    for (const t of earlier) {
      const meta = document.createElement("small");
      meta.textContent = attemptLabel(t);
      details.append(meta, renderAttempt(t));
    }
    card.append(details);
  }
  return card;
}

async function loadHistory() {
  const recordings = await api<Recording[]>("/api/recordings");
  history.replaceChildren(...recordings.map(renderRecording));
  if (recordings.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Nothing recorded yet.";
    history.append(p);
  }
}

await loadSettings();
if (models.length === 0) el<HTMLDetailsElement>("settings").open = true;
await loadHistory();

export {};
