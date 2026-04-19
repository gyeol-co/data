"""Gemini API 클라이언트.

Silver ETL에서 텍스트 정제에 사용한다.
API 키는 환경변수 GEMINI_API_KEY 또는 .env 파일에서 로드.
"""

import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# --- API 키 관리 ---

_keys: list[str] = []
_key_index: int = 0


def _load_keys() -> list[str]:
    """환경변수 또는 ETL/.env에서 Gemini API 키를 로드한다."""
    keys: list[str] = []

    # 1. 환경변수에서 GEMINI_API_KEY, GEMINI_API_KEY1, ... 탐색
    for key in sorted(os.environ):
        if re.match(r"GEMINI_API_KEY\d*$", key):
            val = os.environ[key].strip().strip('"')
            if val:
                keys.append(val)

    if keys:
        return keys

    # 2. 폴백: ETL/.env 파일에서 직접 파싱
    env_path = Path(__file__).resolve().parents[3] / "ETL" / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                m = re.match(r'GEMINI_API_KEY\d*\s*=\s*"?([^"\s]+)"?', line.strip())
                if m:
                    keys.append(m.group(1))

    return keys


def _next_key() -> str:
    """라운드 로빈으로 다음 API 키를 반환한다."""
    global _keys, _key_index

    if not _keys:
        _keys = _load_keys()
    if not _keys:
        raise RuntimeError("Gemini API 키가 없습니다. GEMINI_API_KEY 환경변수를 설정하세요.")

    key = _keys[_key_index % len(_keys)]
    _key_index += 1
    return key


# --- API 호출 ---


def call_gemini(prompt: str, max_retries: int = 3) -> str:
    """Gemini API를 호출하고 텍스트 응답을 반환한다.

    키 로테이션 + 재시도 포함.
    """
    last_error = None

    for attempt in range(max_retries):
        key = _next_key()
        try:
            resp = httpx.post(
                f"{GEMINI_URL}?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=30.0,
            )
            if resp.status_code == 429:
                logger.warning("Gemini rate limited (key=%s...)", key[:10])
                continue
            resp.raise_for_status()

            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

        except Exception as e:
            last_error = e
            logger.warning("Gemini call failed (attempt %d): %s", attempt + 1, e)

    raise RuntimeError(f"Gemini API 호출 실패 ({max_retries}회 시도): {last_error}")


# --- 브랜드 설명 정제 ---


REFINE_PROMPT = """\
아래는 패션 편집샵에서 크롤링한 브랜드 설명 원문이다.
이 텍스트에서 **브랜드 소개에 해당하는 내용만** 추출하여 정제해라.

규칙:
- 브랜드의 역사, 철학, 특징, 스타일을 설명하는 부분만 남겨라
- 상품 스펙(소재, 사이즈, 가격), 배송/반품 안내, UI 텍스트, 매장 정보는 모두 제거
- 원문의 의미를 훼손하지 마라. 없는 내용을 추가하지 마라
- 자연스러운 한국어 문장으로 정리해라. 영문 고유명사는 그대로 유지
- 결과만 출력해라. 부가 설명이나 마크다운 서식 없이 순수 텍스트만

브랜드명: {brand_name}

원문:
{raw_description}
"""


def refine_brand_description(brand_name: str, raw_description: str) -> str:
    """크롤링된 브랜드 설명을 Gemini로 정제한다."""
    if not raw_description or len(raw_description.strip()) < 10:
        return raw_description or ""

    prompt = REFINE_PROMPT.format(
        brand_name=brand_name,
        raw_description=raw_description,
    )
    return call_gemini(prompt).strip()
