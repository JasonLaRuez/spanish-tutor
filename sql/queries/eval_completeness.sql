-- New-word teaching completeness: every word outside the learner's vocabulary in a tutor
-- reply must be taught on that reply (a `taught` event on its turn). A reply is complete
-- when it taught exactly as many words as the detector flagged in it (final_out_of_bank).
--
-- Topic words taught before a conversation are logged on its opening reply with source
-- 'pre_teach', so they're left out: they weren't in the reply. Lookups are taught on
-- their own `study` turns (since 2026-10-06), so they never land on a reply either.
-- Per skill, then 'all' (last). :session_id limits it to one session (NULL: all).
-- eval_completeness_gaps.sql lists the replies that fall short or over.
WITH replies AS (
    SELECT s.skill,
           t.turn_id,
           t.final_out_of_bank AS flagged,
           (SELECT COUNT(*) FROM word_events AS e
            WHERE e.turn_id = t.turn_id
              AND e.event_type = 'taught'
              AND e.source <> 'pre_teach') AS taught
    FROM turns AS t
    JOIN sessions AS s USING (session_id)
    WHERE t.role = 'tutor'
      AND t.kind = 'conversation'
      AND t.final_out_of_bank IS NOT NULL
      AND (:session_id IS NULL OR t.session_id = :session_id)
),
by_skill AS (
    SELECT skill, flagged, taught FROM replies
    UNION ALL
    SELECT 'all', flagged, taught FROM replies
)
SELECT skill,
       COUNT(*)                                                   AS replies,
       SUM(CASE WHEN flagged > 0 THEN 1 ELSE 0 END)               AS replies_with_new_words,
       SUM(CASE WHEN flagged > 0 AND taught = flagged THEN 1 ELSE 0 END) AS complete,
       SUM(CASE WHEN taught <> flagged THEN 1 ELSE 0 END)         AS gaps,
       SUM(flagged)                                               AS words_flagged,
       SUM(taught)                                                AS words_taught
FROM by_skill
GROUP BY skill
ORDER BY skill = 'all', skill;
