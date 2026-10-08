-- Migration 13: senses kept apart from a word's main definition. The lexicon review found
-- that nearly half the flagged definitions led with (or only gave) a rare, regional, slang,
-- vulgar, archaic or technical sense. Those senses are real, and some matter (slang in
-- songs; a vulgar sense to avoid), so when a definition is fixed to its common sense, its
-- other senses move here, labeled, instead of being lost.
CREATE TABLE IF NOT EXISTS lexeme_senses (
    sense_id   INTEGER PRIMARY KEY,
    lexeme_id  INTEGER NOT NULL REFERENCES lexemes (lexeme_id),
    sense_en   TEXT NOT NULL,
    register   TEXT NOT NULL CHECK (register IN
                   ('rare', 'regional', 'slang', 'vulgar', 'archaic', 'technical')),
    region     TEXT,          -- for a regional sense: Chile, México, Spain...
    source     TEXT NOT NULL, -- where the sense came from (e.g. 'wiktionary')
    reviewer   TEXT,          -- the model that split it out
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_lexeme_senses_lexeme ON lexeme_senses (lexeme_id);
