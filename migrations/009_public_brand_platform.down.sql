DROP INDEX IF EXISTS public.idx_brand_platform;
ALTER TABLE public.brand DROP COLUMN IF EXISTS platform;
