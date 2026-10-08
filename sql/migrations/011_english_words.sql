-- Migration 11 (approved by Jason 2026-10-08; the kind column added at his request the same
-- day, for English loanwords): English inside songs. Many modern songs mix
-- Spanish and English, and words are resolved one at a time, so an English "come" or "me"
-- would count (and be taught) as Spanish comer or me. When a song is indexed, one model call
-- marks each line's English words in context; they're kept here, and every step that reads
-- the text (indexing, studying, finishing, lookups) skips them on that line.

-- Which items have been checked, so the check runs once per song.
CREATE TABLE IF NOT EXISTS english_checks (
    content_id    INTEGER PRIMARY KEY REFERENCES content_items (content_id),
    model         TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    checked_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The marked words of each line (line_no = the line's number in the analyzed text, as in
-- song_translation_lines), lowercase, by kind: 'english' (the song switching into English:
-- skipped as vocabulary) or 'loanword' (an English word used as Spanish, such as la party:
-- counted and taught, only marked for the reader). A song with none still has its check row.
CREATE TABLE IF NOT EXISTS english_words (
    content_id INTEGER NOT NULL REFERENCES english_checks (content_id),
    line_no    INTEGER NOT NULL CHECK (line_no >= 1),
    word       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('english', 'loanword')),
    PRIMARY KEY (content_id, line_no, word)
) WITHOUT ROWID;
