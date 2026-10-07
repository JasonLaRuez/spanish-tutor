-- One judge run's ratings, gathered per item and criterion: every repeat's score (or
-- label, for verdicts) as a JSON array in repeat order, with the item's snapshot, for the
-- consistency measures in evaluation/translation.py (agreement across repeats,
-- Krippendorff's alpha, and agreement with an attempt's intended verdict).
WITH ordered AS (
    SELECT r.item_id, r.criterion, r.repeat_no, COALESCE(r.score, r.label) AS rating
    FROM ratings AS r
    WHERE r.run_id = :run_id
    ORDER BY r.item_id, r.criterion, r.repeat_no
)
SELECT o.item_id,
       o.criterion,
       COUNT(*)                    AS repeats,
       json_group_array(o.rating)  AS ratings,
       i.content
FROM ordered AS o
JOIN eval_items AS i USING (item_id)
GROUP BY o.item_id, o.criterion
ORDER BY o.criterion, o.item_id;
