-- public.product 정리:
--   - name_original 제거 (Bronze raw, 서비스 노출 불필요)
--   - attributes JSONB bucket 폐기 — 중요한 건 컬럼으로 승격
--   - color / material / pattern / details / sub_category_detail 1급 컬럼 신규

ALTER TABLE public.product
    DROP COLUMN IF EXISTS name_original,
    DROP COLUMN IF EXISTS attributes,
    ADD COLUMN IF NOT EXISTS color                text,
    ADD COLUMN IF NOT EXISTS material             text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS pattern              text,
    ADD COLUMN IF NOT EXISTS details              text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS sub_category_detail  text;

-- 검색/필터링 후보 — material 배열 gin
CREATE INDEX IF NOT EXISTS idx_product_material
    ON public.product USING gin (material) WHERE is_active;
