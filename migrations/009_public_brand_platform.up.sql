-- public.brand 에 platform 컬럼 추가.
-- 유저 노출용 (이 브랜드가 어느 편집샵/브랜드샵에서 온 상품인지).

ALTER TABLE public.brand
    ADD COLUMN IF NOT EXISTS platform text;

-- 현재 존재하는 row 들에 staging.brand 로부터 소급 기입.
UPDATE public.brand pb
SET platform = sb.platform
FROM staging.brand sb
WHERE pb.name = sb.name
  AND pb.platform IS NULL;

-- platform 기반 쿼리용 인덱스 (partial — active 만)
CREATE INDEX IF NOT EXISTS idx_brand_platform
    ON public.brand (platform) WHERE is_active;
