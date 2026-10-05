-- The stats for one session's end-of-conversation summary, computed from the log so they
-- can never disagree with it (session_summaries stores only the tutor's notes).
--
--   minutes:         first turn to the end (ended_at, or the last turn if never ended)
--   messages:        the learner's conversation turns
--   how_to_say:      "¿cómo se dice?" requests
--   corrections:     tutor replies that came with a correction note
--   words_used:      distinct words the learner used
--   words_taught:    distinct words taught, including the topic words taught up front
--   first_time:      words the learner used for the first time ever, in this session: their
--                    first `used` event (ROW_NUMBER over each word's history) is here
--   pre_taught:      topic words taught before the conversation
--   pre_taught_used: ... and which of them the learner used during it
--
-- Each measure has its own CTE at its own grain, so joining them never multiplies rows.
WITH session_turns AS (
    SELECT * FROM turns WHERE session_id = :session_id
),
session_events AS (
    SELECT e.*, l.lemma
    FROM word_events AS e
    JOIN session_turns AS t USING (turn_id)
    JOIN lexemes AS l USING (lexeme_id)
),
first_uses AS (
    -- LEFT JOIN: seed events have no turn, but a word marked "can produce" in the seed was
    -- used before any session, so its first use is the seed's (session NULL).
    SELECT e.lexeme_id, t.session_id,
           ROW_NUMBER() OVER (PARTITION BY e.lexeme_id ORDER BY e.occurred_at, e.event_id) AS nth
    FROM word_events AS e
    LEFT JOIN turns AS t USING (turn_id)
    WHERE e.event_type = 'used'
),
first_time AS (
    SELECT DISTINCT l.lemma
    FROM first_uses AS f JOIN lexemes AS l USING (lexeme_id)
    WHERE f.nth = 1 AND f.session_id = :session_id
),
pre_taught AS (
    SELECT DISTINCT lexeme_id, lemma FROM session_events WHERE source = 'pre_teach'
),
used AS (
    SELECT DISTINCT lexeme_id, lemma FROM session_events WHERE event_type = 'used'
)
SELECT s.session_id,
       s.topic,
       s.started_at,
       s.ended_at,
       ROUND((julianday(COALESCE(s.ended_at, (SELECT MAX(created_at) FROM session_turns)))
              - julianday((SELECT MIN(created_at) FROM session_turns))) * 24 * 60) AS minutes,
       (SELECT COUNT(*) FROM session_turns
        WHERE role = 'learner' AND kind = 'conversation') AS messages,
       (SELECT COUNT(*) FROM session_turns
        WHERE role = 'learner' AND kind = 'translation') AS how_to_say,
       (SELECT COUNT(*) FROM session_turns
        WHERE role = 'tutor' AND kind = 'conversation' AND note_en IS NOT NULL) AS corrections,
       (SELECT COUNT(*) FROM used) AS words_used,
       (SELECT COUNT(DISTINCT lexeme_id) FROM session_events
        WHERE event_type = 'taught') AS words_taught,
       (SELECT GROUP_CONCAT(lemma, ', ' ORDER BY lemma) FROM first_time) AS first_time,
       (SELECT GROUP_CONCAT(lemma, ', ' ORDER BY lemma) FROM pre_taught) AS pre_taught,
       (SELECT GROUP_CONCAT(p.lemma, ', ' ORDER BY p.lemma)
        FROM pre_taught AS p WHERE p.lexeme_id IN (SELECT lexeme_id FROM used)) AS pre_taught_used
FROM sessions AS s
WHERE s.session_id = :session_id;
