"""silver_upsert — Bronze → staging. 지금은 stub."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def silver_upsert(platform: str, brand_id: str) -> dict:
    logger.info("silver_upsert stub: platform=%s brand_id=%s", platform, brand_id)
    # TODO: etl.silver.pipeline.process_brand(
    #   platform, brand_id,
    #   launch_brand_profile=False, launch_category_review=False, launch_base_name_review=False
    # )
    return {"platform": platform, "brand_id": brand_id, "status": "stub"}
