-- Coverage by frequency band: what share of each band of the frequency list the learner
-- recognizes and can produce. Frequency bands stand in for CEFR levels until words
-- carry PCIC levels (lexemes.cefr_level).
--
-- ROW_NUMBER() ranks every word by subtitle frequency, CASE assigns bands, and EXISTS
-- turns "is this word in the bank, in this mode?" into a 0/1 that SUM can count.
WITH ranked AS (
    SELECT lexeme_id,
           ROW_NUMBER() OVER (ORDER BY frequency_per_million DESC, lexeme_id) AS frequency_rank
    FROM lexemes
    WHERE frequency_per_million IS NOT NULL
),
banded AS (
    SELECT r.lexeme_id,
           CASE WHEN frequency_rank <= 100  THEN 1
                WHEN frequency_rank <= 500  THEN 2
                WHEN frequency_rank <= 1000 THEN 3
                WHEN frequency_rank <= 2000 THEN 4
                WHEN frequency_rank <= 5000 THEN 5
                ELSE 6 END AS band,
           EXISTS (SELECT 1 FROM word_bank AS b
                   WHERE b.lexeme_id = r.lexeme_id AND b.mode = 'recognition') AS recognized,
           EXISTS (SELECT 1 FROM word_bank AS b
                   WHERE b.lexeme_id = r.lexeme_id AND b.mode = 'production') AS produced
    FROM ranked AS r
)
SELECT band,
       CASE band WHEN 1 THEN 'top 100'
                 WHEN 2 THEN '101–500'
                 WHEN 3 THEN '501–1,000'
                 WHEN 4 THEN '1,001–2,000'
                 WHEN 5 THEN '2,001–5,000'
                 ELSE 'beyond 5,000' END AS label,
       COUNT(*) AS words,
       SUM(recognized) AS recognized,
       SUM(produced) AS produced
FROM banded
GROUP BY band
ORDER BY band;
