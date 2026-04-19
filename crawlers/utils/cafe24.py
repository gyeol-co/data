"""Cafe24 기반 크롤러 전용 유틸리티.

etcseoul, barbershop, sculpstore, modeman, bastong 등 Cafe24 플랫폼 공통 헬퍼.
"""

import json
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from crawlers.utils.common import normalize_url


def extract_cate_no(url: str) -> str | None:
    """URL에서 cate_no 파라미터를 추출한다."""
    qs = parse_qs(urlparse(url).query)
    vals = qs.get("cate_no", [])
    return vals[0] if vals else None


def extract_json_ld(soup: BeautifulSoup) -> dict | None:
    """JSON-LD Product 스키마를 추출한다."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if data.get("@type") == "Product":
                return data
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def extract_gallery_from_json_ld(json_ld: dict | None, base_url: str) -> list[str]:
    """JSON-LD image 배열에서 갤러리 이미지 URL을 추출한다."""
    if not json_ld:
        return []
    images = json_ld.get("image", [])
    if isinstance(images, str):
        images = [images]
    return [normalize_url(base_url, url) for url in images if isinstance(url, str)]


def check_soldout(json_ld: dict | None, html: str, product_no: str) -> bool:
    """Cafe24 품절 여부 확인.

    JSON-LD offers 우선: 모든 offer가 OutOfStock이면 품절.
    폴백: aSoldoutDisplay JS 변수.
    """
    if json_ld:
        offers = json_ld.get("offers", [])
        if isinstance(offers, list) and offers:
            return all(
                "OutOfStock" in str(o.get("availability", ""))
                for o in offers
            )
    if not product_no:
        return False
    match = re.search(r"aSoldoutDisplay\s*=\s*(\{[^}]*\})", html)
    if not match:
        return False
    try:
        soldout_map = json.loads(match.group(1))
        return product_no in soldout_map
    except (json.JSONDecodeError, TypeError):
        return False
