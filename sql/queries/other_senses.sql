-- Every labeled sense kept apart from a word's main definition (lexeme_senses: rare,
-- regional, slang, vulgar, archaic, technical), with where the *word* appears: in how many
-- songs, and in how many other texts (stories, chapters, poems). The index counts words,
-- not senses, so a song containing the word doesn't mean it uses that sense.
--
-- `recognized`: whether the learner knows the word (in its main sense).
WITH word_use AS (
    SELECT v.lexeme_id,
           SUM(c.kind = 'song')  AS songs,
           SUM(c.kind <> 'song') AS texts
    FROM content_vocab AS v
    JOIN content_items AS c ON c.content_id = v.content_id
    GROUP BY v.lexeme_id
)
SELECT s.sense_id, l.lexeme_id, l.lemma, l.pos, l.definition_en,
       s.sense_en, s.register, s.region,
       COALESCE(u.songs, 0) AS songs,
       COALESCE(u.texts, 0) AS texts,
       EXISTS (SELECT 1 FROM word_bank AS b
               WHERE b.lexeme_id = l.lexeme_id AND b.mode = 'recognition') AS recognized
FROM lexeme_senses AS s
JOIN lexemes AS l ON l.lexeme_id = s.lexeme_id
LEFT JOIN word_use AS u ON u.lexeme_id = l.lexeme_id
ORDER BY l.lemma, l.pos, s.sense_id;
