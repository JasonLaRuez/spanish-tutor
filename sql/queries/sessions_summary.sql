-- One row per session for the history list: what was said and learned.
--
-- Turns and events are counted in separate CTEs and joined to sessions afterwards.
-- Joining turns and word_events first and counting once would multiply rows (every
-- event repeated per turn), so each count is computed at its own grain.
--   messages:     the learner's conversation turns
--   how_to_say:   "¿cómo se dice?" requests (the learner's translation turns)
--   corrections:  tutor replies that came with a correction note
--   words_taught: distinct words taught, including the topic words taught up front
--   words_used:   distinct words the learner used
WITH turn_counts AS (
    SELECT session_id,
           SUM(role = 'learner' AND kind = 'conversation') AS messages,
           SUM(role = 'learner' AND kind = 'translation') AS how_to_say,
           SUM(role = 'tutor' AND kind = 'conversation' AND note_en IS NOT NULL) AS corrections,
           MAX(created_at) AS last_at
    FROM turns
    GROUP BY session_id
),
event_counts AS (
    SELECT t.session_id,
           COUNT(DISTINCT CASE WHEN e.event_type = 'taught' THEN e.lexeme_id END) AS words_taught,
           COUNT(DISTINCT CASE WHEN e.event_type = 'used' THEN e.lexeme_id END) AS words_used
    FROM word_events AS e
    JOIN turns AS t USING (turn_id)
    GROUP BY t.session_id
)
SELECT s.session_id,
       s.skill,
       s.topic,
       s.started_at,
       s.ended_at,
       tc.last_at,
       COALESCE(tc.messages, 0) AS messages,
       COALESCE(tc.how_to_say, 0) AS how_to_say,
       COALESCE(tc.corrections, 0) AS corrections,
       COALESCE(ec.words_taught, 0) AS words_taught,
       COALESCE(ec.words_used, 0) AS words_used
FROM sessions AS s
LEFT JOIN turn_counts AS tc USING (session_id)
LEFT JOIN event_counts AS ec USING (session_id)
ORDER BY s.started_at DESC, s.session_id DESC;
