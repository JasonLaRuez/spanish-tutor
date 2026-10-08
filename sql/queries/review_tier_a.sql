-- The lexicon review's first tier: the words the learner will meet soonest. Every word in
-- the items within reach (at most :ceiling of their running words unknown), every word in
-- the word bank, and every definition a model wrote while indexing (never checked against a
-- source). Each word once, most frequent first (NULL frequencies last).
WITH known AS (
    SELECT lexeme_id FROM word_bank WHERE mode = 'recognition'
),
share AS (
    SELECT v.content_id,
           SUM(CASE WHEN k.lexeme_id IS NULL THEN v.occurrences ELSE 0 END) * 1.0 / c.tokens
               AS unknown_share
    FROM content_vocab AS v
    JOIN content_items AS c USING (content_id)
    LEFT JOIN known AS k USING (lexeme_id)
    WHERE c.tokens > 0
    GROUP BY v.content_id
),
tier AS (
    SELECT v.lexeme_id FROM content_vocab AS v
    JOIN share AS s USING (content_id)
    WHERE s.unknown_share <= :ceiling
    UNION
    SELECT lexeme_id FROM word_bank
    UNION
    SELECT lexeme_id FROM lexemes WHERE definition_source LIKE 'model:%'
)
SELECT l.lexeme_id
FROM tier
JOIN lexemes AS l USING (lexeme_id)
ORDER BY l.frequency_per_million IS NULL, l.frequency_per_million DESC, l.lexeme_id;
