import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from common.constants import PLATFORMS
from common.schemas import CrawlMetadata, RawBrand, RawProduct
import crawlers.utils.cafe24 as cafe_utils
import crawlers.utils.common as crawl_utils

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 80


class ModemanCrawler(BaseCrawler):
    """mode-man.com 크롤러 (Cafe24 기반).

    fetch_product_urls: 상품 목록 HTML 파싱 (페이지네이션)
    crawl_product: JSON-LD 갤러리 + div#detail 설명 추출
    crawl_brand: 목록 페이지에서 브랜드명 추출
    """

    def __init__(self) -> None:
        super().__init__(PLATFORMS["modeman"])
        self._product_cache: dict[str, dict] = {}

    # --- 상품 목록 ---

    async def fetch_product_urls(self, url: str) -> list[str]:
        urls: list[str] = []
        page = 1

        while True:
            separator = "&" if "?" in url else "?"
            list_url = f"{url}{separator}page={page}"
            resp = await self.fetch(list_url)
            soup = BeautifulSoup(resp.text, "html.parser")

            ul = soup.find("ul", class_="p")
            if not ul:
                break
            items = ul.find_all("li", class_="xans-record-", recursive=False)

            for item in items:
                parsed = self._parse_list_item(item)
                if not parsed:
                    continue
                if parsed.get("sold_out"):
                    continue
                product_url = parsed["url"]
                self._product_cache[product_url] = parsed
                urls.append(product_url)

            logger.debug("page=%d items=%d urls=%d", page, len(items), len(urls))

            if len(items) < ITEMS_PER_PAGE:
                break
            page += 1
            await asyncio.sleep(0.3)

        return urls

    def _parse_list_item(self, item) -> dict | None:
        # 상품 URL
        link = item.find("a", href=lambda h: h and "/category/" in h)
        if not link:
            return None
        href = link["href"]

        match = re.search(r"/product/[^/]+/(\d+)/category/", href)
        if not match:
            return None
        product_no = match.group(1)
        url = crawl_utils.normalize_url(self.platform.base_url, href)

        # 브랜드, 상품명
        brand_el = item.select_one("p.b")
        name_el = item.select_one("p.nm")
        brand = brand_el.get_text(strip=True) if brand_el else ""
        name = name_el.get_text(strip=True) if name_el else ""

        # 가격
        pr1 = item.select_one("span.pr1")
        pr2 = item.select_one("span.pr2")
        original_price = crawl_utils.parse_price(pr1.get_text(strip=True)) if pr1 else None
        discounted_price = crawl_utils.parse_price(pr2.get_text(strip=True)) if pr2 else None

        # 썸네일
        img = item.find("img", src=lambda s: s and "/web/product/" in s)
        thumbnail = crawl_utils.normalize_url(self.platform.base_url, img["src"]) if img else None

        # 품절
        sold_span = item.select_one("span.sold")
        sold_out = bool(
            sold_span and "displaynone" not in sold_span.get("class", [])
        )

        return {
            "product_no": product_no,
            "url": url,
            "brand": brand,
            "name": name,
            "original_price": original_price,
            "discounted_price": discounted_price,
            "thumbnail": thumbnail,
            "sold_out": sold_out,
        }

    # --- 상품 상세 ---

    async def crawl_product(self, url: str) -> RawProduct:
        cached = self._product_cache.get(url)

        resp = await self.fetch_with_retry(url)
        html = resp.text if resp else ""
        soup = BeautifulSoup(html, "html.parser")

        json_ld = cafe_utils.extract_json_ld(soup)
        gallery_urls = cafe_utils.extract_gallery_from_json_ld(json_ld, self.platform.base_url)
        desc_image_urls = self._extract_description_images(soup, gallery_urls)
        desc_txt = self._extract_description(soup)
        now = datetime.now()

        if cached:
            product_no = cached["product_no"]
            name = cached["name"]
            brand = cached["brand"]
            original_price = cached["original_price"]
            discounted_price = cached["discounted_price"]
            thumbnail = cached["thumbnail"]
        else:
            match = re.search(r"/product/[^/]+/(\d+)/", url)
            product_no = match.group(1) if match else url
            name = ""
            brand = ""
            original_price = None
            discounted_price = None
            thumbnail = gallery_urls[0] if gallery_urls else None

            # JSON-LD 폴백
            if json_ld:
                name = json_ld.get("name", "")
                offers = json_ld.get("offers", {})
                if isinstance(offers, dict):
                    original_price = offers.get("price")
                elif isinstance(offers, list) and offers:
                    original_price = offers[0].get("price")

        if discounted_price and discounted_price == original_price:
            discounted_price = None

        return RawProduct(
            platform_product_id=str(product_no),
            name=name,
            brand=brand,
            url=url,
            original_price=original_price,
            discounted_price=discounted_price,
            sold_out=False,

            thumbnail_url=thumbnail,
            gallery_image_urls=gallery_urls,
            description_image_urls=desc_image_urls,
            description_txt=desc_txt,
            metadata=CrawlMetadata(
                platform="modeman",
                crawled_at=now,
                source_url=url,
            ),
        )

    # --- HTML 파싱 ---

    def _extract_description_images(
        self, soup: BeautifulSoup, gallery_urls: list[str]
    ) -> list[str]:
        """#detail 영역의 img 태그에서 설명 이미지 추출. gallery와 중복 제거."""
        detail = soup.find("div", id="detail")
        if not detail:
            return []
        gallery_set = set(gallery_urls)
        urls: list[str] = []
        for img in detail.find_all("img", src=True):
            src = crawl_utils.normalize_url(self.platform.base_url, img["src"])
            if src and src not in gallery_set and src not in urls:
                urls.append(src)
        return urls

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        detail = soup.find("div", id="detail")
        if not detail:
            return None
        text = detail.get_text(separator="\n", strip=True)
        if not text:
            return None
        # UI 텍스트 제거
        lines = [
            line for line in text.split("\n")
            if "스타일링 바로가기" not in line
        ]
        return "\n".join(lines).strip() or None

    # --- 브랜드 정보 ---

    async def crawl_brand(self, url: str) -> RawBrand | None:
        try:
            resp = await self.fetch(url)
            soup = BeautifulSoup(resp.text, "html.parser")

            cate_no = cafe_utils.extract_cate_no(url)

            # 첫 번째 상품의 브랜드명
            brand_el = soup.select_one("ul.p li.xans-record- p.b")
            brand_name = brand_el.get_text(strip=True) if brand_el else f"brand_{cate_no}"

            return RawBrand(
                platform_brand_id=cate_no,
                name=brand_name,
                url=url,
                metadata=CrawlMetadata(
                    platform="modeman",
                    crawled_at=datetime.now(),
                    source_url=url,
                ),
            )
        except Exception as e:
            logger.warning("Brand info failed: %s — %s", url, e)
            return None


