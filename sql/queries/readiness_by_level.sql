-- Vocabulary readiness: of the words Spanish textbooks introduce up to each CEFR level
-- (lexemes.cefr_level, from ELELex), how many the learner recognizes and can produce.
-- Vocabulary only, never a CEFR level: the exams also test grammar, listening and writing.
--
-- Per level first (the words introduced at that level), then running totals with
-- SUM() OVER: "up to B1" = A1 + A2 + B1. The level codes sort in CEFR order as text.
WITH per_level AS (
    SELECT l.cefr_level AS level,
           COUNT(*) AS words,
           SUM(EXISTS (SELECT 1 FROM word_bank AS b
                       WHERE b.lexeme_id = l.lexeme_id AND b.mode = 'recognition')) AS recognized,
           SUM(EXISTS (SELECT 1 FROM word_bank AS b
                       WHERE b.lexeme_id = l.lexeme_id AND b.mode = 'production')) AS produced
    FROM lexemes AS l
    WHERE l.cefr_level IS NOT NULL
    GROUP BY l.cefr_level
)
SELECT level, words, recognized, produced,
       SUM(words)      OVER up_to AS words_up_to,
       SUM(recognized) OVER up_to AS recognized_up_to,
       SUM(produced)   OVER up_to AS produced_up_to
FROM per_level
WINDOW up_to AS (ORDER BY level ROWS UNBOUNDED PRECEDING)
ORDER BY level;
