import json
import logging
from datetime import datetime
from pathlib import Path

from common.constants import BRONZE_DIR
from common.schemas import CrawlResult

logger = logging.getLogger(__name__)


class BronzeWriter:
    """CrawlResult를 Bronze JSONL로 직렬화한다.

    저장 구조:
        store/bronze/{platform}/{date}/{brand_id}.jsonl   — 상품 (한 줄씩)
        store/bronze/{platform}/{date}/brands/{brand_id}.json — 브랜드 (단일 JSON)
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or BRONZE_DIR

    def _products_path(self, platform: str, brand_id: str, date: str) -> Path:
        return self.base_dir / platform / date / f"{brand_id}.jsonl"

    def _brand_path(self, platform: str, brand_id: str, date: str) -> Path:
        return self.base_dir / platform / date / "brands" / f"{brand_id}.json"

    def write(self, result: CrawlResult, brand_id: str) -> Path:
        """CrawlResult를 Bronze로 저장한다.

        Args:
            result: 크롤러가 반환한 결과.
            brand_id: 브랜드 식별자 (파일명에 사용).

        Returns:
            상품 JSONL 파일 경로.
        """
        date = datetime.now().strftime("%Y-%m-%d")
        platform_name = result.platform.name

        # 상품 JSONL 저장
        products_path = self._products_path(platform_name, brand_id, date)
        products_path.parent.mkdir(parents=True, exist_ok=True)

        with open(products_path, "w", encoding="utf-8") as f:
            for product in result.products:
                f.write(product.model_dump_json() + "\n")

        # 브랜드 JSON 저장 (있을 때만)
        if result.brand:
            brand_path = self._brand_path(platform_name, brand_id, date)
            brand_path.parent.mkdir(parents=True, exist_ok=True)

            with open(brand_path, "w", encoding="utf-8") as f:
                f.write(result.brand.model_dump_json(indent=2))

            logger.info("Brand saved: %s", brand_path)

        # 결과 로깅
        logger.info(
            "Bronze write complete: platform=%s brand=%s products=%d errors=%d path=%s",
            platform_name,
            brand_id,
            len(result.products),
            result.error_count,
            products_path,
        )

        if result.errors:
            for error in result.errors:
                logger.warning(
                    "Crawl error: url=%s type=%s message=%s",
                    error.url,
                    error.error_type,
                    error.message,
                )

        return products_path
