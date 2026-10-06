-- The tutor replies that taught a different number of words than they had outside the
-- learner's vocabulary (see eval_completeness.sql), with the words they did teach, so each
-- gap can be looked at. :session_id limits it to one session (NULL: all).
-- :from_session keeps only sessions from that one on (NULL: all): a benchmark run's own,
-- on a copy of the word bank that also holds the real history.
SELECT t.session_id,
       t.turn_no,
       s.skill,
       t.text_es,
       t.final_out_of_bank AS flagged,
       COUNT(e.event_id)   AS taught,
       GROUP_CONCAT(l.lemma, ', ') AS taught_words
FROM turns AS t
JOIN sessions AS s USING (session_id)
LEFT JOIN word_events AS e
       ON e.turn_id = t.turn_id AND e.event_type = 'taught' AND e.source <> 'pre_teach'
LEFT JOIN lexemes AS l ON l.lexeme_id = e.lexeme_id
WHERE t.role = 'tutor'
  AND t.kind = 'conversation'
  AND t.final_out_of_bank IS NOT NULL
  AND (:session_id IS NULL OR t.session_id = :session_id)
  AND (:from_session IS NULL OR t.session_id >= :from_session)
GROUP BY t.turn_id
HAVING COUNT(e.event_id) <> t.final_out_of_bank
ORDER BY t.session_id, t.turn_no;
