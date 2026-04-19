import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from common.constants import PLATFORMS
from common.schemas import CrawlError, CrawlMetadata, CrawlResult, RawBrand, RawProduct

logger = logging.getLogger(__name__)

BASE_URL = "https://sculpstore.com"


import crawlers.utils.cafe24 as cafe_utils
import crawlers.utils.common as crawl_utils


class SculpstoreCrawler(BaseCrawler):
    """sculpstore.com 크롤러 (Cafe24 기반 셀렉샵).

    fetch_product_urls: HTML 리스팅 페이지 파싱 (페이지네이션)
    crawl_product: JSON-LD + prd-info HTML에서 추출
    crawl_brand: 리스팅 1페이지에서 브랜드명 추출
    """

    def __init__(self) -> None:
        super().__init__(PLATFORMS["sculpstore"])
        self._product_cache: dict[str, dict] = {}

    # --- 상품 목록 ---

    async def fetch_product_urls(self, url: str) -> list[str]:
        urls: list[str] = []
        page = 1

        while True:
            separator = "&" if "?" in url else "?"
            list_url = f"{url}{separator}page={page}"
            resp = await self.fetch(list_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            items = soup.select("ul.p > li[data-no]")
            if not items:
                break

            page_count = 0
            for item in items:
                parsed = self._parse_list_item(item)
                if not parsed:
                    continue
                product_url = parsed["url"]
                self._product_cache[product_url] = parsed
                urls.append(product_url)
                page_count += 1

            logger.debug(
                "page=%d items=%d added=%d total_so_far=%d",
                page, len(items), page_count, len(urls),
            )

            # 다음 페이지 존재 확인
            next_page = page + 1
            next_link = soup.select_one(f"a[href*='page={next_page}']")
            if not next_link:
                break

            page = next_page
            await asyncio.sleep(0.3)

        return urls

    def _parse_list_item(self, item) -> dict | None:
        """리스팅 li에서 상품 데이터 추출. 품절은 None 반환."""
        # 품절 체크: span.sold에 displaynone 없으면 품절 표시 중
        sold_span = item.select_one("span.sold")
        if sold_span and "displaynone" not in sold_span.get("class", []):
            return None

        # 상품 링크
        link = item.select_one("a[href*='/product/']")
        if not link:
            return None

        href = link.get("href", "")
        product_no_match = re.search(r"/product/[^/]+/(\d+)/", href)
        if not product_no_match:
            return None

        product_no = product_no_match.group(1)
        full_url = crawl_utils.normalize_url(BASE_URL,href)

        # 브랜드
        brand_el = item.select_one("p.b")
        brand = brand_el.get_text(strip=True) if brand_el else ""

        # 상품명
        name_el = item.select_one("p.nm span")
        name = name_el.get_text(strip=True) if name_el else ""

        # 썸네일
        img = item.select_one("div.img img")
        thumbnail = crawl_utils.normalize_url(BASE_URL,img["src"]) if img and img.get("src") else None

        # 원가: ec-data-price 속성
        dsc = item.select_one("div.dsc")
        original_price = None
        if dsc and dsc.get("ec-data-price"):
            original_price = crawl_utils.parse_price(dsc["ec-data-price"])

        # 할인가
        sale_el = item.select_one("span.sale_price")
        discounted_price = None
        if sale_el:
            discounted_price = crawl_utils.parse_price(sale_el.get_text())

        if discounted_price and discounted_price == original_price:
            discounted_price = None

        return {
            "product_no": product_no,
            "url": full_url,
            "brand": brand,
            "name": name,
            "thumbnail": thumbnail,
            "original_price": original_price,
            "discounted_price": discounted_price,
        }

    # --- 상품 상세 ---

    async def crawl_product(self, url: str) -> RawProduct:
        cached = self._product_cache.get(url, {})

        resp = await self.fetch_with_retry(url)
        html = resp.text if resp else ""
        soup = BeautifulSoup(html, "html.parser")
        await asyncio.sleep(0.5)

        json_ld = cafe_utils.extract_json_ld(soup)

        # 기본 정보: JSON-LD 우선, 캐시 폴백
        name = ""
        if json_ld:
            name = json_ld.get("name", "")
        if not name:
            name = cached.get("name", "")

        brand = ""
        if json_ld:
            brand_data = json_ld.get("brand", {})
            if isinstance(brand_data, dict):
                brand = brand_data.get("name", "")
        if not brand:
            brand = cached.get("brand", "")

        # 가격: 상세 > 캐시
        detail_prices = self._extract_detail_prices(html, json_ld)
        original_price = detail_prices.get("original") or cached.get("original_price")
        discounted_price = detail_prices.get("discounted") or cached.get("discounted_price")
        if discounted_price and discounted_price == original_price:
            discounted_price = None

        # 품절: JSON-LD offers 기준 (모든 사이즈 OutOfStock이면 품절)
        if cafe_utils.check_soldout(json_ld, html, cached.get("product_no", "")):
            return None

        # 이미지: JSON-LD image 배열
        gallery_urls = cafe_utils.extract_gallery_from_json_ld(json_ld, BASE_URL)
        thumbnail = cached.get("thumbnail") or (gallery_urls[0] if gallery_urls else None)

        # 소재 & 설명: prd-info div
        desc_txt = self._extract_description(soup)

        product_no = cached.get("product_no")
        if not product_no:
            m = re.search(r"/product/[^/]+/(\d+)/", url)
            product_no = m.group(1) if m else url

        now = datetime.now()
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
            description_image_urls=[],  # sculpstore는 설명 이미지 없음 (텍스트 기반)
            description_txt=desc_txt,
            metadata=CrawlMetadata(
                platform="sculpstore",
                crawled_at=now,
                source_url=url,
            ),
        )

    # --- 브랜드 정보 ---

    async def crawl_brand(self, url: str) -> RawBrand | None:
        """리스팅 + 첫 상품 상세에서 브랜드 정보 추출."""
        try:
            resp = await self.fetch(url)
            soup = BeautifulSoup(resp.text, "html.parser")

            cate_no = cafe_utils.extract_cate_no(url)

            # 브랜드명: 첫 상품 카드의 p.b에서 추출
            name = None
            brand_el = soup.select_one("ul.p li[data-no] p.b")
            if brand_el:
                name = brand_el.get_text(strip=True)

            if not name:
                name = f"brand_{cate_no}"

            # 브랜드 설명: 첫 상품 상세 페이지의 prd-info에서 추출
            description = None
            first_link = soup.select_one("ul.p li[data-no] a[href*='/product/']")
            if first_link:
                href = first_link.get("href", "")
                product_url = crawl_utils.normalize_url(BASE_URL,href)
                detail_resp = await self.fetch(product_url)
                if detail_resp.status_code == 200:
                    detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                    description = _extract_brand_description(detail_soup)
                await asyncio.sleep(0.3)

            return RawBrand(
                platform_brand_id=cate_no,
                name=name,
                url=url,
                name_eng=name,
                description=description,
                metadata=CrawlMetadata(
                    platform="sculpstore",
                    crawled_at=datetime.now(),
                    source_url=url,
                ),
            )
        except Exception as e:
            logger.warning("Brand info failed: %s — %s", url, e)
            return None

    # --- HTML/JSON-LD 파싱 헬퍼 ---

    def _extract_detail_prices(
        self, html: str, json_ld: dict | None
    ) -> dict[str, int | None]:
        """상세 페이지에서 원가/할인가 추출."""
        prices: dict[str, int | None] = {}

        # JSON-LD offers → 원가
        if json_ld:
            offers = json_ld.get("offers", [])
            if offers and isinstance(offers, list):
                prices["original"] = offers[0].get("price")

        # JS 변수 → 할인가
        sale_match = re.search(r"product_sale_price\s*=\s*(\d+)", html)
        if sale_match:
            sale_price = int(sale_match.group(1))
            if sale_price > 0:
                prices["discounted"] = sale_price

        return prices

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        """prd-info에서 상품 설명 텍스트 추출 (배송 안내 제외)."""
        prd_info = soup.select_one("div.prd-info")
        if not prd_info:
            return None
        parts: list[str] = []
        for div in prd_info.find_all("div", recursive=False):
            h6 = div.find("h6")
            p = div.find("p")
            if not h6 or not p:
                continue
            label = h6.get_text(strip=True)
            text = p.get_text(strip=True)
            if label and text and label != "배송 안내":
                parts.append(f"{label}: {text}")
        return "\n".join(parts) if parts else None


def _extract_brand_description(soup: BeautifulSoup) -> str | None:
    """prd-info > 브랜드 설명에서 브랜드 소개 텍스트 추출."""
    prd_info = soup.select_one("div.prd-info")
    if not prd_info:
        return None
    for div in prd_info.find_all("div", recursive=False):
        h6 = div.find("h6")
        if h6 and "브랜드 설명" in h6.get_text():
            p = div.find("p")
            if p:
                text = p.get_text(strip=True)
                return text if len(text) >= 10 else None
    return None


