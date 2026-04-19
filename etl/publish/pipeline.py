"""staging → public 이관 (publish).

운영 모델:
    매 N분 돌면서 publishable 조건을 만족한 row 를 public.brand/product 에 INSERT
    후 staging.product.published / staging.brand.published 에 스탬프 찍음.
    멱등 — 같은 row 두 번 처리되지 않는다 (staging.published 가 NULL 인 것만 선택).

실패 정책:
    row 단위 트랜잭션. 예외 발생 시 ROLLBACK + 로그만 남기고 다음 row 로 진행.
    별도 publish_failed_at 컬럼 없음 — 재시도 허용 (실패 원인은 로그로 추적).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import psycopg

from common.constants import STAGING_DB_URL

logger = logging.getLogger(__name__)


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(STAGING_DB_URL)
    conn.execute("SET search_path TO public, staging")
    return conn


# --- slug ---

_SLUG_RE_SAFE = re.compile(r"[^a-z0-9]+")
_SLUG_RE_COLLAPSE = re.compile(r"-+")


def slugify(name: str) -> str:
    """ASCII slug 생성. 한글/기호는 날린다. 빈 결과면 'unknown'."""
    s = name.lower().strip()
    s = _SLUG_RE_SAFE.sub("-", s)
    s = _SLUG_RE_COLLAPSE.sub("-", s).strip("-")
    return s or "unknown"


# --- brand publish ---

_BRAND_UPSERT_SQL = """
INSERT INTO public.brand (
    slug, platform, name, name_ko, description, styles, website_url, product_added
) VALUES (
    %(slug)s, %(platform)s, %(name)s, %(name_ko)s, %(description)s,
    %(styles)s, %(website_url)s, now()
)
ON CONFLICT (slug) DO UPDATE SET
    platform      = EXCLUDED.platform,
    name          = EXCLUDED.name,
    name_ko       = COALESCE(EXCLUDED.name_ko, public.brand.name_ko),
    description   = COALESCE(EXCLUDED.description, public.brand.description),
    styles        = CASE
                      WHEN array_length(EXCLUDED.styles, 1) > 0 THEN EXCLUDED.styles
                      ELSE public.brand.styles
                    END,
    website_url   = EXCLUDED.website_url,
    product_added = now(),
    updated       = now()
