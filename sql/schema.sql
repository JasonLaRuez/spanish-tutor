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
    -- Where example_en came from when it isn't the example's own source, e.g. a model id
    -- for a model-translated sentence met in a song or book. NULL = Tatoeba's human
    -- translation of the sentence named in example_source.
    example_en_source TEXT,
    UNIQUE (lemma, pos)
);


-- Reviews of lexeme data (by an LLM or a person), for checking definitions,
-- examples and translations. Append-only like word_events: a new review never
-- overwrites an old one, so reviewers can be compared and re-run, and the sourced
-- data in lexemes stays intact. Applying an accepted fix to lexemes is a separate step.
-- A model's verdicts belong to a run (eval_runs), so runs can be compared (migration 12).
CREATE TABLE IF NOT EXISTS lexeme_reviews (
    review_id   INTEGER PRIMARY KEY,
    lexeme_id   INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    field       TEXT NOT NULL CHECK (field IN
                    ('lemma', 'pos', 'definition_en', 'example_es', 'example_en')),
    verdict     TEXT NOT NULL CHECK (verdict IN ('correct', 'incorrect', 'unsure')),
    suggestion  TEXT,                 -- the reviewer's proposed fix, if any
    reason      TEXT,                 -- why, for an incorrect or unsure verdict
    reviewer    TEXT NOT NULL,        -- model id, or 'human'
    run_id      INTEGER REFERENCES eval_runs (run_id),
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((reviewer = 'human') = (run_id IS NULL))   -- models belong to a run; people don't
);

CREATE INDEX IF NOT EXISTS ix_lexeme_reviews_lexeme ON lexeme_reviews (lexeme_id, field);
CREATE INDEX IF NOT EXISTS ix_lexeme_reviews_run ON lexeme_reviews (run_id);


-- One run of a skill (a conversation, a song, a reading session).
CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY,
    skill      TEXT NOT NULL CHECK (skill IN ('conversation', 'lyrics', 'reading')),
    topic      TEXT,
    model      TEXT NOT NULL,                -- LLM model id used for the session
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- When the learner ended it. NULL: still open, or left without ending (abandoned, or
    -- cut off by a server restart).
    ended_at   TEXT
);


-- The tutor's end-of-conversation notes: zero or one row per session. The summary's stats
-- are not stored; sql/queries/session_stats.sql computes them from turns and word_events.
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id        INTEGER PRIMARY KEY REFERENCES sessions (session_id),
    went_well_en      TEXT NOT NULL,   -- what the learner did well (a sentence or two)
    work_on_en        TEXT NOT NULL,   -- what to practice next: 2-3 points, one per line
    model             TEXT NOT NULL,
    input_tokens      INTEGER,         -- the same cost columns turns has
    cache_read_tokens INTEGER,
    output_tokens     INTEGER,
    latency_ms        INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- The transcript, one row per message, with per-turn metrics. Append-only.
