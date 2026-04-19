-- Gyeol 초기 스키마 롤백

-- Event tables (hypertables)
DROP TABLE IF EXISTS search_event;
DROP TABLE IF EXISTS brand_event;
DROP TABLE IF EXISTS product_event;

-- State tables
DROP TABLE IF EXISTS brand_follow;
DROP TABLE IF EXISTS wishlist;

-- Product
DROP INDEX IF EXISTS public.idx_product_embedding;
DROP INDEX IF EXISTS public.idx_product_search;
DROP INDEX IF EXISTS public.idx_product_tags;
DROP INDEX IF EXISTS public.idx_product_fit;
DROP INDEX IF EXISTS public.idx_product_styles;
DROP INDEX IF EXISTS public.idx_product_category;
DROP INDEX IF EXISTS public.idx_product_brand;
DROP TABLE IF EXISTS product;

-- Brand
DROP INDEX IF EXISTS public.idx_brand_created;
DROP INDEX IF EXISTS public.idx_brand_product_added;
DROP INDEX IF EXISTS public.idx_brand_featured;
DROP INDEX IF EXISTS public.idx_brand_styles;
DROP TABLE IF EXISTS brand;

-- Consent
DROP INDEX IF EXISTS public.idx_consent_log_current;
DROP TABLE IF EXISTS consent_log;

-- Oauth
DROP INDEX IF EXISTS public.idx_oauth_user;
DROP TABLE IF EXISTS oauth;

-- User
DROP TABLE IF EXISTS "user";

-- Staging
DROP INDEX IF EXISTS staging.idx_staging_brand_publishable;
DROP INDEX IF EXISTS staging.idx_staging_product_publishable;
DROP TABLE IF EXISTS staging.product;
DROP TABLE IF EXISTS staging.brand;
DROP SCHEMA IF EXISTS staging CASCADE;

-- Enums
DROP TYPE IF EXISTS brand_event_type;
DROP TYPE IF EXISTS product_event_type;
DROP TYPE IF EXISTS consent_method;
DROP TYPE IF EXISTS consent_type;
DROP TYPE IF EXISTS product_category;

-- Extensions (유지 — 다른 곳에서 쓸 수 있음)
-- DROP EXTENSION IF EXISTS timescaledb;
-- DROP EXTENSION IF EXISTS vector;
