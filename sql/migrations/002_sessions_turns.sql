-- Migration 2: session and turn logs, and word_events.turn_id linking each event to the
-- turn that caused it. New databases get all of this from schema.sql directly; this
-- brings databases created before it to the same shape. The tables are repeated here
-- (rather than left to schema.sql's CREATE TABLE IF NOT EXISTS) because the new
-- word_events column references turns.
CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY,
    skill      TEXT NOT NULL CHECK (skill IN ('conversation', 'lyrics', 'reading')),
    topic      TEXT,
    model      TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id           INTEGER PRIMARY KEY,
    session_id        INTEGER NOT NULL REFERENCES sessions (session_id),
    turn_no           INTEGER NOT NULL CHECK (turn_no >= 1),
    role              TEXT NOT NULL CHECK (role IN ('learner', 'tutor')),
    text_es           TEXT NOT NULL,
    correction_en     TEXT,
    draft_out_of_bank INTEGER CHECK (draft_out_of_bank >= 0),
    final_out_of_bank INTEGER CHECK (final_out_of_bank >= 0),
    retried           INTEGER CHECK (retried IN (0, 1)),
    input_tokens      INTEGER,
    cache_read_tokens INTEGER,
    output_tokens     INTEGER,
    latency_ms        INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, turn_no)
);

ALTER TABLE word_events ADD COLUMN turn_id INTEGER REFERENCES turns (turn_id);

CREATE INDEX IF NOT EXISTS ix_word_events_turn ON word_events (turn_id);
