-- Migration 8 (slice 4.3, approved by Jason 2026-10-06): poems as their own kind of
-- content, and stored translations of songs and poems.
--
-- content_items.kind gains 'poem' (Bécquer's Rimas). A poem stands alone like a song or a
-- story. SQLite can't change a CHECK constraint, so content_items is rebuilt (create,
-- copy, drop, rename). content_vocab, content_events and word_resolutions reference it,
-- so this migration runs with foreign keys off; db.init_schema checks every foreign key
-- before committing.
-- foreign_keys: off
CREATE TABLE content_items_new (
    content_id  INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('song', 'poem', 'story', 'chapter')),
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

INSERT INTO content_items_new
    (content_id, kind, title, author, source, is_private, book_id, chapter_no, tokens,
     unresolved_tokens, analyzer, indexed_at, added_at, text_es)
SELECT content_id, kind, title, author, source, is_private, book_id, chapter_no, tokens,
       unresolved_tokens, analyzer, indexed_at, added_at, text_es
FROM content_items;

DROP TABLE content_items;

ALTER TABLE content_items_new RENAME TO content_items;

CREATE TABLE IF NOT EXISTS song_translations (
    translation_id    INTEGER PRIMARY KEY,
    content_id        INTEGER NOT NULL REFERENCES content_items (content_id),
    model             TEXT NOT NULL,
    input_tokens      INTEGER,
    cache_read_tokens INTEGER,
    output_tokens     INTEGER,
    latency_ms        INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_song_translations_content
    ON song_translations (content_id, translation_id);

CREATE TABLE IF NOT EXISTS song_translation_lines (
    translation_id INTEGER NOT NULL REFERENCES song_translations (translation_id),
    line_no        INTEGER NOT NULL CHECK (line_no >= 1),
    natural_en     TEXT NOT NULL,
    literal_en     TEXT NOT NULL,
    note_en        TEXT,
    PRIMARY KEY (translation_id, line_no)
) WITHOUT ROWID;
