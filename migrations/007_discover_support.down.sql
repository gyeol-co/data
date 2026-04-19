DROP TABLE IF EXISTS user_product_affinity;

DROP INDEX IF EXISTS idx_product_popularity;
ALTER TABLE product DROP COLUMN IF EXISTS popularity;
