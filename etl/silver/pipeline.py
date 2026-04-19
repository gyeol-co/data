"""Silver ETL 코어 로직.

Airflow, CLI, 직접 호출 어디서든 동작하는 순수 함수.
"""

import logging

from etl.silver.base_name_review import start_base_name_review
from etl.silver.brand_profile import start_brand_profile
from etl.silver.category_review import start_brand_review
from etl.silver.reader import list_brands, read_brand, read_products
from etl.silver.writer import write_brand, write_products

logger = logging.getLogger(__name__)


def process_brand(
    platform: str,
    brand_id: str,
    *,
    launch_brand_profile: bool = True,
    launch_category_review: bool = True,
    launch_base_name_review: bool = True,
) -> int:
    """브랜드 하나를 Bronze → Silver로 처리한다.

    Returns:
        처리된 상품 수.
    """
    logger.info("Silver ETL start: %s/%s", platform, brand_id)

    # 브랜드 정보
    brand = read_brand(platform, brand_id)
    if brand:
        write_brand(platform, brand)

    # 상품 정제 + 적재
    products = read_products(platform, brand_id)
    count = write_products(platform, brand_id, products)

    if launch_brand_profile and brand:
        start_brand_profile(platform, brand_id, brand.name)

    if launch_category_review:
        start_brand_review(platform, brand_id)

    if launch_base_name_review:
        start_base_name_review(platform, brand_id)

    logger.info("Silver ETL done: %s/%s — %d products", platform, brand_id, count)
    return count


def process_platform(
    platform: str,
    *,
    launch_brand_profile: bool = True,
    launch_category_review: bool = True,
    launch_base_name_review: bool = True,
) -> dict[str, int]:
    """플랫폼 전체 브랜드를 처리한다.

    Returns:
        {brand_id: 처리된 상품 수}
    """
    results = {}
    for plat, brand_id in list_brands():
        if plat != platform:
            continue
        results[brand_id] = process_brand(
            plat,
            brand_id,
            launch_brand_profile=launch_brand_profile,
            launch_category_review=launch_category_review,
            launch_base_name_review=launch_base_name_review,
        )
    return results


def process_all(
    *,
    launch_brand_profile: bool = True,
    launch_category_review: bool = True,
    launch_base_name_review: bool = True,
) -> dict[str, dict[str, int]]:
    """전체 Bronze → Silver 처리.

    Returns:
        {platform: {brand_id: 처리된 상품 수}}
    """
    results: dict[str, dict[str, int]] = {}
    for platform, brand_id in list_brands():
        if platform not in results:
            results[platform] = {}
        results[platform][brand_id] = process_brand(
            platform,
            brand_id,
            launch_brand_profile=launch_brand_profile,
            launch_category_review=launch_category_review,
            launch_base_name_review=launch_base_name_review,
        )
    return results
