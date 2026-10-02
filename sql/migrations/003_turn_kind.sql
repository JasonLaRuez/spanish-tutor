-- Migration 3: mark what kind of exchange a turn belongs to, and generalize the note.
-- A "cómo se dice" translation pauses the conversation for a turn. New words are the
-- point of that turn, so it must be excluded from the vocabulary-adherence metric;
-- `kind` marks it. Its English explanation goes in the same column as a correction, so
-- correction_en becomes note_en. Existing rows are all conversation turns.
ALTER TABLE turns RENAME COLUMN correction_en TO note_en;

ALTER TABLE turns ADD COLUMN kind TEXT NOT NULL DEFAULT 'conversation'
    CHECK (kind IN ('conversation', 'translation'));
