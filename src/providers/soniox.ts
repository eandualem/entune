import { errorFromResponse, unexpectedBody, type Provider, type TranscribeResult } from "./types.ts";

const BASE = "https://api.soniox.com/v1";
const POLL_MS = 500;
const POLL_LIMIT_MS = 120_000;

/**
 * Soniox has no synchronous endpoint: upload the file, create a transcription,
 * poll until it is done, fetch the text. The uploaded file and the transcription
 * are deleted afterwards so the clip does not stay on Soniox's servers.
 */
export const soniox: Provider = {
  id: "soniox",
  name: "Soniox",
  models: ["stt-async-v5"],
  async transcribe({ audio, filename, model, apiKey }) {
    const headers = { Authorization: `Bearer ${apiKey}` };

    const form = new FormData();
    form.append("file", audio, filename);
    const upload = await fetch(`${BASE}/files`, { method: "POST", headers, body: form });
    if (!upload.ok) return { ok: false, error: await errorFromResponse(upload) };
    const file = (await upload.json()) as { id?: unknown };
    if (typeof file.id !== "string") return unexpectedBody(file);

    try {
      const create = await fetch(`${BASE}/transcriptions`, {
        method: "POST",
        headers: { ...headers, "content-type": "application/json" },
        body: JSON.stringify({ file_id: file.id, model }),
      });
      if (!create.ok) return { ok: false, error: await errorFromResponse(create) };
      const job = (await create.json()) as { id?: unknown };
      if (typeof job.id !== "string") return unexpectedBody(job);

      try {
        return await waitForTranscript(job.id, headers);
      } finally {
        void fetch(`${BASE}/transcriptions/${job.id}`, { method: "DELETE", headers }).catch(() => {});
      }
    } finally {
      void fetch(`${BASE}/files/${file.id}`, { method: "DELETE", headers }).catch(() => {});
    }
  },
};

async function waitForTranscript(id: string, headers: Record<string, string>): Promise<TranscribeResult> {
  const deadline = Date.now() + POLL_LIMIT_MS;
  while (Date.now() < deadline) {
    const res = await fetch(`${BASE}/transcriptions/${id}`, { headers });
    if (!res.ok) return { ok: false, error: await errorFromResponse(res) };
    const status = (await res.json()) as { status?: string; error_type?: string | null; error_message?: string | null };
    if (status.status === "completed") {
      const tr = await fetch(`${BASE}/transcriptions/${id}/transcript`, { headers });
      if (!tr.ok) return { ok: false, error: await errorFromResponse(tr) };
      const body = (await tr.json()) as { text?: unknown };
      return typeof body.text === "string" ? { ok: true, text: body.text } : unexpectedBody(body);
    }
    if (status.status === "error") {
      return { ok: false, error: `Transcription failed\n${JSON.stringify(status)}` };
    }
    await Bun.sleep(POLL_MS);
  }
  return { ok: false, error: `Transcription did not finish within ${POLL_LIMIT_MS / 1000} seconds` };
}
