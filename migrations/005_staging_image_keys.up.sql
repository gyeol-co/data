-- staging.product 에 S3 키 컬럼 추가.
-- publish 시 public.product.thumbnail_key, image_keys 로 복사.
-- 값 형식: products/{product_id}/thumb.jpg, products/{product_id}/gallery_001.jpg ...

ALTER TABLE staging.product
    ADD COLUMN IF NOT EXISTS thumbnail_key text,
    ADD COLUMN IF NOT EXISTS image_keys    text[] DEFAULT '{}';

-- publishable 인덱스에 thumbnail_key 추가 (public.product.thumbnail_key NOT NULL 이므로 필수)
DROP INDEX IF EXISTS staging.idx_staging_product_publishable;
CREATE INDEX idx_staging_product_publishable ON staging.product (id)
    WHERE published IS NULL
      AND category IS NOT NULL
      AND original_price IS NOT NULL
      AND thumbnail_key IS NOT NULL
      AND embedding IS NOT NULL
      AND search_text IS NOT NULL
      AND vectorized IS NOT NULL;
