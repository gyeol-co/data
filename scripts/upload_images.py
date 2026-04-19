"""S3 업로더 — store/images/{product_id}/*.jpg → s3://{bucket}/{prefix}/{product_id}/...

동작:
    1. staging.product 에서 thumbnail_key IS NULL 인 row 조회
    2. 로컬 store/images/{id}/ 에서 파일 읽어 S3 PUT (멱등 — 이미 있으면 skip)
    3. staging.product.thumbnail_key, image_keys UPDATE
    4. ContentType 은 확장자 기반 자동 설정

환경변수:
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION
    GYEOL_S3_BUCKET (default: gyeol)
    GYEOL_S3_PREFIX (default: products)

실행:
    uv run python scripts/upload_images.py            # 전체
    uv run python scripts/upload_images.py --brand BASTONG
    uv run python scripts/upload_images.py --limit 20
"""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
import httpx
import psycopg
from botocore.exceptions import ClientError

from common.constants import STAGING_DB_URL

logger = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).resolve().parents[1] / "store" / "images"
BUCKET = os.environ.get("GYEOL_S3_BUCKET", "gyeol")
PREFIX = os.environ.get("GYEOL_S3_PREFIX", "products").strip("/")
MAX_WORKERS = int(os.environ.get("GYEOL_S3_WORKERS", "8"))


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(STAGING_DB_URL)
    conn.execute("SET search_path TO staging, public")
    return conn


def _s3_client():
    return boto3.client("s3")


def _key_for(product_id: str, filename: str) -> str:
    return f"{PREFIX}/{product_id}/{filename}"


def _content_type(path: Path) -> str:
    ctype, _ = mimetypes.guess_type(path.name)
    return ctype or "application/octet-stream"


def _exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _upload_file(s3, path: Path, key: str) -> bool:
    if _exists(s3, key):
        return True
    s3.upload_file(
        Filename=str(path),
        Bucket=BUCKET,
        Key=key,
        ExtraArgs={"ContentType": _content_type(path), "CacheControl": "public, max-age=31536000"},
    )
    return True


def _ext_from_url(url: str) -> str:
    tail = url.split("?")[0].rsplit("/", 1)[-1]
    if "." in tail:
        return "." + tail.rsplit(".", 1)[-1].lower()
    return ".jpg"


def _guess_content_type_from_url(url: str) -> str:
    ctype, _ = mimetypes.guess_type(url.split("?")[0])
    return ctype or "image/jpeg"


def _upload_url(s3, http: httpx.Client, url: str, key: str) -> bool:
    if _exists(s3, key):
        return True
    resp = http.get(url, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "").split(";")[0].strip() or _guess_content_type_from_url(url)
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=resp.content,
        ContentType=ctype,
        CacheControl="public, max-age=31536000",
    )
    return True


def _upload_one(
    product_id: str,
    thumbnail_url: str | None,
    gallery_urls: list[str] | None,
    s3,
    http: httpx.Client,
) -> dict:
    """한 상품의 모든 이미지 업로드. 로컬 우선, 없으면 URL에서 직접."""
    product_dir = IMAGES_DIR / product_id
    thumb_key: str | None = None
    gallery_keys: list[str] = []
    uploaded = 0
    skipped = 0
    failed = 0

    # ── thumbnail ──
    local_thumb = None
    if product_dir.exists():
        for f in sorted(product_dir.iterdir()):
            if f.name.startswith("thumb"):
                local_thumb = f
                break

    if local_thumb:
        key = _key_for(product_id, local_thumb.name)
        try:
            if _exists(s3, key):
                skipped += 1
            else:
                _upload_file(s3, local_thumb, key)
                uploaded += 1
            thumb_key = key
        except Exception as e:
            failed += 1
            logger.warning("thumb(local) failed %s: %s", local_thumb, e)
    elif thumbnail_url:
        key = _key_for(product_id, f"thumb{_ext_from_url(thumbnail_url)}")
        try:
            if _exists(s3, key):
                skipped += 1
            else:
                _upload_url(s3, http, thumbnail_url, key)
                uploaded += 1
            thumb_key = key
        except Exception as e:
            failed += 1
            logger.warning("thumb(url) failed %s: %s", thumbnail_url, e)

    # ── gallery ──
    local_gallery = []
    if product_dir.exists():
        for f in sorted(product_dir.iterdir()):
            if f.name.startswith("gallery_"):
                local_gallery.append(f)

    if local_gallery:
        for f in local_gallery:
            key = _key_for(product_id, f.name)
            try:
                if _exists(s3, key):
                    skipped += 1
                else:
                    _upload_file(s3, f, key)
                    uploaded += 1
                gallery_keys.append(key)
            except Exception as e:
                failed += 1
                logger.warning("gallery(local) failed %s: %s", f, e)
    else:
        for i, url in enumerate(gallery_urls or []):
            key = _key_for(product_id, f"gallery_{i + 1:03d}{_ext_from_url(url)}")
            try:
                if _exists(s3, key):
                    skipped += 1
                else:
                    _upload_url(s3, http, url, key)
                    uploaded += 1
                gallery_keys.append(key)
            except Exception as e:
                failed += 1
                logger.warning("gallery(url) failed %s: %s", url, e)

    return {
        "thumb_key": thumb_key,
        "gallery_keys": gallery_keys,
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
    }


