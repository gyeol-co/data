DROP INDEX IF EXISTS staging.idx_staging_product_publishable;
CREATE INDEX idx_staging_product_publishable ON staging.product (id)
    WHERE published IS NULL
      AND category IS NOT NULL
      AND original_price IS NOT NULL
      AND thumbnail_url IS NOT NULL
      AND vectorized IS NOT NULL;

ALTER TABLE staging.product DROP COLUMN IF EXISTS embedding;
