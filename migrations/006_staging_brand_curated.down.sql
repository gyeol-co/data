DROP INDEX IF EXISTS staging.idx_staging_brand_publishable;
CREATE INDEX idx_staging_brand_publishable ON staging.brand (id)
    WHERE published IS NULL
      AND description IS NOT NULL
      AND styles != '{}';

ALTER TABLE staging.brand DROP COLUMN IF EXISTS description_curated;
