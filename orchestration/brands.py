"""Airflow DAG parse 시점에 brands.yaml 읽어 브랜드 목록 제공."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

_BRANDS_FILE = Path(os.environ.get("GYEOL_BRANDS_FILE", "/opt/airflow/brands.yaml"))
if not _BRANDS_FILE.exists():
    _BRANDS_FILE = Path(__file__).resolve().parents[1] / "brands.yaml"


def list_brands(platform: str) -> list[dict[str, str]]:
    """{brand_id, url} 리스트 반환. 플랫폼 미정의면 빈 리스트."""
    if not _BRANDS_FILE.exists():
        return []

    with open(_BRANDS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    entries = data.get(platform) or {}
    return [
        {"brand_id": bid, "url": url, "platform": platform}
        for bid, url in entries.items()
    ]
