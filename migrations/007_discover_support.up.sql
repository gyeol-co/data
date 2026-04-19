-- Discover feature 지원 — Popular 점수 + For You 추천 캐시.
-- Popular 정렬은 product.popularity DESC, id DESC. 점수 채우기는 data pipeline 책임.
-- For You는 user_product_affinity(rank)를 읽기 전용으로 사용. BE는 읽기만, data pipeline이 rebuild.

-- =====================
-- product.popularity — Discover "Popular" 탭 정렬 키
-- =====================
ALTER TABLE product
    ADD COLUMN popularity double precision NOT NULL DEFAULT 0;

-- Popular 피드 keyset 정렬: (popularity DESC, id DESC) composite index.
-- `WHERE is_active` partial로 비활성 상품 제외.
CREATE INDEX idx_product_popularity
    ON product (popularity DESC, id DESC)
    WHERE is_active;

-- =====================
-- user_product_affinity — For You precomputed recommendation cache
-- =====================
-- rank 0부터 오름차순. 낮을수록 먼저 노출. data pipeline이 DELETE + INSERT 배치로 관리.
-- created = 마지막 rebuild 시점 (staleness 판단은 data pipeline 책임).
-- BE 관점: (user_id, product_id, rank) 읽기만.

CREATE TABLE user_product_affinity (
    user_id     uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    product_id  uuid NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    rank        int  NOT NULL,
    created     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, product_id)
);

-- For You 페이지네이션 — `WHERE user_id = ? AND rank > ? ORDER BY rank`.
CREATE INDEX idx_user_product_affinity_order
    ON user_product_affinity (user_id, rank);
