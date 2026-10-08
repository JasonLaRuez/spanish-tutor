-- Merge the general lexicon into lexemes.
--
-- Reads lexicon_staging, a TEMP table filled by src/spanish_tutor/ingest/build_lexicon.py
-- with one row per (lemma, pos). Rules:
--   * Referenced rows are never deleted, so lexeme_ids (and the learner's word_events
--     that reference them) stay valid across rebuilds. A row this build no longer
--     produces is deleted only if nothing references it.
--   * Frequencies are derived data, recomputed on every build, so they are overwritten,
--     and cleared on kept rows the build no longer produces.
--   * Definitions and examples only fill empty fields, so values already present
--     (seeded examples, later reviewed fixes) are never replaced.
--   * The example fields move as a unit, so a sentence is never paired with another
--     sentence's translation or author. Lexicon examples are Tatoeba's, with a human
--     translation, so a filled example has no example_en_source.
INSERT INTO lexemes
    (lemma, pos, frequency_per_million, definition_en, definition_source,
     example_es, example_en, example_source, example_author)
SELECT lemma, pos, frequency_per_million, definition_en, definition_source,
       example_es, example_en, example_source, example_author
FROM lexicon_staging
WHERE true  -- SQLite needs a WHERE here to parse ON CONFLICT after a SELECT
ON CONFLICT (lemma, pos) DO UPDATE SET
    frequency_per_million = excluded.frequency_per_million,
    definition_en     = COALESCE(lexemes.definition_en, excluded.definition_en),
    definition_source = CASE WHEN lexemes.definition_en IS NULL
                             THEN excluded.definition_source ELSE lexemes.definition_source END,
    example_es        = CASE WHEN lexemes.example_es IS NULL
                             THEN excluded.example_es ELSE lexemes.example_es END,
    example_en        = CASE WHEN lexemes.example_es IS NULL
                             THEN excluded.example_en ELSE lexemes.example_en END,
    example_source    = CASE WHEN lexemes.example_es IS NULL
                             THEN excluded.example_source ELSE lexemes.example_source END,
    example_author    = CASE WHEN lexemes.example_es IS NULL
                             THEN excluded.example_author ELSE lexemes.example_author END,
    example_en_source = CASE WHEN lexemes.example_es IS NULL
                             THEN NULL ELSE lexemes.example_en_source END;

-- Rows this build no longer produces: entries from an earlier build that a pipeline fix
-- has since corrected away (e.g. a misspelling such as "tambien" now respelled
-- "también"). Delete them only when nothing references them, so every lexeme_id that has
-- learner history, a review, a place in indexed content, a word resolution or a kept sense
-- survives
-- (the last two include words added from Wiktionary or by a model while indexing, which
-- no build produces). word_bank needs no check: it only has rows for words with events. If a new table ever references lexemes and isn't listed here, the
-- foreign key makes this DELETE fail and the whole build rolls back, rather than
-- silently deleting a row someone points at.
DELETE FROM lexemes
WHERE NOT EXISTS (SELECT 1 FROM lexicon_staging AS s
                  WHERE s.lemma = lexemes.lemma AND s.pos = lexemes.pos)
  AND NOT EXISTS (SELECT 1 FROM word_events AS e WHERE e.lexeme_id = lexemes.lexeme_id)
  AND NOT EXISTS (SELECT 1 FROM lexeme_reviews AS r WHERE r.lexeme_id = lexemes.lexeme_id)
  AND NOT EXISTS (SELECT 1 FROM content_vocab AS v WHERE v.lexeme_id = lexemes.lexeme_id)
  AND NOT EXISTS (SELECT 1 FROM word_resolutions AS w WHERE w.lexeme_id = lexemes.lexeme_id)
  AND NOT EXISTS (SELECT 1 FROM lexeme_senses AS s WHERE s.lexeme_id = lexemes.lexeme_id);

-- The ones that remain have history, so they stay, but their frequency came from an
-- older build and no longer describes the corpus: clear it rather than leave it stale.
UPDATE lexemes SET frequency_per_million = NULL
WHERE frequency_per_million IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM lexicon_staging AS s
                  WHERE s.lemma = lexemes.lemma AND s.pos = lexemes.pos);
