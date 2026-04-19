"""Bronze 데이터 읽기.

store/bronze/{platform}/{date}/{brand_id}.jsonl → RawProduct 리스트
store/bronze/{platform}/{date}/brands/{brand_id}.json → RawBrand
"""

import json
import logging
from pathlib import Path

from common.constants import BRONZE_DIR
from common.schemas import RawBrand, RawProduct

logger = logging.getLogger(__name__)


def _latest_date_dir(platform_dir: Path) -> Path | None:
    """플랫폼 디렉토리에서 가장 최근 날짜 디렉토리를 반환한다."""
    date_dirs = sorted(
        [d for d in platform_dir.iterdir() if d.is_dir() and d.name != "brands"],
        reverse=True,
    )
    return date_dirs[0] if date_dirs else None


def list_brands() -> list[tuple[str, str]]:
    """Bronze에 존재하는 (platform, brand_id) 목록을 반환한다."""
    brands = []
    for platform_dir in sorted(BRONZE_DIR.iterdir()):
        if not platform_dir.is_dir():
            continue
        date_dir = _latest_date_dir(platform_dir)
        if not date_dir:
            continue
        for jsonl in sorted(date_dir.glob("*.jsonl")):
            brands.append((platform_dir.name, jsonl.stem))
    return brands


def read_products(platform: str, brand_id: str) -> list[RawProduct]:
    """최신 Bronze JSONL에서 RawProduct 리스트를 읽는다."""
    platform_dir = BRONZE_DIR / platform
    if not platform_dir.exists():
        return []

    date_dir = _latest_date_dir(platform_dir)
    if not date_dir:
        return []

    jsonl_path = date_dir / f"{brand_id}.jsonl"
    if not jsonl_path.exists():
        return []

    products = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                products.append(RawProduct(**json.loads(line)))

    logger.info("Read %d products: %s/%s", len(products), platform, brand_id)
    return products


def read_brand(platform: str, brand_id: str) -> RawBrand | None:
    """최신 Bronze에서 RawBrand를 읽는다."""
    platform_dir = BRONZE_DIR / platform
    if not platform_dir.exists():
        return None

    date_dir = _latest_date_dir(platform_dir)
    if not date_dir:
        return None

    brand_path = date_dir / "brands" / f"{brand_id}.json"
    if not brand_path.exists():
        return None

    with open(brand_path, encoding="utf-8") as f:
        data = json.loads(f.read())

    logger.info("Read brand: %s/%s", platform, brand_id)
    return RawBrand(**data)
