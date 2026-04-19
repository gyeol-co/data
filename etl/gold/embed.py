"""Text embedding — staging.product.description_curated 등 → bge-m3 (TEI) → embedding column.

TEI 서버:
    http://localhost:8080 (로컬) or http://embedding:80 (docker network)
    POST /embed  body={"inputs": [text, ...]} → [[1024d], ...]

실행:
    embedding 타깃: description_curated IS NOT NULL AND vectorized IS NULL
    배치당 N개씩 TEI 한 번 호출, 결과를 UPDATE.
"""

from __future__ import annotations

import logging
import os
import time

import httpx
import psycopg

from common.constants import STAGING_DB_URL

logger = logging.getLogger(__name__)

TEI_URL = os.environ.get("GYEOL_TEI_URL", "http://localhost:8080")
TEI_BATCH_SIZE = int(os.environ.get("GYEOL_TEI_BATCH", "16"))
TEI_TIMEOUT = float(os.environ.get("GYEOL_TEI_TIMEOUT", "60"))

# bge-m3 실질 입력 한도 여유두고 자름 (chars — 토큰 아님, 대략 넉넉한 수)
TEXT_CHAR_CAP = 2000


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(STAGING_DB_URL)
    # vector 타입은 public 스키마에 있으므로 search_path에 public 포함 필수
    conn.execute("SET search_path TO staging, public")
    return conn


def _build_text(row: dict) -> str:
    """임베딩 입력 텍스트. 상품명 + sub_category + 큐레이션 텍스트 결합."""
    parts: list[str] = []
    if row.get("name"):
        parts.append(row["name"])
    if row.get("sub_category"):
        parts.append(str(row["sub_category"]))
    meta = row.get("enrichment_meta") or {}
    if meta.get("sub_category_detail"):
        parts.append(meta["sub_category_detail"])
    if row.get("description_curated"):
        parts.append(row["description_curated"])
    text = " | ".join(p for p in parts if p)
    return text[:TEXT_CHAR_CAP]


def _fetch_pending(
    conn: psycopg.Connection,
    batch_size: int,
    platform: str | None = None,
    brand_id: str | None = None,
) -> list[dict]:
    """임베딩 대상 row. (platform, brand) scope 가능."""
    where = [
        "description_curated IS NOT NULL",
        "vectorized IS NULL",
    ]
    params: list[object] = []
    if platform:
        where.append("platform = %s")
        params.append(platform)
    if brand_id:
        where.append("platform_brand_id = %s")
        params.append(brand_id)

    sql = f"""
        SELECT id, platform, platform_brand_id, name, sub_category,
               description_curated, enrichment_meta
        FROM product
        WHERE {' AND '.join(where)}
        ORDER BY id
        LIMIT %s
    """
    params.append(batch_size)
    rows = conn.execute(sql, params).fetchall()
    cols = ["id", "platform", "platform_brand_id", "name", "sub_category",
            "description_curated", "enrichment_meta"]
    return [dict(zip(cols, r)) for r in rows]


def _call_tei(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    """TEI /embed → list of 1024d vectors."""
    resp = client.post(
        f"{TEI_URL}/embed",
        json={"inputs": texts},
        timeout=TEI_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Unexpected TEI response: {str(data)[:200]}")
    return data


def _update_batch(
    conn: psycopg.Connection,
    ids_vectors: list[tuple[str, list[float]]],
) -> int:
    """embedding + vectorized UPDATE."""
    if not ids_vectors:
        return 0
    # pgvector는 '[v1,v2,...]' 문자열로 바인딩 받음
    payload = [
        {
            "id": pid,
            "embedding": "[" + ",".join(f"{v:.6f}" for v in vec) + "]",
        }
        for pid, vec in ids_vectors
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE product
            SET embedding = %(embedding)s::vector,
                vectorized = now()
            WHERE id = %(id)s::uuid
            """,
            payload,
        )
    return len(payload)


def embed_batch(
    batch_size: int = TEI_BATCH_SIZE,
    platform: str | None = None,
    brand_id: str | None = None,
) -> dict:
    """한 배치 임베딩 생성. {processed, remaining} 반환."""
    with _connect() as conn:
        pending = _fetch_pending(conn, batch_size, platform, brand_id)
        if not pending:
            return {"processed": 0, "remaining": 0}

        texts = [_build_text(r) for r in pending]
        logger.info("Embedding batch: %d items (TEI=%s)", len(pending), TEI_URL)

        start = time.monotonic()
        with httpx.Client() as client:
            vectors = _call_tei(client, texts)
        elapsed = time.monotonic() - start

        if len(vectors) != len(pending):
            raise RuntimeError(
                f"TEI returned {len(vectors)} vectors for {len(pending)} inputs"
            )

        ids_vectors = [(str(p["id"]), v) for p, v in zip(pending, vectors)]
        updated = _update_batch(conn, ids_vectors)
        conn.commit()

        # 남은 카운트
        row = conn.execute(
            """
            SELECT count(*) FROM product
            WHERE description_curated IS NOT NULL AND vectorized IS NULL
            """
        ).fetchone()
        remaining = row[0]

        logger.info(
            "Embedded %d in %.2fs (avg %.0fms/item). Remaining: %d",
            updated, elapsed, elapsed / max(len(pending), 1) * 1000, remaining,
        )
        return {"processed": updated, "remaining": remaining, "elapsed_sec": elapsed}


def embed_all(
    batch_size: int = TEI_BATCH_SIZE,
    platform: str | None = None,
    brand_id: str | None = None,
    max_batches: int = 200,
) -> dict:
    """대상 소진까지 반복."""
    total = 0
    for batch_no in range(1, max_batches + 1):
        result = embed_batch(batch_size, platform, brand_id)
        total += result["processed"]
        if result["processed"] == 0 or result["remaining"] == 0:
            break
        logger.info("Batch %d done: +%d (remaining %d)",
                    batch_no, result["processed"], result["remaining"])
    logger.info("Embedding pass complete: %d total", total)
    return {"total_embedded": total}
