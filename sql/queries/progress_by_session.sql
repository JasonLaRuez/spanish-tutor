-- Word bank growth per session, with a running total per mode.
--
-- A word enters the recognition bank with its first `taught` event and the production
-- bank with its first `used` event. ROW_NUMBER() finds that first event per (word, mode);
-- its turn gives the session. Seed words have no turn, so they form one row with a NULL
-- session, sorted first. The running total is SUM(COUNT(*)) OVER (...): the window runs
-- over the grouped rows, in session order.
WITH entering AS (
    SELECT e.lexeme_id,
           e.mode,
           t.session_id,
           ROW_NUMBER() OVER (
               PARTITION BY e.lexeme_id, e.mode
               ORDER BY e.occurred_at, e.event_id
           ) AS nth
    FROM word_events AS e
    LEFT JOIN turns AS t USING (turn_id)
    WHERE e.event_type IN ('taught', 'used')
)
SELECT en.session_id,
       s.topic,
       s.started_at,
       en.mode,
       COUNT(*) AS words_added,
       SUM(COUNT(*)) OVER (
           PARTITION BY en.mode
           ORDER BY s.started_at NULLS FIRST, en.session_id
       ) AS running_total
FROM entering AS en
LEFT JOIN sessions AS s USING (session_id)
WHERE en.nth = 1
GROUP BY en.session_id, en.mode
ORDER BY s.started_at NULLS FIRST, en.session_id, en.mode;