RETURNING id
"""


def upsert_brand(conn: psycopg.Connection, staging_brand: dict) -> uuid.UUID:
    """staging.brand → public.brand UPSERT. public.brand.id 반환."""
    slug = slugify(staging_brand["name_eng"] or staging_brand["name"])
    description = staging_brand.get("description_curated") or staging_brand.get("description")
    params = {
        "slug": slug,
        "platform": staging_brand.get("platform"),
        "name": staging_brand["name"],
        "name_ko": staging_brand.get("name_ko"),
        "description": description,
        "styles": staging_brand.get("styles") or [],
        "website_url": staging_brand.get("url"),
    }
    row = conn.execute(_BRAND_UPSERT_SQL, params).fetchone()
    return row[0]


def _mark_brand_published(conn: psycopg.Connection, staging_brand_id: uuid.UUID) -> None:
    conn.execute(
        "UPDATE staging.brand SET published = now() WHERE id = %s",
        (staging_brand_id,),
    )


# --- product publish ---

_PRODUCT_INSERT_SQL = """
INSERT INTO public.product (
    url, brand_id, name, description,
    thumbnail_key, image_keys,
    category, subcategory, sub_category_detail,
    styles, fit, color,
    material, pattern, details,
    original_price, discounted_price,
    search_text, embedding
)
VALUES (
    %(url)s, %(brand_id)s, %(name)s, %(description)s,
    %(thumbnail_key)s, %(image_keys)s,
    %(category)s, %(subcategory)s, %(sub_category_detail)s,
    %(styles)s, %(fit)s, %(color)s,
    %(material)s, %(pattern)s, %(details)s,
    %(original_price)s, %(discounted_price)s,
    %(search_text)s, %(embedding)s::vector
)
"""


def _str_list_from_meta(meta: Any, key: str) -> list[str]:
    if not isinstance(meta, dict):
        return []
    v = meta.get(key) or []
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in v if x]


def _str_from_meta(meta: Any, key: str) -> str | None:
    if not isinstance(meta, dict):
        return None
    v = meta.get(key)
    return str(v) if v else None


def _embedding_literal(emb: Any) -> str | None:
    """psycopg 의 list/array → pgvector literal 변환."""
    if emb is None:
        return None
    # psycopg3 는 list[float] 로 받음
    if isinstance(emb, list):
        return "[" + ",".join(f"{v:.6f}" for v in emb) + "]"
    # 문자열로 이미 온 경우
    if isinstance(emb, str):
        return emb
    return None


def publish_product(conn: psycopg.Connection, row: dict, brand_id: uuid.UUID) -> None:
    """한 row 를 public.product 에 INSERT."""
    meta = row.get("enrichment_meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    params = {
        "url": row["url"],
        "brand_id": brand_id,
        "name": row["name"],
        "description": row.get("description_curated") or row.get("description"),
        "thumbnail_key": row["thumbnail_key"],
        "image_keys": row.get("image_keys") or [],
        "category": row["category"],
        "subcategory": row.get("sub_category"),
        "sub_category_detail": _str_from_meta(meta, "sub_category_detail"),
        "styles": _str_list_from_meta(meta, "style") or _str_list_from_meta(meta, "styles"),
        "fit": row.get("fit"),
        "color": row.get("color"),
        "material": _str_list_from_meta(meta, "material"),
        "pattern": _str_from_meta(meta, "pattern"),
        "details": _str_list_from_meta(meta, "details"),
        "original_price": row["original_price"],
        "discounted_price": row.get("discounted_price"),
        "search_text": row.get("search_text"),
        "embedding": _embedding_literal(row.get("embedding")),
    }
    conn.execute(_PRODUCT_INSERT_SQL, params)


def _mark_product_published(conn: psycopg.Connection, staging_product_id: uuid.UUID) -> None:
    conn.execute(
        "UPDATE staging.product SET published = now() WHERE id = %s",
        (staging_product_id,),
    )


# --- batch ---

_PUBLISHABLE_SQL = """
SELECT
    p.id,
    p.platform,
    p.platform_brand_id,
    p.brand,
    p.name,
    p.name_original,
    p.base_name,
    p.category,
    p.sub_category,
    p.fit,
    p.color,
    p.original_price,
    p.discounted_price,
    p.thumbnail_key,
    p.image_keys,
    p.description,
    p.description_curated,
    p.enrichment_meta,
    p.url,
    p.search_text,
    p.embedding,
    b.id           AS staging_brand_id,
    b.platform     AS brand_platform,
    b.name         AS brand_name,
    b.name_eng     AS brand_name_eng,
    b.name_ko      AS brand_name_ko,
    b.description  AS brand_description_raw,
    b.description_curated AS brand_description_curated,
    b.styles       AS brand_styles,
    b.url          AS brand_url
FROM staging.product p
JOIN staging.brand b
     ON b.platform = p.platform AND b.name = p.brand
WHERE p.published IS NULL
  AND p.category IS NOT NULL
  AND p.original_price IS NOT NULL
  AND p.thumbnail_key IS NOT NULL
  AND p.embedding IS NOT NULL
  AND p.search_text IS NOT NULL
  AND p.vectorized IS NOT NULL
