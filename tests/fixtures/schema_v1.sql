-- Snapshot of sql/schema.sql at schema version 1 (commit 994e3f2), for migration tests.
-- Never edit: it records what real version-1 databases look like.
-- Word bank schema (SQLite; kept close to ANSI SQL for the Phase 5 platform migration).
--
-- Design: word_events is an append-only log and the single source of truth.
-- word_bank is a TABLE derived from it: a trigger keeps each (lexeme, mode) row
-- current as events arrive, so reads are indexed lookups instead of full-log
-- scans. The view word_bank_rebuild recomputes the same rows from full history;
-- use it to rebuild word_bank whenever the scoring formula changes. Phase 6
-- replaces the placeholder familiarity formula with SM-2.
--
-- The familiarity/encounter logic therefore exists twice (trigger and rebuild
-- view). tests/test_schema.py asserts they agree; change them together.
--
-- Connections must run `PRAGMA foreign_keys = ON;` or REFERENCES is not enforced.
--
-- This file always describes the LATEST schema, for new databases. Existing databases
-- are upgraded by the numbered files in sql/migrations/; PRAGMA user_version records
-- how many have been applied (see db.init_schema). A schema change therefore means
-- editing this file AND adding a migration that brings an older database to the same
-- shape.


-- Every dictionary form the system knows about: taught words AND (from Phase 3)
-- words found in indexed content. A lexeme existing here does NOT mean the
-- learner knows it; that's what word_bank is for.
CREATE TABLE IF NOT EXISTS lexemes (
    lexeme_id     INTEGER PRIMARY KEY,
    -- Normalized by src/spanish_tutor/lexicon.py: NFC, lowercase, accents kept.
    lemma         TEXT NOT NULL CHECK (lemma <> '' AND lemma = trim(lemma)),
    -- Universal Dependencies POS tag as produced by spaCy, plus EXPR for
    -- multi-word expressions ('echar de menos'). AUX is folded into VERB at
    -- ingestion so 'ser' is one lexeme, not two. PROPN/PUNCT/SYM/X are not vocabulary.
    pos           TEXT NOT NULL CHECK (pos IN (
                      'ADJ', 'ADP', 'ADV', 'CCONJ', 'DET', 'INTJ', 'NOUN',
                      'NUM', 'PART', 'PRON', 'SCONJ', 'VERB', 'EXPR')),
    -- Target level from the Plan Curricular del Instituto Cervantes, when known.
    cefr_level    TEXT CHECK (cefr_level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')),
    definition_en TEXT,
    -- Cached Spanish definition. Whether it can be SHOWN depends on the learner's
    -- current word bank, so that check happens at display time, not here.
    definition_es TEXT,
    -- Provenance, required for attribution: Wiktionary is CC BY-SA, and Tatoeba
    -- (CC BY 2.0 FR) requires crediting each sentence's author.
    definition_source TEXT,           -- e.g. 'wiktionary'
    example_es        TEXT,
    example_en        TEXT,
    example_source    TEXT,           -- e.g. 'tatoeba:12345' -> tatoeba.org/en/sentences/show/12345
    example_author    TEXT,           -- Tatoeba username
    -- Estimated occurrences per million words of subtitles (SUBTLEX-ESP form counts
    -- split across lemmas by Tatoeba usage). General data, independent of any learner.
    frequency_per_million REAL CHECK (frequency_per_million >= 0),
    UNIQUE (lemma, pos)
);


-- Reviews of lexeme data (by an LLM or a person), for checking definitions,
-- examples and translations. Append-only like word_events: a new review never
-- overwrites an old one, so reviewers can be compared and re-run, and the sourced
-- data in lexemes stays intact. Applying an accepted fix to lexemes is a separate step.
CREATE TABLE IF NOT EXISTS lexeme_reviews (
    review_id   INTEGER PRIMARY KEY,
    lexeme_id   INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    field       TEXT NOT NULL CHECK (field IN
                    ('lemma', 'pos', 'definition_en', 'example_es', 'example_en')),
    verdict     TEXT NOT NULL CHECK (verdict IN ('correct', 'incorrect', 'unsure')),
    suggestion  TEXT,                 -- the reviewer's proposed fix, if any
    reviewer    TEXT NOT NULL,        -- model id and version, or 'human'
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_lexeme_reviews_lexeme ON lexeme_reviews (lexeme_id, field);


-- Append-only learning history. Never UPDATE or DELETE rows here.
CREATE TABLE IF NOT EXISTS word_events (
    event_id    INTEGER PRIMARY KEY,
    lexeme_id   INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    -- recognition: understanding the word when reading/hearing it
    -- production:  using the word yourself
    mode        TEXT NOT NULL CHECK (mode IN ('recognition', 'production')),
    -- taught:    word introduced and defined (enters the recognition word bank)
    -- seen:      learner encountered a known word in content or model output
    -- looked_up: learner had to ask what a known word means (a recognition miss)
    -- used:      learner used the word in their own writing/speech (enters production)
    event_type  TEXT NOT NULL CHECK (event_type IN ('taught', 'seen', 'looked_up', 'used')),
    source      TEXT NOT NULL CHECK (source IN ('seed', 'pre_teach', 'conversation', 'lyrics', 'reading')),
    -- SM-2-style quality score 0-5, where one can be assigned; NULL otherwise.
    grade       INTEGER CHECK (grade BETWEEN 0 AND 5),
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- ISO-8601, UTC
    -- Each event type belongs to exactly one mode. Extend this mapping when
    -- adding event types (e.g. a production quiz).
    CHECK (
        (event_type IN ('taught', 'seen', 'looked_up') AND mode = 'recognition')
        OR (event_type = 'used' AND mode = 'production')
    )
);

CREATE INDEX IF NOT EXISTS ix_word_events_lexeme_mode_time
    ON word_events (lexeme_id, mode, occurred_at);


-- The word bank: one row per (lexeme, mode) the learner has acquired.
--   recognition rows exist once a word has been taught;
--   production rows exist once the learner has used the word.
-- Derived data, maintained by trg_word_bank_refresh. Never write to it directly.
CREATE TABLE IF NOT EXISTS word_bank (
    lexeme_id    INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    mode         TEXT NOT NULL CHECK (mode IN ('recognition', 'production')),
    learned_at   TEXT NOT NULL,
    learned_via  TEXT NOT NULL,
    -- Placeholder until Phase 6 (SM-2): mean of the last 5 grades, scaled to 0-1.
    -- NULL means "not assessed yet", which is different from 0.
    familiarity  REAL,
    encounters   INTEGER NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (lexeme_id, mode)
);


-- Recompute the affected word_bank row from that word's own events: an indexed
-- range, not the whole log. Recomputing (rather than incrementing counters) keeps
-- the row correct even when events are inserted out of chronological order.
-- SQLite doesn't allow WITH clauses inside triggers, hence the subqueries.
-- CURRENT_TIMESTAMP has one-second resolution, so event_id breaks ordering ties.
CREATE TRIGGER IF NOT EXISTS trg_word_bank_refresh
AFTER INSERT ON word_events
BEGIN
    DELETE FROM word_bank
    WHERE lexeme_id = NEW.lexeme_id AND mode = NEW.mode;

    INSERT INTO word_bank
        (lexeme_id, mode, learned_at, learned_via, familiarity, encounters, last_seen_at)
    SELECT
        NEW.lexeme_id,
        NEW.mode,
        entry.occurred_at,
        entry.source,
        (SELECT AVG(recent.grade) / 5.0
         FROM (SELECT grade
               FROM word_events
               WHERE lexeme_id = NEW.lexeme_id AND mode = NEW.mode AND grade IS NOT NULL
               ORDER BY occurred_at DESC, event_id DESC
               LIMIT 5) AS recent),
        (SELECT COUNT(*)
         FROM word_events
         WHERE lexeme_id = NEW.lexeme_id AND mode = NEW.mode),
        (SELECT MAX(occurred_at)
         FROM word_events
         WHERE lexeme_id = NEW.lexeme_id AND mode = NEW.mode)
    -- No entry event (taught/used) yet means no word_bank row: this yields zero rows.
    FROM (SELECT occurred_at, source
          FROM word_events
          WHERE lexeme_id = NEW.lexeme_id AND mode = NEW.mode
            AND event_type IN ('taught', 'used')
          ORDER BY occurred_at, event_id
          LIMIT 1) AS entry;
END;


-- The same rows as word_bank, recomputed from full history. Slow on a large log
-- (it scans everything), so it is never read on the hot path. Used by
-- sql/rebuild_word_bank.sql after a formula change, and by the tests to check
-- the trigger. Column order matches word_bank.
CREATE VIEW IF NOT EXISTS word_bank_rebuild AS
WITH entry_events AS (
    -- The event that put each (lexeme, mode) into the word bank.
    SELECT
        lexeme_id,
        mode,
        occurred_at,
        source,
        ROW_NUMBER() OVER (
            PARTITION BY lexeme_id, mode
            ORDER BY occurred_at, event_id
        ) AS nth
    FROM word_events
    WHERE event_type IN ('taught', 'used')
),
recent_grades AS (
    SELECT
        lexeme_id,
        mode,
        grade,
        ROW_NUMBER() OVER (
            PARTITION BY lexeme_id, mode
            ORDER BY occurred_at DESC, event_id DESC
        ) AS recency
    FROM word_events
    WHERE grade IS NOT NULL
),
familiarity AS (
    SELECT lexeme_id, mode, AVG(grade) / 5.0 AS familiarity
    FROM recent_grades
    WHERE recency <= 5
    GROUP BY lexeme_id, mode
),
activity AS (
    SELECT
        lexeme_id,
        mode,
        COUNT(*) AS encounters,
        MAX(occurred_at) AS last_seen_at
    FROM word_events
    GROUP BY lexeme_id, mode
)
SELECT
    e.lexeme_id,
    e.mode,
    e.occurred_at AS learned_at,
    e.source AS learned_via,
    f.familiarity,
    a.encounters,
    a.last_seen_at
FROM entry_events AS e
JOIN activity AS a ON a.lexeme_id = e.lexeme_id AND a.mode = e.mode
LEFT JOIN familiarity AS f ON f.lexeme_id = e.lexeme_id AND f.mode = e.mode
WHERE e.nth = 1;
