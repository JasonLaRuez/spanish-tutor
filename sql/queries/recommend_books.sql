-- Books, ranked separately from songs and stories (Jason's decision, 2026-10-05): by
-- new-word density across the WHOLE book, i.e. unknown running words / all running words
-- (1 - token coverage). A count of new words would favor short books, and the first
-- chapter alone could make a hard book look easy (a short, simple preface).
--
-- Reading order: a started book always comes first. Its next chapter, the first one after
-- the highest finished chapter, is recommended before any new book is begun. Among several
-- started books, the most recently read leads. A later chapter read on explicit request
-- moves the pointer past it. Finished books, and books with a chapter not yet indexed (no
-- density for the whole book), are left out. One row per book; the first row is the
-- default suggestion.
WITH known AS (
    SELECT lexeme_id FROM word_bank WHERE mode = 'recognition'
),
chapters AS (
    SELECT book_id, content_id, chapter_no, title, tokens, indexed_at
    FROM content_items
    WHERE kind = 'chapter'
),
-- Per chapter: distinct new words and their running count.
chapter_cost AS (
    SELECT v.content_id,
           SUM(CASE WHEN k.lexeme_id IS NULL THEN 1 ELSE 0 END) AS new_words,
           SUM(CASE WHEN k.lexeme_id IS NULL THEN v.occurrences ELSE 0 END) AS new_tokens
    FROM content_vocab AS v
    JOIN chapters AS ch ON ch.content_id = v.content_id
    LEFT JOIN known AS k ON k.lexeme_id = v.lexeme_id
    GROUP BY v.content_id
),
-- Per book: running words over all chapters, and only books indexed in full
-- (COUNT(column) skips NULLs, so it equals COUNT(*) only when every chapter is indexed).
book_cost AS (
    SELECT ch.book_id,
           COUNT(*) AS chapters,
           SUM(ch.tokens) AS tokens,
           SUM(COALESCE(cc.new_tokens, 0)) AS new_tokens
    FROM chapters AS ch
    LEFT JOIN chapter_cost AS cc ON cc.content_id = ch.content_id
    GROUP BY ch.book_id
    HAVING COUNT(ch.indexed_at) = COUNT(*)
),
-- Distinct new words in the whole book: a word in five chapters is still one word to learn.
book_new_words AS (
    SELECT ch.book_id, COUNT(DISTINCT v.lexeme_id) AS new_words
    FROM chapters AS ch
    JOIN content_vocab AS v ON v.content_id = ch.content_id
    WHERE NOT EXISTS (SELECT 1 FROM known AS k WHERE k.lexeme_id = v.lexeme_id)
    GROUP BY ch.book_id
),
-- What the learner did with each book. event_id orders events that share a
-- one-second timestamp, as in the word-bank trigger.
progress AS (
    SELECT ch.book_id,
           MAX(CASE WHEN e.event = 'finished' THEN ch.chapter_no END) AS last_finished,
           MAX(e.occurred_at) AS last_activity,
           MAX(e.event_id) AS last_event
    FROM content_events AS e
    JOIN chapters AS ch ON ch.content_id = e.content_id
    GROUP BY ch.book_id
),
-- The first chapter after the highest finished one (chapter 1 for a book not started).
-- No row means every chapter is finished: the book is done.
next_chapter AS (
    SELECT ch.book_id, MIN(ch.chapter_no) AS chapter_no
    FROM chapters AS ch
    LEFT JOIN progress AS p ON p.book_id = ch.book_id
    WHERE ch.chapter_no > COALESCE(p.last_finished, 0)
    GROUP BY ch.book_id
)
SELECT b.book_id,
       b.title,
       b.author,
       b.is_private,
       CASE WHEN p.book_id IS NULL THEN 'new' ELSE 'in progress' END AS state,
       bc.chapters,
       bc.tokens,
       bc.new_tokens,
       CASE WHEN bc.tokens > 0 THEN bc.new_tokens * 1.0 / bc.tokens END AS density,
       COALESCE(bw.new_words, 0) AS new_words,
       ch.content_id AS next_content_id,
       ch.chapter_no AS next_chapter_no,
       ch.title AS next_chapter_title,
       -- What pre-teaching the next chapter would take.
       COALESCE(cc.new_words, 0) AS next_chapter_new_words,
       p.last_activity
FROM books AS b
JOIN book_cost AS bc ON bc.book_id = b.book_id
JOIN next_chapter AS n ON n.book_id = b.book_id
JOIN chapters AS ch ON ch.book_id = n.book_id AND ch.chapter_no = n.chapter_no
LEFT JOIN chapter_cost AS cc ON cc.content_id = ch.content_id
LEFT JOIN progress AS p ON p.book_id = b.book_id
LEFT JOIN book_new_words AS bw ON bw.book_id = b.book_id
ORDER BY p.book_id IS NULL,   -- started books first (FALSE sorts before TRUE)
         p.last_event DESC,   -- the most recently read of them leads
         density,
         b.title,
         b.book_id;
