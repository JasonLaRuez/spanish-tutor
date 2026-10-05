-- Migration 4: ending a conversation, and the tutor's notes on it.
-- ended_at tells an ended conversation from one left open (abandoned, or cut off by a
-- server restart), and makes session length plain SQL. The tutor's end-of-conversation
-- notes get their own table: zero or one row per session, with the same cost columns as
-- turns. The summary's stats are not stored; sql/queries/session_stats.sql computes them
-- from turns and word_events, so they can't disagree with the log.
ALTER TABLE sessions ADD COLUMN ended_at TEXT;

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id        INTEGER PRIMARY KEY REFERENCES sessions (session_id),
    went_well_en      TEXT NOT NULL,
    work_on_en        TEXT NOT NULL,
    model             TEXT NOT NULL,
    input_tokens      INTEGER,
    cache_read_tokens INTEGER,
    output_tokens     INTEGER,
    latency_ms        INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
