FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY packages/domain /app/packages/domain
COPY apps/api/pyproject.toml /app/apps/api/pyproject.toml

RUN pip install --no-cache-dir ./packages/domain \
 && pip install --no-cache-dir \
      "fastapi>=0.115,<1.0" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0.30" \
      "alembic>=1.13" "pydantic[email]>=2.7" "pydantic-settings>=2.3" \
      "python-multipart>=0.0.9" "pyjwt>=2.8" "psycopg[binary]>=3.1"

COPY apps/api /app/apps/api

ENV PYTHONPATH=/app/apps/api/src

# Runs as a non-root user: nothing in the image needs to be written to.
RUN useradd --system --uid 10001 metreo && chown -R metreo /app
USER metreo

EXPOSE 8000
CMD ["uvicorn", "metreo_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
