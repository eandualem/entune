import { afterEach, expect, test } from "bun:test";
import { assemblyai } from "../src/providers/assemblyai.ts";
import { groq } from "../src/providers/groq.ts";
import { resolveModel } from "../src/providers/index.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function stubFetch(reply: (req: Request) => Response) {
  const calls: Request[] = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const req = new Request(input, init);
    calls.push(req);
    return reply(req);
  }) as typeof fetch;
  return calls;
}

const input = { audio: new Blob(["x"], { type: "audio/webm" }), filename: "clip.webm", apiKey: "k" };

test("AssemblyAI sends multipart audio with the model header and returns the text", async () => {
  const calls = stubFetch(() => Response.json({ text: "hello", confidence: 0.9 }));
  const result = await assemblyai.transcribe({ ...input, model: "universal-3-5-pro" });
  expect(result).toEqual({ ok: true, text: "hello" });
  const req = calls[0]!;
  expect(req.url).toBe("https://sync.assemblyai.com/transcribe");
  expect(req.headers.get("authorization")).toBe("k");
  expect(req.headers.get("x-aai-model")).toBe("universal-3-5-pro");
  expect((await req.formData()).get("audio")).toBeInstanceOf(Blob);
});

test("Groq sends the OpenAI-style form and returns the text", async () => {
  const calls = stubFetch(() => Response.json({ text: "hi" }));
  const result = await groq.transcribe({ ...input, model: "whisper-large-v3-turbo" });
  expect(result).toEqual({ ok: true, text: "hi" });
  const form = await calls[0]!.formData();
  expect(calls[0]!.headers.get("authorization")).toBe("Bearer k");
  expect(form.get("model")).toBe("whisper-large-v3-turbo");
  expect(form.get("file")).toBeInstanceOf(Blob);
});

test("a failed response is returned verbatim, status line and body", async () => {
  stubFetch(() => new Response('{"error":{"message":"Invalid API Key"}}', { status: 401, statusText: "Unauthorized" }));
  expect(await groq.transcribe({ ...input, model: "whisper-large-v3-turbo" })).toEqual({
    ok: false,
    error: 'HTTP 401 Unauthorized\n{"error":{"message":"Invalid API Key"}}',
  });
});

test("model ids resolve only to known provider/model pairs", () => {
  expect(resolveModel("groq/whisper-large-v3-turbo")?.provider.id).toBe("groq");
  expect(resolveModel("groq/nope")).toBeNull();
  expect(resolveModel("whisper")).toBeNull();
});

test("Soniox uploads, creates a job, polls to completion and fetches the text", async () => {
  const { soniox } = await import("../src/providers/soniox.ts");
  let polls = 0;
  const calls = stubFetch((req) => {
    const path = new URL(req.url).pathname;
    if (req.method === "DELETE") return new Response(null, { status: 204 });
    if (path === "/v1/files") return Response.json({ id: "f1" }, { status: 201 });
    if (path === "/v1/transcriptions") return Response.json({ id: "t1", status: "queued" }, { status: 201 });
    if (path === "/v1/transcriptions/t1") return Response.json({ id: "t1", status: ++polls < 2 ? "processing" : "completed" });
    if (path === "/v1/transcriptions/t1/transcript") return Response.json({ id: "t1", text: "hey", tokens: [] });
    return new Response("unexpected", { status: 500 });
  });
  const result = await soniox.transcribe({ ...input, model: "stt-async-v5" });
  expect(result).toEqual({ ok: true, text: "hey" });
  expect(calls[0]!.headers.get("authorization")).toBe("Bearer k");
  expect(await calls[1]!.json()).toEqual({ file_id: "f1", model: "stt-async-v5" });
  expect(calls.filter((c) => c.method === "DELETE").map((c) => new URL(c.url).pathname).sort()).toEqual(["/v1/files/f1", "/v1/transcriptions/t1"]);
});
