FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime deps from pyproject.toml (no dev deps).
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /app/data
ENV GTFS_CACHE_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=2).status==200 else 1)"

CMD ["uvicorn", "ptv_departures.main:app", "--host", "0.0.0.0", "--port", "8000"]
