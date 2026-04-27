FROM python:3.11-slim-bookworm

# OpenCV runtime libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-runtime.txt

COPY src/ ./services/detector/src/
COPY models/efficientdet_lite0.tflite /opt/models/efficientdet_lite0.tflite

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.detector.src.main"]
