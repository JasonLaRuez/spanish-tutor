-- Recommendation quality, from the reading log (content_events): every time the learner
-- started an item, how it was chosen, how hard it was at that moment, and whether it was
-- finished. Grouped by how it was chosen and by difficulty band:
--
--   chosen_via:   'recommended' (the default suggestion or a surprise pick) or 'requested'
--                 (any other pick). The take rate is recommended / all starts.
--   band:         the share of the item's running words unknown when it was started, in
--                 5-point bands up to the ceiling (recommend.MAX_UNKNOWN_SHARE, 20%);
--                 '20%+' is only reachable by request ("too hard for now").
--   finished:     a `finished` event for the item in the same session (or, for a start
--                 outside any session, any later finish). A start in a session still open
--                 counts as not finished, so read recent numbers with that in mind.
--
-- "Unknown at the start" is rebuilt from the log as in eval_reading.sql: no recognition
-- `taught` event for the word before the start.
-- :from_session keeps only sessions from that one on (NULL: all): a benchmark run's own,
-- on a copy of the word bank that also holds the real history.
WITH starts AS (
    SELECT ce.event_id,
           ce.content_id,
           ce.chosen_via,
           ce.session_id,
           ce.occurred_at,
           c.kind,
           c.tokens,
           (SELECT COALESCE(SUM(v.occurrences), 0)
            FROM content_vocab AS v
            WHERE v.content_id = ce.content_id
              AND NOT EXISTS (SELECT 1 FROM word_events AS e
                              WHERE e.lexeme_id = v.lexeme_id
                                AND e.mode = 'recognition'
                                AND e.event_type = 'taught'
                                AND e.occurred_at < ce.occurred_at)) AS unknown_tokens,
           EXISTS (SELECT 1 FROM content_events AS f
                   WHERE f.content_id = ce.content_id
                     AND f.event = 'finished'
                     AND f.occurred_at >= ce.occurred_at
                     AND (ce.session_id IS NULL OR f.session_id = ce.session_id)) AS finished
    FROM content_events AS ce
    JOIN content_items AS c ON c.content_id = ce.content_id
    WHERE ce.event = 'started'
      AND (:from_session IS NULL OR ce.session_id >= :from_session)
),
banded AS (
    SELECT *,
           CASE WHEN tokens > 0 THEN unknown_tokens * 1.0 / tokens ELSE 0 END AS unknown_share
    FROM starts
)
SELECT chosen_via,
       CASE WHEN unknown_share < 0.05 THEN '0-5%'
            WHEN unknown_share < 0.10 THEN '5-10%'
            WHEN unknown_share < 0.15 THEN '10-15%'
            WHEN unknown_share <= 0.20 THEN '15-20%'
            ELSE '20%+' END                             AS band,
       COUNT(*)                                         AS starts,
       SUM(finished)                                    AS finished,
       ROUND(AVG(unknown_share), 3)                     AS mean_unknown_share
FROM banded
GROUP BY chosen_via, band
ORDER BY chosen_via, MIN(unknown_share);
