import index from "./index.html";
import { Store, extensionFor, type Recording } from "./db.ts";
import { providers, resolveModel, type ModelRef } from "./providers/index.ts";

const port = Number(process.env.PORT ?? 4187);
const store = new Store(process.env.DICTUM_DATA ?? "data");

const keySetting = (providerId: string) => `key:${providerId}`;
const DEFAULT_MODEL = "default_model";

function maskKey(key: string): string {
  return key.length <= 4 ? "••••" : `••••${key.slice(-4)}`;
}

function availableModels() {
  const def = store.getSetting(DEFAULT_MODEL);
  return providers
    .filter((p) => store.getSetting(keySetting(p.id)) !== null)
    .flatMap((p) =>
      p.models.map((model) => {
        const id = `${p.id}/${model}`;
        return { id, label: `${p.name} / ${model}`, default: id === def };
      }),
    );
}

function bad(message: string, status = 400): Response {
  return new Response(message, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
}

/** Run one transcription attempt and record it. Never throws; every failure becomes a stored error. */
async function transcribe(recording: Recording, ref: ModelRef): Promise<Recording> {
  const apiKey = store.getSetting(keySetting(ref.provider.id));
  let result: { ok: true; text: string } | { ok: false; error: string };
  if (apiKey === null) {
    result = { ok: false, error: `No API key set for ${ref.provider.name}` };
  } else {
    try {
      const audio = new Blob([await Bun.file(store.audioPath(recording)).arrayBuffer()], { type: recording.mime });
      result = await ref.provider.transcribe({ audio, filename: `clip.${extensionFor(recording.mime)}`, model: ref.model, apiKey });
    } catch (e) {
      result = { ok: false, error: e instanceof Error ? `${e.name}: ${e.message}` : String(e) };
    }
  }
  store.addTranscription(recording.id, {
    provider: ref.provider.id,
    model: ref.model,
    status: result.ok ? "ok" : "error",
    text: result.ok ? result.text : null,
    error: result.ok ? null : result.error,
  });
  return store.getRecording(recording.id)!;
}

const server = Bun.serve({
  port,
  routes: {
    "/": index,

    "/api/settings": {
      GET: () =>
        Response.json({
          providers: providers.map((p) => {
            const key = store.getSetting(keySetting(p.id));
            return { id: p.id, name: p.name, keyHint: key === null ? null : maskKey(key) };
          }),
          defaultModel: store.getSetting(DEFAULT_MODEL),
        }),
      PUT: async (req) => {
        const body = (await req.json()) as { keys?: Record<string, string>; defaultModel?: string | null };
        for (const [providerId, key] of Object.entries(body.keys ?? {})) {
          if (!providers.some((p) => p.id === providerId)) return bad(`Unknown provider: ${providerId}`);
          if (typeof key !== "string" || key.trim() === "") return bad(`Empty key for ${providerId}`);
          store.setSetting(keySetting(providerId), key.trim());
        }
        if (body.defaultModel !== undefined) {
          if (body.defaultModel !== null && !resolveModel(body.defaultModel)) return bad(`Unknown model: ${body.defaultModel}`);
          store.setSetting(DEFAULT_MODEL, body.defaultModel);
        }
        return Response.json({ ok: true });
      },
    },

    "/api/models": () => Response.json(availableModels()),

    "/api/recordings": {
      GET: () => Response.json(store.listRecordings()),
      POST: async (req) => {
        const form = await req.formData();
        const audio = form.get("audio");
        if (!(audio instanceof Blob) || audio.size === 0) return bad("No audio in request");
        const modelId = form.get("model");
        const chosen = typeof modelId === "string" && modelId !== "" ? modelId : store.getSetting(DEFAULT_MODEL);
        if (chosen === null) return bad("No default model is set. Pick one in Settings.");
        const ref = resolveModel(chosen);
        if (!ref) return bad(`Unknown model: ${chosen}`);
        const recording = await store.createRecording(audio, audio.type || "application/octet-stream");
        return Response.json(await transcribe(recording, ref));
      },
    },

    "/api/recordings/:id/transcriptions": {
      POST: async (req) => {
        const recording = store.getRecording(Number(req.params.id));
        if (!recording) return bad("No such recording", 404);
        const body = (await req.json()) as { model?: string };
        const ref = typeof body.model === "string" ? resolveModel(body.model) : null;
        if (!ref) return bad(`Unknown model: ${body.model}`);
        return Response.json(await transcribe(recording, ref));
      },
    },

    "/api/recordings/:id/audio": (req) => {
      const recording = store.getRecording(Number(req.params.id));
      if (!recording) return bad("No such recording", 404);
      return new Response(Bun.file(store.audioPath(recording)), { headers: { "content-type": recording.mime } });
    },
  },
});

console.log(`Dictum listening on ${server.url}`);
