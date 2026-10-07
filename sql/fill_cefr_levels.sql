-- Tag lexemes with the CEFR level at which Spanish textbooks introduce them (ELELex).
--
-- Reads elelex_staging, a TEMP table filled by src/spanish_tutor/ingest/elelex.py with one
-- row per ELELex entry after normalization: (lemma, pos, docs_a1 ... docs_c1), where
-- docs_<level> is how many of that level's textbook documents use the word. A word's
-- level is the first level whose documents use it at least :min_docs times (3, measured:
-- with 1, a single stray textbook puts 3,094 words at A1). ELELex has no C2.
--
-- Re-runnable: every level is cleared first, so a changed threshold or a rebuilt lexicon
-- never leaves a stale level behind. Only existing lexemes are tagged; ELELex words that
-- aren't in the lexicon are ignored, never inserted.

UPDATE lexemes SET cefr_level = NULL WHERE cefr_level IS NOT NULL;

-- Exact (lemma, pos) matches. One entry can be staged more than once (ELELex lists some
-- nouns as both NCM and NCF), so take the earliest level.
WITH first_level AS (
    SELECT lemma, pos,
           MIN(CASE WHEN docs_a1 >= :min_docs THEN 1
                    WHEN docs_a2 >= :min_docs THEN 2
                    WHEN docs_b1 >= :min_docs THEN 3
                    WHEN docs_b2 >= :min_docs THEN 4
                    WHEN docs_c1 >= :min_docs THEN 5 END) AS level_no
    FROM elelex_staging
    GROUP BY lemma, pos
)
UPDATE lexemes
SET cefr_level = CASE f.level_no WHEN 1 THEN 'A1' WHEN 2 THEN 'A2' WHEN 3 THEN 'B1'
                                 WHEN 4 THEN 'B2' ELSE 'C1' END
FROM first_level AS f
WHERE f.lemma = lexemes.lemma AND f.pos = lexemes.pos AND f.level_no IS NOT NULL;

-- Function words: two taggers disagree on their POS (our sí is INTJ, ELELex's an adverb;
-- mismo is DET, PRON or ADJ), so one still untagged takes its lemma's earliest level
-- under any POS. Open-class words never do: bajo the noun isn't bajo the preposition.
WITH by_lemma AS (
    SELECT lemma,
           MIN(CASE WHEN docs_a1 >= :min_docs THEN 1
                    WHEN docs_a2 >= :min_docs THEN 2
                    WHEN docs_b1 >= :min_docs THEN 3
                    WHEN docs_b2 >= :min_docs THEN 4
                    WHEN docs_c1 >= :min_docs THEN 5 END) AS level_no
    FROM elelex_staging
    GROUP BY lemma
)
UPDATE lexemes
SET cefr_level = CASE b.level_no WHEN 1 THEN 'A1' WHEN 2 THEN 'A2' WHEN 3 THEN 'B1'
                                 WHEN 4 THEN 'B2' ELSE 'C1' END
FROM by_lemma AS b
WHERE b.lemma = lexemes.lemma AND b.level_no IS NOT NULL
  AND lexemes.cefr_level IS NULL
  AND lexemes.pos IN ('PRON', 'DET', 'ADP', 'SCONJ', 'CCONJ', 'ADV', 'INTJ');
