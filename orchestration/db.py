"""Postgres 연결 헬퍼. 지연 import로 DAG parse는 가볍게 유지."""

from __future__ import annotations

import os


def get_db_url() -> str:
    return os.environ.get(
        "DB_URL",
        "postgresql://gyeol:gyeol_secret@postgres:5432/gyeol",
    )


def connect():
    import psycopg
    return psycopg.connect(get_db_url())
