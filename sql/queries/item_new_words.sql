-- The words one item would teach: in its vocabulary and not yet recognized. Most frequent
-- in the item first, which is the order to pre-teach them in; then by general frequency,
-- with words that have none last.
SELECT l.lexeme_id,
       l.lemma,
       l.pos,
       l.definition_en,
       v.occurrences,
       l.definition_source LIKE 'model:%' AS model_written
FROM content_vocab AS v
JOIN lexemes AS l ON l.lexeme_id = v.lexeme_id
WHERE v.content_id = :content_id
  AND NOT EXISTS (SELECT 1 FROM word_bank AS b
                  WHERE b.lexeme_id = v.lexeme_id AND b.mode = 'recognition')
ORDER BY v.occurrences DESC,
         l.frequency_per_million IS NULL,
         l.frequency_per_million DESC,
         l.lemma;
