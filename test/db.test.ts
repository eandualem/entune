import { expect, test } from "bun:test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Store, extensionFor } from "../src/db.ts";

test("settings, recordings and transcriptions persist and list newest first", async () => {
  const dir = mkdtempSync(join(tmpdir(), "dictum-"));
  const store = new Store(dir);
  expect(store.getSetting("default_model")).toBeNull();
  store.setSetting("default_model", "groq/whisper-large-v3-turbo");

  const first = await store.createRecording(new Blob(["a"], { type: "audio/webm" }), "audio/webm;codecs=opus");
  const second = await store.createRecording(new Blob(["b"]), "audio/mp4");
  store.addTranscription(first.id, { provider: "groq", model: "whisper-large-v3-turbo", status: "error", text: null, error: "HTTP 401 Unauthorized\n{}" });
  store.addTranscription(first.id, { provider: "assemblyai", model: "universal-3-5-pro", status: "ok", text: "hello", error: null });

  const reopened = new Store(dir);
  expect(reopened.getSetting("default_model")).toBe("groq/whisper-large-v3-turbo");
  const list = reopened.listRecordings();
  expect(list.map((r) => r.id)).toEqual([second.id, first.id]);
  expect(list[1]!.transcriptions.map((t) => t.status)).toEqual(["ok", "error"]);
  expect(await Bun.file(reopened.audioPath(first)).text()).toBe("a");
});

test("file extension follows the mime type", () => {
  expect(extensionFor("audio/webm;codecs=opus")).toBe("webm");
  expect(extensionFor("audio/mp4")).toBe("m4a");
  expect(extensionFor("application/octet-stream")).toBe("audio");
});
