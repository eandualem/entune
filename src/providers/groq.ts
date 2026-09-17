import { errorFromResponse, unexpectedBody, type Provider } from "./types.ts";

export const groq: Provider = {
  id: "groq",
  name: "Groq",
  models: ["whisper-large-v3-turbo"],
  async transcribe({ audio, filename, model, apiKey }) {
    const form = new FormData();
    form.append("file", audio, filename);
    form.append("model", model);
    form.append("response_format", "json");
    const res = await fetch("https://api.groq.com/openai/v1/audio/transcriptions", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}` },
      body: form,
    });
    if (!res.ok) return { ok: false, error: await errorFromResponse(res) };
    const body = (await res.json()) as { text?: unknown };
    return typeof body.text === "string" ? { ok: true, text: body.text } : unexpectedBody(body);
  },
};
