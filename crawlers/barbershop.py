import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from common.constants import PLATFORMS
from common.schemas import CrawlMetadata, RawBrand, RawProduct

logger = logging.getLogger(__name__)

BASE_URL = "https://barbershop.co.kr"

import crawlers.utils.cafe24 as cafe_utils
import crawlers.utils.common as crawl_utils


class BarbershopCrawler(BaseCrawler):
    """barbershop.co.kr 크롤러 (Cafe24 기반 셀렉샵).

    fetch_product_urls: HTML 리스팅 페이지 파싱 (페이지네이션)
    crawl_product: JSON-LD + accordion HTML에서 추출
    crawl_brand: 리스팅 + 첫 상품 상세에서 브랜드 정보 추출
    """

    def __init__(self) -> None:
        super().__init__(PLATFORMS["barbershop"])
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

            items = soup.select(".xans-product-listnormal ul.prdList li.item.infinite-item")
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

            next_page = page + 1
            next_link = soup.select_one(f"a[href*='page={next_page}']")
            if not next_link:
                break

            page = next_page
            await asyncio.sleep(0.3)

        return urls

    def _parse_list_item(self, item) -> dict | None:
        """리스팅 li에서 상품 데이터 추출. 품절은 None 반환."""
        # 품절 체크: icon_sellout.gif (alt 텍스트가 "New"로 설정돼 있어 src로 감지)
        soldout_icon = item.select_one("img[src*='icon_sellout']")
        if soldout_icon:
            return None

        # 상품 링크
        link = item.select_one("a[href*='/product/detail.html']")
        if not link:
            return None

        href = link.get("href", "")
        full_url = crawl_utils.normalize_url(BASE_URL, href)

        # product_no: id 속성에서 추출
        item_id = item.get("id", "")
        product_no_match = re.search(r"(\d+)$", item_id)
        if not product_no_match:
            return None
        product_no = product_no_match.group(1)

        # 브랜드
        brand_el = item.select_one("li[rel='브랜드']")
        brand = ""
        if brand_el:
            spans = brand_el.select("span")
            brand = spans[-1].get_text(strip=True) if spans else ""

        # 상품명
        name_el = item.select_one("div.name a span")
        name = name_el.get_text(strip=True) if name_el else ""

        # 썸네일
        img = item.select_one("img.thumb")
        thumbnail = crawl_utils.normalize_url(BASE_URL, img["src"]) if img and img.get("src") else None

        # 판매가: li[rel="판매가"]의 직접 자식 span (strong 안의 span 제외)
        price_el = item.select_one("li[rel='판매가']")
        original_price = None
        if price_el:
            for span in price_el.find_all("span", recursive=False):
                text = span.get_text(strip=True)
                if text:
                    original_price = crawl_utils.parse_price(text)
                    break

        return {
            "product_no": product_no,
            "url": full_url,
            "brand": brand,
            "name": name,
            "thumbnail": thumbnail,
            "original_price": original_price,
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

        # 가격: JSON-LD offers > 캐시 (offers는 dict 또는 list)
        original_price = None
        if json_ld:
            offers = json_ld.get("offers", {})
            if isinstance(offers, dict):
                price_val = offers.get("price")
                if price_val is not None:
                    original_price = int(price_val)
            elif isinstance(offers, list) and offers:
                price_val = offers[0].get("price")
                if price_val is not None:
                    original_price = int(price_val)
        if not original_price:
            original_price = cached.get("original_price")

        # 할인가: product_sale_price JS 변수
        discounted_price = None
        sale_match = re.search(r"product_sale_price\s*=\s*['\"]?(\d+)", html)
        if sale_match:
            sale_price = int(sale_match.group(1))
            if sale_price > 0 and original_price and sale_price < original_price:
                discounted_price = sale_price

        # 품절: JSON-LD offers 전체 OutOfStock (+ aSoldoutDisplay 폴백)
        if cafe_utils.check_soldout(json_ld, html, cached.get("product_no", "")):
            return None

        # 갤러리 이미지: JSON-LD image 배열
        gallery_urls = cafe_utils.extract_gallery_from_json_ld(json_ld, BASE_URL)
        thumbnail = cached.get("thumbnail") or (gallery_urls[0] if gallery_urls else None)

        # 설명 텍스트: simple_desc + DETAILS accordion
        desc_txt = _extract_description(soup)

        # 설명 이미지: accordion 내 NNEditor 이미지
        desc_img_urls = _extract_description_images(soup, gallery_urls)

        # 소재
        product_no = cached.get("product_no")
        if not product_no:
            m = re.search(r"product_no=(\d+)", url)
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
            description_image_urls=desc_img_urls,
            description_txt=desc_txt,
            metadata=CrawlMetadata(
                platform="barbershop",
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

            # 브랜드명: 일반 리스팅(listnormal)의 첫 상품에서 추출
            name = None
            brand_el = soup.select_one(
                ".xans-product-listnormal ul.prdList li.item li[rel='브랜드']"
            )
            if brand_el:
                spans = brand_el.select("span")
                if spans:
                    name = spans[-1].get_text(strip=True)

            if not name:
                name = f"brand_{cate_no}"

            # 브랜드 설명: 상품 상세의 simple_desc에서 추출
            # 첫 상품이 콜라보일 수 있으므로 최대 3개 시도, 브랜드명이 포함된 설명 우선
            description = None
            product_links = soup.select(
                ".xans-product-listnormal ul.prdList li.item a[href*='/product/detail.html']"
            )
            seen_urls: set[str] = set()
            candidates: list[str] = []
            for link in product_links:
                href = link.get("href", "")
                product_url = crawl_utils.normalize_url(BASE_URL, href)
                if product_url in seen_urls:
                    continue
                seen_urls.add(product_url)
                candidates.append(product_url)
                if len(candidates) >= 5:
                    break

            for product_url in candidates:
                detail_resp = await self.fetch(product_url)
                if detail_resp.status_code == 200:
                    detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                    desc = _extract_brand_description(detail_soup)
                    if not desc:
                        continue
                    if not description:
                        description = desc
                    if name and name.lower() in desc[:200].lower():
                        description = desc
                        break
                await asyncio.sleep(0.3)

            return RawBrand(
                platform_brand_id=cate_no,
                name=name,
                url=url,
                name_eng=name,
                description=description,
                metadata=CrawlMetadata(
                    platform="barbershop",
                    crawled_at=datetime.now(),
                    source_url=url,
                ),
            )
        except Exception as e:
            logger.warning("Brand info failed: %s — %s", url, e)
            return None


# --- 모듈 레벨 헬퍼 ---


def _extract_description(soup: BeautifulSoup) -> str | None:
    """simple_desc + DETAILS accordion에서 설명 텍스트 추출."""
    parts: list[str] = []

    # 1. simple_desc (브랜드/상품 소개)
    simple_desc = soup.select_one("#simple_desc .simple_desc_wrap")
    if simple_desc:
        text = simple_desc.get_text(separator="\n", strip=True)
        # "...+" / "...-" 토글 텍스트 제거
        text = re.sub(r"\s*\.{3}[+-]$", "", text, flags=re.MULTILINE).strip()
        if text:
            parts.append(text)

    # 2. DETAILS accordion (스펙, 소재 정보)
    accordion = soup.select_one("#accordion")
    if accordion:
        first_content = accordion.select_one(".accordion-list .accordion-content")
        if first_content:
            text = first_content.get_text(separator="\n", strip=True)
            if text and len(text) > 10:
                parts.append(text)

    combined = "\n\n".join(parts)
    return combined if combined and len(combined) >= 20 else None


def _extract_description_images(soup: BeautifulSoup, gallery_urls: list[str]) -> list[str]:
    """accordion 내 NNEditor 이미지 추출 (갤러리와 중복 제거)."""
    desc_imgs: list[str] = []
    gallery_set = set(gallery_urls)

    accordion = soup.select_one("#accordion")
    if not accordion:
        return []

    for img in accordion.select("img[src*='/web/upload/NNEditor/']"):
        src = img.get("src", "")
        if not src:
            continue
        # 소형 아이콘 제외 (scroll-down, scroll-up 등)
        name = src.rsplit("/", 1)[-1].lower()
        if "scroll" in name:
            continue
        full_url = crawl_utils.normalize_url(BASE_URL, src)
        if full_url not in gallery_set and full_url not in desc_imgs:
            desc_imgs.append(full_url)

    return desc_imgs


def _extract_brand_description(soup: BeautifulSoup) -> str | None:
    """상품 상세의 simple_desc에서 브랜드 설명 추출."""
    simple_desc = soup.select_one("#simple_desc .simple_desc_wrap")
    if not simple_desc:
        return None
    text = simple_desc.get_text(separator="\n", strip=True)
    text = re.sub(r"\s*\.{3}[+-]$", "", text, flags=re.MULTILINE).strip()
    return text if len(text) >= 10 else None
