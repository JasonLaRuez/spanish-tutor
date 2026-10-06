-- Migration 9 (slice 4.4, approved by Jason 2026-10-06): the evaluation framework's
-- tables. Model judges and Jason's own ratings share one append-only `ratings` table, so
-- judge consistency (repeats of one judge) and calibration (judge vs Jason) are both
-- self-joins on it. New tables only: nothing existing changes.

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id         INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('benchmark', 'judge')),
    model          TEXT NOT NULL,
    prompt_version TEXT,
    config         TEXT,
    started_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note           TEXT
);

CREATE TABLE IF NOT EXISTS eval_items (
    item_id    INTEGER PRIMARY KEY,
    item_type  TEXT NOT NULL CHECK (item_type IN
                   ('translation_line', 'attempt', 'retrieval', 'reply', 'new_word_flag')),
    source_ref TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (item_type, source_ref)
);

CREATE TABLE IF NOT EXISTS ratings (
    rating_id     INTEGER PRIMARY KEY,
    item_id       INTEGER NOT NULL REFERENCES eval_items (item_id),
    criterion     TEXT NOT NULL,
    rater         TEXT NOT NULL,
    run_id        INTEGER REFERENCES eval_runs (run_id),
    repeat_no     INTEGER NOT NULL DEFAULT 1 CHECK (repeat_no >= 1),
    score         REAL,
    label         TEXT,
    rationale     TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    rated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (score IS NOT NULL OR label IS NOT NULL),
    CHECK ((rater = 'human') = (run_id IS NULL)),
    UNIQUE (run_id, item_id, criterion, repeat_no)
);

CREATE INDEX IF NOT EXISTS ix_ratings_item ON ratings (item_id, criterion);
