import { assemblyai } from "./assemblyai.ts";
import { groq } from "./groq.ts";
import { soniox } from "./soniox.ts";
import type { Provider } from "./types.ts";

export const providers: Provider[] = [assemblyai, groq, soniox];

export type ModelRef = { provider: Provider; model: string; id: string };

/** Resolve a "provider/model" id to a known provider and one of its models. */
export function resolveModel(id: string): ModelRef | null {
  const slash = id.indexOf("/");
  if (slash < 0) return null;
  const provider = providers.find((p) => p.id === id.slice(0, slash));
  const model = id.slice(slash + 1);
  return provider && provider.models.includes(model) ? { provider, model, id } : null;
}
