"""Gold batch — Gemini enrichment + text embedding."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pending_gemini() -> int:
    from .db import connect
    with connect() as conn:
        row = conn.execute(
            """
            SELECT count(*) FROM staging.product
            WHERE category IS NOT NULL AND description_curated IS NULL
            """
        ).fetchone()
    return row[0]


def pending_embedding() -> int:
    from .db import connect
    with connect() as conn:
        row = conn.execute(
            """
            SELECT count(*) FROM staging.product
            WHERE description_curated IS NOT NULL AND vectorized IS NULL
            """
        ).fetchone()
    return row[0]


# 뒤로 호환 — 기존 DAG 파일에서 import 중
def pending_count() -> int:
    return pending_gemini()


# ── Gemini ────────────────────────────────────────────────────────────

def gold_batch_for_brand(platform: str, brand_id: str) -> dict:
    """Per-brand Gemini enrichment — 해당 스코프의 DISTINCT ON base_name 그룹만."""
    from etl.gold import pipeline as gold_pipeline
    logger.info("gemini enrich: platform=%s brand_id=%s", platform, brand_id)
    # 현 pipeline.process_all은 전역 대상 — per-brand 필터 옵션은 없지만 본질 idempotent.
    # 우선 전역 실행 (나중에 필터 파라미터 확장 시 교체).
    return gold_pipeline.process_all()


def gold_batch_all() -> dict:
    """전역 Gemini enrichment."""
    from etl.gold import pipeline as gold_pipeline
    logger.info("gemini enrich: all")
    return gold_pipeline.process_all()


# ── Text embedding ────────────────────────────────────────────────────

def embed_batch_all() -> dict:
    """staging.product 에서 description_curated 있는 모든 row 임베딩."""
    from etl.gold.embed import embed_all
    return embed_all()


def embed_batch_for_brand(platform: str, brand_id: str) -> dict:
    """Per-brand 임베딩."""
    from etl.gold.embed import embed_all
    return embed_all(platform=platform, brand_id=brand_id)


# ── Search tokenization (MeCab) ───────────────────────────────────────

def pending_search() -> int:
    from .db import connect
    with connect() as conn:
        row = conn.execute(
            """
            SELECT count(*) FROM staging.product
            WHERE description_curated IS NOT NULL AND search_text IS NULL
            """
        ).fetchone()
    return row[0]


def tokenize_search_all() -> dict:
    from etl.gold.tokenize import tokenize_all
    return tokenize_all()


def tokenize_search_for_brand(platform: str, brand_id: str) -> dict:
    from etl.gold.tokenize import tokenize_all
    return tokenize_all(platform=platform, brand_id=brand_id)
