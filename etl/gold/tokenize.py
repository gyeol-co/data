"""MeCab 기반 search_text 생성 — Postgres BM25 인덱싱용.

입력:
    staging.product.name + brand + sub_category + description_curated +
    enrichment_meta (sub_category_detail, material, pattern, details)

처리:
    MeCab 형태소 분석 → NNG/NNP/SL/SN 만 선택 → 한글 1자 제거 →
    stop word 제거 → 공백 join

출력:
    staging.product.search_text UPDATE. publish 시 public.product 로 복사.
"""

from __future__ import annotations

import logging
import re

import psycopg

from common.constants import STAGING_DB_URL

logger = logging.getLogger(__name__)

# 포함 품사
_KEEP_POS = {"NNG", "NNP", "SL", "SN"}

# stop word — 검색 변별력 낮은 단어
_STOP_WORDS: frozenset[str] = frozenset({
    # 일반 의존명사/추상
    "수", "것", "등", "때", "점", "중",
    # fashion 문맥 너무 일반
    "상품", "아이템", "브랜드", "스타일", "컬렉션",
    "제품", "시즌", "디자인", "컨셉", "라인",
    # 기타
    "및", "또는", "그", "이", "저", "때문",
})

_HANGUL = re.compile(r"^[\uAC00-\uD7A3]+$")

# --- MeCab lazy init ---

_mecab = None


def _get_mecab():
    global _mecab
    if _mecab is None:
        from mecab import MeCab  # python-mecab-ko
        _mecab = MeCab()
    return _mecab


# --- tokenization ---


def _is_hangul(s: str) -> bool:
    return bool(_HANGUL.match(s))


def tokenize(text: str) -> list[str]:
    """Fashion-domain MeCab tokenizer. NNG/NNP/SL/SN + stop filter."""
    if not text:
        return []

    mecab = _get_mecab()
    tokens: list[str] = []

    for word in mecab.pos(text):
        # python-mecab-ko 의 pos()는 (surface, tag) 튜플 반환
        surface, tag = word

        # tag 는 'NNG' or 'NNG+NNG' 같이 합성일 수 있음 → 첫 tag 만 봄
        primary_tag = tag.split("+")[0]

        if primary_tag not in _KEEP_POS:
            continue
        if surface in _STOP_WORDS:
            continue

        # 한글 1글자 제거 (영어/숫자는 1자도 허용 — 사이즈 S/M/L 등)
        if _is_hangul(surface) and len(surface) < 2:
            continue

        tokens.append(surface)

    return tokens


def build_search_text(
    name: str | None,
    brand: str | None,
    sub_category: str | None,
    description_curated: str | None,
    enrichment_meta: dict | None,
) -> str:
    """토큰화 입력 조립 → MeCab → 공백 join."""
    parts: list[str] = []

    if brand:
        parts.append(brand)
    if name:
        parts.append(name)
    if sub_category:
        # field_jacket → field jacket
        parts.append(str(sub_category).replace("_", " "))

    meta = enrichment_meta or {}
    detail = meta.get("sub_category_detail")
    if detail:
        parts.append(detail)

    for list_field in ("material", "pattern", "details"):
        v = meta.get(list_field)
        if isinstance(v, list):
            parts.extend(str(x) for x in v if x)
        elif v:
            parts.append(str(v))

    if description_curated:
        parts.append(description_curated)

    text = " ".join(parts)
    tokens = tokenize(text)
    return " ".join(tokens)


# --- batch runner ---


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(STAGING_DB_URL)
    conn.execute("SET search_path TO staging, public")
    return conn


def _fetch_pending(
    conn: psycopg.Connection,
    batch_size: int,
    platform: str | None = None,
    brand_id: str | None = None,
) -> list[dict]:
    where = [
        "description_curated IS NOT NULL",
        "search_text IS NULL",
    ]
    params: list[object] = []
    if platform:
        where.append("platform = %s")
        params.append(platform)
    if brand_id:
        where.append("platform_brand_id = %s")
        params.append(brand_id)

    sql = f"""
        SELECT id, name, brand, sub_category, description_curated, enrichment_meta
        FROM product
        WHERE {' AND '.join(where)}
        ORDER BY id
        LIMIT %s
    """
    params.append(batch_size)
    rows = conn.execute(sql, params).fetchall()
    cols = ["id", "name", "brand", "sub_category", "description_curated", "enrichment_meta"]
    return [dict(zip(cols, r)) for r in rows]


def tokenize_batch(
    batch_size: int = 100,
    platform: str | None = None,
    brand_id: str | None = None,
) -> dict:
    """한 배치 tokenize. {processed, remaining}."""
    with _connect() as conn:
        pending = _fetch_pending(conn, batch_size, platform, brand_id)
        if not pending:
            return {"processed": 0, "remaining": 0}

        payload = []
        for row in pending:
            search_text = build_search_text(
                name=row["name"],
                brand=row["brand"],
                sub_category=row["sub_category"],
                description_curated=row["description_curated"],
                enrichment_meta=row["enrichment_meta"],
            )
            payload.append({"id": str(row["id"]), "search_text": search_text})

        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE product SET search_text = %(search_text)s WHERE id = %(id)s::uuid",
                payload,
            )
        conn.commit()

        row = conn.execute(
            """
            SELECT count(*) FROM product
            WHERE description_curated IS NOT NULL AND search_text IS NULL
            """
        ).fetchone()
        remaining = row[0]

        logger.info("Tokenized %d (remaining %d)", len(payload), remaining)
        return {"processed": len(payload), "remaining": remaining}


def tokenize_all(
    batch_size: int = 100,
    platform: str | None = None,
    brand_id: str | None = None,
    max_batches: int = 200,
) -> dict:
    total = 0
    for i in range(1, max_batches + 1):
        r = tokenize_batch(batch_size, platform, brand_id)
        total += r["processed"]
        if r["processed"] == 0 or r["remaining"] == 0:
            break
        logger.info("Batch %d: +%d (remaining %d)", i, r["processed"], r["remaining"])
    logger.info("Tokenize pass complete: %d total", total)
    return {"total_tokenized": total}
