"""Per-platform DAG factory.

pipelines/*.yaml + brands.yaml 을 읽어 플랫폼마다 1개씩 DAG 생성.

per-brand TaskGroup 구조:
    crawl → silver
              ├─ enqueue_brand_profile → wait_brand_profile         (독립 sink)
              ├─ enqueue_category_review → wait_category_review ─┐
              └─ enqueue_base_name_review → wait_base_name_review┘→ gold_enrich
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.providers.common.sql.sensors.sql import SqlSensor
from airflow.utils.task_group import TaskGroup

from orchestration.brands import list_brands
from orchestration.codex import enqueue_codex
from orchestration.crawl import crawl_brand
from orchestration.gold import gold_batch_for_brand
from orchestration.pipelines import list_manifests
from orchestration.silver import silver_upsert

logger = logging.getLogger(__name__)

GYEOL_CONN_ID = "gyeol_postgres"
DEFAULT_START = datetime(2026, 4, 1)


def _build_brand_group(
    platform: str,
    brand: dict[str, str],
    codex_skills: list[str],
    gold_enabled: bool,
) -> TaskGroup:
    brand_id = brand["brand_id"]
    brand_url = brand["url"]

    with TaskGroup(group_id=brand_id) as tg:
        crawl = PythonOperator(
            task_id="crawl",
            python_callable=crawl_brand,
            op_kwargs={"platform": platform, "brand_id": brand_id, "url": brand_url},
            pool="crawl_pool",
        )

        silver = PythonOperator(
            task_id="silver",
            python_callable=silver_upsert,
            op_kwargs={"platform": platform, "brand_id": brand_id},
        )

        crawl >> silver

        # codex skill lane — enqueue + sensor 쌍
        codex_waits: dict[str, SqlSensor] = {}
        for skill in codex_skills:
            data = {"platform": platform, "brand_id": brand_id}
            if skill == "brand_profile":
                # brand_profile은 brand_name 필요 — 현재 brand_id 를 임시로 사용
                data["brand_name"] = brand_id

            enqueue_task_id = f"enqueue_{skill}"
            wait_task_id = f"wait_{skill}"

            enqueue_op = PythonOperator(
                task_id=enqueue_task_id,
                python_callable=enqueue_codex,
                op_kwargs={"skill": skill, "data": data},
            )

            wait_op = SqlSensor(
                task_id=wait_task_id,
                conn_id=GYEOL_CONN_ID,
                sql=f"""
                    SELECT 1 FROM orchestration.task
                    WHERE id = '{{{{ ti.xcom_pull(task_ids='{platform}.{brand_id}.{enqueue_task_id}') }}}}'::uuid
                      AND status IN ('completed', 'failed', 'cancelled')
                """,
                mode="reschedule",
                poke_interval=30,
                timeout=60 * 60,
            )

            silver >> enqueue_op >> wait_op
            codex_waits[skill] = wait_op

        # gold — category_review + base_name_review 만 기다림 (BP는 독립)
        if gold_enabled:
            gold = PythonOperator(
                task_id="gold_enrich",
                python_callable=gold_batch_for_brand,
                op_kwargs={"platform": platform, "brand_id": brand_id},
            )
            gating = []
            if "category_review" in codex_waits:
                gating.append(codex_waits["category_review"])
            if "base_name_review" in codex_waits:
                gating.append(codex_waits["base_name_review"])
            if gating:
                gold.set_upstream(gating)
            else:
                silver >> gold

    return tg


def _build_dag(manifest: dict[str, Any]) -> DAG:
    platform = manifest["platform"]
    schedule = manifest.get("schedule", "0 3 * * *")
    codex_skills = manifest.get("codex_skills", []) or []
    gold_enabled = bool((manifest.get("gold") or {}).get("enabled", False))

    dag = DAG(
        dag_id=f"gyeol_ingest_{platform}",
        description=f"Gyeol {platform} ingest — crawl → silver → codex → gold",
        schedule=schedule,
        start_date=DEFAULT_START,
        catchup=False,
        default_args={
            "owner": "gyeol",
            "retries": 1,
            "retry_delay": timedelta(minutes=5),
        },
        tags=["gyeol", "ingest", platform, manifest.get("type", "")],
    )

    with dag:
        brands = list_brands(platform)
        if not brands:
            EmptyOperator(task_id="no_brands_configured")
            return dag

        for brand in brands:
            _build_brand_group(platform, brand, codex_skills, gold_enabled)

    return dag


# --- 매니페스트마다 DAG 등록 ---
for _manifest in list_manifests():
    _dag = _build_dag(_manifest)
    globals()[_dag.dag_id] = _dag
