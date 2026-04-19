"""Detection API 요청/응답 스키마."""

from enum import Enum, StrEnum

from pydantic import BaseModel, Field


# --- Category ---


class Category(StrEnum):
    OUTER = "outer"
    TOP = "top"
    BOTTOM = "bottom"


# --- 요청 ---


class ClassifyRequest(BaseModel):
    """단건 분류 요청."""
    url: str


class ClassifyBatchRequest(BaseModel):
    """배치 분류 요청."""
    urls: list[str]


# --- 응답 ---


class Detection(BaseModel):
    """단일 detection 결과."""
    class_name: Category
    class_id: int
    confidence: float
    bbox: list[float] = Field(description="[x1, y1, x2, y2]")


class ClassifyResponse(BaseModel):
    """분류 응답."""
    url: str
    category: Category | None = None
    confidence: float | None = None
    detections: list[Detection] = []


class ClassifyBatchResponse(BaseModel):
    """배치 분류 응답."""
    results: list[ClassifyResponse]


class FilterGalleryResponse(BaseModel):
    """갤러리 필터링 응답. 옷 감지 여부로 분리."""
    clothing: list[str] = []
    non_clothing: list[str] = []
    errors: list[str] = []


class HealthResponse(BaseModel):
    """서버 상태."""
    status: str
    model_loaded: bool
    providers: list[str] = []


# --- 에러 ---


class ErrorCode(Enum):
    """Detection API 에러 코드."""

    IMAGE_FETCH_FAILED = "IMAGE_FETCH_FAILED"
    DETECTION_FAILED = "DETECTION_FAILED"
    MODEL_NOT_LOADED = "MODEL_NOT_LOADED"
    INVALID_INPUT = "INVALID_INPUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    @property
    def code(self) -> str:
        return self.value

    @property
    def message(self) -> str:
        return {
            ErrorCode.IMAGE_FETCH_FAILED: "Failed to fetch image from URL.",
            ErrorCode.DETECTION_FAILED: "Detection inference failed.",
            ErrorCode.MODEL_NOT_LOADED: "Model is not loaded.",
            ErrorCode.INVALID_INPUT: "Invalid input provided.",
            ErrorCode.INTERNAL_ERROR: "An internal server error occurred.",
        }[self]

    @property
    def status_code(self) -> int:
        return {
            ErrorCode.IMAGE_FETCH_FAILED: 422,
            ErrorCode.DETECTION_FAILED: 500,
            ErrorCode.MODEL_NOT_LOADED: 503,
            ErrorCode.INVALID_INPUT: 400,
            ErrorCode.INTERNAL_ERROR: 500,
        }[self]


class ErrorResponse(BaseModel):
    """표준 에러 응답."""
    code: str
    message: str
    detail: str | None = None
