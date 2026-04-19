"""10분마다 orchestration.task 에서 stuck row 정리."""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from orchestration.reconcile import mark_stuck_failed


with DAG(
    dag_id="gyeol_task_reconciler",
    description="Mark stuck codex/gemini tasks failed",
    schedule=timedelta(minutes=10),
    start_date=datetime(2026, 4, 1),
    catchup=False,
    default_args={"owner": "gyeol", "retries": 0},
    max_active_runs=1,
    tags=["gyeol", "infra"],
):
    PythonOperator(
        task_id="reconcile",
        python_callable=mark_stuck_failed,
        op_kwargs={"max_age_minutes": 30},
    )
