-- Recompute the word bank from the full event log. Run after changing the
-- familiarity formula (in BOTH the trigger and word_bank_rebuild in schema.sql).
-- word_bank is derived data; deleting it loses nothing that word_events doesn't hold.
BEGIN;
DELETE FROM word_bank;
INSERT INTO word_bank SELECT * FROM word_bank_rebuild;
COMMIT;
