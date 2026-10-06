-- The rating page's items of one type, with Jason's latest rating of each on the type's
-- criterion (NULL: not rated yet). Unrated first, in the order they were queued; then the
-- rated ones, most recently rated first, so a rating can be checked or changed.
--
-- `ratings` is append-only: changing a rating adds a row, and the latest one counts
-- (ROW_NUMBER over each item's human ratings, newest first).
WITH latest AS (
    SELECT item_id, score, label, rated_at,
           ROW_NUMBER() OVER (PARTITION BY item_id
                              ORDER BY rated_at DESC, rating_id DESC) AS nth
    FROM ratings
    WHERE rater = 'human' AND criterion = :criterion
)
SELECT i.item_id,
       i.item_type,
       i.source_ref,
       i.content,
       l.score,
       l.label,
       l.rated_at
FROM eval_items AS i
LEFT JOIN latest AS l ON l.item_id = i.item_id AND l.nth = 1
WHERE i.item_type = :item_type
ORDER BY l.item_id IS NOT NULL,
         CASE WHEN l.item_id IS NULL THEN i.item_id END,
         l.rated_at DESC;
