-- The passive/active gap: words the learner recognizes but has never used, most frequent
-- first, so the most useful words to start producing come first ("try using these").
-- An anti-join: recognition rows with no production row for the same word.
SELECT l.lemma,
       l.pos,
       l.definition_en,
       l.frequency_per_million
FROM word_bank AS r
JOIN lexemes AS l USING (lexeme_id)
WHERE r.mode = 'recognition'
  AND NOT EXISTS (
      SELECT 1 FROM word_bank AS p
      WHERE p.lexeme_id = r.lexeme_id AND p.mode = 'production'
  )
ORDER BY l.frequency_per_million DESC NULLS LAST, l.lemma, l.pos
LIMIT :limit;
