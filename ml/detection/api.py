"""YOLO Detection API 서버 (ONNX Runtime).

사용:
    uvicorn ml.detection.api:app --host 0.0.0.0 --port 8001
"""

import dataclasses
import logging
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

from ml.detection.schemas import (
    Category,
    ClassifyBatchRequest,
    ClassifyBatchResponse,
    ClassifyRequest,
    ClassifyResponse,
    Detection,
    ErrorCode,
    ErrorResponse,
    FilterGalleryResponse,
    HealthResponse,
)

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "runs" / "detect" / "menswear_v1_3class_b64_e100" / "weights" / "best.onnx"
CLASS_MAP: dict[int, Category] = {0: Category.OUTER, 1: Category.TOP, 2: Category.BOTTOM}
INPUT_SIZE = 640
CONF_THRESHOLD = 0.25


# --- AppCtx ---


@dataclasses.dataclass(frozen=True)
class AppCtx:
    """Detection API 리소스 컨텍스트."""

    session: ort.InferenceSession
    http: httpx.Client

    def cleanup(self) -> None:
        self.http.close()


def _get_ctx(request: Request) -> AppCtx:
    return request.app.state.ctx


# --- 에러 ---


class DetectionError(Exception):
    """Detection API 에러."""

    def __init__(self, error_code: ErrorCode, detail: str | None = None) -> None:
        super().__init__(error_code.message)
        self.error_code = error_code
        self.detail = detail


# --- 전처리 / 후처리 ---


def _preprocess(img: Image.Image) -> tuple[np.ndarray, float, int, int]:
    """이미지를 YOLO 입력 형태로 전처리. letterbox + normalize."""
    w, h = img.size
    scale = min(INPUT_SIZE / w, INPUT_SIZE / h)
    new_w, new_h = int(w * scale), int(h * scale)

    resized = img.resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (INPUT_SIZE, INPUT_SIZE), (114, 114, 114))
    pad_x, pad_y = (INPUT_SIZE - new_w) // 2, (INPUT_SIZE - new_h) // 2
    canvas.paste(resized, (pad_x, pad_y))

    arr = np.array(canvas, dtype=np.float32) / 255.0
    arr = arr.transpose(2, 0, 1)[np.newaxis]  # HWC → NCHW
    return arr, scale, pad_x, pad_y


def _postprocess(
    output: np.ndarray, scale: float, pad_x: int, pad_y: int,
) -> list[Detection]:
    """ONNX 출력 (1, 7, 8400) → Detection 리스트. NMS 포함."""
    preds = output[0].T  # (8400, 7)

    class_scores = preds[:, 4:]
    max_scores = class_scores.max(axis=1)
    mask = max_scores > CONF_THRESHOLD
    preds = preds[mask]
    if len(preds) == 0:
        return []

    class_scores = preds[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    confidences = class_scores[np.arange(len(preds)), class_ids]

    cx, cy, bw, bh = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
    x1 = (cx - bw / 2 - pad_x) / scale
    y1 = (cy - bh / 2 - pad_y) / scale
    x2 = (cx + bw / 2 - pad_x) / scale
    y2 = (cy + bh / 2 - pad_y) / scale

    boxes = np.stack([x1, y1, x2, y2], axis=1)
    keep = _nms(boxes, confidences, iou_threshold=0.5)

    return [
        Detection(
            class_name=CLASS_MAP.get(int(class_ids[i]), Category.TOP),
            class_id=int(class_ids[i]),
            confidence=float(confidences[i]),
            bbox=[float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i])],
        )
        for i in keep
    ]


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Numpy NMS."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while len(order) > 0:
        i = order[0]
        keep.append(i)
        if len(order) == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= iou_threshold]
    return keep


# --- 추론 ---


def _fetch_image(ctx: AppCtx, url: str) -> Image.Image:
    """URL에서 이미지 다운로드."""
    try:
        resp = ctx.http.get(url, timeout=10)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        raise DetectionError(ErrorCode.IMAGE_FETCH_FAILED, detail=f"{url}: {e}") from e


