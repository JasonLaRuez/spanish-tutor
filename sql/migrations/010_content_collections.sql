-- Migration 10 (expanding the library, approved by Jason 2026-10-07): the collection a
-- story, poem or song belongs to (Rimas, Platero y yo, an album), so the library pages can
-- group a few hundred items. Chapters already belong to a book, so they never have one.
--
-- Approved as ALTER TABLE ... ADD COLUMN; done as a rebuild instead, because a new column
-- would land after text_es, which is kept last so that queries reading the other columns
-- never walk a long text's overflow pages. Same column, same CHECK. content_vocab,
-- content_events, word_resolutions and song_translations reference content_items, so this
-- runs with foreign keys off; db.init_schema checks every foreign key before committing.
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
    collection  TEXT CHECK (collection IS NULL OR kind <> 'chapter'),
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

UPDATE content_items SET collection = 'Rimas' WHERE source = 'gutenberg:53552';
