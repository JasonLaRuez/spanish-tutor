-- Every content item, for choosing one yourself (an explicit request always wins over the
-- recommendation): songs, stories, and every chapter of every book, each with the words it
-- would teach and whether the learner has started or finished it. Not ranked: grouped by
-- book and chapter, then by collection in the order its items were added (a collection's
-- own order, e.g. Rima I, II, III), then standalone items by title. Items not indexed yet
-- have no counts.
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
),
-- Finished beats started: an item read once and opened again is still finished.
reading AS (
    SELECT content_id,
           CASE WHEN MAX(event = 'finished') = 1 THEN 'finished' ELSE 'started' END AS state
    FROM content_events
    GROUP BY content_id
)
SELECT c.content_id,
       c.kind,
       c.title,
       COALESCE(c.author, b.author) AS author,
       c.book_id,
       b.title AS book_title,
       c.chapter_no,
       c.collection,
       c.indexed_at IS NOT NULL AS indexed,
       CASE WHEN c.indexed_at IS NOT NULL THEN COALESCE(cost.new_words, 0) END AS new_words,
       c.tokens,
       CASE WHEN c.tokens > 0
            THEN 1.0 - COALESCE(cost.new_tokens, 0) * 1.0 / c.tokens END AS coverage,
       r.state
FROM content_items AS c
LEFT JOIN books AS b ON b.book_id = c.book_id
LEFT JOIN cost ON cost.content_id = c.content_id
LEFT JOIN reading AS r ON r.content_id = c.content_id
ORDER BY c.book_id IS NOT NULL, b.title, c.chapter_no,
         c.collection IS NULL, c.collection, CASE WHEN c.collection IS NULL THEN c.title END,
         c.content_id;
