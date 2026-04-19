"""Gemini multimodal enrichment — 상품 이미지 + 텍스트 → 구조화 JSON."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time

import httpx
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator

from common.taxonomy import (
    FIT_FORBIDDEN,
    FIT_REQUIRED,
    Fit,
    Style,
    SubCategory,
    STYLES,
    is_valid_sub_category,
)
from etl.gold.prompts import build_prompt

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
MAX_IMAGES = 10
IMAGES_DIR = Path(__file__).resolve().parents[1] / "store" / "images"

_SCHEMA_PATH = Path(__file__).parent / "prompts" / "schema.json"
_RESPONSE_SCHEMA: dict | None = None


def _load_schema() -> dict:
    global _RESPONSE_SCHEMA
    if _RESPONSE_SCHEMA is None:
        _RESPONSE_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
    return _RESPONSE_SCHEMA


_keys: list[str] = []
_key_index: int = 0
_key_lock = __import__("threading").Lock()
_key_last_call: dict[str, float] = {}

# 키당 연속 호출 최소 간격(초). 키 6개 × 1s = 6 RPM 여유분.
# 환경변수로 튜닝 가능.
GEMINI_MIN_INTERVAL_PER_KEY = float(os.environ.get("GEMINI_MIN_INTERVAL_PER_KEY", "1.0"))

_STYLE_ALIASES: dict[str, str] = {
    "minimal": "minimalism",
    "minimalist": "minimalism",
    "streetwear": "street",
}
def _load_keys() -> list[str]:
    """GEMINI_API_KEY, GEMINI_API_KEY1, ... 또는 .env에서 로드."""
    import re

    keys: list[str] = []

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                m = re.match(r'GEMINI_API_KEY\d*\s*=\s*"?([^"\s]+)"?', line.strip())
                if m:
                    keys.append(m.group(1))

    if keys:
        return keys

    for k in sorted(os.environ):
        if re.match(r"GEMINI_API_KEY\d*$", k):
            val = os.environ[k].strip().strip('"')
            if val:
                keys.append(val)

    return keys


def _get_key() -> str:
    """키 round-robin + 키당 min-interval 대기. 20 worker 가 동시에 호출해도
    각 key 는 GEMINI_MIN_INTERVAL_PER_KEY 초 간격을 보장한다."""
    global _keys, _key_index

    if not _keys:
        _keys = _load_keys()
    if not _keys:
        raise RuntimeError("GEMINI_API_KEY 환경변수를 설정하세요.")

    # 락 안에서는 키 고르고 예약만 — sleep 은 밖에서 (다른 스레드 블록 X)
    with _key_lock:
        key = _keys[_key_index % len(_keys)]
        _key_index += 1

        last = _key_last_call.get(key, 0.0)
        now = time.monotonic()
        wait = GEMINI_MIN_INTERVAL_PER_KEY - (now - last)
        # 다음 호출의 기준점을 미리 기록 — wait 초 후 쓰이게.
        _key_last_call[key] = max(now, last + GEMINI_MIN_INTERVAL_PER_KEY)

    if wait > 0:
        time.sleep(wait)

    return key


def _load_local_image(path: Path) -> dict | None:
    """로컬 이미지 → Gemini inline_data 포맷."""
    try:
        data = path.read_bytes()
        ext = path.suffix.lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "gif": "image/gif"}.get(ext.lstrip("."), "image/jpeg")
        return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}
    except Exception as e:
        logger.debug("Failed to load image %s: %s", path, e)
        return None


def _build_image_parts_local(product_id: str) -> list[dict]:
    """로컬 store/images/{product_id}/ 에서 이미지 로드."""
    product_dir = IMAGES_DIR / product_id
    if not product_dir.exists():
        return []

    parts = []
    # thumb first
    for f in sorted(product_dir.iterdir()):
        if f.name.startswith("thumb"):
            part = _load_local_image(f)
            if part:
                parts.append(part)
            break

    # then gallery
    for f in sorted(product_dir.iterdir()):
        if len(parts) >= MAX_IMAGES:
            break
        if f.name.startswith("gallery_"):
            part = _load_local_image(f)
            if part:
                parts.append(part)

    return parts


def _download_image(url: str, client: httpx.Client) -> dict | None:
    """이미지 다운로드 → Gemini inline_data 포맷. (fallback용)"""
    try:
        resp = client.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        b64 = base64.b64encode(resp.content).decode()
        return {"inline_data": {"mime_type": ct, "data": b64}}
    except Exception as e:
        logger.debug("Image download failed %s: %s", url, e)
        return None


def _build_image_parts_remote(
    thumbnail_url: str | None,
    gallery_urls: list[str],
    client: httpx.Client,
) -> list[dict]:
    """URL에서 이미지 다운로드. (로컬 없을 때 fallback)"""
    urls: list[str] = []
    if thumbnail_url:
        urls.append(thumbnail_url)
    for u in gallery_urls:
        if len(urls) >= MAX_IMAGES:
            break
        if u != thumbnail_url:
            urls.append(u)

    parts = []
    for url in urls:
        part = _download_image(url, client)
        if part:
            parts.append(part)
    return parts


def _call_gemini(
    system_prompt: str,
    user_prompt: str,
    image_parts: list[dict],
    api_key: str,
    client: httpx.Client,
) -> dict:
    """Gemini API 호출 → parsed JSON dict."""
    user_parts: list[dict] = [{"text": user_prompt}] + image_parts

    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": user_parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _load_schema(),
            "temperature": 0.3,
        },
    }

    resp = client.post(
        f"{GEMINI_URL}?key={api_key}",
        json=body,
        timeout=60.0,
    )

    if resp.status_code == 429:
        raise RateLimitError("Gemini rate limited")

    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


class RateLimitError(Exception):
    pass


class StructuredValidationError(ValueError):
    """검증 실패 시 재시도 가능한 구조화 필드 에러."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


