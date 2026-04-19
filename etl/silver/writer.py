"""Staging Writer — PostgreSQL UPSERT."""

import logging

import psycopg

from common.constants import STAGING_DB_URL
from common.schemas import RawBrand, RawProduct
from etl.silver.transforms import parse_name, validate_price

logger = logging.getLogger(__name__)


def _connect():
    conn = psycopg.connect(STAGING_DB_URL)
    conn.execute("SET search_path TO staging")
    return conn


def write_brand(platform: str, brand: RawBrand) -> None:
    """staging.brand에 UPSERT. raw 필드만 덮어 쓴다.

    description            — Bronze raw. 매 실행마다 최신 크롤링 반영 (덮어쓰기).
    description_curated    — brand_profile 스킬이 채운다. silver 는 건드리지 않음.
    styles / name_ko       — brand_profile 스킬이 채운다. silver 는 건드리지 않음.
    platform_brand_id / name_eng / logo_url / url — raw. 덮어씀.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO brand (platform, platform_brand_id, name, name_eng, description, logo_url, url)
            VALUES (%(platform)s, %(platform_brand_id)s, %(name)s, %(name_eng)s, %(description)s, %(logo_url)s, %(url)s)
            ON CONFLICT (platform, name) DO UPDATE SET
                platform_brand_id = EXCLUDED.platform_brand_id,
                name_eng          = EXCLUDED.name_eng,
                description       = EXCLUDED.description,
                logo_url          = EXCLUDED.logo_url,
                url               = EXCLUDED.url
                -- description_curated, styles, name_ko 는 curated 영역이라 여기서 건드리지 않음.
            """,
            {
                "platform": platform,
                "platform_brand_id": brand.platform_brand_id,
                "name": brand.name,
                "name_eng": brand.name_eng,
                "description": brand.description,
                "logo_url": brand.logo_url,
                "url": brand.url,
            },
        )
    logger.info("Brand upserted: %s/%s", platform, brand.name)


def write_products(platform: str, brand_id: str, products: list[RawProduct]) -> int:
    """staging.product에 bulk UPSERT. 삽입/갱신된 row 수 반환."""
    if not products:
        return 0

    rows = []
    for p in products:
        name_cleaned, base_name, color = parse_name(platform, p.name)
        original_price, discounted_price = validate_price(p.original_price, p.discounted_price)

        rows.append({
            "platform": platform,
            "platform_brand_id": brand_id,
            "platform_product_id": p.platform_product_id,
            "brand": p.brand,
            "name": name_cleaned,
            "name_original": p.name,
            "base_name": base_name,
            "color": color,
            "category": p.category,
            "original_price": original_price,
            "discounted_price": discounted_price,
            "thumbnail_url": p.thumbnail_url,
            "gallery_image_urls": p.gallery_image_urls,
            "description": p.description_txt,
            "url": p.url,
            "crawled_at": p.metadata.crawled_at,
        })

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO product
                    (platform, platform_brand_id, platform_product_id, brand, name, name_original,
                     base_name, color, category, original_price, discounted_price, thumbnail_url,
                     gallery_image_urls, description, url, crawled_at)
                VALUES
                    (%(platform)s, %(platform_brand_id)s, %(platform_product_id)s, %(brand)s, %(name)s, %(name_original)s,
                     %(base_name)s, %(color)s, %(category)s, %(original_price)s, %(discounted_price)s, %(thumbnail_url)s,
                     %(gallery_image_urls)s, %(description)s, %(url)s, %(crawled_at)s)
                ON CONFLICT (platform, platform_product_id) DO UPDATE SET
                    platform_brand_id = EXCLUDED.platform_brand_id,
                    brand = EXCLUDED.brand,
                    name = EXCLUDED.name,
                    name_original = EXCLUDED.name_original,
                    -- curated/파생 컬럼은 codex/Gemini 결과를 보존 (EXCLUDED 가 비어 있을 때만 raw 적용)
                    base_name  = COALESCE(EXCLUDED.base_name, product.base_name),
                    color      = COALESCE(EXCLUDED.color, product.color),
                    category   = COALESCE(EXCLUDED.category, product.category),
                    -- raw 컬럼은 항상 최신 Bronze 로 덮어씀
                    original_price = EXCLUDED.original_price,
                    discounted_price = EXCLUDED.discounted_price,
                    thumbnail_url = EXCLUDED.thumbnail_url,
                    gallery_image_urls = EXCLUDED.gallery_image_urls,
                    description = EXCLUDED.description,
                    url = EXCLUDED.url,
                    crawled_at = EXCLUDED.crawled_at
                    -- description_curated / sub_category / fit / enrichment_meta /
                    -- embedding / search_text / thumbnail_key / image_keys / vectorized / published
                    -- 은 후속 job 이 쓰므로 silver 에서 건드리지 않음.
                """,
                rows,
            )

    logger.info("Products upserted: %s — %d rows", platform, len(rows))
    return len(rows)
