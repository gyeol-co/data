import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from common.constants import PLATFORMS
from common.schemas import CrawlError, CrawlMetadata, CrawlResult, RawBrand, RawProduct
import crawlers.utils.cafe24 as cafe_utils

logger = logging.getLogger(__name__)

# cate_no → category 매핑
CATEGORIES = {
    63: "outer",
    60: "top",
    81: "top",       # knit → top
    61: "bottom",
    62: "acc",
    83: "acc",       # belt → acc
}
ABOUT_PATH = "/postfixo/newpage/about.html"

# prdDetail 공통 이미지 필터 (factory 소개, 모델 사이즈, 교환/반품 안내)
_COMMON_IMAGE = re.compile(r"(factory|modelsize|Policy)", re.IGNORECASE)


class BastongCrawler(BaseCrawler):
    """m.bastong.co.kr 크롤러 (Cafe24 기반).

    fetch_product_urls: 카테고리별 리스팅에서 상품 URL 추출 + 카테고리 태깅
    crawl_product: JSON-LD + #prdDetail 이미지/텍스트 추출
    crawl_brand: About 페이지에서 브랜드 정보 추출
    """

    def __init__(self) -> None:
        super().__init__(PLATFORMS["bastong"])
        self._product_cache: dict[str, dict] = {}

    # --- 상품 목록 ---

    async def fetch_product_urls(self, url: str) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []

        for cate_no, category in CATEGORIES.items():
            list_url = (
                f"{self.platform.base_url}/product/list_thumb.html"
                f"?cate_no={cate_no}"
            )
            resp = await self.fetch(list_url)
            soup = BeautifulSoup(resp.text, "html.parser")

            count = 0
            for item in soup.select("ul.grid2 > li"):
                parsed = self._parse_list_item(item)
                if not parsed:
                    continue
                url = parsed["url"]
                product_no = parsed["product_no"]

                if product_no in seen:
                    continue

                seen.add(product_no)
                parsed["category"] = category
                self._product_cache[url] = parsed
                urls.append(url)
                count += 1

            logger.info("Category %s (cate_no=%d): %d new products", category, cate_no, count)
            await asyncio.sleep(0.3)

        logger.info("Total unique products: %d", len(urls))
        return urls

    def _parse_list_item(self, item) -> dict | None:
        link = item.select_one(".thumbnail a")
        if not link or not link.get("href"):
            return None
        href = link["href"]

        match = re.search(r"/product/[^/]+/(\d+)/", href)
        if not match:
            return None

        product_no = match.group(1)
        url = f"{self.platform.base_url}{href}"

        name_el = item.select_one(".information .name a")
        name = name_el.get_text(strip=True) if name_el else ""

        img = item.select_one(".thumbnail img")
        thumbnail = (
            _normalize_url(img["src"]) if img and img.get("src") else None
        )

        return {
            "product_no": product_no,
            "url": url,
            "name": name,
            "thumbnail": thumbnail,
        }

    # --- 상품 상세 ---

    async def crawl_product(self, url: str) -> RawProduct:
        cached = self._product_cache.get(url, {})

        resp = await self.fetch_with_retry(url)
        html = resp.text if resp else ""
        soup = BeautifulSoup(html, "html.parser")
        await asyncio.sleep(0.5)

        json_ld = cafe_utils.extract_json_ld(soup)

        # 기본 정보: JSON-LD 우선, cache 폴백
        name = ""
        original_price = None
        if json_ld:
            name = json_ld.get("name", "")
            offers = json_ld.get("offers", [])
            if offers and isinstance(offers, list):
                original_price = offers[0].get("price")
                # 컬러 추출: offers[0].name = "109 OLIVE-S" → "OLIVE"
                offer_name = offers[0].get("name", "")
                color = _extract_color(name, offer_name)
                if color:
                    name = f"{name} {color}"

        if not name:
            name = cached.get("name", "")

        # 품절 여부: JSON-LD offers 우선, aSoldoutDisplay 폴백
        if cafe_utils.check_soldout(json_ld, html, cached.get("product_no", "")):
            return None

        product_no = cached.get("product_no")
        if not product_no:
            m = re.search(r"/product/[^/]+/(\d+)/", url)
            product_no = m.group(1) if m else url

        # 이미지: bastong은 Cafe24 갤러리 모듈 미사용, 상품 이미지가 #prdDetail에 있음
        gallery_urls = cafe_utils.extract_gallery_from_json_ld(json_ld, self.platform.base_url)
        detail_imgs = self._extract_description_images(soup, gallery_urls)
        gallery_urls += [u for u in detail_imgs if not _COMMON_IMAGE.search(u.rsplit("/", 1)[-1])]
        thumbnail = cached.get("thumbnail") or (
            gallery_urls[0] if gallery_urls else None
        )

        # 텍스트
        desc_txt = self._extract_description(soup)
        category = cached.get("category")

        return RawProduct(
            platform_product_id=str(product_no),
            name=name,
            brand="바스통",
            url=url,

            category=category,
            original_price=original_price,
            discounted_price=None,
            sold_out=False,

            thumbnail_url=thumbnail,
            gallery_image_urls=gallery_urls,
            description_image_urls=[],
            description_txt=desc_txt,
            metadata=CrawlMetadata(
                platform="bastong",
                crawled_at=datetime.now(),
                source_url=url,
            ),
        )

    # --- HTML 파싱 ---

    def _extract_description_images(
        self, soup: BeautifulSoup, gallery_urls: list[str]
    ) -> list[str]:
        detail = soup.find("div", id="prdDetail")
        if not detail:
            return []
        gallery_set = set(gallery_urls)
        urls: list[str] = []
        for img in detail.find_all("img", src=True):
            src = _normalize_url(img["src"])
            if src and src not in gallery_set and src not in urls:
                urls.append(src)
        return urls

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        detail = soup.find("div", id="prdDetail")
        if not detail:
            return None
        text = detail.get_text(separator="\n", strip=True)
        return text.strip() or None

    # --- 브랜드 정보 ---

    async def crawl_brand(self, url: str) -> RawBrand | None:
        about_url = f"{self.platform.base_url}{ABOUT_PATH}"
        try:
            resp = await self.fetch(about_url)
            soup = BeautifulSoup(resp.text, "html.parser")

            content = (
                soup.find("div", id="about")
                or soup.find("div", id="fixoContent")
            )
            description = (
                content.get_text(separator="\n", strip=True) if content else None
            )

            return RawBrand(
                platform_brand_id=None,
                name="바스통",
                name_eng="BASTONG",
                url=url,
                description=description,
                metadata=CrawlMetadata(
                    platform="bastong",
                    crawled_at=datetime.now(),
                    source_url=about_url,
                ),
            )
        except Exception as e:
            logger.warning("Brand info failed: %s", e)
            return None


def _extract_color(name: str, offer_name: str) -> str | None:
    """offers[0].name에서 컬러 추출. '109 OLIVE-S' → 'OLIVE'."""
    if not offer_name or not name:
        return None
    # offer_name: "{model} {COLOR}-{SIZE}"
    parts = offer_name.rsplit("-", 1)
    if len(parts) != 2:
        return None
    name_color = parts[0]  # "109 OLIVE"
    color = name_color.replace(name, "", 1).strip()
    return color if color else None


def _normalize_url(url: str) -> str:
    """프로토콜 생략 / 상대 경로 URL 정규화."""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://m.bastong.co.kr{url}"
    return url