def _detect(ctx: AppCtx, url: str) -> ClassifyResponse:
    """단일 이미지 detection → 단독 detect 우선 로직."""
    img = _fetch_image(ctx, url)

    try:
        input_tensor, scale, pad_x, pad_y = _preprocess(img)
        input_name = ctx.session.get_inputs()[0].name
        output = ctx.session.run(None, {input_name: input_tensor})[0]
        detections = _postprocess(output, scale, pad_x, pad_y)
    except DetectionError:
        raise
    except Exception as e:
        raise DetectionError(ErrorCode.DETECTION_FAILED, detail=str(e)) from e

    if not detections:
        return ClassifyResponse(url=url)

    if len(detections) == 1:
        return ClassifyResponse(
            url=url,
            category=detections[0].class_name,
            confidence=detections[0].confidence,
            detections=detections,
        )

    best = max(detections, key=lambda d: d.confidence)
    return ClassifyResponse(
        url=url,
        category=best.class_name,
        confidence=best.confidence,
        detections=detections,
    )


def _classify_gallery(ctx: AppCtx, urls: list[str]) -> ClassifyResponse:
    """갤러리 이미지 순회 — 단독 detect 우선, 최대 5장."""
    best_fallback: ClassifyResponse | None = None

    for url in urls[:5]:
        try:
            result = _detect(ctx, url)
        except DetectionError as e:
            logger.warning("Gallery image skipped: %s", e.detail)
            continue

        if not result.category:
            continue

        if len(result.detections) == 1:
            return result

        if best_fallback is None or (result.confidence or 0) > (best_fallback.confidence or 0):
            best_fallback = result

    return best_fallback or ClassifyResponse(url=urls[0] if urls else "")


# --- FastAPI ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    logger.info("Loading ONNX model: %s (providers: %s)", WEIGHTS_PATH, providers)
    session = ort.InferenceSession(str(WEIGHTS_PATH), providers=providers)
    logger.info("Model loaded — active providers: %s", session.get_providers())

    http = httpx.Client(
        headers={"User-Agent": "GyeolDetection/1.0"},
        follow_redirects=True,
    )

    app.state.ctx = AppCtx(session=session, http=http)
    yield
    app.state.ctx.cleanup()


app = FastAPI(title="Gyeol Detection API", lifespan=lifespan)


@app.exception_handler(DetectionError)
async def detection_error_handler(_request: Request, exc: DetectionError) -> JSONResponse:
    body = ErrorResponse(
        code=exc.error_code.code,
        message=exc.error_code.message,
        detail=exc.detail,
    )
    return JSONResponse(status_code=exc.error_code.status_code, content=body.model_dump())


@app.exception_handler(Exception)
async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    body = ErrorResponse(
        code=ErrorCode.INTERNAL_ERROR.code,
        message=ErrorCode.INTERNAL_ERROR.message,
        detail=str(exc),
    )
    return JSONResponse(status_code=500, content=body.model_dump())


@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest, request: Request) -> ClassifyResponse:
    """단일 이미지 분류."""
    return _detect(_get_ctx(request), req.url)


@app.post("/classify/gallery", response_model=ClassifyResponse)
def classify_gallery(req: ClassifyBatchRequest, request: Request) -> ClassifyResponse:
    """갤러리 이미지 순회 분류. 단독 detect 우선."""
    return _classify_gallery(_get_ctx(request), req.urls)


@app.post("/filter/gallery", response_model=FilterGalleryResponse)
def filter_gallery(req: ClassifyBatchRequest, request: Request) -> FilterGalleryResponse:
    """갤러리 이미지를 옷 감지 여부로 분리."""
    ctx = _get_ctx(request)
    clothing: list[str] = []
    non_clothing: list[str] = []
    errors: list[str] = []

    for url in req.urls:
        try:
            result = _detect(ctx, url)
            if result.detections:
                clothing.append(url)
            else:
                non_clothing.append(url)
        except DetectionError:
            errors.append(url)

    return FilterGalleryResponse(
        clothing=clothing,
        non_clothing=non_clothing,
        errors=errors,
    )


@app.post("/classify/batch", response_model=ClassifyBatchResponse)
def classify_batch(req: ClassifyBatchRequest, request: Request) -> ClassifyBatchResponse:
    """여러 상품의 thumbnail 일괄 분류."""
    ctx = _get_ctx(request)
    results = []
    for url in req.urls:
        try:
            results.append(_detect(ctx, url))
        except DetectionError as e:
            logger.warning("Batch item skipped: %s", e.detail)
            results.append(ClassifyResponse(url=url))
    return ClassifyBatchResponse(results=results)


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """서버 상태."""
    ctx = _get_ctx(request)
    return HealthResponse(
        status="ok",
        model_loaded=ctx.session is not None,
        providers=ctx.session.get_providers(),
    )
