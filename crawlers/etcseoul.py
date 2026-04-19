import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import unquote

from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from common.constants import PLATFORMS
from common.schemas import CrawlMetadata, RawBrand, RawProduct

logger = logging.getLogger(__name__)

BASE_URL = "https://www.etcseoul.com"

# 설명 이미지에서 제외할 패턴 (브랜드 로고, 공통 안내 배너)
_DESCRIPTION_IMAGE_EXCLUDE = re.compile(
    r"로고|logo|banner|guide|안내|공지|notice|event",
    re.IGNORECASE,
)


import crawlers.utils.cafe24 as cafe_utils
import crawlers.utils.common as crawl_utils


class EtcseoulCrawler(BaseCrawler):
    """etcseoul.com 크롤러 (Cafe24 기반).

    fetch_product_urls: HTML 목록 페이지 파싱 (페이지네이션)
    crawl_product: 상세 페이지 HTML에서 갤러리/설명 추출
    crawl_brand: 목록 1페이지 brand_title_wrap에서 브랜드 메타 추출
    """

    ITEMS_PER_PAGE = 40

    def __init__(self) -> None:
        super().__init__(PLATFORMS["etcseoul"])
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

            prd_list = soup.select_one("ul.prdList")
            if not prd_list:
                break

            items = prd_list.select("li.item.xans-record-")
            if not items:
                break

            for item in items:
                parsed = self._parse_list_item(item)
                if not parsed:
                    continue
                product_url = parsed["url"]
                self._product_cache[product_url] = parsed
                urls.append(product_url)

            logger.debug("page=%d items=%d total_so_far=%d", page, len(items), len(urls))

            if len(items) < self.ITEMS_PER_PAGE:
                break
            page += 1
            await asyncio.sleep(0.3)

        return urls

    def _parse_list_item(self, item) -> dict | None:
        """목록 li에서 기본 데이터를 추출한다. 품절 상품은 None 반환."""
        # 품절 필터링 — icon img alt="품절"
        icon = item.select_one("div.icon img")
        if icon and icon.get("alt", "") == "품절":
            return None

        # 가격 없는 항목(룩북 등) 필터링
        item_text = item.get_text()
        if "원" not in item_text:
            return None

        thumb_a = item.select_one("a.thumb")
        if not thumb_a:
            return None

        href = thumb_a.get("href", "")
        product_no_match = re.search(r"product_no=(\d+)", href)
        if not product_no_match:
            return None

        product_no = product_no_match.group(1)
        full_url = f"{BASE_URL}{href}" if href.startswith("/") else href

        # 상품명
        name_a = item.select_one("a.name")
        name = ""
        if name_a:
            spans = name_a.select("span:not(.title)")
            name = spans[-1].text.strip() if spans else name_a.text.strip()

        # 브랜드
        brand_a = item.select_one("div.brand a")
        brand = brand_a.text.strip() if brand_a else ""

        # 썸네일
        thumb_img = thumb_a.select_one("img")
        thumbnail = crawl_utils.normalize_url(BASE_URL,thumb_img["src"]) if thumb_img and thumb_img.get("src") else None

        # 가격 — 원가/할인가 분리
        original_price = None
        discounted_price = None
        price_lis = [
            li for li in item.select("ul.xans-product-listitem > li")
            if "원" in li.get_text()
        ]
        if len(price_lis) >= 2:
            # 첫 번째: 원가 (line-through), 두 번째: 할인가
            original_price = crawl_utils.parse_price(price_lis[0].get_text())
            discounted_price = crawl_utils.parse_price(price_lis[1].get_text())
        elif len(price_lis) == 1:
            original_price = crawl_utils.parse_price(price_lis[0].get_text())

        return {
            "product_no": product_no,
            "url": full_url,
            "name": name,
            "brand": brand,
            "thumbnail": thumbnail,
            "original_price": original_price,
            "discounted_price": discounted_price,
        }

    # --- 상품 상세 ---

    async def crawl_product(self, url: str) -> RawProduct:
        cached = self._product_cache.get(url, {})

        # 상세 페이지 fetch (403 시 exponential backoff)
        resp = await self.fetch_with_retry(url)
        soup = BeautifulSoup(resp.text, "html.parser") if resp else None

        # 품절 체크 — early return
        if soup and self._check_sold_out(soup):
            return None

        gallery_urls = self._extract_gallery_images(soup) if soup else []
        desc_txt = self._extract_description_txt(soup) if soup else None
        detail_prices = self._extract_detail_prices(soup) if soup else {}

        # 가격: 상세 페이지 > 목록 캐시 순서
        original_price = detail_prices.get("original") or cached.get("original_price")
        discounted_price = detail_prices.get("discounted") or cached.get("discounted_price")
        # 원가와 같으면 None
        if discounted_price and discounted_price == original_price:
            discounted_price = None

        now = datetime.now()
        return RawProduct(
            platform_product_id=cached.get("product_no", ""),
            name=cached.get("name", ""),
            brand=cached.get("brand", ""),
            url=url,
            original_price=original_price,
            discounted_price=discounted_price,
            sold_out=False,
            thumbnail_url=cached.get("thumbnail"),
            gallery_image_urls=gallery_urls,
            description_image_urls=[],
            description_txt=desc_txt,
            metadata=CrawlMetadata(
                platform="etcseoul",
                crawled_at=now,
                source_url=url,
            ),
        )

    # --- HTML 파싱 헬퍼 ---

    def _extract_gallery_images(self, soup: BeautifulSoup) -> list[str]:
        """NNEditor 이미지를 갤러리로 추출. etcseoul에서는 이것이 옷 사진."""
        imgs = soup.find_all("img", src=lambda s: s and "/web/upload/NNEditor/" in s)
        seen: set[str] = set()
        images: list[str] = []
        for img in imgs:
            src = img.get("src", "")
            decoded = unquote(src)
            if _DESCRIPTION_IMAGE_EXCLUDE.search(decoded):
                continue
            full = crawl_utils.normalize_url(BASE_URL,src)
            if full not in seen:
                seen.add(full)
                images.append(full)
        return images

    def _extract_description_txt(self, soup: BeautifulSoup) -> str | None:
        """#contents 영역에서 설명 텍스트만 추출."""
        contents = soup.select_one("#contents")
        if not contents:
            return None
        text = contents.get_text(separator="\n", strip=True)
        return text if text else None

    def _check_sold_out(self, soup: BeautifulSoup) -> bool:
        """soldout_text에 displaynone이 없으면 품절."""
        el = soup.select_one(".soldout_text")
        if not el:
            return False
        return "displaynone" not in el.get("class", [])

    def _extract_detail_prices(self, soup: BeautifulSoup) -> dict[str, int | None]:
        """상세 페이지에서 원가와 할인가를 추출."""
        prices: dict[str, int | None] = {}
        original = soup.select_one("#span_product_price_text")
        if original:
            prices["original"] = crawl_utils.parse_price(original.text)
        sale = soup.select_one("#span_product_price_sale")
        if sale:
            prices["discounted"] = crawl_utils.parse_price(sale.text)
        return prices

    # --- 브랜드 정보 ---

    async def crawl_brand(self, url: str) -> RawBrand | None:
        """목록 1페이지의 brand_title_wrap에서 브랜드 메타를 추출."""
        try:
            resp = await self.fetch(url)
            soup = BeautifulSoup(resp.text, "html.parser")

            cate_no = cafe_utils.extract_cate_no(url)

            # 브랜드명
            brand_title = soup.select_one(".brand_title_wrap ol li a")
            name = brand_title.text.strip() if brand_title else f"brand_{cate_no}"
            brand_href = brand_title.get("href", "") if brand_title else ""
            brand_url = crawl_utils.normalize_url(BASE_URL,brand_href) if brand_href else url

            # 설명 (map 태그 안 텍스트)
            description = None
            map_el = soup.select_one(".brand_title_wrap map")
            if map_el:
                description = map_el.get_text(separator="\n", strip=True)

            # 로고/배너 이미지
            banner_img = soup.select_one(".brand_title_wrap .banner img")
            logo_url = crawl_utils.normalize_url(BASE_URL,banner_img["src"]) if banner_img and banner_img.get("src") else None

            return RawBrand(
                platform_brand_id=cate_no,
                name=name,
                url=url,
                name_eng=name,  # etcseoul은 영문명만 사용
                logo_url=logo_url,
                description=description,
                metadata=CrawlMetadata(
                    platform="etcseoul",
                    crawled_at=datetime.now(),
                    source_url=url,
                ),
            )
        except Exception as e:
            logger.warning("Brand info failed: %s — %s", url, e)
            return None
