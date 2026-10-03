-- How many words the learner has, per mode: recognition (understands) and production
-- (has used). word_bank holds one row per (word, mode) the learner has acquired.
SELECT mode, COUNT(*) AS words
FROM word_bank
GROUP BY mode;
