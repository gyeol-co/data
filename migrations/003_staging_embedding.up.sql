-- staging.product 에 embedding 컬럼 추가.
-- public.product.embedding 과 동일한 차원 (1024d, bge-m3).
-- Gemini enrichment 완료 (description_curated) 후 TEI 임베딩 생성 → 여기 INSERT.
-- vectorized 타임스탬프는 기존 컬럼 그대로 사용.

ALTER TABLE staging.product
    ADD COLUMN IF NOT EXISTS embedding vector(1024);

-- publish 시 조회 패턴에 맞춰 부분 인덱스 확장.
DROP INDEX IF EXISTS staging.idx_staging_product_publishable;
CREATE INDEX idx_staging_product_publishable ON staging.product (id)
    WHERE published IS NULL
      AND category IS NOT NULL
      AND original_price IS NOT NULL
      AND thumbnail_url IS NOT NULL
      AND embedding IS NOT NULL
      AND vectorized IS NOT NULL;
