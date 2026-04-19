-- staging.product.search_text — MeCab 형태소 토큰을 공백 구분 저장.
-- publish 시 public.product.search_text 로 복사되고, public의 search_vector
-- (GENERATED ALWAYS AS to_tsvector('simple', search_text) STORED) 가 자동 인덱싱.

ALTER TABLE staging.product
    ADD COLUMN IF NOT EXISTS search_text text;

-- publishable 인덱스 확장 — search_text 까지 채워진 행만 이관 가능하게
DROP INDEX IF EXISTS staging.idx_staging_product_publishable;
CREATE INDEX idx_staging_product_publishable ON staging.product (id)
    WHERE published IS NULL
      AND category IS NOT NULL
      AND original_price IS NOT NULL
      AND thumbnail_url IS NOT NULL
      AND embedding IS NOT NULL
      AND search_text IS NOT NULL
      AND vectorized IS NOT NULL;
