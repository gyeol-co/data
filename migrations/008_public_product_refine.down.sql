DROP INDEX IF EXISTS public.idx_product_material;

ALTER TABLE public.product
    DROP COLUMN IF EXISTS color,
    DROP COLUMN IF EXISTS material,
    DROP COLUMN IF EXISTS pattern,
    DROP COLUMN IF EXISTS details,
    DROP COLUMN IF EXISTS sub_category_detail,
    ADD COLUMN IF NOT EXISTS name_original text,
    ADD COLUMN IF NOT EXISTS attributes jsonb NOT NULL DEFAULT '{}';
