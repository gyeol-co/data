"""Codex skill enqueue + wait.

enqueue_codex: orchestration.task 에 INSERT → task_id 반환
wait_codex: SqlSensor가 평가하는 SQL 생성 헬퍼
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .db import connect

logger = logging.getLogger(__name__)


def _scope_key(skill: str, data: dict) -> str:
    """task_type 내 scope 식별자. (platform, brand_id/brand_name) 조합."""
    if skill == "brand_profile":
        return f"{data['platform']}__{data.get('brand_name', '')}"
    return f"{data['platform']}__{data.get('brand_id', '')}"


def enqueue_codex(skill: str, data: dict[str, Any]) -> str:
    """orchestration.task 에 INSERT. task id (uuid str) 반환."""
    scope = _scope_key(skill, data)
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO orchestration.task (task_type, scope_key, data)
            VALUES (%s, %s, %s::jsonb)
            RETURNING id::text
            """,
            (skill, scope, json.dumps(data)),
        ).fetchone()
        conn.commit()
    task_id = row[0]
    logger.info("enqueued %s %s → task_id=%s", skill, scope, task_id)
    return task_id


def wait_sql(task_id_xcom: str) -> str:
    """SqlSensor용 SQL. task가 completed/failed/cancelled면 truthy."""
    return f"""
    SELECT 1 FROM orchestration.task
    WHERE id = '{{{{ ti.xcom_pull(task_ids='{task_id_xcom}') }}}}'::uuid
      AND status IN ('completed', 'failed', 'cancelled')
    """
