-- Migration 5: break out cache writes in the per-turn cost columns. With the conversation
-- history cached, a turn's input has four prices: cache reads (0.05x), 5-minute cache
-- writes (1.25x), 1-hour cache writes (2x), and full-price input (1x).
-- input_tokens keeps its meaning: everything not read from the cache. The new columns say
-- how much of it was written to the cache, by lifetime; NULL on turns from before
-- migration 5, where the breakdown is unknown.
ALTER TABLE turns ADD COLUMN cache_write_5m_tokens INTEGER;
ALTER TABLE turns ADD COLUMN cache_write_1h_tokens INTEGER;