class StructuredEnrichmentPayload(BaseModel):
    """Gemini 응답의 structured field 검증 모델."""

    model_config = ConfigDict(extra="ignore")

    # ── Closed set (enum-typed, taxonomy.yaml 기준) ──
    sub_category: SubCategory
    fit: Fit | None = None
    style: list[Style] = Field(default_factory=list, min_length=1, max_length=3)

    # ── Open set (한국어 자유 텍스트) ──
    sub_category_detail: str = Field(min_length=1)
    material: list[str] = Field(default_factory=list, min_length=1)
    pattern: str = Field(min_length=1)
    details: list[str] = Field(default_factory=list, min_length=1)
    description_ko: str = Field(min_length=1)

    # ── Before validators: enum 필드 정리 ──

    @field_validator("sub_category", mode="before")
    @classmethod
    def _clean_sub_category(cls, v: str | None) -> str:
        return _clean_scalar(v).lower()

    @field_validator("fit", mode="before")
    @classmethod
    def _clean_fit(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = _clean_scalar(v).lower()
        return cleaned or None

    @field_validator("style", mode="before")
    @classmethod
    def _normalize_style(cls, v: list[str] | str | None) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return _normalize_styles(v)

    # ── Before validators: 한국어 텍스트 정리 ──

    @field_validator("sub_category_detail", "pattern", "description_ko", mode="before")
    @classmethod
    def _clean_open_str(cls, v: str | None) -> str:
        return _clean_scalar(v)

    @field_validator("material", "details", mode="before")
    @classmethod
    def _clean_korean_list(cls, v: list[str] | str | None) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return _normalize_korean_list(v)

    # ── Cross-field validation ──

    @model_validator(mode="after")
    def _validate_cross_fields(self, info: ValidationInfo) -> StructuredEnrichmentPayload:
        category = (info.context or {}).get("category")
        if not category:
            raise ValueError("missing validation context: category")

        if not is_valid_sub_category(category, self.sub_category):
            raise ValueError(
                f"sub_category '{self.sub_category}' invalid for '{category}'"
            )

        if category in FIT_REQUIRED and not self.fit:
            raise ValueError(f"fit required for category '{category}'")
        if category in FIT_FORBIDDEN and self.fit:
            raise ValueError(f"fit must be null for category '{category}'")

        return self


class EnrichmentResult:
    """Gemini enrichment 결과."""

    __slots__ = (
        "sub_category", "sub_category_detail", "fit",
        "material", "style", "pattern", "details",
        "description_ko", "raw",
    )

    def __init__(self, data: dict, category: str):
        self.raw = data
        payload = StructuredEnrichmentPayload.model_validate(
            data,
            context={"category": category},
        )
        self.sub_category = payload.sub_category
        self.sub_category_detail = payload.sub_category_detail
        self.fit = payload.fit
        self.material = payload.material
        self.style = payload.style
        self.pattern = payload.pattern
        self.details = payload.details
        self.description_ko = _normalize_description(payload.description_ko)

    @property
    def enrichment_meta(self) -> dict:
        """JSONB에 저장할 메타데이터."""
        return {
            "sub_category_detail": self.sub_category_detail,
            "material": self.material,
            "style": self.style,
            "pattern": self.pattern,
            "details": self.details,
        }


def _clean_scalar(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalize_styles(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        style = _clean_scalar(value).lower().replace("-", "_")
        style = style.replace(" ", "_")
        style = _STYLE_ALIASES.get(style, style)
        if style in STYLES:
            normalized.append(style)
    return _dedupe_keep_order(normalized)


def _normalize_korean_list(values: list[str]) -> list[str]:
    cleaned = [_clean_scalar(v) for v in values]
    return _dedupe_keep_order([v for v in cleaned if v])


def _normalize_description(value: str | None) -> str:
    return _clean_scalar(value)


def _format_validation_error(exc: ValidationError) -> list[str]:
    issues: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ()))
        msg = error.get("msg", "validation error")
        issues.append(f"{loc}: {msg}" if loc else msg)
    return issues or ["structured field validation failed"]


def _build_retry_prompt(base_user_prompt: str, issues: list[str], data: dict) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in issues[:6])
    previous_json = json.dumps(data, ensure_ascii=False)
    return (
        f"{base_user_prompt}\n\n"
        "--- Validation Feedback ---\n"
        "The previous JSON did not pass structured-field validation.\n"
        "Fix the JSON and return the full corrected JSON only.\n"
        f"{issue_lines}\n"
        f"Previous JSON:\n{previous_json}"
    )


