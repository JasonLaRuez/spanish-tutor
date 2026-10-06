-- Vocabulary-constraint adherence: how often the tutor's replies stayed within the limit of
-- one word outside the learner's vocabulary (the i+1 rule, conversation.MAX_NEW_WORDS), in
-- the first draft and in the reply shown after the one retry.
--
-- Conversation replies only: a "¿cómo se dice?" answer (kind 'translation') was asked for
-- and may teach any number of words. Per skill (a conversation, or the talk after reading
-- a text or a song), then every skill together ('all', last). :session_id limits it to one
-- session; NULL means every session.
--
-- The counts come from turns.draft_out_of_bank / final_out_of_bank, written by the tutor
-- as it replies (the new-word detector: lexicon.analyze against the word bank).
-- :from_session keeps only sessions from that one on (NULL: all): a benchmark run's own,
-- on a copy of the word bank that also holds the real history.
WITH replies AS (
    SELECT s.skill,
           t.draft_out_of_bank AS draft,
           t.final_out_of_bank AS final,
           t.retried
    FROM turns AS t
    JOIN sessions AS s USING (session_id)
    WHERE t.role = 'tutor'
      AND t.kind = 'conversation'
      AND t.final_out_of_bank IS NOT NULL
      AND (:session_id IS NULL OR t.session_id = :session_id)
      AND (:from_session IS NULL OR t.session_id >= :from_session)
),
by_skill AS (
    SELECT skill, draft, final, retried FROM replies
    UNION ALL
    SELECT 'all', draft, final, retried FROM replies
)
SELECT skill,
       COUNT(*)                                          AS replies,
       SUM(CASE WHEN draft = 0 THEN 1 ELSE 0 END)        AS no_new_words,
       SUM(CASE WHEN draft <= 1 THEN 1 ELSE 0 END)       AS within_limit_draft,
       SUM(CASE WHEN final <= 1 THEN 1 ELSE 0 END)       AS within_limit_final,
       SUM(retried)                                      AS retried,
       SUM(final)                                        AS new_words_shown,
       ROUND(AVG(draft), 3)                              AS mean_new_words_draft
FROM by_skill
GROUP BY skill
ORDER BY skill = 'all', skill;
