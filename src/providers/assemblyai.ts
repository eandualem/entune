import { errorFromResponse, unexpectedBody, type Provider } from "./types.ts";

export const assemblyai: Provider = {
  id: "assemblyai",
  name: "AssemblyAI",
  models: ["universal-3-5-pro"],
  async transcribe({ audio, filename, model, apiKey }) {
    const form = new FormData();
    form.append("audio", audio, filename);
    const res = await fetch("https://sync.assemblyai.com/transcribe", {
      method: "POST",
      headers: { Authorization: apiKey, "X-AAI-Model": model },
      body: form,
    });
    if (!res.ok) return { ok: false, error: await errorFromResponse(res) };
    const body = (await res.json()) as { text?: unknown };
    return typeof body.text === "string" ? { ok: true, text: body.text } : unexpectedBody(body);
  },
};
