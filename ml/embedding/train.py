"""bge-m3 fine-tuning — dynamic negative sampling + train/val/test 평가."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
from sentence_transformers import (
    InputExample,
    SentenceTransformer,
    losses,
)
from sentence_transformers.evaluation import InformationRetrievalEvaluator
from torch.utils.data import DataLoader

logger = logging.getLogger(__name__)

SPLITS_DIR = Path(__file__).resolve().parents[1] / ".." / "store" / "training" / "splits"
MODEL_NAME = "BAAI/bge-m3"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / ".." / "models" / "gyeol-embedding-v1"


def _load_pairs(path: Path) -> list[tuple[str, str]]:
    pairs = []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            pairs.append((d["query"], d["positive"]))
    return pairs


def _build_train_loader(pairs: list[tuple[str, str]], batch_size: int) -> DataLoader:
    """positive pair만 넣으면 MultipleNegativesRankingLoss가 in-batch negative 자동 생성."""
    examples = [InputExample(texts=[q, p]) for q, p in pairs]
    return DataLoader(examples, shuffle=True, batch_size=batch_size)


def _build_evaluator(pairs: list[tuple[str, str]], name: str) -> InformationRetrievalEvaluator:
    """(query, positive) pairs → IR evaluator (NDCG, Recall, MRR)."""
    queries = {}
    corpus = {}
    relevant_docs = {}

    # corpus: 모든 unique product texts
    product_to_id = {}
    for _, product in pairs:
        if product not in product_to_id:
            cid = f"c{len(product_to_id)}"
            product_to_id[product] = cid
            corpus[cid] = product

    # queries + relevance
    for i, (query, product) in enumerate(pairs):
        qid = f"q{i}"
        queries[qid] = query
        relevant_docs[qid] = {product_to_id[product]}

    return InformationRetrievalEvaluator(
        queries=queries,
        corpus=corpus,
        relevant_docs=relevant_docs,
        name=name,
        show_progress_bar=False,
        ndcg_at_k=[5, 10, 20],
        precision_recall_at_k=[5, 10, 20],
        mrr_at_k=[10],
        accuracy_at_k=[1, 3, 5, 10],
    )


def evaluate_base_model(model: SentenceTransformer) -> dict:
    """fine-tuning 전 base model 성능 측정."""
    results = {}

    for split in ["val", "test"]:
        path = SPLITS_DIR / f"{split}.jsonl"
        if not path.exists():
            continue
        pairs = _load_pairs(path)
        evaluator = _build_evaluator(pairs, split)
        metrics = evaluator(model)
        results[split] = metrics
        logger.info("Base model %s: %s", split, {k: f"{v:.4f}" for k, v in metrics.items() if "ndcg" in k.lower() or "mrr" in k.lower()})

    return results


def train(
    batch_size: int = 16,
    epochs: int = 3,
    lr: float = 5e-6,
    warmup_ratio: float = 0.1,
):
    """full training pipeline: load → base eval → train → final eval."""
    # 데이터 로드
    train_pairs = _load_pairs(SPLITS_DIR / "train.jsonl")
    val_pairs = _load_pairs(SPLITS_DIR / "val.jsonl")
    test_pairs = _load_pairs(SPLITS_DIR / "test.jsonl")

    logger.info("Data: train=%d, val=%d, test=%d", len(train_pairs), len(val_pairs), len(test_pairs))

    # 모델 로드
    logger.info("Loading %s...", MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME)

    # base model 평가 (before fine-tuning)
    logger.info("=== Base Model Evaluation ===")
    base_results = evaluate_base_model(model)

    # train setup
    train_loader = _build_train_loader(train_pairs, batch_size)
    val_evaluator = _build_evaluator(val_pairs, "val")
    test_evaluator = _build_evaluator(test_pairs, "test")

    # loss: in-batch dynamic negatives
    train_loss = losses.MultipleNegativesRankingLoss(model)

    warmup_steps = int(len(train_loader) * epochs * warmup_ratio)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # train with validation per epoch
    logger.info("=== Training ===")
    logger.info("batch=%d, epochs=%d, lr=%s, warmup=%d steps", batch_size, epochs, lr, warmup_steps)
    start = time.time()

    model.fit(
        train_objectives=[(train_loader, train_loss)],
        evaluator=val_evaluator,
        epochs=epochs,
        warmup_steps=warmup_steps,
        optimizer_params={"lr": lr},
        output_path=str(OUTPUT_DIR),
        evaluation_steps=len(train_loader) // 2,  # epoch 중간 + 끝에 평가
        save_best_model=True,
        show_progress_bar=True,
    )

    elapsed = time.time() - start
    logger.info("Training done in %.1f minutes", elapsed / 60)

    # best model 로드
    model = SentenceTransformer(str(OUTPUT_DIR))

    # final test evaluation (production data)
    logger.info("=== Final Test Evaluation (Production Data) ===")
    test_metrics = test_evaluator(model)

    # 결과 비교
    logger.info("=== Results Comparison ===")
    for key in sorted(test_metrics.keys()):
        if "ndcg" in key.lower() or "mrr" in key.lower() or "recall" in key.lower() or "accuracy" in key.lower():
            base_val = base_results.get("test", {}).get(key, 0)
            final_val = test_metrics[key]
            diff = final_val - base_val
            logger.info("  %s: %.4f → %.4f (%+.4f)", key, base_val, final_val, diff)

    # 결과 저장
    summary = {
        "model": MODEL_NAME,
        "data": {
            "train": len(train_pairs),
            "val": len(val_pairs),
            "test": len(test_pairs),
        },
        "config": {
            "batch_size": batch_size,
            "epochs": epochs,
            "lr": lr,
        },
        "base_test": {k: round(v, 4) for k, v in base_results.get("test", {}).items()},
        "final_test": {k: round(v, 4) for k, v in test_metrics.items()},
        "training_time_minutes": round(elapsed / 60, 1),
    }

    with open(OUTPUT_DIR / "training_summary.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info("Model saved: %s", OUTPUT_DIR)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    summary = train()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
