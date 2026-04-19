"""Gold enrichment pipeline — staging.product enrichment 오케스트레이션."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import psycopg

from common.constants import STAGING_DB_URL
from etl.gold.enrichment import EnrichmentResult, enrich_product

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
MAX_WORKERS = 20


def _connect():
    conn = psycopg.connect(STAGING_DB_URL)
    conn.execute("SET search_path TO staging")
    return conn


def _fetch_pending(conn, batch_size: int = BATCH_SIZE) -> list[dict]:
    """enrichment 대상: base_name 그룹당 대표 1개만 조회."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (platform, brand, base_name)
               id, platform, category, name, brand, base_name, description,
               thumbnail_url, gallery_image_urls
        FROM product
        WHERE category IS NOT NULL
          AND description_curated IS NULL
        ORDER BY platform, brand, base_name, id
        LIMIT %s
        """,
        (batch_size,),
    ).fetchall()

    cols = ["id", "platform", "category", "name", "brand", "base_name",
            "description", "thumbnail_url", "gallery_image_urls"]
    return [dict(zip(cols, row)) for row in rows]


def _update_group(conn, product: dict, result: EnrichmentResult) -> int:
    """enrichment 결과를 base_name 그룹 전체에 UPDATE. 반환: 업데이트된 row 수.

    base_name 이 NULL 인 그룹도 매칭되도록 IS NOT DISTINCT FROM 사용
    (NULL = NULL 은 항상 False 이지만 IS NOT DISTINCT FROM 은 True).
    """
    cur = conn.execute(
        """
        UPDATE product
        SET sub_category = %(sub_category)s,
            fit = %(fit)s,
            description_curated = %(description_curated)s,
            enrichment_meta = %(enrichment_meta)s
        WHERE platform = %(platform)s
          AND brand = %(brand)s
          AND base_name IS NOT DISTINCT FROM %(base_name)s
          AND description_curated IS NULL
        """,
        {
            "platform": product["platform"],
            "brand": product["brand"],
            "base_name": product["base_name"],
            "sub_category": result.sub_category,
            "fit": result.fit,
            "description_curated": result.description_ko,
            "enrichment_meta": json.dumps(result.enrichment_meta, ensure_ascii=False),
        },
    )
    return cur.rowcount


def _enrich_one(product: dict) -> tuple[str, str, EnrichmentResult | None, str | None]:
    """단일 상품 enrichment (스레드에서 실행)."""
    pid = product["id"]
    name = product["name"]
    try:
        result = enrich_product(
            product_id=str(pid),
            category=product["category"],
            name=name,
            brand=product["brand"] or "",
            description=product["description"],
            thumbnail_url=product["thumbnail_url"],
            gallery_urls=product["gallery_image_urls"] or [],
        )
        return pid, name, result, None
    except Exception as e:
        return pid, name, None, str(e)


def process_batch(
    *,
    batch_size: int = BATCH_SIZE,
    workers: int = MAX_WORKERS,
    include_images: bool = True,
) -> dict:
    """한 배치 병렬 enrichment.

    Returns:
        {"processed": int, "success": int, "failed": int, "remaining": int}
    """
    with _connect() as conn:
        pending = _fetch_pending(conn, batch_size)
        if not pending:
            logger.info("No pending products for enrichment")
            return {"processed": 0, "success": 0, "failed": 0, "remaining": 0}

        logger.info("Enrichment batch: %d products (%d workers)", len(pending), workers)
        success = 0
        failed = 0

        total_rows_updated = 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_enrich_one, p): p for p in pending}
            for future in as_completed(futures):
                pid, name, result, error = future.result()
                product = futures[future]
                if result:
                    try:
                        rows_updated = _update_group(conn, product, result)
                        conn.commit()
                        success += 1
                        total_rows_updated += rows_updated
                        logger.info(
                            "Enriched: %s → %s/%s (%s) [%d rows]",
                            name[:30], result.sub_category,
                            result.sub_category_detail, result.fit or "n/a",
                            rows_updated,
                        )
                    except Exception as e:
                        conn.rollback()
                        failed += 1
                        logger.warning("DB write failed: %s — %s", name[:30], e)
                else:
                    failed += 1
                    logger.warning("Failed: %s — %s", name[:30], error)

        remaining = conn.execute(
            """
            SELECT count(*) FROM product
            WHERE category IS NOT NULL AND description_curated IS NULL
            """,
        ).fetchone()[0]

    return {
        "processed": len(pending),
        "success": success,
        "failed": failed,
        "remaining": remaining,
    }


def process_all(
    *,
    batch_size: int = BATCH_SIZE,
    workers: int = MAX_WORKERS,
    include_images: bool = True,
    max_batches: int = 200,
) -> dict:
    """전체 상품 enrichment. 배치 반복.

    Returns:
        {"total_success": int, "total_failed": int}
    """
    total_success = 0
    total_failed = 0

    for batch_no in range(1, max_batches + 1):
        result = process_batch(
            batch_size=batch_size,
            workers=workers,
            include_images=include_images,
        )
        total_success += result["success"]
        total_failed += result["failed"]

        logger.info(
            "Batch %d done: +%d success, +%d failed, %d remaining",
            batch_no, result["success"], result["failed"], result["remaining"],
        )

        if result["remaining"] == 0 or result["processed"] == 0:
            break

    logger.info(
        "Enrichment complete: %d success, %d failed", total_success, total_failed,
    )
    return {"total_success": total_success, "total_failed": total_failed}
