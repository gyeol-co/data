"""Stuck task cleanup — worker 죽어서 assigned 상태로 30분+ 머문 row 정리."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def mark_stuck_failed(max_age_minutes: int = 30) -> int:
    from .db import connect
    with connect() as conn:
        row = conn.execute(
            """
            UPDATE orchestration.task
            SET failed = now(),
                failed_reason = 'worker_stuck'
            WHERE status = 'assigned'
              AND assigned < now() - (%s || ' minutes')::interval
            RETURNING id
            """,
            (str(max_age_minutes),),
        ).fetchall()
        conn.commit()
    n = len(row)
    if n:
        logger.warning("reconciled %d stuck tasks", n)
    return n
