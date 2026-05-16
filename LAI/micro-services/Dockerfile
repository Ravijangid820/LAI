FROM python:3.11-slim

WORKDIR /app

# System dependencies:
#   tesseract-ocr + deu/eng  — OCR fallback for scanned PDFs
#   libgeos-dev              — C library for Shapely spatial operations (polygon intersection)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    libgeos-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Build context: the LAI/ repo root. Compose sets context: .. so this Dockerfile
# can reach both micro-services/ (the app) and src/lai/ (the shared library).

# 1) Microservice runtime deps + lai.common's transitive runtime deps (listed
#    explicitly in requirements.txt). We do not let lai's own pyproject pull its
#    full dep set — that would drag in vllm, docling, celery, asyncpg, … none of
#    which this container uses.
COPY micro-services/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 2) The lai package itself. --no-deps because we just installed everything
#    lai.common actually imports above. The hatchling build only needs
#    pyproject.toml + README.md + src/lai/.
COPY pyproject.toml /tmp/lai_src/pyproject.toml
COPY README.md /tmp/lai_src/README.md
COPY src/lai /tmp/lai_src/src/lai
RUN pip install --no-cache-dir --no-deps /tmp/lai_src && rm -rf /tmp/lai_src

# 3) Microservice application code.
COPY micro-services/ /app/

# Non-root user for production security
RUN useradd -m -r appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
