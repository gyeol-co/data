"""crawl_brand — Airflow 태스크 콜러블. 지금은 실행 자체는 stub.

진짜 로직은 crawlers/run.py 의 run_one 을 감싸야 하지만, Airflow 컨테이너에
Playwright 등 의존이 아직 없으므로 로그만 남김. 나중에 worker 이미지 별도 빌드 시 대체.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def crawl_brand(platform: str, brand_id: str, url: str) -> dict:
    logger.info("crawl_brand stub: platform=%s brand_id=%s url=%s", platform, brand_id, url)
    # TODO: await crawlers.run.run_one(platform, brand_id, url)
    return {"platform": platform, "brand_id": brand_id, "status": "stub"}
