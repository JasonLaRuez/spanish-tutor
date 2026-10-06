-- Migration 7: turns.kind gains the reading and lyrics skills' turns (approved by Jason
-- 2026-10-06):
--   study    a batch of words taught before or during reading (role 'tutor'; text_es
--            lists the words). A word looked up while reading is a one-word study turn,
--            so lookups never land on a conversation turn.
--   reading  the learner finished reading (role 'learner'); carries the 'seen' events
--            for the item's known words.
--   attempt  the lyrics skill: the learner's own translation (role 'learner') and the
--            tutor's comment on it (role 'tutor').
-- SQLite can't change a CHECK constraint, so turns is rebuilt: create, copy, drop, rename
-- (SQLite's documented procedure). word_events references turns, so this migration runs
-- with foreign keys off; db.init_schema checks every foreign key before committing.
-- foreign_keys: off
CREATE TABLE turns_new (
    turn_id           INTEGER PRIMARY KEY,
    session_id        INTEGER NOT NULL REFERENCES sessions (session_id),
    turn_no           INTEGER NOT NULL CHECK (turn_no >= 1),
    role              TEXT NOT NULL CHECK (role IN ('learner', 'tutor')),
    kind              TEXT NOT NULL DEFAULT 'conversation'
                      CHECK (kind IN ('conversation', 'translation', 'study', 'reading',
                                      'attempt')),
    text_es           TEXT NOT NULL,
    note_en           TEXT,
    draft_out_of_bank INTEGER CHECK (draft_out_of_bank >= 0),
    final_out_of_bank INTEGER CHECK (final_out_of_bank >= 0),
    retried           INTEGER CHECK (retried IN (0, 1)),
    input_tokens      INTEGER,
    cache_read_tokens INTEGER,
    cache_write_5m_tokens INTEGER,
    cache_write_1h_tokens INTEGER,
    output_tokens     INTEGER,
    latency_ms        INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, turn_no)
);

INSERT INTO turns_new
    (turn_id, session_id, turn_no, role, kind, text_es, note_en, draft_out_of_bank,
     final_out_of_bank, retried, input_tokens, cache_read_tokens, cache_write_5m_tokens,
     cache_write_1h_tokens, output_tokens, latency_ms, created_at)
SELECT turn_id, session_id, turn_no, role, kind, text_es, note_en, draft_out_of_bank,
       final_out_of_bank, retried, input_tokens, cache_read_tokens, cache_write_5m_tokens,
       cache_write_1h_tokens, output_tokens, latency_ms, created_at
FROM turns;

DROP TABLE turns;

ALTER TABLE turns_new RENAME TO turns;