ORDER BY p.id
LIMIT %s
FOR UPDATE OF p SKIP LOCKED
"""


def publish_batch(limit: int = 100) -> dict:
    """한 배치 이관. 브랜드는 caller-local 캐시로 중복 UPSERT 방지.

    Returns:
        {"processed": int, "success": int, "failed": int, "remaining": int}
    """
    success = 0
    failed = 0

    with _connect() as conn:
        rows = conn.execute(_PUBLISHABLE_SQL, (limit,)).fetchall()
        if not rows:
            return {"processed": 0, "success": 0, "failed": 0, "remaining": 0}

        cols = [
            "id", "platform", "platform_brand_id", "brand", "name", "name_original",
            "base_name", "category", "sub_category", "fit", "color",
            "original_price", "discounted_price", "thumbnail_key", "image_keys",
            "description", "description_curated", "enrichment_meta", "url",
            "search_text", "embedding",
            "staging_brand_id", "brand_platform",
            "brand_name", "brand_name_eng", "brand_name_ko",
            "brand_description_raw", "brand_description_curated",
            "brand_styles", "brand_url",
        ]

        # 브랜드 UPSERT 캐시 (batch 내 중복 방지)
        brand_id_cache: dict[uuid.UUID, uuid.UUID] = {}
        brands_upserted: set[uuid.UUID] = set()

        for raw in rows:
            row = dict(zip(cols, raw))
            try:
                staging_brand_id: uuid.UUID = row["staging_brand_id"]

                if staging_brand_id not in brand_id_cache:
                    public_brand_id = upsert_brand(
                        conn,
                        {
                            "platform": row["brand_platform"],
                            "name": row["brand_name"],
                            "name_eng": row["brand_name_eng"],
                            "name_ko": row["brand_name_ko"],
                            "description": row["brand_description_raw"],
                            "description_curated": row["brand_description_curated"],
                            "styles": row["brand_styles"],
                            "url": row["brand_url"],
                        },
                    )
                    brand_id_cache[staging_brand_id] = public_brand_id
                    brands_upserted.add(staging_brand_id)

                brand_id = brand_id_cache[staging_brand_id]
                publish_product(conn, row, brand_id)
                _mark_product_published(conn, row["id"])

                conn.commit()
                success += 1
            except Exception as e:
                conn.rollback()
                failed += 1
                logger.warning("publish failed pid=%s name=%s err=%s",
                               row.get("id"), str(row.get("name"))[:40], e)

        # 해당 브랜드의 publishable 상품이 모두 published 됐으면 brand.published 도 스탬프
        for staging_brand_id in brands_upserted:
            try:
                remaining_in_brand = conn.execute(
                    """
                    SELECT count(*) FROM staging.product p
                    JOIN staging.brand b ON b.platform = p.platform AND b.name = p.brand
                    WHERE b.id = %s
                      AND p.published IS NULL
                      AND p.category IS NOT NULL
                      AND p.embedding IS NOT NULL
                      AND p.search_text IS NOT NULL
                      AND p.thumbnail_key IS NOT NULL
                    """,
                    (staging_brand_id,),
                ).fetchone()[0]
                if remaining_in_brand == 0:
                    _mark_brand_published(conn, staging_brand_id)
                    conn.commit()
            except Exception:
                conn.rollback()

        remaining = conn.execute(
            """
            SELECT count(*) FROM staging.product
            WHERE published IS NULL
              AND category IS NOT NULL
              AND thumbnail_key IS NOT NULL
              AND embedding IS NOT NULL
              AND search_text IS NOT NULL
              AND original_price IS NOT NULL
              AND vectorized IS NOT NULL
            """
        ).fetchone()[0]

    logger.info("publish batch: +%d success, +%d failed, %d remaining",
                success, failed, remaining)
    return {
        "processed": len(rows),
        "success": success,
        "failed": failed,
        "remaining": remaining,
    }


def publish_all(batch_size: int = 100, max_batches: int = 100) -> dict:
    total_success = 0
    total_failed = 0
    for i in range(1, max_batches + 1):
        r = publish_batch(batch_size)
        total_success += r["success"]
        total_failed += r["failed"]
        if r["processed"] == 0:
            break
    logger.info("publish pass complete: %d success, %d failed",
                total_success, total_failed)
    return {"total_success": total_success, "total_failed": total_failed}
