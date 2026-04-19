"""크롤러 CLI.

사용:
    uv run python -m crawlers.run                          # 전체
    uv run python -m crawlers.run --platform etcseoul      # 플랫폼 전체
    uv run python -m crawlers.run --brand artifacts        # 브랜드 하나
    uv run python -m crawlers.run --retry                  # 실패 재시도
"""

import argparse
import asyncio
import logging
import sys

from common.brands import YamlBrandRegistry
from common.failures import FailureLogger
from crawlers import (
    BarbershopCrawler,
    BastongCrawler,
    EtcseoulCrawler,
    ModemanCrawler,
    SculpstoreCrawler,
)
from etl.bronze.writer import BronzeWriter

logger = logging.getLogger(__name__)

CRAWLERS = {
    "etcseoul": EtcseoulCrawler,
    "barbershop": BarbershopCrawler,
    "bastong": BastongCrawler,
    "sculpstore": SculpstoreCrawler,
    "modeman": ModemanCrawler,
}

MAX_BRAND_RETRIES = 2


# --- 코어 ---


async def run_one(platform: str, brand_id: str, url: str, failure_logger: FailureLogger | None = None) -> "CrawlResult":
    """브랜드 하나를 크롤링하고 Bronze에 저장한다."""
    crawler_cls = CRAWLERS.get(platform)
    if not crawler_cls:
        print(f"Unknown platform: {platform}. Available: {list(CRAWLERS.keys())}")
        sys.exit(1)

    writer = BronzeWriter()

    async with crawler_cls() as crawler:
        result = await crawler.crawl(url)
        output_path = writer.write(result, brand_id)

        if failure_logger and result.errors:
            for error in result.errors:
                failure_logger.add_product(platform, brand_id, error.url, error.message)

    print(f"\n{'='*50}")
    print(f"Platform:  {platform}")
    print(f"Brand:     {brand_id}")
    print(f"Products:  {len(result.products)}")
    print(f"Errors:    {result.error_count}")
    print(f"Duration:  {(result.crawl_finished_at - result.crawl_started_at).total_seconds():.1f}s")
    print(f"Output:    {output_path}")
    print(f"{'='*50}")

    return result


async def _run_with_retry(
    platform: str,
    brand_id: str,
    url: str,
    failure_logger: FailureLogger,
) -> bool:
    """retry 포함 실행. 성공 시 True."""
    for attempt in range(1, MAX_BRAND_RETRIES + 1):
        try:
            await run_one(platform, brand_id, url, failure_logger=failure_logger)
            return True
        except Exception as e:
            if attempt < MAX_BRAND_RETRIES:
                wait = 2 ** attempt
                logger.warning(
                    "Brand failed %s/%s (attempt %d/%d) — retrying in %ds: %s",
                    platform, brand_id, attempt, MAX_BRAND_RETRIES, wait, e,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "Brand failed %s/%s — giving up after %d attempts: %s",
                    platform, brand_id, MAX_BRAND_RETRIES, e,
                )
                failure_logger.add_brand(platform, brand_id, str(e))
                return False


# --- CLI 모드 ---


async def _run_brands(platform: str | None = None, brand: str | None = None) -> None:
    """brands.yaml에서 대상을 조회하고 순차 실행한다."""
    registry = YamlBrandRegistry()

    if brand:
        # --brand: 전체 yaml에서 brand_id로 검색
        all_brands = registry.load_all()
        found = None
        for plat, entries in all_brands.items():
            for entry in entries:
                if entry.brand_id == brand:
                    found = (plat, entry)
                    break
            if found:
                break
        if not found:
            print(f"Brand '{brand}' not found in brands.yaml")
            sys.exit(1)
        plat, entry = found
        await run_one(plat, entry.brand_id, entry.url)
        return

    # --platform 또는 전체
    if platform:
        platform_brands = {platform: registry.load_platform(platform)}
    else:
        platform_brands = registry.load_all()

    total = sum(len(entries) for entries in platform_brands.values())
    done, failed = 0, 0
    failure_logger = FailureLogger()

    for plat, entries in platform_brands.items():
        if plat not in CRAWLERS:
            logger.warning("Unknown platform: %s — skipping", plat)
            continue
        for entry in entries:
            success = await _run_with_retry(plat, entry.brand_id, entry.url, failure_logger)
            if success:
                done += 1
            else:
                failed += 1

    failed_path = failure_logger.save()

    print(f"\n{'='*50}")
    print(f"Total: {total} | Done: {done} | Failed brands: {failed} | Failed products: {failure_logger.count}")
    if failed_path:
        print(f"Failure log: {failed_path}")
        print(f"Retry with: uv run python -m crawlers.run --retry")
    print(f"{'='*50}")


async def _run_retry() -> None:
    """최근 실패 로그에서 재시도한다."""
    latest = FailureLogger.latest()
    if not latest:
        print("No failure log found.")
        return

    entries = FailureLogger.load(latest)
    product_entries = [e for e in entries if e.product_url]
    brand_entries = [e for e in entries if not e.product_url]

    print(f"Retrying from: {latest}")
    print(f"  Brand failures: {len(brand_entries)} (will re-crawl entire brand)")
    print(f"  Product failures: {len(product_entries)} (will re-crawl individual products)")

    failure_logger = FailureLogger()
    done, failed = 0, 0

    # 1. 브랜드 레벨 실패 — 전체 재크롤링
    for entry in brand_entries:
        if entry.platform not in CRAWLERS:
            continue
        success = await _run_with_retry(
            entry.platform, entry.brand_id, "", failure_logger,
        )
        if success:
            done += 1
        else:
            failed += 1

    # 2. 상품 레벨 실패 — 개별 상품만 재크롤링
    for entry in product_entries:
        if entry.platform not in CRAWLERS:
            continue
        crawler_cls = CRAWLERS[entry.platform]
        try:
            async with crawler_cls() as crawler:
                product = await crawler.crawl_product(entry.product_url)
                logger.info("Retry OK: %s/%s %s", entry.platform, entry.brand_id, entry.product_url)
                done += 1
        except Exception as e:
            logger.error("Retry FAILED: %s/%s %s — %s", entry.platform, entry.brand_id, entry.product_url, e)
            failure_logger.add_product(entry.platform, entry.brand_id, entry.product_url, str(e))
            failed += 1

    failed_path = failure_logger.save()

    print(f"\n{'='*50}")
    print(f"Retry done | Success: {done} | Failed: {failed}")
    if failed_path:
        print(f"Remaining failures: {failed_path}")
    else:
        print("All retries succeeded!")
    print(f"{'='*50}")


# --- 엔트리포인트 ---


def main():
    parser = argparse.ArgumentParser(description="Gyeol Crawler")
    parser.add_argument("--platform", choices=list(CRAWLERS.keys()), help="특정 플랫폼만")
    parser.add_argument("--brand", help="특정 브랜드만 (brands.yaml에서 조회)")
    parser.add_argument("--url", help="URL 직접 지정 (--platform 필수, brands.yaml 미사용)")
    parser.add_argument("--retry", action="store_true", help="최근 실패 로그에서 재시도")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.url and not args.platform:
        parser.error("--url 사용 시 --platform 필수")

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.retry:
        asyncio.run(_run_retry())
    elif args.url:
        brand_id = args.brand or args.platform
        asyncio.run(run_one(args.platform, brand_id, args.url))
    else:
        asyncio.run(_run_brands(platform=args.platform, brand=args.brand))


if __name__ == "__main__":
    main()
