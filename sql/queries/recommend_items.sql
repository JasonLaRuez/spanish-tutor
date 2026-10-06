-- Songs and short stories, ranked by how many words each would teach: the distinct words
-- in it the learner doesn't recognize yet. This is Krashen's i+1 as a query: the least new
-- input first. Ties go to higher coverage (more of the running text already known), then
-- the title. The first row is the default suggestion; an explicit request skips this query.
--
-- Known means recognized (word_bank mode 'recognition'): content is read or heard. Items
-- the learner finished, and items not indexed yet, are left out.
--
-- The LEFT JOIN keeps every word of an item; known words find a word_bank row and unknown
-- ones find NULL, which the CASE expressions count. SUM(CASE ...) rather than FILTER, so
-- the query runs unchanged on the cloud platform (Phase 5).
WITH known AS (
    SELECT lexeme_id FROM word_bank WHERE mode = 'recognition'
),
cost AS (
    SELECT v.content_id,
           SUM(CASE WHEN k.lexeme_id IS NULL THEN 1 ELSE 0 END) AS new_words,
           SUM(CASE WHEN k.lexeme_id IS NULL THEN v.occurrences ELSE 0 END) AS new_tokens
    FROM content_vocab AS v
    LEFT JOIN known AS k ON k.lexeme_id = v.lexeme_id
    GROUP BY v.content_id
)
SELECT c.content_id,
       c.kind,
       c.title,
       c.author,
       c.is_private,
       COALESCE(cost.new_words, 0) AS new_words,
       COALESCE(cost.new_tokens, 0) AS new_tokens,
       c.tokens,
       -- Share of running words already known. NULL for an item with no words at all.
       CASE WHEN c.tokens > 0
            THEN 1.0 - COALESCE(cost.new_tokens, 0) * 1.0 / c.tokens END AS coverage
FROM content_items AS c
LEFT JOIN cost ON cost.content_id = c.content_id
WHERE c.kind IN ('song', 'story')
  AND c.indexed_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM content_events AS e
                  WHERE e.content_id = c.content_id AND e.event = 'finished')
ORDER BY new_words, coverage DESC, c.title, c.content_id
LIMIT :limit;
