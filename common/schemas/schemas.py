from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Platform(BaseModel):
    name: str
    base_url: str
    type: str = "select_shop"  # "select_shop" | "brand_shop"



class CrawlMetadata(BaseModel):
    platform: str
    crawled_at: datetime
    source_url: str


class CrawlError(BaseModel):
    url: str
    error_type: str
    message: str
    occurred_at: datetime


class RawProduct(BaseModel):
    # 식별
    platform_product_id: str
    name: str
    brand: str
    url: str

    # 상품 속성
    category: str | None = None
    original_price: int | None = None
    discounted_price: int | None = None
    sold_out: bool = False


    # 이미지 — 종류별 분리
    thumbnail_url: str | None = None
    gallery_image_urls: list[str] = []
    description_image_urls: list[str] = []

    # 텍스트
    description_txt: str | None = None

    # 메타
    metadata: CrawlMetadata

    # 사이트별 확장
    extra_fields: dict[str, Any] = {}


class RawBrand(BaseModel):
    platform_brand_id: str | None = None
    name: str
    url: str
    name_eng: str | None = None
    logo_url: str | None = None
    description: str | None = None
    metadata: CrawlMetadata
    extra_fields: dict[str, Any] = {}


class CrawlResult(BaseModel):
    brand: RawBrand | None = None
    products: list[RawProduct] = []
    platform: Platform
    crawl_started_at: datetime
    crawl_finished_at: datetime
    total_count: int
    error_count: int
    errors: list[CrawlError] = []
