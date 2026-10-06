-- Migration 6: content and the difficulty index (Phase 3).
-- Songs, stories and book chapters (content_items, books), each item's vocabulary with
-- occurrence counts (content_vocab, the difficulty index), what the learner started and
-- finished (content_events, append-only), and how word forms neither the lexicon nor
-- Wiktionary knows were resolved (word_resolutions, append-only). lexemes gains
-- example_en_source, for examples whose English is a model's translation.
-- schema.sql has the commented definitions; these are the same tables.
ALTER TABLE lexemes ADD COLUMN example_en_source TEXT;

CREATE TABLE IF NOT EXISTS books (
    book_id    INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    author     TEXT,
    source     TEXT NOT NULL,
    is_private INTEGER NOT NULL CHECK (is_private IN (0, 1)),
    added_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_items (
    content_id  INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('song', 'story', 'chapter')),
    title       TEXT NOT NULL,
    author      TEXT,
    source      TEXT,
    is_private  INTEGER CHECK (is_private IN (0, 1)),
    book_id     INTEGER REFERENCES books (book_id),
    chapter_no  INTEGER CHECK (chapter_no >= 1),
    tokens            INTEGER CHECK (tokens >= 0),
    unresolved_tokens INTEGER CHECK (unresolved_tokens >= 0),
    analyzer    TEXT,
    indexed_at  TEXT,
    added_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    text_es     TEXT NOT NULL,
    CHECK (CASE WHEN kind = 'chapter'
                THEN book_id IS NOT NULL AND chapter_no IS NOT NULL
                     AND source IS NULL AND is_private IS NULL
                ELSE book_id IS NULL AND chapter_no IS NULL
                     AND source IS NOT NULL AND is_private IS NOT NULL END),
    UNIQUE (book_id, chapter_no)
);

CREATE TABLE IF NOT EXISTS content_vocab (
    content_id  INTEGER NOT NULL REFERENCES content_items (content_id),
    lexeme_id   INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    occurrences INTEGER NOT NULL CHECK (occurrences >= 1),
    PRIMARY KEY (content_id, lexeme_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_content_vocab_lexeme ON content_vocab (lexeme_id);

CREATE TABLE IF NOT EXISTS content_events (
    event_id    INTEGER PRIMARY KEY,
    content_id  INTEGER NOT NULL REFERENCES content_items (content_id),
    event       TEXT NOT NULL CHECK (event IN ('started', 'finished')),
    chosen_via  TEXT CHECK (chosen_via IN ('recommended', 'requested')),
    session_id  INTEGER REFERENCES sessions (session_id),
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((event = 'started') = (chosen_via IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_content_events_content ON content_events (content_id, event);

CREATE TABLE IF NOT EXISTS word_resolutions (
    resolution_id INTEGER PRIMARY KEY,
    form          TEXT NOT NULL,
    tagged_lemma  TEXT NOT NULL,
    tagged_pos    TEXT NOT NULL,
    verdict       TEXT NOT NULL CHECK (verdict IN ('word', 'variant', 'not_spanish')),
    lexeme_id     INTEGER REFERENCES lexemes (lexeme_id),
    reason        TEXT,
    reviewer      TEXT NOT NULL,
    content_id    INTEGER REFERENCES content_items (content_id),
    resolved_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((verdict = 'not_spanish') = (lexeme_id IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_word_resolutions_form
    ON word_resolutions (form, tagged_lemma, tagged_pos);
