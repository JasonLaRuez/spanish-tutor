-- One session's transcript, with the words each turn taught, today's topic words shown
-- before it (logged with the opening turn, source 'pre_teach': new words `taught`,
-- practice words `seen`), and the words the learner used in it. Each list is built by a correlated subquery: the turn's events grouped to
-- one row per word (keeping its first event id), then folded into one comma-separated
-- string by GROUP_CONCAT, in the order the words were logged.
SELECT t.turn_id,
       t.turn_no,
       t.role,
       t.kind,
       t.text_es,
       t.note_en,
       t.created_at,
       (SELECT GROUP_CONCAT(lemma, ', ' ORDER BY first_event)
        FROM (SELECT l.lemma, MIN(e.event_id) AS first_event
              FROM word_events AS e JOIN lexemes AS l USING (lexeme_id)
              WHERE e.turn_id = t.turn_id AND e.event_type = 'taught'
              GROUP BY l.lemma)) AS taught,
       (SELECT GROUP_CONCAT(lemma, ', ' ORDER BY first_event)
        FROM (SELECT l.lemma, MIN(e.event_id) AS first_event
              FROM word_events AS e JOIN lexemes AS l USING (lexeme_id)
              WHERE e.turn_id = t.turn_id AND e.source = 'pre_teach'
              GROUP BY l.lemma)) AS pre_taught,
       (SELECT GROUP_CONCAT(lemma, ', ' ORDER BY first_event)
        FROM (SELECT l.lemma, MIN(e.event_id) AS first_event
              FROM word_events AS e JOIN lexemes AS l USING (lexeme_id)
              WHERE e.turn_id = t.turn_id AND e.event_type = 'used'
              GROUP BY l.lemma)) AS used
FROM turns AS t
WHERE t.session_id = :session_id
ORDER BY t.turn_no;