def enrich_product(
    *,
    product_id: str | None = None,
    category: str,
    name: str,
    brand: str,
    description: str | None,
    thumbnail_url: str | None = None,
    gallery_urls: list[str] | None = None,
    include_images: bool = True,
    client: httpx.Client | None = None,
    api_key: str | None = None,
) -> EnrichmentResult:
    """단일 상품 enrichment. 로컬 이미지 우선, 없으면 URL 다운로드."""
    provided_api_key = api_key
    own_client = client is None
    if own_client:
        client = httpx.Client()

    try:
        system_prompt, user_prompt = build_prompt(category, name, brand, description)
        base_user_prompt = user_prompt

        image_parts = []
        if include_images:
            # 로컬 이미지 우선
            if product_id:
                image_parts = _build_image_parts_local(product_id)
            # 로컬 없으면 URL 다운로드
            if not image_parts and thumbnail_url:
                image_parts = _build_image_parts_remote(
                    thumbnail_url, gallery_urls or [], client,
                )

        last_error = None
        for attempt in range(3):
            current_api_key = provided_api_key or _get_key()
            try:
                data = _call_gemini(
                    system_prompt, user_prompt, image_parts, current_api_key, client,
                )
                return EnrichmentResult(data, category)
            except RateLimitError:
                wait = 10 * (attempt + 1)
                logger.warning("Rate limited, waiting %ds...", wait)
                time.sleep(wait)
                last_error = RateLimitError()
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Parse error (attempt %d): %s", attempt + 1, e)
                last_error = e
            except ValidationError as e:
                issues = _format_validation_error(e)
                logger.warning(
                    "Structured validation failed (attempt %d): %s",
                    attempt + 1,
                    "; ".join(issues),
                )
                user_prompt = _build_retry_prompt(base_user_prompt, issues, data)
                last_error = StructuredValidationError(issues)
            except Exception as e:
                logger.warning("Gemini call failed (attempt %d): %s", attempt + 1, e)
                last_error = e

        raise RuntimeError(f"Enrichment failed after 3 attempts: {last_error}")
    finally:
        if own_client:
            client.close()
