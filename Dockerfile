# Photo Quality Engine
# Build : docker build -t photo-quality-engine .
# Tanpa OCR (image lebih kecil): docker build --build-arg WITH_OCR=false -t photo-quality-engine .
FROM python:3.12-slim

ARG WITH_OCR=true
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PQE_TENANTS_DIR=/app/config/tenants

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libglib2.0-0 libgomp1 \
 && if [ "$WITH_OCR" = "true" ]; then \
      apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind; \
    fi \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml README.md ./
COPY engine ./engine
RUN pip install --no-deps .
COPY config ./config

RUN useradd --system --uid 10001 --home /app pqe && chown -R pqe /app
USER pqe

EXPOSE 8000 9100
# API/run: /healthz di :8000. Worker/ingestor: endpoint metrik di :PQE_METRICS_PORT (default 9100).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || curl -fsS "http://127.0.0.1:${PQE_METRICS_PORT:-9100}/metrics" >/dev/null || exit 1

# Mode default: API + worker + ingestor dalam satu proses.
# Di Kubernetes jalankan terpisah: `pqe api`, `pqe worker`, `pqe ingestor`.
CMD ["pqe", "run"]
