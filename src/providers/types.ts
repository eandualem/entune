export type TranscribeInput = {
  audio: Blob;
  filename: string;
  model: string;
  apiKey: string;
};

export type TranscribeResult = { ok: true; text: string } | { ok: false; error: string };

export type Provider = {
  id: string;
  name: string;
  models: string[];
  transcribe(input: TranscribeInput): Promise<TranscribeResult>;
};

/** The provider's response, verbatim: status line plus body. */
export async function errorFromResponse(res: Response): Promise<string> {
  return `HTTP ${res.status} ${res.statusText}\n${await res.text()}`.trim();
}

export function unexpectedBody(body: unknown): TranscribeResult {
  return { ok: false, error: `Response had no transcript text\n${JSON.stringify(body)}` };
}
