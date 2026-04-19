FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3-pip curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages \
    onnxruntime-gpu \
    fastapi \
    uvicorn[standard] \
    httpx \
    pillow \
    numpy

COPY ml/ ml/

EXPOSE 8001
CMD ["uvicorn", "ml.detection.api:app", "--host", "0.0.0.0", "--port", "8001"]
