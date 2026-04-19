-- Gyeol 초기 스키마: staging (ETL) + public (서비스)
-- 단일 init 파일로 통합. 프로덕션 이전엔 이 파일만 유지.

-- =====================
-- Extensions
-- =====================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- =====================
-- Enum types
-- =====================
CREATE TYPE product_category AS ENUM ('outer', 'top', 'bottom', 'acc', 'shoes');

CREATE TYPE consent_type     AS ENUM ('terms', 'privacy', 'marketing', 'age_14_plus');
CREATE TYPE consent_method   AS ENUM ('signup', 'settings', 're_consent');

CREATE TYPE product_event_type AS ENUM (
    'view',
    'click',
    'outlink',
    'wishlist_add',
    'wishlist_remove',
    'hide'
);

CREATE TYPE brand_event_type AS ENUM (
    'view',
    'click',
    'follow',
    'unfollow'
);

-- =====================
-- staging schema: ETL 파이프라인용
-- =====================
CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE staging.brand (
    id                  uuid PRIMARY KEY DEFAULT uuidv7(),
    platform            text NOT NULL,
    platform_brand_id   text,
    name                text NOT NULL,
    name_eng            text,
    name_ko             text,
    description         text,
    logo_url            text,
    styles              text[] DEFAULT '{}',
    url                 text NOT NULL,
    published           timestamptz,
    created             timestamptz NOT NULL DEFAULT now(),
    UNIQUE(platform, name)
);

CREATE TABLE staging.product (
    id                   uuid PRIMARY KEY DEFAULT uuidv7(),
    platform             text NOT NULL,
    platform_brand_id    text,
    platform_product_id  text NOT NULL,
    brand                text NOT NULL,
    name                 text NOT NULL,
    name_original        text NOT NULL,
    base_name            text,
    category             product_category,
    sub_category         text,
    fit                  text,
    color                text,
    original_price       int,
    discounted_price     int,
    thumbnail_url        text,
    gallery_image_urls   text[],
    description          text,
    description_curated  text,
    enrichment_meta      jsonb,
    url                  text NOT NULL,
    crawled_at           timestamptz NOT NULL,
    vectorized           timestamptz,
    published            timestamptz,
    created              timestamptz NOT NULL DEFAULT now(),
    UNIQUE(platform, platform_product_id)
);

-- staging partial indexes — public 이관 대상 후보
CREATE INDEX idx_staging_product_publishable ON staging.product (id)
    WHERE published IS NULL
      AND category IS NOT NULL
      AND original_price IS NOT NULL
      AND thumbnail_url IS NOT NULL
      AND vectorized IS NOT NULL;

CREATE INDEX idx_staging_brand_publishable ON staging.brand (id)
    WHERE published IS NULL
      AND description IS NOT NULL
      AND styles != '{}';

-- =====================
-- public schema: 서비스용
-- =====================

-- user (reserved keyword, quoted)
CREATE TABLE "user" (
    id                uuid PRIMARY KEY DEFAULT uuidv7(),
    email             text UNIQUE,
    password_hash     text,
    password_changed  timestamptz,
    display_name      text NOT NULL,
    styles            text[] NOT NULL DEFAULT '{}',
    is_active         boolean NOT NULL DEFAULT true,
    visited           timestamptz,
    created           timestamptz NOT NULL DEFAULT now(),
    updated           timestamptz NOT NULL DEFAULT now()
);

-- oauth (1 user : N providers — kakao/naver/google)
CREATE TABLE oauth (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id        uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    provider       text NOT NULL,
    provider_uid   text NOT NULL,
    provider_email text,
    created        timestamptz NOT NULL DEFAULT now(),
    UNIQUE(provider, provider_uid)
);
CREATE INDEX idx_oauth_user ON oauth (user_id);

