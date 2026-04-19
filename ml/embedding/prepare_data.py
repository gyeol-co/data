"""학습 데이터 준비 — synthetic corpus + production data → train/val/test split."""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

import psycopg

from common.constants import STAGING_DB_URL

logger = logging.getLogger(__name__)

CORPUS_DIR = Path(__file__).resolve().parents[1] / ".." / "store" / "training" / "corpus"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / ".." / "store" / "training" / "splits"

def _make_queries(meta: dict, sub_category: str) -> list[str]:
    """production 상품에서 검색 쿼리 생성."""
    queries = []
    detail = meta.get("sub_category_detail", "")
    styles = meta.get("style", [])
    materials = meta.get("material", [])

    if detail:
        queries.append(f"{detail} {sub_category}")
    if styles:
        queries.append(f"{', '.join(styles[:2])} {sub_category}")
    if materials:
        queries.append(f"{', '.join(materials[:2])} {sub_category}")

    return [q for q in queries if q.strip()]


def build_product_text(meta: dict, sub_category: str, fit: str | None) -> str:
    """enrichment 데이터에서 structured embedding text 생성."""
    parts = [
        f"[TYPE] {meta.get('sub_category_detail', '')} {sub_category}".strip(),
        f"[MATERIAL] {', '.join(meta.get('material', []))}",
    ]
    if fit:
        parts.append(f"[FIT] {fit}")
    parts.append(f"[STYLE] {', '.join(meta.get('style', []))}")
    parts.append(f"[DETAIL] {', '.join(meta.get('details', []))}")
    return " ".join(parts)


def load_synthetic_pairs() -> list[tuple[str, str]]:
    """synthetic corpus에서 (query, product_text) pairs 로드."""
    pairs = []
    for f in sorted(CORPUS_DIR.glob("*.jsonl")):
        with open(f) as fh:
            for line in fh:
                d = json.loads(line)
                product = d["product"]
                for q in d.get("queries", []):
                    pairs.append((q, product))
    logger.info("Synthetic pairs: %d", len(pairs))
    return pairs


def load_production_pairs() -> list[tuple[str, str]]:
    """production DB에서 (query, product_text) pairs 생성."""
    pairs = []
    with psycopg.connect(STAGING_DB_URL) as conn:
        conn.execute("SET search_path TO staging")
        rows = conn.execute(
            """
            SELECT sub_category, fit, enrichment_meta
            FROM product
            WHERE description_curated IS NOT NULL
              AND enrichment_meta IS NOT NULL
              AND sub_category IS NOT NULL
            """
        ).fetchall()

    for sub_category, fit, meta in rows:
        if isinstance(meta, str):
            meta = json.loads(meta)
        if not meta:
            continue

        product_text = build_product_text(meta, sub_category, fit)

        for q in _make_queries(meta, sub_category):
            pairs.append((q, product_text))

    logger.info("Production pairs: %d", len(pairs))
    return pairs


def split_and_save(
    train_ratio: float = 0.85,
    val_ratio: float = 0.10,
    seed: int = 42,
):
    """데이터 로드 → split → JSONL 저장."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    synthetic = load_synthetic_pairs()
    production = load_production_pairs()

    # production은 test + validation 우선 (실제 데이터로 평가해야 하니까)
    random.seed(seed)
    random.shuffle(production)

    n_prod = len(production)
    n_test = int(n_prod * 0.15)        # production 15% → test
    n_val_prod = int(n_prod * 0.15)    # production 15% → validation

    test_pairs = production[:n_test]
    val_prod_pairs = production[n_test:n_test + n_val_prod]
    train_prod_pairs = production[n_test + n_val_prod:]

    # synthetic은 대부분 train, 일부 validation
    random.shuffle(synthetic)
    n_val_syn = int(len(synthetic) * val_ratio)
    val_syn_pairs = synthetic[:n_val_syn]
    train_syn_pairs = synthetic[n_val_syn:]

    # 합치기
    train_pairs = train_syn_pairs + train_prod_pairs
    val_pairs = val_syn_pairs + val_prod_pairs

    random.shuffle(train_pairs)
    random.shuffle(val_pairs)

    # 저장
    def save_pairs(pairs: list[tuple[str, str]], path: Path):
        with open(path, "w") as f:
            for query, product in pairs:
                f.write(json.dumps(
                    {"query": query, "positive": product},
                    ensure_ascii=False,
                ) + "\n")

    save_pairs(train_pairs, OUTPUT_DIR / "train.jsonl")
    save_pairs(val_pairs, OUTPUT_DIR / "val.jsonl")
    save_pairs(test_pairs, OUTPUT_DIR / "test.jsonl")

    stats = {
        "synthetic_total": len(synthetic),
        "production_total": len(production),
        "train": len(train_pairs),
        "val": len(val_pairs),
        "test": len(test_pairs),
        "seed": seed,
    }

    with open(OUTPUT_DIR / "stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    logger.info("Train: %d, Val: %d, Test: %d", len(train_pairs), len(val_pairs), len(test_pairs))
    logger.info("Test is 100%% production data (실제 상품 기반 평가)")
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    stats = split_and_save()
    print(json.dumps(stats, indent=2))
