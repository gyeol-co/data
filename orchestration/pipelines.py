"""Pipeline manifest 로더.

pipelines/{platform}.yaml → dict. DAG factory가 DAG 생성 시 사용.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_PIPELINES_DIR = Path(os.environ.get("GYEOL_PIPELINES_DIR", "/opt/airflow/pipelines"))
if not _PIPELINES_DIR.exists():
    _PIPELINES_DIR = Path(__file__).resolve().parents[1] / "pipelines"


def list_manifests() -> list[dict[str, Any]]:
    """pipelines/ 디렉토리의 모든 매니페스트를 반환."""
    manifests: list[dict[str, Any]] = []
    if not _PIPELINES_DIR.exists():
        return manifests

    for path in sorted(_PIPELINES_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict) and "platform" in data:
            manifests.append(data)
    return manifests


def load_manifest(platform: str) -> dict[str, Any] | None:
    path = _PIPELINES_DIR / f"{platform}.yaml"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
