-- Reading and song sessions, one row each: what the difficulty index predicted the text
-- would teach, what was actually taught, and how much was studied before finishing.
--
--   predicted_new:     the item's words (content_vocab) not yet recognized when the session
--                      started: what the recommender ranked it by
--   studied:           ... of those, taught in the session before the learner finished
--                      (study batches and lookups; all of them if not finished)
--   taught_unpredicted: words taught in the session that the index didn't predict (the
--                      index is stale, or the session analyzed the text differently)
--   new_tokens / studied_tokens: the same, counted in running words
--
-- "Known at the start" is rebuilt from the log: a recognition `taught` event before the
-- session began, outside this session. Recognition only enters the word bank through
-- `taught` (word_events' CHECK puts `used` in production).
-- :session_id limits it to one session (NULL: all).
WITH readings AS (
    SELECT s.session_id,
           s.skill,
           s.started_at,
           ce.content_id,
           (SELECT MIN(t.turn_no) FROM turns AS t
            WHERE t.session_id = s.session_id AND t.kind = 'reading') AS finish_turn
    FROM sessions AS s
    JOIN content_events AS ce ON ce.session_id = s.session_id AND ce.event = 'started'
    WHERE s.skill IN ('reading', 'lyrics')
      AND (:session_id IS NULL OR s.session_id = :session_id)
),
predicted AS (
    SELECT r.session_id, v.lexeme_id, v.occurrences
    FROM readings AS r
    JOIN content_vocab AS v ON v.content_id = r.content_id
    WHERE NOT EXISTS (
        SELECT 1 FROM word_events AS e
        LEFT JOIN turns AS et ON et.turn_id = e.turn_id
        WHERE e.lexeme_id = v.lexeme_id
          AND e.mode = 'recognition'
          AND e.event_type = 'taught'
          AND e.occurred_at <= r.started_at
          AND (et.session_id IS NULL OR et.session_id <> r.session_id)
    )
),
taught AS (
    SELECT DISTINCT r.session_id, e.lexeme_id
    FROM readings AS r
    JOIN turns AS t ON t.session_id = r.session_id AND t.kind = 'study'
    JOIN word_events AS e ON e.turn_id = t.turn_id AND e.event_type = 'taught'
    WHERE r.finish_turn IS NULL OR t.turn_no < r.finish_turn
)
SELECT r.session_id,
       r.skill,
       c.kind,
       c.title,
       r.finish_turn IS NOT NULL                                        AS finished,
       (SELECT COUNT(*) FROM predicted AS p WHERE p.session_id = r.session_id) AS predicted_new,
       (SELECT COUNT(*) FROM predicted AS p
        JOIN taught AS tw ON tw.session_id = p.session_id AND tw.lexeme_id = p.lexeme_id
        WHERE p.session_id = r.session_id)                             AS studied,
       (SELECT COUNT(*) FROM taught AS tw
        WHERE tw.session_id = r.session_id
          AND NOT EXISTS (SELECT 1 FROM predicted AS p
                          WHERE p.session_id = tw.session_id
                            AND p.lexeme_id = tw.lexeme_id))           AS taught_unpredicted,
       (SELECT COALESCE(SUM(p.occurrences), 0) FROM predicted AS p
        WHERE p.session_id = r.session_id)                             AS new_tokens,
       (SELECT COALESCE(SUM(p.occurrences), 0) FROM predicted AS p
        JOIN taught AS tw ON tw.session_id = p.session_id AND tw.lexeme_id = p.lexeme_id
        WHERE p.session_id = r.session_id)                             AS studied_tokens
FROM readings AS r
JOIN content_items AS c ON c.content_id = r.content_id
ORDER BY r.session_id;
