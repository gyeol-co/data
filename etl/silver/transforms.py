"""Silver 정제 규칙.

상품명 정리 + 가격 정규화.
"""

import re

# --- 상품명 정리 ---

# etcseoul: "아트 이프 액츠_Layered Long Sleeve [Black/Cliff]"
#         → "Layered Long Sleeve [Black/Cliff]"
# 패턴: 한글 브랜드명_ 또는 한글 브랜드명 _ (공백 포함)
_ETCSEOUL_PREFIX = re.compile(r"^[가-힣\s]+_\s*")

# modeman: "01-8270-84 Relax Fit Chambray Work Shirt Indigo"
#        → "Relax Fit Chambray Work Shirt Indigo"
# 패턴: 숫자-숫자 또는 숫자-숫자-숫자 prefix
_MODEMAN_MODEL_NO = re.compile(r"^\d{2}-\d{3,5}(?:-\d{2,3})?\s+")

# sculpstore warehouse: "(EXCLUSIVE) LOT 2ND-HAND ..." → "LOT 2ND-HAND ..."
_SCULPSTORE_EXCLUSIVE = re.compile(r"^\(EXCLUSIVE\)\s*", re.IGNORECASE)


def clean_name(platform: str, name: str) -> str:
    """플랫폼별 상품명 정리. 원본이 모호하면 그대로 반환."""
    cleaned = name.strip()

    if platform == "etcseoul":
        cleaned = _ETCSEOUL_PREFIX.sub("", cleaned)

    elif platform == "modeman":
        cleaned = _MODEMAN_MODEL_NO.sub("", cleaned)

    elif platform == "sculpstore":
        cleaned = _SCULPSTORE_EXCLUSIVE.sub("", cleaned)

    # 공통: 앞뒤 공백, 연속 공백 정리
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned if cleaned else name


# --- base_name 추출 (색상/변형 제거) ---

# etcseoul: "Layered Long Sleeve [Black/Cliff]" → "Layered Long Sleeve"
_BRACKET_SUFFIX = re.compile(r"\s*_?\s*\[[^\]]+\]\s*$")

# barbershop: "Watch Cap - Smoke" → "Watch Cap"
# bastong: "307 NAVY" → "307"
_BASTONG_MODEL = re.compile(r"^(\d{2,4})\b")


def extract_base_name(platform: str, cleaned_name: str) -> str | None:
    """색상/변형 제거한 제품 본명. 패턴 없는 플랫폼은 None."""
    if platform == "etcseoul":
        return _BRACKET_SUFFIX.sub("", cleaned_name).strip() or None

    if platform == "barbershop":
        base = cleaned_name.split(" - ", 1)[0].strip()
        return base or None

    if platform == "bastong":
        m = _BASTONG_MODEL.match(cleaned_name)
        return m.group(1) if m else None

    # sculpstore, modeman → 패턴 불가, 별도 처리
    return None


# --- color 추출 (name - base_name 차분) ---

_BRACKET_COLOR = re.compile(r"\[([^\]]+)\]\s*$")


def extract_color(platform: str, cleaned_name: str, base_name: str | None) -> str | None:
    """name에서 base_name을 빼고 남는 색상 부분 추출. 패턴 없는 플랫폼은 None."""
    if not base_name:
        return None

    if platform == "etcseoul":
        m = _BRACKET_COLOR.search(cleaned_name)
        return m.group(1).strip() if m else None

    if platform == "barbershop":
        parts = cleaned_name.split(" - ", 1)
        return parts[1].strip() if len(parts) == 2 else None

    if platform == "bastong":
        suffix = cleaned_name[len(base_name):].strip()
        return suffix or None

    # sculpstore, modeman → Codex가 처리
    return None


def parse_name(platform: str, name: str) -> tuple[str, str | None, str | None]:
    """상품명 정리 + base_name + color 한번에 추출.

    Returns:
        (cleaned_name, base_name, color)
    """
    cleaned = clean_name(platform, name)
    base = extract_base_name(platform, cleaned)
    color = extract_color(platform, cleaned, base)
    return cleaned, base, color


# --- 가격 정규화 ---

PRICE_MIN = 1_000
PRICE_MAX = 10_000_000


def validate_price(
    original_price: int | None,
    discounted_price: int | None,
) -> tuple[int | None, int | None]:
    """가격 범위 체크 + 할인가 검증."""
    # 범위 밖이면 None
    if original_price is not None and not (PRICE_MIN <= original_price <= PRICE_MAX):
        original_price = None

    if discounted_price is not None and not (PRICE_MIN <= discounted_price <= PRICE_MAX):
        discounted_price = None

    # 할인가 >= 원가면 할인 아님
    if original_price and discounted_price and discounted_price >= original_price:
        discounted_price = None

    # 원가 없는데 할인가만 있으면 원가로 승격
    if original_price is None and discounted_price is not None:
        original_price = discounted_price
        discounted_price = None

    return original_price, discounted_price
