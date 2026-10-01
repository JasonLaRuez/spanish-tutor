-- Merge the general lexicon into lexemes.
--
-- Reads lexicon_staging, a TEMP table filled by src/spanish_tutor/ingest/build_lexicon.py
-- with one row per (lemma, pos). Rules:
--   * Additive: rows are never deleted, so lexeme_ids (and the learner's word_events
--     that reference them) stay valid across rebuilds.
--   * Frequencies are derived data, recomputed on every build, so they are overwritten.
--   * Definitions and examples only fill empty fields, so values already present
--     (seeded examples, later reviewed fixes) are never replaced.
--   * The four example fields move as a unit, so a sentence is never paired with
--     another sentence's translation or author.
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
                             THEN excluded.example_author ELSE lexemes.example_author END;