-- The adherence columns are the raw data for the Phase 4 vocabulary-adherence metric;
-- the token and latency columns are basic observability. All are NULL on learner turns,
-- and the adherence columns are NULL on translation turns (new words are their point).
CREATE TABLE IF NOT EXISTS turns (
    turn_id           INTEGER PRIMARY KEY,
    session_id        INTEGER NOT NULL REFERENCES sessions (session_id),
    turn_no           INTEGER NOT NULL CHECK (turn_no >= 1),
    role              TEXT NOT NULL CHECK (role IN ('learner', 'tutor')),
    -- conversation: an ordinary exchange.
    -- translation:  the learner asked "¿cómo se dice ...?", pausing the conversation.
    -- study:        words taught before or during reading (a batch, or one looked-up word).
    -- reading:      the learner finished reading an item (carries its 'seen' events).
    -- attempt:      the lyrics skill: the learner's own translation, and the tutor's comment.
    kind              TEXT NOT NULL DEFAULT 'conversation'
                      CHECK (kind IN ('conversation', 'translation', 'study', 'reading',
                                      'attempt')),
    text_es           TEXT NOT NULL,
    note_en           TEXT,              -- a correction, or a translation's explanation
    draft_out_of_bank INTEGER CHECK (draft_out_of_bank >= 0),  -- words outside the bank, first draft
    final_out_of_bank INTEGER CHECK (final_out_of_bank >= 0),  -- ... in the reply shown (all taught)
    retried           INTEGER CHECK (retried IN (0, 1)),
    input_tokens      INTEGER,           -- uncached input tokens, summed over any retry
    cache_read_tokens INTEGER,
    -- How much of input_tokens was written to the cache, by lifetime (5-minute writes cost
    -- 1.25x, 1-hour 2x). NULL on turns from before migration 5.
    cache_write_5m_tokens INTEGER,
    cache_write_1h_tokens INTEGER,
    output_tokens     INTEGER,           -- includes thinking
    latency_ms        INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, turn_no)
);


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
    -- The turn that caused the event; NULL for events outside a session (the seed).
    turn_id     INTEGER REFERENCES turns (turn_id),
    -- Each event type belongs to exactly one mode. Extend this mapping when
    -- adding event types (e.g. a production quiz).
    CHECK (
        (event_type IN ('taught', 'seen', 'looked_up') AND mode = 'recognition')
        OR (event_type = 'used' AND mode = 'production')
    )
);

CREATE INDEX IF NOT EXISTS ix_word_events_lexeme_mode_time
    ON word_events (lexeme_id, mode, occurred_at);

CREATE INDEX IF NOT EXISTS ix_word_events_turn ON word_events (turn_id);


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


-- ===== Content and the difficulty index (Phase 3) =====================================
--
-- Songs, stories and book chapters, each indexed by its full vocabulary so the
-- recommender's ranking is a join against the word bank, not a re-analysis of the text.


