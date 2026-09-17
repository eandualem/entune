import { Database } from "bun:sqlite";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

export type Transcription = {
  id: number;
  recording_id: number;
  provider: string;
  model: string;
  status: "ok" | "error";
  text: string | null;
  error: string | null;
  created_at: string;
};

export type Recording = {
  id: number;
  created_at: string;
  file: string;
  mime: string;
  transcriptions: Transcription[];
};

const EXT_BY_MIME: Record<string, string> = {
  "audio/webm": "webm",
  "video/webm": "webm",
  "audio/ogg": "ogg",
  "audio/mp4": "m4a",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
  "audio/mpeg": "mp3",
  "audio/flac": "flac",
};

/** Identify the audio container from its first bytes, or null if it is not one we know. */
export function sniffAudioMime(bytes: Uint8Array): string | null {
  const ascii = (start: number, len: number) => String.fromCharCode(...bytes.subarray(start, start + len));
  if (bytes.length < 12) return null;
  if (bytes[0] === 0x1a && bytes[1] === 0x45 && bytes[2] === 0xdf && bytes[3] === 0xa3) return "audio/webm";
  if (ascii(0, 4) === "OggS") return "audio/ogg";
  if (ascii(0, 4) === "RIFF" && ascii(8, 4) === "WAVE") return "audio/wav";
  if (ascii(4, 4) === "ftyp") return "audio/mp4";
  if (ascii(0, 4) === "fLaC") return "audio/flac";
  if (ascii(0, 3) === "ID3" || (bytes[0] === 0xff && (bytes[1]! & 0xe0) === 0xe0)) return "audio/mpeg";
  return null;
}

export function extensionFor(mime: string): string {
  const base = mime.split(";")[0]!.trim().toLowerCase();
  return EXT_BY_MIME[base] ?? "audio";
}

export class Store {
  readonly db: Database;
  readonly audioDir: string;

  constructor(dataDir: string) {
    this.audioDir = join(dataDir, "audio");
    mkdirSync(this.audioDir, { recursive: true });
    this.db = new Database(join(dataDir, "dictum.db"), { create: true });
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS recordings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        file TEXT NOT NULL,
        mime TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS transcriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recording_id INTEGER NOT NULL REFERENCES recordings(id),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
        text TEXT,
        error TEXT,
        created_at TEXT NOT NULL
      );
    `);
  }

  getSetting(key: string): string | null {
    const row = this.db.query<{ value: string }, [string]>("SELECT value FROM settings WHERE key = ?").get(key);
    return row?.value ?? null;
  }

  setSetting(key: string, value: string | null): void {
    if (value === null) {
      this.db.run("DELETE FROM settings WHERE key = ?", [key]);
    } else {
      this.db.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", [key, value]);
    }
  }

  audioPath(recording: { file: string }): string {
    return join(this.audioDir, recording.file);
  }

  /** Store a clip. The container is read from the bytes; the label the browser sent is only a fallback. */
  async createRecording(audio: Blob): Promise<Recording> {
    const bytes = new Uint8Array(await audio.arrayBuffer());
    const mime = sniffAudioMime(bytes) ?? (audio.type || "application/octet-stream");
    const file = `${crypto.randomUUID()}.${extensionFor(mime)}`;
    await Bun.write(join(this.audioDir, file), bytes);
    const created_at = new Date().toISOString();
    const { lastInsertRowid } = this.db.run("INSERT INTO recordings (created_at, file, mime) VALUES (?, ?, ?)", [created_at, file, mime]);
    return { id: Number(lastInsertRowid), created_at, file, mime, transcriptions: [] };
  }

  addTranscription(recordingId: number, t: Pick<Transcription, "provider" | "model" | "status" | "text" | "error">): void {
    this.db.run(
      "INSERT INTO transcriptions (recording_id, provider, model, status, text, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      [recordingId, t.provider, t.model, t.status, t.text, t.error, new Date().toISOString()],
    );
  }

  getRecording(id: number): Recording | null {
    const row = this.db.query<Omit<Recording, "transcriptions">, [number]>("SELECT id, created_at, file, mime FROM recordings WHERE id = ?").get(id);
    return row ? { ...row, transcriptions: this.transcriptionsFor(row.id) } : null;
  }

  listRecordings(): Recording[] {
    const rows = this.db.query<Omit<Recording, "transcriptions">, []>("SELECT id, created_at, file, mime FROM recordings ORDER BY id DESC").all();
    return rows.map((row) => ({ ...row, transcriptions: this.transcriptionsFor(row.id) }));
  }

  private transcriptionsFor(recordingId: number): Transcription[] {
    return this.db.query<Transcription, [number]>("SELECT * FROM transcriptions WHERE recording_id = ? ORDER BY id DESC").all(recordingId);
  }
}
