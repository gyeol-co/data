"""상품 이미지 다운로드 — store/images/{product_uuid}/ 에 저장."""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import psycopg

from common.constants import STAGING_DB_URL

logger = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).resolve().parents[1] / "store" / "images"
MAX_WORKERS = 20
TIMEOUT = 15


def _connect():
    conn = psycopg.connect(STAGING_DB_URL)
    conn.execute("SET search_path TO staging")
    return conn


def _download_one(url: str, dest: Path, client: httpx.Client) -> bool:
    if dest.exists():
        return True
    try:
        resp = client.get(url, timeout=TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except Exception as e:
        logger.debug("Failed: %s — %s", url, e)
        return False


def _ext_from_url(url: str) -> str:
    path = url.split("?")[0]
    if "." in path.split("/")[-1]:
        return "." + path.split(".")[-1].lower()
    return ".jpg"


def download_product_images(
    product_id: str,
    thumbnail_url: str | None,
    gallery_urls: list[str],
    client: httpx.Client,
) -> dict:
    """한 상품의 이미지 다운로드. Returns {"downloaded": int, "skipped": int, "failed": int}."""
    product_dir = IMAGES_DIR / str(product_id)
    downloaded = 0
    skipped = 0
    failed = 0

    # thumbnail
    if thumbnail_url:
        ext = _ext_from_url(thumbnail_url)
        dest = product_dir / f"thumb{ext}"
        if dest.exists():
            skipped += 1
        elif _download_one(thumbnail_url, dest, client):
            downloaded += 1
        else:
            failed += 1

    # gallery
    for i, url in enumerate(gallery_urls or []):
        ext = _ext_from_url(url)
        dest = product_dir / f"gallery_{i + 1:03d}{ext}"
        if dest.exists():
            skipped += 1
        elif _download_one(url, dest, client):
            downloaded += 1
        else:
            failed += 1

    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def run(batch_size: int = 0):
    """전체 상품 이미지 다운로드."""
    with _connect() as conn:
        query = """
            SELECT id, thumbnail_url, gallery_image_urls
            FROM product
            WHERE thumbnail_url IS NOT NULL
        """
        if batch_size > 0:
            query += f" LIMIT {batch_size}"

        rows = conn.execute(query).fetchall()

    logger.info("Downloading images for %d products (%d workers)", len(rows), MAX_WORKERS)

    total = {"downloaded": 0, "skipped": 0, "failed": 0}

    with httpx.Client() as client:

        def _process(row):
            pid, thumb, gallery = row
            return pid, download_product_images(
                str(pid), thumb, gallery or [], client,
            )

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_process, row): row for row in rows}
            done = 0
            for future in as_completed(futures):
                pid, result = future.result()
                total["downloaded"] += result["downloaded"]
                total["skipped"] += result["skipped"]
                total["failed"] += result["failed"]
                done += 1
                if done % 100 == 0:
                    logger.info(
                        "Progress: %d/%d products | +%d downloaded, %d skipped, %d failed",
                        done, len(rows),
                        total["downloaded"], total["skipped"], total["failed"],
                    )

    logger.info(
        "Done: %d downloaded, %d skipped, %d failed",
        total["downloaded"], total["skipped"], total["failed"],
    )
    return total


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    run(batch)
