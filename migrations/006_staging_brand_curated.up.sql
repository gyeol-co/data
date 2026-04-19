-- staging.brand.description_curated — brand_profile 스킬 출력물.
-- description 은 Bronze 크롤러 raw, description_curated 는 큐레이션된 텍스트.
-- product 의 description / description_curated 와 동일 패턴.

ALTER TABLE staging.brand
    ADD COLUMN IF NOT EXISTS description_curated text;

-- publishable 조건에 description_curated 존재도 함께 반영 (raw만 있고 큐레이션 없으면 미공개)
DROP INDEX IF EXISTS staging.idx_staging_brand_publishable;
CREATE INDEX idx_staging_brand_publishable ON staging.brand (id)
    WHERE published IS NULL
      AND description_curated IS NOT NULL
      AND styles != '{}';
