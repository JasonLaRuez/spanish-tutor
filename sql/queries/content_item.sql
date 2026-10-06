-- One item for the reader: its text, where it sits in its book, and whether the learner has
-- started or finished it. Its new words come from item_new_words.sql.
SELECT c.content_id,
       c.kind,
       c.title,
       COALESCE(c.author, b.author) AS author,
       COALESCE(c.source, b.source) AS source,
       c.book_id,
       b.title AS book_title,
       c.chapter_no,
       (SELECT COUNT(*) FROM content_items AS ch WHERE ch.book_id = c.book_id) AS chapters,
       c.tokens,
       c.unresolved_tokens,
       c.indexed_at IS NOT NULL AS indexed,
       EXISTS (SELECT 1 FROM content_events AS e
               WHERE e.content_id = c.content_id AND e.event = 'started') AS started,
       EXISTS (SELECT 1 FROM content_events AS e
               WHERE e.content_id = c.content_id AND e.event = 'finished') AS finished,
       c.text_es
FROM content_items AS c
LEFT JOIN books AS b ON b.book_id = c.book_id
WHERE c.content_id = :content_id;
