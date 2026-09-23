"""The SQLite schema: settings, recordings, every transcription attempt, learning."""

# Counters from an earlier processing design, still present in databases made then;
# they are ignored when rows are read.
OBSOLETE_COLUMNS = ("jev_seconds", "jev_fixed", "jev_kept", "jev_error")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    file TEXT NOT NULL,
    mime TEXT NOT NULL,
    notice TEXT
);
CREATE TABLE IF NOT EXISTS transcriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recordings(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    text TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    raw_text TEXT,
    audio_seconds REAL,
    elapsed_seconds REAL,
    fast INTEGER NOT NULL DEFAULT 0,
    correction TEXT,
    formatting TEXT,
    cleanup TEXT,
    processing_state TEXT NOT NULL DEFAULT 'complete'
);
CREATE INDEX IF NOT EXISTS transcriptions_by_recording ON transcriptions(recording_id);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    heard TEXT NOT NULL,
    meant TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dictionary_audio (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    created_at TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS learning_runs (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_coverage (
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    input_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (model, source, input_id)
);
"""
