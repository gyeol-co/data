"""Gold DAG — Gemini enrichment → text embedding → search tokenization.

파이프라인:
    check_gemini → gemini_batch
      → check_embedding → embed_batch
        → check_search → tokenize_search
          → summary

각 단계 ShortCircuit — pending=0 이면 건너뜀.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator, ShortCircuitOperator
from airflow.utils.trigger_rule import TriggerRule

from orchestration.gold import (
    embed_batch_all,
    gold_batch_all,
    pending_embedding,
    pending_gemini,
    pending_search,
    tokenize_search_all,
)


def _has_pending_gemini() -> bool:
    try:
        return pending_gemini() > 0
    except Exception:
        return False


def _has_pending_embedding() -> bool:
    try:
        return pending_embedding() > 0
    except Exception:
        return False


def _has_pending_search() -> bool:
    try:
        return pending_search() > 0
    except Exception:
        return False


with DAG(
    dag_id="gyeol_gold_enrich",
    description="Gemini enrichment → text embedding (bge-m3) → MeCab search tokens",
    schedule="0 5 * * *",
    start_date=datetime(2026, 4, 1),
    catchup=False,
    default_args={"owner": "gyeol", "retries": 1, "retry_delay": timedelta(minutes=10)},
    tags=["gyeol", "gold", "gemini", "embedding", "search"],
):
    check_gemini = ShortCircuitOperator(
        task_id="check_pending_gemini",
        python_callable=_has_pending_gemini,
        ignore_downstream_trigger_rules=False,
    )
    gemini = PythonOperator(
        task_id="gemini_batch",
        python_callable=gold_batch_all,
        execution_timeout=timedelta(hours=6),
    )

    check_embedding = ShortCircuitOperator(
        task_id="check_pending_embedding",
        python_callable=_has_pending_embedding,
        trigger_rule=TriggerRule.NONE_FAILED,
        ignore_downstream_trigger_rules=False,
    )
    embed = PythonOperator(
        task_id="embed_batch",
        python_callable=embed_batch_all,
        execution_timeout=timedelta(hours=2),
    )

    check_search = ShortCircuitOperator(
        task_id="check_pending_search",
        python_callable=_has_pending_search,
        trigger_rule=TriggerRule.NONE_FAILED,
        ignore_downstream_trigger_rules=False,
    )
    tokenize = PythonOperator(
        task_id="tokenize_search",
        python_callable=tokenize_search_all,
        execution_timeout=timedelta(hours=1),
    )

    summary = EmptyOperator(
        task_id="summary",
        trigger_rule=TriggerRule.NONE_FAILED,
    )

    check_gemini >> gemini >> check_embedding >> embed >> check_search >> tokenize >> summary
