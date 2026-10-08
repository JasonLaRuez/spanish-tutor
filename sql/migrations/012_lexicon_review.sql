-- Migration 12 (approved by Jason 2026-10-08): the LLM review of the word database.
--
-- lexeme_reviews (empty, never used, nothing references it) is rebuilt to tie each model
-- verdict to its run (eval_runs, so two runs can be compared like the translation judge's
-- repeats) and keep its reason; a human review has no run.
--
-- eval_items gains two rating types: 'lexeme_flag' (a field the reviewer flagged: is the
-- problem real, and is its fix right?) and 'lexeme_entry' (a word it passed: is anything
-- wrong?). SQLite can't change a CHECK, so eval_items is rebuilt; ratings references it,
-- so this runs with foreign keys off, and db.init_schema checks every foreign key before
-- committing, as in migrations 7, 8 and 10.
-- foreign_keys: off

-- lexeme_reviews was added to schema.sql without a migration, so a database older than it
-- gets schema.sql's table only after the migrations run: create its old shape first.
CREATE TABLE IF NOT EXISTS lexeme_reviews (
    review_id   INTEGER PRIMARY KEY,
    lexeme_id   INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    field       TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    suggestion  TEXT,
    reviewer    TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE lexeme_reviews_new (
    review_id   INTEGER PRIMARY KEY,
    lexeme_id   INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    field       TEXT NOT NULL CHECK (field IN
                    ('lemma', 'pos', 'definition_en', 'example_es', 'example_en')),
    verdict     TEXT NOT NULL CHECK (verdict IN ('correct', 'incorrect', 'unsure')),
    suggestion  TEXT,
    reason      TEXT,
    reviewer    TEXT NOT NULL,
    run_id      INTEGER REFERENCES eval_runs (run_id),
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((reviewer = 'human') = (run_id IS NULL))
);

INSERT INTO lexeme_reviews_new (review_id, lexeme_id, field, verdict, suggestion, reviewer, reviewed_at)
SELECT review_id, lexeme_id, field, verdict, suggestion, reviewer, reviewed_at FROM lexeme_reviews;

DROP TABLE lexeme_reviews;

ALTER TABLE lexeme_reviews_new RENAME TO lexeme_reviews;

CREATE INDEX IF NOT EXISTS ix_lexeme_reviews_lexeme ON lexeme_reviews (lexeme_id, field);
CREATE INDEX IF NOT EXISTS ix_lexeme_reviews_run ON lexeme_reviews (run_id);

CREATE TABLE eval_items_new (
    item_id    INTEGER PRIMARY KEY,
    item_type  TEXT NOT NULL CHECK (item_type IN
                   ('translation_line', 'attempt', 'retrieval', 'reply', 'new_word_flag',
                    'lexeme_flag', 'lexeme_entry')),
    source_ref TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (item_type, source_ref)
);

INSERT INTO eval_items_new (item_id, item_type, source_ref, content, created_at)
SELECT item_id, item_type, source_ref, content, created_at FROM eval_items;

DROP TABLE eval_items;

ALTER TABLE eval_items_new RENAME TO eval_items;
