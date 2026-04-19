"""Detection API 클라이언트.

elib external_api 패턴 기반: Pydantic 응답 검증 + retry + 커스텀 에러.

사용:
    async with DetectionClient() as client:
        result = await client.classify("https://example.com/image.jpg")
        result = await client.classify_gallery(["url1", "url2", ...])
        results = await client.classify_batch(["url1", "url2", ...])
"""

import asyncio
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel

from ml.detection.schemas import (
    ClassifyBatchResponse,
    ClassifyResponse,
    ErrorResponse,
    FilterGalleryResponse,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class DetectionApiError(Exception):
    """Detection API 호출 실패."""

    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class DetectionClient:
    """Detection API 클라이언트."""

    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        response_model: type[T],
        json_data: dict | None = None,
    ) -> T:
        """HTTP 요청 + Pydantic 응답 검증 + retry."""
        client = await self._ensure_client()
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.request(method, url, json=json_data, timeout=self.timeout)
                resp.raise_for_status()
                return response_model(**resp.json())

            except httpx.HTTPStatusError as e:
                # 서버가 ErrorResponse 형태로 응답했으면 파싱
                detail: dict = {"url": url, "status": e.response.status_code, "attempt": attempt + 1}
                try:
                    err = ErrorResponse(**e.response.json())
                    detail["code"] = err.code
                    detail["server_message"] = err.message
                except Exception:
                    pass
                last_error = DetectionApiError(
                    message=f"HTTP {e.response.status_code}",
                    detail=detail,
                )
                if e.response.status_code >= 500 and attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise last_error from e

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = DetectionApiError(
                    message=f"Network error: {type(e).__name__}",
                    detail={"url": url, "error": str(e), "attempt": attempt + 1},
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise last_error from e

        raise last_error or DetectionApiError("No attempts made")

    # --- Public API ---

    async def classify(self, image_url: str) -> ClassifyResponse:
        """단일 이미지 분류."""
        return await self._request(
            "POST", "/classify",
            response_model=ClassifyResponse,
            json_data={"url": image_url},
        )

    async def classify_gallery(self, gallery_urls: list[str]) -> ClassifyResponse:
        """갤러리 이미지 순회 분류. 단독 detect 우선."""
        return await self._request(
            "POST", "/classify/gallery",
            response_model=ClassifyResponse,
            json_data={"urls": gallery_urls},
        )

    async def filter_gallery(self, gallery_urls: list[str]) -> FilterGalleryResponse:
        """갤러리 이미지를 옷 감지 여부로 분리."""
        return await self._request(
            "POST", "/filter/gallery",
            response_model=FilterGalleryResponse,
            json_data={"urls": gallery_urls},
        )

    async def classify_batch(self, image_urls: list[str]) -> ClassifyBatchResponse:
        """여러 상품의 thumbnail 일괄 분류."""
        return await self._request(
            "POST", "/classify/batch",
            response_model=ClassifyBatchResponse,
            json_data={"urls": image_urls},
        )

    async def health(self) -> bool:
        """서버 상태 확인."""
        try:
            client = await self._ensure_client()
            resp = await client.get(f"{self.base_url}/health")
            return resp.status_code == 200
        except Exception:
            return False
