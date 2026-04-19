import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime

import httpx
from playwright.async_api import Browser, Playwright, async_playwright

from common.schemas import CrawlError, CrawlResult, Platform, RawBrand, RawProduct

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    """하나의 객체, 두 가지 도구.

    httpx(가벼움)와 Playwright(비싸지만 필요할 때만)를 내부에서 위임.
    Playwright는 lazy init — _ensure_browser() 호출 시점에만 브라우저 기동.
    """

    DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._client: httpx.AsyncClient | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    # --- 리소스 관리 ---

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers=self.DEFAULT_HEADERS,
            )
        return self._client

    async def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch()
        return self._browser

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # --- HTTP 도구 ---

    async def fetch(self, url: str, **kwargs) -> httpx.Response:
        client = await self._ensure_client()
        return await client.get(url, **kwargs)

    async def fetch_json(self, url: str, **kwargs) -> dict:
        response = await self.fetch(url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def fetch_with_retry(
        self,
        url: str,
        *,
        max_retries: int = 3,
        **kwargs,
    ) -> httpx.Response | None:
        """403 시 exponential backoff 재시도.

        성공 시 Response, 모든 시도 실패 시 None 반환.
        """
        for attempt in range(max_retries):
            try:
                resp = await self.fetch(url, **kwargs)
                if resp.status_code == 403:
                    wait = 2 ** attempt
                    logger.warning(
                        "403 at %s — retry %d/%d (wait %ds)",
                        url, attempt + 1, max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                return resp
            except Exception as e:
                logger.warning("Fetch failed: %s — %s", url, e)
                return None
        logger.warning("Max retries exceeded: %s", url)
        return None

    # --- 브라우저 도구 ---

    async def fetch_dynamic(self, url: str, wait_for: str | None = None) -> str:
        """Playwright로 페이지 렌더링 후 HTML 반환."""
        browser = await self._ensure_browser()
        page = await browser.new_page()
        try:
            await page.goto(url)
            if wait_for:
                await page.wait_for_selector(wait_for)
            return await page.content()
        finally:
            await page.close()

    # --- 크롤링 인터페이스 (서브클래스가 구현) ---

    @abstractmethod
    async def fetch_product_urls(self, url: str) -> list[str]:
        """브랜드의 상품 URL 목록을 가져온다. API/HTML에서 추출."""
        ...

    @abstractmethod
    async def crawl_product(self, url: str) -> RawProduct | None:
        """상품 URL에서 상세 정보를 크롤링한다.

        브랜드 독립 — URL만으로 동작. 단독 호출 가능.
        품절이면 None 반환.
        """
        ...

    async def crawl_brand(self, url: str) -> RawBrand | None:
        """브랜드 정보를 크롤링한다. 필요 없으면 오버라이드하지 않아도 됨."""
        return None

    # --- 템플릿 메서드 ---

    async def crawl(self, url: str) -> CrawlResult:
        """브랜드 크롤: 브랜드 정보 → 상품 URL 수집 → 상세 크롤링."""
        started_at = datetime.now()
        products: list[RawProduct] = []
        errors: list[CrawlError] = []

        logger.info("crawl_brand: platform=%s url=%s", self.platform.name, url)

        # 1. 브랜드 정보
        brand = await self.crawl_brand(url)

        # 2. 상품 URL 수집
        urls = await self.fetch_product_urls(url)
        logger.info("Product URLs: %d", len(urls))

        # 3. 상세 크롤링
        skipped = 0
        for product_url in urls:
            try:
                product = await self.crawl_product(product_url)
                if product is None or product.sold_out:
                    skipped += 1
                    continue
                products.append(product)
            except Exception as e:
                logger.warning("Product failed: url=%s error=%s", product_url, e)
                errors.append(
                    CrawlError(
                        url=product_url,
                        error_type=type(e).__name__,
                        message=str(e),
                        occurred_at=datetime.now(),
                    )
                )

        duration = (datetime.now() - started_at).total_seconds()
        logger.info(
            "crawl_brand done: products=%d skipped_soldout=%d errors=%d duration=%.1fs",
            len(products), skipped, len(errors), duration,
        )

        return CrawlResult(
            brand=brand,
            products=products,
            platform=self.platform,
            crawl_started_at=started_at,
            crawl_finished_at=datetime.now(),
            total_count=len(urls),
            error_count=len(errors),
            errors=errors,
        )
