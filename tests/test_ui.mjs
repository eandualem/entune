import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Load the browser module without requiring a package-wide Node module setting.
globalThis.navigator ??= { userAgent: "" };
const source = readFileSync(new URL("../src/entune/web/ui.js", import.meta.url));
const { modelName } = await import(`data:text/javascript;base64,${source.toString("base64")}`);

test("model labels remove only a literal provider prefix and separator", () => {
  assert.equal(modelName("parakeet/PARAKEET-tdt", []), "parakeet · tdt");
  assert.equal(modelName("groq/groq_whisper", []), "groq · whisper");
  assert.equal(modelName("groq/groqwhisper", []), "groq · groqwhisper");
  assert.equal(modelName("[/model", []), "[ · model");
  assert.equal(modelName("a.b/axb-model", []), "a.b · axb-model");
  assert.equal(modelName("a.b/a.b-model", []), "a.b · model");
  const subject = "a".repeat(50_000) + "!";
  assert.equal(modelName(`(a+)+/${subject}`, []), `(a+)+ · ${subject}`);
});