-- A book: an ordered set of chapters. Not itself something you read in one sitting, so
-- it isn't a content item; its chapters are. Books are ranked by new-word density across
-- all their chapters, so an easy first chapter can't make a hard book look easy.
CREATE TABLE IF NOT EXISTS books (
    book_id    INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    author     TEXT,
    source     TEXT NOT NULL,          -- 'gutenberg:<ebook no>', or 'private'
    -- 1 = copyrighted: local use only, never in a public demo (Phase 7 filters on it).
    is_private INTEGER NOT NULL CHECK (is_private IN (0, 1)),
    added_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- Anything the learner can read or listen to and the recommender can rank.
-- Chapters take author, source and privacy from their book, so those facts are stored
-- once; songs, poems and stories carry their own. The CASE check enforces that split.
-- Songs and poems use the lyrics skill; stories and chapters the reading skill.
CREATE TABLE IF NOT EXISTS content_items (
    content_id  INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('song', 'poem', 'story', 'chapter')),
    title       TEXT NOT NULL,
    author      TEXT,
    source      TEXT,
    is_private  INTEGER CHECK (is_private IN (0, 1)),
    book_id     INTEGER REFERENCES books (book_id),
    chapter_no  INTEGER CHECK (chapter_no >= 1),
    -- The collection a story, poem or song belongs to (Rimas, Platero y yo, an album),
    -- for grouping in the library; chapters belong to a book instead.
    collection  TEXT CHECK (collection IS NULL OR kind <> 'chapter'),
    -- Filled by the indexer; NULL until indexed (the re-index job's work list).
    -- tokens: running words counted as vocabulary (per analysis: 'del' is two).
    -- unresolved_tokens: words that aren't Spanish vocabulary or couldn't be resolved;
    -- excluded from the cost, and shown so the exclusion is visible.
    tokens            INTEGER CHECK (tokens >= 0),
    unresolved_tokens INTEGER CHECK (unresolved_tokens >= 0),
    analyzer    TEXT,   -- tagger + expression-list fingerprint the index was built with
    indexed_at  TEXT,
    added_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Last column: SQLite reads a long value's overflow pages only when it's asked for,
    -- so the ranking queries never pay for the text.
    text_es     TEXT NOT NULL,
    CHECK (CASE WHEN kind = 'chapter'
                THEN book_id IS NOT NULL AND chapter_no IS NOT NULL
                     AND source IS NULL AND is_private IS NULL
                ELSE book_id IS NULL AND chapter_no IS NULL
                     AND source IS NOT NULL AND is_private IS NOT NULL END),
    UNIQUE (book_id, chapter_no)
);


-- The difficulty index: each item's vocabulary, with how often each word occurs.
-- Derived data, replaced whenever an item is re-indexed (unlike the append-only logs).
-- WITHOUT ROWID: the primary key is the only way in, so no hidden rowid is stored.
CREATE TABLE IF NOT EXISTS content_vocab (
    content_id  INTEGER NOT NULL REFERENCES content_items (content_id),
    lexeme_id   INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    occurrences INTEGER NOT NULL CHECK (occurrences >= 1),
    PRIMARY KEY (content_id, lexeme_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_content_vocab_lexeme ON content_vocab (lexeme_id);


-- Append-only reading and listening history, like word_events. Never UPDATE or DELETE.
-- A book's next chapter is derived from it: the first one after the highest finished.
CREATE TABLE IF NOT EXISTS content_events (
    event_id    INTEGER PRIMARY KEY,
    content_id  INTEGER NOT NULL REFERENCES content_items (content_id),
    event       TEXT NOT NULL CHECK (event IN ('started', 'finished')),
    -- How a started item was chosen: the recommender's default, or the learner's own
    -- request. Raw data for the recommendation-quality metric (Phase 4).
    chosen_via  TEXT CHECK (chosen_via IN ('recommended', 'requested')),
    session_id  INTEGER REFERENCES sessions (session_id),   -- NULL outside a session
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((event = 'started') = (chosen_via IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_content_events_content ON content_events (content_id, event);


-- How a word form neither the lexicon nor Wiktionary knows was resolved, by an LLM (or a
-- person). Append-only like lexeme_reviews: the latest row for a form wins, older ones
-- stay. It is also the cache that keeps each form to one model call.
--   word:        a real word missing from the lexicon; lexeme_id is the row added for it
--   variant:     another spelling of an existing word (fué -> ser, pa' -> para)
--   not_spanish: English, a sound ('la la la'), a name the tagger missed
CREATE TABLE IF NOT EXISTS word_resolutions (
    resolution_id INTEGER PRIMARY KEY,
    form          TEXT NOT NULL,   -- as written, normalized like lexicon.py
    tagged_lemma  TEXT NOT NULL,   -- what the tagger proposed
    tagged_pos    TEXT NOT NULL,
    verdict       TEXT NOT NULL CHECK (verdict IN ('word', 'variant', 'not_spanish')),
    lexeme_id     INTEGER REFERENCES lexemes (lexeme_id),
    reason        TEXT,            -- the reviewer's one-line explanation
    reviewer      TEXT NOT NULL,   -- model id, or 'human'
    content_id    INTEGER REFERENCES content_items (content_id),  -- where it was met
    resolved_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((verdict = 'not_spanish') = (lexeme_id IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_word_resolutions_form
    ON word_resolutions (form, tagged_lemma, tagged_pos);


-- One translation of a song or poem by a model (a run). Append-only: re-translating adds
-- a run, the latest is shown, older ones stay for comparison (the naturalness metric).
-- The lyrics skill stores it once and reuses it in every later session.
CREATE TABLE IF NOT EXISTS song_translations (
    translation_id    INTEGER PRIMARY KEY,
    content_id        INTEGER NOT NULL REFERENCES content_items (content_id),
    model             TEXT NOT NULL,
    input_tokens      INTEGER,      -- the call's cost, as turns record it
    cache_read_tokens INTEGER,
    output_tokens     INTEGER,      -- includes thinking
    latency_ms        INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_song_translations_content
    ON song_translations (content_id, translation_id);


-- Its lines, numbered from 1 in the order the text is analyzed (content.sentences: one
-- per line of verse).
CREATE TABLE IF NOT EXISTS song_translation_lines (
    translation_id INTEGER NOT NULL REFERENCES song_translations (translation_id),
    line_no        INTEGER NOT NULL CHECK (line_no >= 1),
    natural_en     TEXT NOT NULL,   -- idiomatic English: the meaning, as a speaker would say it
    literal_en     TEXT NOT NULL,   -- word for word, idioms translated literally
    note_en        TEXT,            -- the figurative meaning, where the two differ
    PRIMARY KEY (translation_id, line_no)
) WITHOUT ROWID;

-- English inside songs (migration 11): each line's English words, marked once per song by a
-- model call in context, so an English "come" never counts as Spanish comer. Every step
-- that reads the text skips them on that line. A song with none still has its check row.
CREATE TABLE IF NOT EXISTS english_checks (
    content_id    INTEGER PRIMARY KEY REFERENCES content_items (content_id),
    model         TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    checked_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS english_words (
    content_id INTEGER NOT NULL REFERENCES english_checks (content_id),
    line_no    INTEGER NOT NULL CHECK (line_no >= 1),  -- as in song_translation_lines
    word       TEXT NOT NULL,                          -- lowercase
    -- english: the song switching into English (skipped as vocabulary); loanword: an English
    -- word used as Spanish (la party), counted and taught, only marked for the reader.
    kind       TEXT NOT NULL CHECK (kind IN ('english', 'loanword')),
    PRIMARY KEY (content_id, line_no, word)
) WITHOUT ROWID;


-- --- Evaluation (slice 4.4, migration 9) ----------------------------------------------

-- One evaluation pass: a benchmark run (scripted conversations on a database copy), or a
-- batch of judge calls.
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id         INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('benchmark', 'judge')),
    model          TEXT NOT NULL,   -- the tutor's model (benchmark) or the judge's
    prompt_version TEXT,            -- which rubric or prompt, so runs can be compared
    config         TEXT,            -- JSON: repeats, topics, sample sizes...
    started_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note           TEXT
);


-- What gets rated: one row per thing a judge or Jason scores, with exactly what the rater
-- sees. `source_ref` says where it came from ('song_translation_lines:4:2', 'turn:381',
-- 'benchmark:3:turn:57'); it's text, not a foreign key, because items come from several
-- tables and from benchmark copies. `content` (JSON) is a snapshot, so a rating stays
-- interpretable even if its source changes later.
CREATE TABLE IF NOT EXISTS eval_items (
    item_id    INTEGER PRIMARY KEY,
    item_type  TEXT NOT NULL CHECK (item_type IN
                   ('translation_line', 'attempt', 'retrieval', 'reply', 'new_word_flag',
                    'lexeme_flag', 'lexeme_entry')),  -- lexeme_*: the lexicon review (12)
    source_ref TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (item_type, source_ref)
);


-- Append-only: every score, by a model judge (one row per repeat) or by Jason ('human').
-- A re-rating is a new row; the latest one counts. One table for both raters, so judge
-- consistency and judge-vs-human agreement are self-joins.
CREATE TABLE IF NOT EXISTS ratings (
    rating_id     INTEGER PRIMARY KEY,
    item_id       INTEGER NOT NULL REFERENCES eval_items (item_id),
    criterion     TEXT NOT NULL,    -- naturalness, faithfulness, relevance, supported,
                                    -- verdict, truly_new
    rater         TEXT NOT NULL,    -- 'human', or the judge model's id
    run_id        INTEGER REFERENCES eval_runs (run_id),
    repeat_no     INTEGER NOT NULL DEFAULT 1 CHECK (repeat_no >= 1),
    score         REAL,             -- a number on the criterion's scale (e.g. 1-5)
    label         TEXT,             -- or a category (right / close / wrong)
    rationale     TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    rated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (score IS NOT NULL OR label IS NOT NULL),
    CHECK ((rater = 'human') = (run_id IS NULL)),   -- judges belong to a run; Jason doesn't
    UNIQUE (run_id, item_id, criterion, repeat_no)
);

CREATE INDEX IF NOT EXISTS ix_ratings_item ON ratings (item_id, criterion);
