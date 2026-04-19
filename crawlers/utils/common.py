"""크롤러 범용 유틸리티.

모든 크롤러가 공통으로 사용하는 헬퍼 함수.
"""

import re


def normalize_url(base_url: str, src: str) -> str:
    """프로토콜 생략(//), 상대경로(/) → 절대 https URL로."""
    src = src.strip()
    if src.startswith("//"):
        return f"https:{src}"
    if src.startswith("/"):
        return f"{base_url}{src}"
    return src


def parse_price(text: str) -> int | None:
    """'258,000원' → 258000. '331,200원10%' → 331200 (% 이후 무시)."""
    idx = text.find("원")
    if idx != -1:
        text = text[:idx]
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None