def _fetch_pending(conn: psycopg.Connection, brand: str | None, limit: int | None) -> list[dict]:
    where = ["thumbnail_url IS NOT NULL", "thumbnail_key IS NULL"]
    params: list[object] = []
    if brand:
        where.append("brand = %s")
        params.append(brand)
    sql = f"""
        SELECT id::text, thumbnail_url, gallery_image_urls
        FROM product
        WHERE {' AND '.join(where)}
        ORDER BY id
    """
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [
        {"id": r[0], "thumbnail_url": r[1], "gallery_urls": r[2] or []}
        for r in rows
    ]


def _update_keys(conn: psycopg.Connection, product_id: str, thumb_key: str | None, gallery_keys: list[str]) -> None:
    conn.execute(
        """
        UPDATE product
        SET thumbnail_key = COALESCE(%s, thumbnail_key),
            image_keys    = %s
        WHERE id = %s::uuid
        """,
        (thumb_key, gallery_keys, product_id),
    )


def run(brand: str | None = None, limit: int | None = None) -> dict:
    s3 = _s3_client()
    with _connect() as conn:
        pending = _fetch_pending(conn, brand, limit)
        if not pending:
            logger.info("nothing to upload (pending=0)")
            return {"products": 0, "uploaded": 0, "skipped": 0, "failed": 0}

        logger.info("uploading images for %d products (workers=%d, bucket=%s)",
                    len(pending), MAX_WORKERS, BUCKET)

        total = {"uploaded": 0, "skipped": 0, "failed": 0}
        updated_count = 0

        with httpx.Client(headers={"User-Agent": "gyeol-uploader/1.0"}) as http:

            def _work(row: dict) -> tuple[str, dict]:
                return row["id"], _upload_one(
                    row["id"], row["thumbnail_url"], row["gallery_urls"], s3, http,
                )

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {pool.submit(_work, row): row for row in pending}
                for fut in as_completed(futures):
                    pid, res = fut.result()
                    total["uploaded"] += res["uploaded"]
                    total["skipped"] += res["skipped"]
                    total["failed"] += res["failed"]

                    if res["thumb_key"]:
                        _update_keys(conn, pid, res["thumb_key"], res["gallery_keys"])
                        updated_count += 1
                        if updated_count % 50 == 0:
                            conn.commit()
                            logger.info(
                                "progress: %d/%d rows | +%d uploaded, %d skipped, %d failed",
                                updated_count, len(pending),
                                total["uploaded"], total["skipped"], total["failed"],
                            )

        conn.commit()

        logger.info(
            "done: %d rows updated | %d uploaded, %d skipped, %d failed",
            updated_count, total["uploaded"], total["skipped"], total["failed"],
        )
        return {
            "products": len(pending),
            "updated_rows": updated_count,
            **total,
        }


def main():
    parser = argparse.ArgumentParser(description="Upload product images to S3")
    parser.add_argument("--brand", help="특정 브랜드만 (staging.product.brand 일치)")
    parser.add_argument("--limit", type=int, help="최대 N개 상품만")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    result = run(brand=args.brand, limit=args.limit)
    print(result)


if __name__ == "__main__":
    main()