-- consent_log (append-only: 동의/철회 모두 INSERT. user 삭제 시 row 유지 + user_id NULL)
CREATE TABLE consent_log (
    id              uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id         uuid REFERENCES "user"(id) ON DELETE SET NULL,
    type            consent_type NOT NULL,
    policy_version  text NOT NULL,
    is_granted      boolean NOT NULL,
    method          consent_method NOT NULL,
    ip_address      inet,
    user_agent      text,
    created         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_consent_log_current ON consent_log (user_id, type, created DESC);

-- brand
CREATE TABLE brand (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    slug           text NOT NULL UNIQUE,
    name           text NOT NULL,
    name_ko        text,
    description    text,
    logo_key       text,
    cover_key      text,
    styles         text[] NOT NULL DEFAULT '{}',
    website_url    text,
    is_featured    boolean NOT NULL DEFAULT false,
    is_active      boolean NOT NULL DEFAULT true,
    product_added  timestamptz,
    created        timestamptz NOT NULL DEFAULT now(),
    updated        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_brand_styles        ON brand USING gin (styles);
CREATE INDEX idx_brand_featured      ON brand (is_featured) WHERE is_active;
CREATE INDEX idx_brand_product_added ON brand (product_added DESC NULLS LAST) WHERE is_active;
CREATE INDEX idx_brand_created       ON brand (created DESC) WHERE is_active;

-- product
CREATE TABLE product (
    id                  uuid PRIMARY KEY DEFAULT uuidv7(),
    url                 text NOT NULL,
    brand_id            uuid NOT NULL REFERENCES brand(id),
    name                text NOT NULL,
    name_original       text,
    description         text,
    thumbnail_key       text NOT NULL,
    image_keys          text[] NOT NULL DEFAULT '{}',
    category            product_category NOT NULL,
    subcategory         text,
    styles              text[] NOT NULL DEFAULT '{}',
    fit                 text,
    tags                text[] NOT NULL DEFAULT '{}',
    attributes          jsonb NOT NULL DEFAULT '{}',
    original_price      int NOT NULL,
    discounted_price    int,
    discount_rate       numeric GENERATED ALWAYS AS (
        CASE
            WHEN discounted_price IS NOT NULL AND original_price > 0
            THEN (original_price - discounted_price)::numeric / original_price
            ELSE 0
        END
    ) STORED,
    search_text         text,
    search_vector       tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(search_text, ''))
    ) STORED,
    embedding           vector(1024),
    is_active           boolean NOT NULL DEFAULT true,
    is_soldout          boolean NOT NULL DEFAULT false,
    synced              timestamptz,
    created             timestamptz NOT NULL DEFAULT now(),
    updated             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_product_brand     ON product (brand_id) WHERE is_active;
CREATE INDEX idx_product_category  ON product (category) WHERE is_active;
CREATE INDEX idx_product_styles    ON product USING gin (styles);
CREATE INDEX idx_product_fit       ON product (fit) WHERE is_active;
CREATE INDEX idx_product_tags      ON product USING gin (tags);
CREATE INDEX idx_product_search    ON product USING gin (search_vector);
CREATE INDEX idx_product_embedding ON product USING hnsw (embedding vector_cosine_ops);

-- wishlist (user <-> product M:N, 앱에서 유저당 500개 제한)
CREATE TABLE wishlist (
    user_id     uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    product_id  uuid NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    created     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, product_id)
);

CREATE INDEX idx_wishlist_product ON wishlist (product_id);

-- brand_follow (user <-> brand M:N)
CREATE TABLE brand_follow (
    user_id   uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    brand_id  uuid NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    created   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, brand_id)
);

CREATE INDEX idx_brand_follow_brand ON brand_follow (brand_id);

-- =====================
-- Event tables (TimescaleDB hypertables, append-only)
-- =====================

-- product_event
CREATE TABLE product_event (
    id         uuid NOT NULL DEFAULT uuidv7(),
    user_id    uuid NOT NULL,
    type       product_event_type NOT NULL,
    product_id uuid NOT NULL,
    properties jsonb NOT NULL DEFAULT '{}',
    created    timestamptz NOT NULL DEFAULT now()
);

SELECT create_hypertable(
    'product_event', 'created',
    chunk_time_interval => INTERVAL '7 days'
);

ALTER TABLE product_event SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'user_id, type',
    timescaledb.compress_orderby   = 'created DESC'
);
SELECT add_compression_policy('product_event', INTERVAL '7 days');

CREATE INDEX idx_product_event_user    ON product_event (user_id, created DESC);
CREATE INDEX idx_product_event_product ON product_event (product_id, created DESC);

-- brand_event
CREATE TABLE brand_event (
    id         uuid NOT NULL DEFAULT uuidv7(),
    user_id    uuid NOT NULL,
    type       brand_event_type NOT NULL,
    brand_id   uuid NOT NULL,
    properties jsonb NOT NULL DEFAULT '{}',
    created    timestamptz NOT NULL DEFAULT now()
);

SELECT create_hypertable(
    'brand_event', 'created',
    chunk_time_interval => INTERVAL '7 days'
);

ALTER TABLE brand_event SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'user_id, type',
    timescaledb.compress_orderby   = 'created DESC'
);
SELECT add_compression_policy('brand_event', INTERVAL '7 days');

CREATE INDEX idx_brand_event_user  ON brand_event (user_id, created DESC);
CREATE INDEX idx_brand_event_brand ON brand_event (brand_id, created DESC);

-- search_event
CREATE TABLE search_event (
    id           uuid NOT NULL DEFAULT uuidv7(),
    user_id      uuid NOT NULL,
    query        text NOT NULL,
    result_count int NOT NULL,
    created      timestamptz NOT NULL DEFAULT now()
);

SELECT create_hypertable(
    'search_event', 'created',
    chunk_time_interval => INTERVAL '7 days'
);

ALTER TABLE search_event SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'user_id',
    timescaledb.compress_orderby   = 'created DESC'
);
SELECT add_compression_policy('search_event', INTERVAL '7 days');

CREATE INDEX idx_search_user  ON search_event (user_id, created DESC);
CREATE INDEX idx_search_query ON search_event USING gin (to_tsvector('simple', query));
