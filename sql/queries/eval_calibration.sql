-- Calibration: the items Jason rated on a criterion, with his latest score and every
-- repeat of one judge run's score on the same items (a JSON array in repeat order), for
-- judge-vs-human agreement in evaluation/translation.py. Only items rated by both.
WITH human AS (
    SELECT item_id, score,
           ROW_NUMBER() OVER (PARTITION BY item_id
                              ORDER BY rated_at DESC, rating_id DESC) AS nth
    FROM ratings
    WHERE rater = 'human' AND criterion = :criterion
),
judge AS (
    SELECT item_id, json_group_array(score) AS scores
    FROM (SELECT item_id, score FROM ratings
          WHERE run_id = :run_id AND criterion = :criterion
          ORDER BY item_id, repeat_no)
    GROUP BY item_id
)
SELECT h.item_id, h.score AS human, j.scores AS judge, i.content
FROM human AS h
JOIN judge AS j USING (item_id)
JOIN eval_items AS i USING (item_id)
WHERE h.nth = 1
ORDER BY h.item_id;
